"""WOO X order-history CSV parser.

Handles exports with columns such as::

    Order ID, Create Time, Instrument, Type, Side, Price, Quantity, Executed,
    Average Price, Status, Total Fee, Fee Token, Realized Pnl

Perpetual and spot orders (``PERP_BTC_USDT``, ``PERP_1000FLOKI_USDT``, …) are
mapped to BUY/SELL rows using ``Side`` and notional value (executed × avg price).
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional, Tuple

import pandas as pd

from .config import STABLECOIN_ASSETS
from .instruments import format_perp_contract, parse_exchange_instrument
from .kraken import clean_columns, normalize_asset
from .schemas import Transaction, TransactionType


def _squash_column(name: str) -> str:
    return str(name).lower().replace(" ", "_").strip("_")


def _prepare_df(df: pd.DataFrame) -> pd.DataFrame:
    out = clean_columns(df)
    out.columns = [_squash_column(c) for c in out.columns]
    return out


def is_woox_export(df: pd.DataFrame) -> bool:
    """True when the CSV matches the WOO X order-history layout."""
    cols = set(_prepare_df(df).columns)
    return {
        "order_id",
        "create_time",
        "instrument",
        "side",
        "executed",
        "average_price",
        "realized_pnl",
    }.issubset(cols)


def _parse_time(raw: object) -> datetime:
    ts = pd.to_datetime(raw, utc=True, errors="coerce")
    if pd.isna(ts):
        raise ValueError(f"Unparseable timestamp: {raw!r}")
    return ts.to_pydatetime()


def _float(raw: object) -> float:
    if raw is None:
        return 0.0
    text = str(raw).strip()
    if not text or text.lower() in {"-", "null", "nan", "none"}:
        return 0.0
    if isinstance(raw, float) and pd.isna(raw):
        return 0.0
    return float(text)


def _parse_instrument(instrument: str) -> Tuple[str, str, str]:
    """``PERP_BTC_USDT`` → (``BTC``, ``USDT``, ``perp``)."""
    return parse_exchange_instrument(instrument)


def _optional_pnl(raw: object) -> Optional[float]:
    text = str(raw or "").strip()
    if not text or text.lower() in {"-", "null", "nan", "none"}:
        return None
    return float(text)


def _side_to_type(side: str, order_type: str) -> TransactionType:
    side_u = str(side or "").strip().upper()
    type_u = str(order_type or "").strip().upper()
    if side_u == "BUY":
        return TransactionType.BUY
    if side_u == "SELL" or type_u == "LIQUIDATE":
        return TransactionType.SELL
    raise ValueError(f"Unknown WOO X side/type: {side!r} / {order_type!r}")


def _fee_fiat(fee: float, fee_token: Optional[str]) -> float:
    token = normalize_asset(str(fee_token or ""))
    if not fee or not token:
        return 0.0
    if token in STABLECOIN_ASSETS or token in {"USD", "EUR", "GBP"}:
        return round(fee, 8)
    return 0.0


def parse_woox_export(df: pd.DataFrame) -> List[Transaction]:
    """Parse a WOO X order-history CSV into unified transactions."""
    data = _prepare_df(df)
    transactions: List[Transaction] = []

    for row in data.to_dict(orient="records"):
        status = str(row.get("status", "")).strip().upper()
        if status and status != "FILLED":
            continue

        executed = _float(row.get("executed"))
        if executed <= 0:
            executed = _float(row.get("quantity"))
        if executed <= 0:
            continue

        avg_price = _float(row.get("average_price"))
        if avg_price <= 0:
            avg_price = _float(row.get("price"))
        if avg_price <= 0:
            continue

        instrument = str(row.get("instrument", ""))
        asset, quote, kind = _parse_instrument(instrument)
        tx_type = _side_to_type(
            str(row.get("side", "")),
            str(row.get("type", "")),
        )
        order_type = str(row.get("type", "")).strip().upper() or None

        notional = round(executed * avg_price, 2)
        order_id = str(row.get("order_id", "")).strip()
        fee = _fee_fiat(_float(row.get("total_fee")), row.get("fee_token"))
        pnl = _optional_pnl(row.get("realized_pnl"))

        transactions.append(
            Transaction(
                id=(
                    f"woox-{order_id}"
                    if order_id
                    else f"woox-{_parse_time(row.get('create_time')).isoformat()}-{instrument}-{tx_type.value}-{executed}-{avg_price}"
                ),
                timestamp=_parse_time(row.get("create_time")),
                asset=asset,
                transaction_type=tx_type,
                amount=executed,
                fiat_value_at_trigger=notional,
                fee_fiat=fee,
                fiat_currency=quote,
                counter_asset=quote,
                trade_group_id=order_id or None,
                source="woox",
                instrument_kind=kind,
                instrument=format_perp_contract(asset, quote) if kind == "perp" else (instrument or None),
                venue_order_type=order_type,
                realized_pnl=pnl,
            )
        )

    return transactions
