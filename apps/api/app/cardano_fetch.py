"""Fetch Cardano wallet history via Koios (free) or Blockfrost (optional API key)."""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Dict, List, Literal, Optional

from .schemas import Transaction, TransactionType

KOIOS_API = "https://api.koios.rest/api/v1"
BLOCKFROST_MAINNET = "https://cardano-mainnet.blockfrost.io/api/v0"
DEFAULT_MAX_TRANSACTIONS = 5_000
REQUEST_DELAY_SEC = 0.25
LOVELACE_PER_ADA = 1_000_000
AMOUNT_EPS = 1e-12
TX_INFO_BATCH = 50

CARDANO_ADDRESS_RE = re.compile(
    r"^(?:addr1[a-z0-9]{50,}|stake1[a-z0-9]{50,})$"
)


def blockfrost_api_key() -> Optional[str]:
    return os.environ.get("BLOCKFROST_API_KEY") or os.environ.get(
        "CRYPTO_TAX_BLOCKFROST_API_KEY"
    )


def is_valid_cardano_address(address: str) -> bool:
    return bool(CARDANO_ADDRESS_RE.match(address.strip().lower()))


def cardano_wallet_import_enabled() -> bool:
    return True


def _is_stake_address(address: str) -> bool:
    return address.strip().lower().startswith("stake1")


def _request_json(
    url: str,
    *,
    method: str = "GET",
    body: Optional[dict] = None,
    headers: Optional[Dict[str, str]] = None,
) -> object:
    data = None
    req_headers = {
        "User-Agent": "crypto-tax-dashboard/1.0",
        "Accept": "application/json",
        **(headers or {}),
    }
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        req_headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url, data=data, method=method, headers=req_headers)
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ValueError(f"Cardano API error {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise ValueError(f"Could not reach Cardano API: {exc.reason}") from exc


def _koios_post(path: str, body: dict) -> List[dict]:
    payload = _request_json(f"{KOIOS_API}/{path.lstrip('/')}", method="POST", body=body)
    if not isinstance(payload, list):
        raise ValueError(f"Unexpected Koios response for {path}.")
    return payload


def _fetch_tx_refs_koios(address: str, max_transactions: int) -> List[dict]:
    address = address.lower()
    collected: List[dict] = []
    after_height = 0
    path = "stake_address_txs" if _is_stake_address(address) else "address_txs"
    key = "_stake_addresses" if _is_stake_address(address) else "_addresses"

    while len(collected) < max_transactions:
        limit = min(1000, max_transactions - len(collected))
        batch = _koios_post(
            path,
            {
                key: [address],
                "_after_block_height": after_height,
                "_limit": limit,
            },
        )
        if not batch:
            break
        collected.extend(batch)
        after_height = int(batch[-1].get("block_height") or after_height)
        if len(batch) < limit:
            break
        time.sleep(REQUEST_DELAY_SEC)

    return collected[:max_transactions]


def _fetch_tx_info_koios(tx_hashes: List[str]) -> Dict[str, dict]:
    details: Dict[str, dict] = {}
    for i in range(0, len(tx_hashes), TX_INFO_BATCH):
        chunk = tx_hashes[i : i + TX_INFO_BATCH]
        batch = _koios_post("tx_info", {"_tx_hashes": chunk})
        for row in batch:
            tx_hash = str(row.get("tx_hash") or row.get("hash") or "")
            if tx_hash:
                details[tx_hash] = row
        time.sleep(REQUEST_DELAY_SEC)
    return details


def _normalize_output_addr(raw: object) -> str:
    if isinstance(raw, dict):
        return str(
            raw.get("bech32")
            or raw.get("address")
            or raw.get("cred")
            or ""
        ).lower()
    return str(raw or "").lower()


def _matches_address(item: dict, address: str) -> bool:
    payment = _normalize_output_addr(item.get("payment_addr"))
    stake = _normalize_output_addr(item.get("stake_addr"))
    if _is_stake_address(address):
        return stake == address
    return payment == address


def _address_flow_koios(tx_info: dict, address: str) -> tuple[float, float]:
    address = address.lower()
    received = 0.0
    sent = 0.0

    for item in tx_info.get("inputs") or []:
        if _matches_address(item, address):
            sent += int(item.get("value") or 0) / LOVELACE_PER_ADA

    for item in tx_info.get("outputs") or []:
        if _matches_address(item, address):
            received += int(item.get("value") or 0) / LOVELACE_PER_ADA

    return received, sent


def _tx_timestamp_from_info(tx_info: dict, fallback: dict) -> datetime:
    unix = tx_info.get("block_time") or fallback.get("block_time")
    if unix:
        return datetime.fromtimestamp(int(unix), tz=timezone.utc)
    return datetime.now(tz=timezone.utc)


def _row_id(tx_hash: str, direction: str) -> str:
    return f"ada-{tx_hash[:18]}-transfer-{direction}"


def _parse_flow(
    tx_hash: str,
    timestamp: datetime,
    received: float,
    sent: float,
) -> List[Transaction]:
    rows: List[Transaction] = []
    net = received - sent
    if net > AMOUNT_EPS:
        rows.append(
            Transaction(
                id=_row_id(tx_hash, "IN"),
                timestamp=timestamp,
                asset="ADA",
                transaction_type=TransactionType.TRANSFER,
                amount=round(net, 6),
                fiat_value_at_trigger=0.0,
                fee_fiat=0.0,
                source="cardano",
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
                asset="ADA",
                transaction_type=TransactionType.TRANSFER,
                amount=round(abs(net), 6),
                fiat_value_at_trigger=0.0,
                fee_fiat=0.0,
                source="cardano",
                transfer_direction="OUT",
                trade_group_id=tx_hash,
                on_chain_tx_id=tx_hash,
            )
        )
    return rows


def _fetch_wallet_koios(address: str, max_transactions: int) -> List[Transaction]:
    refs = _fetch_tx_refs_koios(address, max_transactions)
    if not refs:
        return []

    hashes = list(dict.fromkeys(str(r.get("tx_hash") or "") for r in refs if r.get("tx_hash")))
    info_by_hash = _fetch_tx_info_koios(hashes)
    ref_by_hash = {str(r.get("tx_hash")): r for r in refs}

    transactions: List[Transaction] = []
    for tx_hash in hashes:
        tx_info = info_by_hash.get(tx_hash)
        if not tx_info:
            continue
        received, sent = _address_flow_koios(tx_info, address)
        timestamp = _tx_timestamp_from_info(tx_info, ref_by_hash.get(tx_hash, {}))
        transactions.extend(_parse_flow(tx_hash, timestamp, received, sent))

    transactions.sort(key=lambda t: (t.timestamp, t.id))
    return transactions


def _fetch_wallet_blockfrost(address: str, max_transactions: int) -> List[Transaction]:
    api_key = blockfrost_api_key()
    if not api_key:
        raise ValueError("Blockfrost API key required.")

    address = address.lower()
    collected_hashes: List[str] = []
    page = 1

    while len(collected_hashes) < max_transactions:
        url = (
            f"{BLOCKFROST_MAINNET}/addresses/{address}/transactions"
            f"?order=asc&count=100&page={page}"
        )
        payload = _request_json(
            url,
            headers={"project_id": api_key},
        )
        if not isinstance(payload, list) or not payload:
            break
        for row in payload:
            tx_hash = str(row.get("tx_hash") or "")
            if tx_hash:
                collected_hashes.append(tx_hash)
        if len(payload) < 100:
            break
        page += 1
        time.sleep(REQUEST_DELAY_SEC)

    collected_hashes = collected_hashes[:max_transactions]
    transactions: List[Transaction] = []

    for tx_hash in collected_hashes:
        utxos = _request_json(
            f"{BLOCKFROST_MAINNET}/txs/{tx_hash}/utxos",
            headers={"project_id": api_key},
        )
        if not isinstance(utxos, dict):
            continue

        received = 0.0
        sent = 0.0
        for item in utxos.get("inputs") or []:
            if str(item.get("address") or "").lower() == address:
                for amt in item.get("amount") or []:
                    if str(amt.get("unit")) == "lovelace":
                        sent += int(amt.get("quantity") or 0) / LOVELACE_PER_ADA
        for item in utxos.get("outputs") or []:
            if str(item.get("address") or "").lower() == address:
                for amt in item.get("amount") or []:
                    if str(amt.get("unit")) == "lovelace":
                        received += int(amt.get("quantity") or 0) / LOVELACE_PER_ADA

        tx_meta = _request_json(
            f"{BLOCKFROST_MAINNET}/txs/{tx_hash}",
            headers={"project_id": api_key},
        )
        block_time = 0
        if isinstance(tx_meta, dict):
            block_time = int(tx_meta.get("block_time") or 0)
        timestamp = (
            datetime.fromtimestamp(block_time, tz=timezone.utc)
            if block_time
            else datetime.now(tz=timezone.utc)
        )
        transactions.extend(_parse_flow(tx_hash, timestamp, received, sent))
        time.sleep(REQUEST_DELAY_SEC)

    transactions.sort(key=lambda t: (t.timestamp, t.id))
    return transactions


def fetch_wallet_transactions(
    address: str,
    *,
    max_transactions: int = DEFAULT_MAX_TRANSACTIONS,
) -> List[Transaction]:
    """Fetch ADA transfers for a Cardano payment or stake address."""
    address = address.strip().lower()
    if not is_valid_cardano_address(address):
        raise ValueError("Invalid Cardano address (expected addr1… or stake1…).")

    if blockfrost_api_key():
        try:
            return _fetch_wallet_blockfrost(address, max_transactions)
        except ValueError:
            pass

    return _fetch_wallet_koios(address, max_transactions)
