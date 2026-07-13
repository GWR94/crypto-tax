"""Fetch Hyperliquid perp/spot fills via the public Info API (no API key)."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import List, Optional

from .config import STABLECOIN_ASSETS
from .evm_fetch import is_valid_evm_address
from .instruments import format_perp_contract
from .kraken import normalize_asset
from .schemas import Transaction, TransactionType

HYPERLIQUID_INFO_API = "https://api.hyperliquid.xyz/info"
DEFAULT_MAX_FILLS = 10_000
REQUEST_DELAY_SEC = 0.15


def hyperliquid_import_enabled() -> bool:
    return True


def _post_info(body: dict) -> object:
    data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        HYPERLIQUID_INFO_API,
        data=data,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "crypto-tax-dashboard/1.0",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="replace")
        raise ValueError(f"Hyperliquid API error {exc.code}: {body_text}") from exc
    except urllib.error.URLError as exc:
        raise ValueError(f"Could not reach Hyperliquid API: {exc.reason}") from exc


def _fetch_fills(address: str, *, max_rows: int) -> List[dict]:
    """Paginate userFillsByTime until exhausted or max_rows reached."""
    user = address.strip().lower()
    now_ms = int(time.time() * 1000)
    start_ms = 0
    merged: List[dict] = []

    while len(merged) < max_rows:
        payload = {
            "type": "userFillsByTime",
            "user": user,
            "startTime": start_ms,
            "endTime": now_ms,
        }
        batch = _post_info(payload)
        if not isinstance(batch, list) or not batch:
            break
        merged.extend(batch)
        if len(batch) < 2000:
            break
        last_time = max(int(row.get("time", 0)) for row in batch)
        if last_time <= start_ms:
            break
        start_ms = last_time + 1
        time.sleep(REQUEST_DELAY_SEC)

    return merged[:max_rows]


def _instrument_kind(coin: str) -> str:
    """Spot pairs on Hyperliquid use ``@``-prefixed coin ids."""
    return "spot" if str(coin).startswith("@") else "perp"


def _side_to_type(side: str) -> TransactionType:
    side_u = str(side or "").strip().upper()
    if side_u == "B":
        return TransactionType.BUY
    if side_u == "A":
        return TransactionType.SELL
    raise ValueError(f"Unknown Hyperliquid side: {side!r}")


def _fee_fiat(fee: object, fee_token: object) -> float:
    amount = float(fee or 0)
    if amount <= 0:
        return 0.0
    token = normalize_asset(str(fee_token or ""))
    if token in STABLECOIN_ASSETS or token in {"USD", "EUR", "GBP"}:
        return round(amount, 8)
    return 0.0


def _parse_fill(row: dict) -> Optional[Transaction]:
    coin = str(row.get("coin", "")).strip()
    if not coin:
        return None

    px = float(row.get("px", 0) or 0)
    sz = float(row.get("sz", 0) or 0)
    if px <= 0 or sz <= 0:
        return None

    ts_ms = int(row.get("time", 0))
    if ts_ms <= 0:
        return None

    tid = row.get("tid")
    oid = row.get("oid")
    side = str(row.get("side", "")).strip()
    if tid is not None:
        tx_id = f"hyperliquid-{tid}"
    elif row.get("hash"):
        tx_id = f"hyperliquid-{row.get('hash')}"
    else:
        # Deterministic fill key so re-fetches collapse to the same row.
        tx_id = f"hyperliquid-{oid}-{ts_ms}-{side}-{sz}-{px}"

    asset = normalize_asset(coin.lstrip("@"))
    kind = _instrument_kind(coin)
    notional = round(px * sz, 2)
    closed_pnl = row.get("closedPnl")
    pnl = float(closed_pnl) if closed_pnl not in (None, "", "0.0") else None

    return Transaction(
        id=tx_id,
        timestamp=datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc),
        asset=asset,
        transaction_type=_side_to_type(str(row.get("side", ""))),
        amount=sz,
        fiat_value_at_trigger=notional,
        fee_fiat=_fee_fiat(row.get("fee"), row.get("feeToken")),
        fiat_currency="USD",
        counter_asset="USDC",
        trade_group_id=str(oid) if oid is not None else None,
        source="hyperliquid",
        instrument_kind=kind,
        instrument=format_perp_contract(asset) if kind == "perp" else coin,
        venue_order_type=str(row.get("dir") or "").strip() or None,
        realized_pnl=pnl,
    )


def fetch_wallet_transactions(
    address: str,
    *,
    max_rows: int = DEFAULT_MAX_FILLS,
) -> List[Transaction]:
    """Return Hyperliquid fills for a 0x trading wallet."""
    if not is_valid_evm_address(address):
        raise ValueError("Hyperliquid uses an EVM (0x) wallet address.")

    fills = _fetch_fills(address, max_rows=max_rows)
    transactions: List[Transaction] = []
    seen_ids: set[str] = set()

    for row in fills:
        if not isinstance(row, dict):
            continue
        tx = _parse_fill(row)
        if tx is None or tx.id in seen_ids:
            continue
        seen_ids.add(tx.id)
        transactions.append(tx)

    transactions.sort(key=lambda t: (t.timestamp, t.id))
    return transactions
