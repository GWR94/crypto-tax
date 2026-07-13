"""Fetch Bitcoin wallet history via the Blockstream Esplora API (no API key)."""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import List, Optional

from .schemas import Transaction, TransactionType

BLOCKSTREAM_API = "https://blockstream.info/api"
DEFAULT_MAX_TRANSACTIONS = 5_000
REQUEST_DELAY_SEC = 0.25
SATOSHI_PER_BTC = 100_000_000
AMOUNT_EPS = 1e-12

# Legacy (1/3), native segwit + taproot bech32 (bc1).
BTC_ADDRESS_RE = re.compile(
    r"^(?:"
    r"[13][a-km-zA-HJ-NP-Z1-9]{25,34}|"
    r"bc1[ac-hj-np-z02-9]{11,71}|"
    r"bc1p[ac-hm-np-z02-9]{58}"
    r")$"
)


def is_valid_btc_address(address: str) -> bool:
    return bool(BTC_ADDRESS_RE.match(address.strip()))


def btc_wallet_import_enabled() -> bool:
    return True


def _addrs_match(a: str, b: str) -> bool:
    a = a.strip()
    b = b.strip()
    if a.lower().startswith("bc1") or b.lower().startswith("bc1"):
        return a.lower() == b.lower()
    return a == b


def _request_json(url: str) -> object:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "crypto-tax-dashboard/1.0", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        if exc.code == 404:
            return []
        raise ValueError(f"Blockstream API error {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise ValueError(f"Could not reach Blockstream API: {exc.reason}") from exc


def _fetch_tx_batch(address: str, last_txid: Optional[str] = None) -> List[dict]:
    if last_txid:
        url = f"{BLOCKSTREAM_API}/address/{address}/txs/chain/{last_txid}"
    else:
        url = f"{BLOCKSTREAM_API}/address/{address}/txs/chain"
    payload = _request_json(url)
    if not isinstance(payload, list):
        raise ValueError("Unexpected Blockstream response — expected a transaction list.")
    return payload


def _fetch_all_txs(address: str, max_transactions: int) -> List[dict]:
    collected: List[dict] = []
    last_txid: Optional[str] = None

    while len(collected) < max_transactions:
        batch = _fetch_tx_batch(address, last_txid)
        if not batch:
            break
        collected.extend(batch)
        last_txid = str(batch[-1]["txid"])
        if len(batch) < 25:
            break
        time.sleep(REQUEST_DELAY_SEC)

    return collected[:max_transactions]


def _tx_timestamp(tx: dict) -> datetime:
    status = tx.get("status") or {}
    block_time = status.get("block_time")
    if block_time:
        return datetime.fromtimestamp(int(block_time), tz=timezone.utc)
    return datetime.now(tz=timezone.utc)


def _address_flow(tx: dict, address: str) -> tuple[float, float]:
    received_sat = 0
    for vout in tx.get("vout") or []:
        spk = str(vout.get("scriptpubkey_address") or "")
        if spk and _addrs_match(spk, address):
            received_sat += int(vout.get("value") or 0)

    sent_sat = 0
    for vin in tx.get("vin") or []:
        prevout = vin.get("prevout") or {}
        spk = str(prevout.get("scriptpubkey_address") or "")
        if spk and _addrs_match(spk, address):
            sent_sat += int(prevout.get("value") or 0)

    return received_sat / SATOSHI_PER_BTC, sent_sat / SATOSHI_PER_BTC


def _row_id(tx_hash: str, direction: str) -> str:
    return f"btc-{tx_hash[:18]}-transfer-{direction}"


def _parse_tx(tx: dict, address: str) -> List[Transaction]:
    tx_hash = str(tx.get("txid", ""))
    if not tx_hash:
        return []

    received, sent = _address_flow(tx, address)
    timestamp = _tx_timestamp(tx)
    rows: List[Transaction] = []

    net = received - sent
    if net > AMOUNT_EPS:
        rows.append(
            Transaction(
                id=_row_id(tx_hash, "IN"),
                timestamp=timestamp,
                asset="BTC",
                transaction_type=TransactionType.TRANSFER,
                amount=round(net, 8),
                fiat_value_at_trigger=0.0,
                fee_fiat=0.0,
                source="bitcoin",
                transfer_direction="IN",
                trade_group_id=tx_hash,
                on_chain_tx_id=tx_hash,
            )
        )
    elif net < -AMOUNT_EPS:
        rows.append(
            Transaction(
                id=_row_id(tx_hash, "OUT"),
                timestamp=timestamp,
                asset="BTC",
                transaction_type=TransactionType.TRANSFER,
                amount=round(abs(net), 8),
                fiat_value_at_trigger=0.0,
                fee_fiat=0.0,
                source="bitcoin",
                transfer_direction="OUT",
                trade_group_id=tx_hash,
                on_chain_tx_id=tx_hash,
            )
        )

    return rows


def fetch_wallet_transactions(
    address: str,
    *,
    max_transactions: int = DEFAULT_MAX_TRANSACTIONS,
) -> List[Transaction]:
    """Fetch confirmed BTC transfers for a wallet address."""
    address = address.strip()
    if not is_valid_btc_address(address):
        raise ValueError("Invalid Bitcoin address.")

    txs = _fetch_all_txs(address, max_transactions)
    transactions: List[Transaction] = []
    for tx in txs:
        transactions.extend(_parse_tx(tx, address))

    transactions.sort(key=lambda t: (t.timestamp, t.id))
    return transactions
