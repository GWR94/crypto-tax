"""Variational perps CSV parser.

Supports two export shapes:

**Transfers** (deposits, funding, realized PnL settlements)::

    id,created_at,qty,asset,transfer_type,status,underlying,instrument_type,...

**Trades** (fills / liquidations)::

    id,created_at,side,instrument_type,underlying,price,qty,trade_type,status,...
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional, Tuple

import pandas as pd

from .kraken import clean_columns
from .instruments import _clean_symbol, format_perp_contract
from .schemas import Transaction, TransactionType

SOURCE = "variational"


def _squash_column(name: str) -> str:
    return str(name).lower().replace(" ", "_").strip("_")


def _prepare_df(df: pd.DataFrame) -> pd.DataFrame:
    out = clean_columns(df)
    out.columns = [_squash_column(c) for c in out.columns]
    return out


def is_variational_export(df: pd.DataFrame) -> bool:
    cols = set(_prepare_df(df).columns)
    transfer = {"id", "created_at", "qty", "asset", "transfer_type"}.issubset(cols)
    trade = {"id", "created_at", "side", "underlying", "price", "qty", "trade_type"}.issubset(
        cols
    )
    return transfer or trade


def _is_transfer_export(df: pd.DataFrame) -> bool:
    return "transfer_type" in set(_prepare_df(df).columns)


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


def _confirmed(row: dict) -> bool:
    status = str(row.get("status", "")).strip().lower()
    return not status or status == "confirmed"


def _optional_asset(raw: object) -> str:
    return _clean_symbol(raw)


def _parse_trade_row(row: dict) -> Optional[Transaction]:
    if not _confirmed(row):
        return None

    qty = abs(_float(row.get("qty")))
    price = _float(row.get("price"))
    if qty <= 0 or price <= 0:
        return None

    underlying = _optional_asset(row.get("underlying"))
    if not underlying:
        return None

    side = str(row.get("side", "")).strip().upper()
    if side == "BUY":
        tx_type = TransactionType.BUY
    elif side == "SELL":
        tx_type = TransactionType.SELL
    else:
        raise ValueError(f"Unknown Variational side: {side!r}")

    row_id = str(row.get("id", "")).strip()
    trade_type = str(row.get("trade_type", "")).strip().lower() or None
    notional = round(qty * price, 2)

    return Transaction(
        id=f"variational-{row_id}" if row_id else f"variational-trade-{qty}",
        timestamp=_parse_time(row.get("created_at")),
        asset=underlying,
        transaction_type=tx_type,
        amount=qty,
        fiat_value_at_trigger=notional,
        fee_fiat=0.0,
        fiat_currency="USDC",
        counter_asset="USDC",
        trade_group_id=row_id or None,
        source=SOURCE,
        instrument_kind="perp",
        instrument=format_perp_contract(underlying),
        venue_order_type=trade_type,
    )


def _parse_transfer_row(row: dict) -> Optional[Transaction]:
    if not _confirmed(row):
        return None

    qty = _float(row.get("qty"))
    if qty == 0:
        return None

    asset = _optional_asset(row.get("asset"))
    if not asset:
        return None

    transfer_type = str(row.get("transfer_type", "")).strip().lower()
    row_id = str(row.get("id", "")).strip()
    underlying = _optional_asset(row.get("underlying"))

    if transfer_type == "deposit":
        return Transaction(
            id=f"variational-{row_id}" if row_id else f"variational-deposit-{len(asset)}",
            timestamp=_parse_time(row.get("created_at")),
            asset=asset,
            transaction_type=TransactionType.TRANSFER,
            amount=abs(qty),
            fiat_value_at_trigger=round(abs(qty), 2) if asset in {"USDC", "USDT"} else 0.0,
            fee_fiat=0.0,
            fiat_currency=asset if asset in {"USDC", "USDT"} else None,
            transfer_direction="IN",
            source=SOURCE,
            instrument_kind="perp",
            instrument=format_perp_contract(underlying, asset) if underlying else format_perp_contract("", asset),
            venue_order_type="deposit",
        )

    if transfer_type == "withdrawal":
        return Transaction(
            id=f"variational-{row_id}" if row_id else f"variational-withdraw-{len(asset)}",
            timestamp=_parse_time(row.get("created_at")),
            asset=asset,
            transaction_type=TransactionType.TRANSFER,
            amount=abs(qty),
            fiat_value_at_trigger=round(abs(qty), 2) if asset in {"USDC", "USDT"} else 0.0,
            fee_fiat=0.0,
            fiat_currency=asset if asset in {"USDC", "USDT"} else None,
            transfer_direction="OUT",
            source=SOURCE,
            instrument_kind="perp",
            instrument=format_perp_contract(underlying, asset) if underlying else format_perp_contract("", asset),
            venue_order_type="withdrawal",
        )

    if transfer_type == "realized_pnl" and underlying:
        return Transaction(
            id=f"variational-{row_id}" if row_id else f"variational-pnl-{underlying}",
            timestamp=_parse_time(row.get("created_at")),
            asset=underlying,
            transaction_type=TransactionType.SELL if qty < 0 else TransactionType.BUY,
            amount=0.0,
            fiat_value_at_trigger=0.0,
            fee_fiat=0.0,
            fiat_currency=asset,
            counter_asset=asset,
            source=SOURCE,
            instrument_kind="perp",
            instrument=format_perp_contract(underlying, asset),
            venue_order_type="realized_pnl",
            realized_pnl=round(qty, 8),
        )

    if transfer_type == "funding" and underlying:
        return Transaction(
            id=f"variational-{row_id}" if row_id else f"variational-funding-{underlying}",
            timestamp=_parse_time(row.get("created_at")),
            asset=underlying,
            transaction_type=TransactionType.FEE,
            amount=0.0,
            fiat_value_at_trigger=0.0,
            fee_fiat=round(abs(qty), 8),
            fiat_currency=asset,
            counter_asset=asset,
            source=SOURCE,
            instrument_kind="perp",
            instrument=format_perp_contract(underlying, asset),
            venue_order_type="funding",
            realized_pnl=round(qty, 8),
        )

    if transfer_type == "fee":
        quote = asset
        fee_asset = quote or underlying or "USDC"
        fee_type = str(row.get("fee_type", "")).strip().lower() or "fee"
        return Transaction(
            id=f"variational-{row_id}" if row_id else f"variational-fee-{fee_asset}",
            timestamp=_parse_time(row.get("created_at")),
            asset=fee_asset,
            transaction_type=TransactionType.FEE,
            amount=0.0,
            fiat_value_at_trigger=0.0,
            fee_fiat=round(abs(qty), 8),
            fiat_currency=quote or fee_asset,
            source=SOURCE,
            instrument_kind="perp",
            instrument=format_perp_contract(underlying, quote) if quote else None,
            venue_order_type=fee_type,
        )

    return None


def _transfer_fee_key(tx: Transaction) -> Optional[Tuple[datetime, str, str]]:
    """Match deposit/withdrawal fees to their parent transfer."""
    if tx.source != SOURCE or tx.transaction_type != TransactionType.FEE:
        return None
    if tx.venue_order_type not in {"deposit", "withdrawal"}:
        return None
    quote = tx.fiat_currency or tx.asset
    if not quote:
        return None
    return (tx.timestamp, tx.venue_order_type, quote)


def _transfer_parent_key(tx: Transaction) -> Optional[Tuple[datetime, str, str]]:
    if tx.source != SOURCE or tx.transaction_type != TransactionType.TRANSFER:
        return None
    if tx.venue_order_type not in {"deposit", "withdrawal"}:
        return None
    quote = tx.fiat_currency or tx.asset
    if not quote:
        return None
    return (tx.timestamp, tx.venue_order_type, quote)


def _merge_transfer_fees(transactions: List[Transaction]) -> List[Transaction]:
    """Roll Variational deposit/withdrawal fee legs into the parent transfer."""
    pending_fees: dict[Tuple[datetime, str, str], float] = {}
    kept: List[Transaction] = []

    for tx in transactions:
        fee_key = _transfer_fee_key(tx)
        if fee_key is not None:
            pending_fees[fee_key] = pending_fees.get(fee_key, 0.0) + tx.fee_fiat
            continue
        kept.append(tx)

    if not pending_fees:
        return transactions

    merged: List[Transaction] = []
    for tx in kept:
        parent_key = _transfer_parent_key(tx)
        extra_fee = pending_fees.pop(parent_key, 0.0) if parent_key else 0.0
        if extra_fee > 0:
            tx = tx.model_copy(
                update={"fee_fiat": round(tx.fee_fiat + extra_fee, 8)}
            )
        merged.append(tx)

    # Unmatched transfer fees — keep as standalone rows.
    for key, fee_total in pending_fees.items():
        if fee_total <= 0:
            continue
        ts, kind, quote = key
        merged.append(
            Transaction(
                id=f"variational-fee-{ts.isoformat()}-{kind}",
                timestamp=ts,
                asset=quote,
                transaction_type=TransactionType.FEE,
                amount=0.0,
                fiat_value_at_trigger=0.0,
                fee_fiat=round(fee_total, 8),
                fiat_currency=quote,
                source=SOURCE,
                instrument_kind="perp",
                venue_order_type=kind,
            )
        )

    return merged


def parse_variational_export(df: pd.DataFrame) -> List[Transaction]:
    """Parse a Variational transfers or trades CSV."""
    data = _prepare_df(df)
    if _is_transfer_export(data):
        parser = _parse_transfer_row
    else:
        parser = _parse_trade_row

    transactions: List[Transaction] = []
    for row in data.to_dict(orient="records"):
        tx = parser(row)
        if tx is not None:
            transactions.append(tx)
    return _merge_transfer_fees(transactions)
