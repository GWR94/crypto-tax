"""Fetch Celestia wallet history via public Cosmos REST."""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Dict, List, Optional

from .cosmos_wallet import DENOM_TO_TICKER
from .schemas import Transaction, TransactionType

CELESTIA_LCD = "https://celestia-rest.publicnode.com"
CELESTIA_ADDRESS_RE = re.compile(r"^celestia1[a-z0-9]{38,}$")
DEFAULT_MAX_TRANSACTIONS = 2_000
REQUEST_DELAY_SEC = 0.3
UTIA_PER_TIA = 1_000_000


def is_valid_celestia_address(address: str) -> bool:
    return bool(CELESTIA_ADDRESS_RE.match(address.strip().lower()))


def celestia_wallet_import_enabled() -> bool:
    return True


def _request_json(url: str) -> dict:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "crypto-tax-dashboard/1.0", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise ValueError(f"Celestia API error {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise ValueError(f"Could not reach Celestia API: {exc.reason}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Unexpected Celestia API response.")
    return payload


def _search_txs(event_query: str, *, limit: int) -> List[dict]:
    query = urllib.parse.urlencode(
        {
            "events": event_query,
            "pagination.limit": str(min(100, limit)),
            "order_by": "ORDER_BY_DESC",
        }
    )
    payload = _request_json(f"{CELESTIA_LCD}/cosmos/tx/v1beta1/txs?{query}")
    responses = payload.get("tx_responses") or []
    return [r for r in responses if isinstance(r, dict)]


def _attr_map(event: dict) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for attr in event.get("attributes") or []:
        if not isinstance(attr, dict):
            continue
        key = str(attr.get("key", ""))
        if key.startswith("base64:"):
            continue
        out[key] = str(attr.get("value", ""))
    return out


def _parse_transfer_events(
    tx_response: dict, wallet: str
) -> List[tuple[str, float, str]]:
    """Return (direction, amount_tia, tx_hash) legs from transfer events."""
    wallet = wallet.lower()
    tx_hash = str(tx_response.get("txhash") or "")
    legs: List[tuple[str, float, str]] = []

    for event in tx_response.get("events") or []:
        if not isinstance(event, dict) or event.get("type") != "transfer":
            continue
        attrs = _attr_map(event)
        amount_raw = attrs.get("amount", "")
        if not amount_raw:
            continue
        qty_str, _, denom = amount_raw.partition("utia")
        if denom and denom != "utia":
            continue
        try:
            amount = int(qty_str) / UTIA_PER_TIA
        except ValueError:
            continue
        if amount <= 0:
            continue

        sender = attrs.get("sender", "").lower()
        recipient = attrs.get("recipient", "").lower()
        if recipient == wallet:
            legs.append(("IN", amount, tx_hash))
        elif sender == wallet:
            legs.append(("OUT", amount, tx_hash))

    return legs


def _tx_timestamp(tx_response: dict) -> datetime:
    raw = str(tx_response.get("timestamp") or "")
    if raw:
        ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts
    return datetime.now(timezone.utc)


def fetch_wallet_transactions(
    address: str,
    *,
    max_transactions: int = DEFAULT_MAX_TRANSACTIONS,
) -> List[Transaction]:
    """Fetch TIA transfers for a Celestia address."""
    address = address.strip().lower()
    if not is_valid_celestia_address(address):
        raise ValueError("Invalid Celestia address (expected celestia1…).")

    seen_hashes: set[str] = set()
    tx_responses: List[dict] = []

    for event in (
        f"transfer.recipient='{address}'",
        f"transfer.sender='{address}'",
    ):
        batch = _search_txs(event, limit=max_transactions)
        for row in batch:
            tx_hash = str(row.get("txhash") or "")
            if not tx_hash or tx_hash in seen_hashes:
                continue
            seen_hashes.add(tx_hash)
            tx_responses.append(row)
        time.sleep(REQUEST_DELAY_SEC)

    asset = DENOM_TO_TICKER.get("utia", "TIA")
    transactions: List[Transaction] = []

    for tx_response in tx_responses[:max_transactions]:
        tx_hash = str(tx_response.get("txhash") or "")
        timestamp = _tx_timestamp(tx_response)
        for direction, amount, leg_hash in _parse_transfer_events(
            tx_response, address
        ):
            transactions.append(
                Transaction(
                    id=f"celestia-{leg_hash[:16]}-{direction}-{asset}",
                    timestamp=timestamp,
                    asset=asset,
                    transaction_type=TransactionType.TRANSFER,
                    amount=round(amount, 6),
                    fiat_value_at_trigger=0.0,
                    fee_fiat=0.0,
                    source="celestia",
                    transfer_direction=direction,  # type: ignore[arg-type]
                    trade_group_id=tx_hash or None,
                    on_chain_tx_id=tx_hash or None,
                )
            )

    transactions.sort(key=lambda t: (t.timestamp, t.id))
    return transactions
