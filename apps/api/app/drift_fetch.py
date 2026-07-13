"""Fetch Drift perp fills and funding via the public Data API (no API key)."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Dict, List, Optional

from .config import STABLECOIN_ASSETS
from .instruments import format_perp_contract
from .kraken import normalize_asset
from .schemas import Transaction, TransactionType
from .solana_fetch import is_valid_solana_address

DRIFT_DATA_API = "https://data.api.drift.trade"
DEFAULT_MAX_ROWS = 10_000
REQUEST_DELAY_SEC = 0.15
BASE_PRECISION = 1_000_000_000
QUOTE_PRECISION = 1_000_000
ORACLE_PRECISION = 10_000_000_000


def drift_import_enabled() -> bool:
    return True


def _request_json(path: str, *, params: Optional[Dict[str, str]] = None) -> object:
    query = ""
    if params:
        query = "?" + "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items())
    url = f"{DRIFT_DATA_API}{path}{query}"
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "crypto-tax-dashboard/1.0",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise ValueError(f"Drift Data API error {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise ValueError(f"Could not reach Drift Data API: {exc.reason}") from exc


def _paginated_records(path: str, *, max_rows: int) -> List[dict]:
    """Walk Drift user endpoints that return {success, records, meta.nextPage}."""
    merged: List[dict] = []
    page: Optional[str] = None

    while len(merged) < max_rows:
        params: Dict[str, str] = {"limit": str(min(1000, max_rows - len(merged)))}
        if page:
            params["page"] = page
        payload = _request_json(path, params=params)
        if not isinstance(payload, dict) or not payload.get("success"):
            break
        batch = payload.get("records")
        if not isinstance(batch, list) or not batch:
            break
        merged.extend(row for row in batch if isinstance(row, dict))
        page = payload.get("meta", {}).get("nextPage") if isinstance(payload.get("meta"), dict) else None
        if not page:
            break
        time.sleep(REQUEST_DELAY_SEC)

    return merged[:max_rows]


def _scaled_int(raw: object, precision: int) -> float:
    try:
        value = int(str(raw or "0"))
    except (TypeError, ValueError):
        return 0.0
    return value / precision


def _perp_base_asset(symbol: str) -> str:
    text = str(symbol or "").strip().upper()
    if text.endswith("-PERP"):
        text = text[: -len("-PERP")]
    return normalize_asset(text)


def _direction_to_type(direction: str) -> TransactionType:
    d = str(direction or "").strip().lower()
    if d in {"long", "buy"}:
        return TransactionType.BUY
    if d in {"short", "sell"}:
        return TransactionType.SELL
    raise ValueError(f"Unknown Drift order direction: {direction!r}")


def _user_fill_direction(row: dict, wallet: str) -> TransactionType:
    """Pick the fill side for the wallet authority."""
    wallet = wallet.strip()
    taker = str(row.get("taker") or "").strip()
    maker = str(row.get("maker") or "").strip()
    user = str(row.get("user") or "").strip()

    if user and user == taker:
        return _direction_to_type(str(row.get("takerOrderDirection") or ""))
    if user and user == maker:
        return _direction_to_type(str(row.get("makerOrderDirection") or ""))
    if wallet and wallet == taker:
        return _direction_to_type(str(row.get("takerOrderDirection") or ""))
    if wallet and wallet == maker:
        return _direction_to_type(str(row.get("makerOrderDirection") or ""))
    # User-scoped endpoint — default to taker direction.
    return _direction_to_type(str(row.get("takerOrderDirection") or ""))


def _fee_fiat_usd(row: dict, wallet: str) -> float:
    wallet = wallet.strip()
    taker = str(row.get("taker") or "").strip()
    user = str(row.get("user") or "").strip()
    if user and user != taker and wallet != taker:
        fee = _scaled_int(row.get("makerFee"), QUOTE_PRECISION)
    else:
        fee = _scaled_int(row.get("takerFee"), QUOTE_PRECISION)
    return round(abs(fee), 8)


def _parse_trade_row(row: dict, wallet: str) -> Optional[Transaction]:
    market_type = str(row.get("marketType") or "perp").strip().lower()
    if market_type != "perp":
        return None

    base_amt = _scaled_int(row.get("baseAssetAmountFilled"), BASE_PRECISION)
    quote_amt = _scaled_int(row.get("quoteAssetAmountFilled"), QUOTE_PRECISION)
    if base_amt <= 0 or quote_amt <= 0:
        return None

    ts = int(row.get("ts") or 0)
    if ts <= 0:
        return None

    symbol = str(row.get("symbol") or "").strip()
    asset = _perp_base_asset(symbol) if symbol else f"MARKET-{row.get('marketIndex')}"
    tx_sig = str(row.get("txSig") or "").strip()
    sig_index = int(row.get("txSigIndex") or 0)
    fill_id = str(row.get("fillRecordId") or "").strip()
    order_id = str(row.get("takerOrderId") or row.get("makerOrderId") or "").strip()

    if fill_id:
        tx_id = f"drift-fill-{fill_id}"
    elif tx_sig:
        tx_id = f"drift-{tx_sig}-{sig_index}"
    else:
        tx_id = f"drift-{ts}-{asset}-{base_amt}"

    notional = round(quote_amt, 2)

    return Transaction(
        id=tx_id,
        timestamp=datetime.fromtimestamp(ts, tz=timezone.utc),
        asset=asset,
        transaction_type=_user_fill_direction(row, wallet),
        amount=base_amt,
        fiat_value_at_trigger=notional,
        fee_fiat=_fee_fiat_usd(row, wallet),
        fiat_currency="USD",
        counter_asset="USDC",
        trade_group_id=order_id or tx_sig or None,
        source="drift",
        instrument_kind="perp",
        instrument=format_perp_contract(asset),
        venue_order_type=str(row.get("actionExplanation") or row.get("action") or "").strip() or None,
        on_chain_tx_id=tx_sig or None,
        realized_pnl=None,
    )


def _parse_funding_row(row: dict, market_symbols: Dict[int, str]) -> Optional[Transaction]:
    payment = _scaled_int(row.get("fundingPayment"), QUOTE_PRECISION)
    if payment == 0:
        return None

    ts = int(row.get("ts") or 0)
    if ts <= 0:
        return None

    market_index = int(row.get("marketIndex") or 0)
    symbol = market_symbols.get(market_index, f"MARKET-{market_index}")
    asset = _perp_base_asset(symbol)

    tx_sig = str(row.get("txSig") or "").strip()
    sig_index = int(row.get("txSigIndex") or 0)
    tx_id = f"drift-funding-{tx_sig}-{sig_index}" if tx_sig else f"drift-funding-{ts}-{market_index}"

    return Transaction(
        id=tx_id,
        timestamp=datetime.fromtimestamp(ts, tz=timezone.utc),
        asset=asset,
        transaction_type=TransactionType.FEE,
        amount=0.0,
        fiat_value_at_trigger=0.0,
        fee_fiat=round(abs(payment), 8),
        fiat_currency="USDC",
        counter_asset="USDC",
        source="drift",
        instrument_kind="perp",
        instrument=format_perp_contract(asset),
        venue_order_type="funding",
        on_chain_tx_id=tx_sig or None,
        realized_pnl=round(payment, 8),
    )


def _load_market_symbols() -> Dict[int, str]:
    payload = _request_json("/stats/markets")
    symbols: Dict[int, str] = {}
    if not isinstance(payload, list):
        return symbols
    for row in payload:
        if not isinstance(row, dict):
            continue
        try:
            idx = int(row.get("marketIndex"))
        except (TypeError, ValueError):
            continue
        sym = str(row.get("symbol") or "").strip()
        if sym:
            symbols[idx] = sym
    return symbols


def fetch_wallet_transactions(
    address: str,
    *,
    max_rows: int = DEFAULT_MAX_ROWS,
) -> List[Transaction]:
    """Return Drift perp fills and funding for a Solana wallet authority."""
    if not is_valid_solana_address(address):
        raise ValueError("Drift uses a Solana wallet address.")

    wallet = address.strip()
    trades = _paginated_records(f"/user/{wallet}/trades", max_rows=max_rows)
    funding = _paginated_records(f"/user/{wallet}/fundingPayments", max_rows=max_rows)

    market_symbols = _load_market_symbols()
    transactions: List[Transaction] = []
    seen_ids: set[str] = set()

    for row in trades:
        tx = _parse_trade_row(row, wallet)
        if tx is None or tx.id in seen_ids:
            continue
        seen_ids.add(tx.id)
        transactions.append(tx)

    for row in funding:
        tx = _parse_funding_row(row, market_symbols)
        if tx is None or tx.id in seen_ids:
            continue
        seen_ids.add(tx.id)
        transactions.append(tx)

    transactions.sort(key=lambda t: (t.timestamp, t.id))
    return transactions
