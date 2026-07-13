"""Fetch EVM wallet history across Etherscan API v2 supported mainnets."""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Dict, List, Optional

from .evm_chains import (
    EVM_AUTO_IMPORT_CHAINS,
    EVM_CHAIN_META,
    EVM_MULTI_IMPORT_MAX_ROWS,
    EvmChain,
    native_asset_for,
)
from .evm_wallet import parse_evm_wallet
from .schemas import Transaction

EVM_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
DEFAULT_PAGE_SIZE = 1000
DEFAULT_MAX_ROWS = 5_000
REQUEST_DELAY_SEC = 0.22
ETHERSCAN_V2_API_BASE = "https://api.etherscan.io/v2/api"

CHAIN_CONFIG = {
    slug: {
        "api_base": ETHERSCAN_V2_API_BASE,
        "chainid": meta["chainid"],
        "label": meta["label"],
        "native": meta["native"],
    }
    for slug, meta in EVM_CHAIN_META.items()
}


def etherscan_api_key() -> Optional[str]:
    return os.environ.get("ETHERSCAN_API_KEY") or os.environ.get(
        "CRYPTO_TAX_ETHERSCAN_API_KEY"
    )


def is_valid_evm_address(address: str) -> bool:
    return bool(EVM_ADDRESS_RE.match(address.strip()))


def _normalize_address(address: str) -> str:
    return address.strip().lower()


def _request_etherscan(
    api_base: str, params: Dict[str, str], *, chainid: str
) -> List[dict]:
    api_key = etherscan_api_key()
    if not api_key:
        raise ValueError(
            "Etherscan API key required. Set ETHERSCAN_API_KEY in your environment "
            "(free tier at etherscan.io) and restart the API."
        )

    query = urllib.parse.urlencode(
        {"chainid": chainid, **params, "apikey": api_key}
    )
    url = f"{api_base}?{query}"
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "crypto-tax-dashboard/1.0", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise ValueError(f"Etherscan API error {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise ValueError(f"Could not reach Etherscan API: {exc.reason}") from exc

    status = str(payload.get("status", ""))
    message = str(payload.get("message", ""))
    result = payload.get("result")

    if status == "0" and message == "No transactions found":
        return []
    if status != "1":
        detail = message
        if isinstance(result, str) and result.strip():
            detail = result.strip()
        raise ValueError(f"Etherscan API error: {detail}")

    if not isinstance(result, list):
        raise ValueError("Unexpected Etherscan response — expected a transaction list.")
    return result


def _fetch_paginated(
    api_base: str,
    *,
    chainid: str,
    address: str,
    action: str,
    max_rows: int,
) -> List[dict]:
    collected: List[dict] = []
    page = 1

    while len(collected) < max_rows:
        offset = min(DEFAULT_PAGE_SIZE, max_rows - len(collected))
        batch = _request_etherscan(
            api_base,
            {
                "module": "account",
                "action": action,
                "address": address,
                "startblock": "0",
                "endblock": "99999999",
                "page": str(page),
                "offset": str(offset),
                "sort": "asc",
            },
            chainid=chainid,
        )
        if not batch:
            break
        collected.extend(batch)
        if len(batch) < offset:
            break
        page += 1
        time.sleep(REQUEST_DELAY_SEC)

    return collected[:max_rows]


def _timestamp(raw: object) -> datetime:
    try:
        stamp = int(str(raw))
    except (TypeError, ValueError):
        stamp = 0
    if stamp <= 0:
        return datetime.now(timezone.utc)
    return datetime.fromtimestamp(stamp, tz=timezone.utc)


def _token_amount(value: object, decimals: object) -> float:
    try:
        raw = int(str(value))
        dec = int(str(decimals or 18))
    except (TypeError, ValueError):
        return 0.0
    if raw <= 0:
        return 0.0
    return raw / (10**dec)


def _eth_amount(wei: object) -> float:
    try:
        raw = int(str(wei))
    except (TypeError, ValueError):
        return 0.0
    if raw <= 0:
        return 0.0
    return raw / 1e18


def _normalize_symbol(symbol: object) -> str:
    text = str(symbol or "").strip().upper()
    return text if text else "UNKNOWN"


def etherscan_to_rows(
    wallet: str,
    *,
    chain: EvmChain,
    normal_txs: List[dict],
    token_txs: List[dict],
    internal_txs: List[dict],
) -> List[dict]:
    wallet = _normalize_address(wallet)
    native = native_asset_for(chain)
    rows: List[dict] = []
    fee_by_hash: Dict[str, dict] = {}

    for tx in normal_txs:
        if str(tx.get("isError") or "0") == "1":
            continue
        tx_hash = str(tx.get("hash") or "")
        stamp = _timestamp(tx.get("timeStamp"))
        amount = _eth_amount(tx.get("value"))
        transfer = _transfer_row(
            tx_hash=tx_hash,
            timestamp=stamp,
            wallet=wallet,
            from_addr=str(tx.get("from") or ""),
            to_addr=str(tx.get("to") or ""),
            amount=amount,
            asset=native,
        )
        if transfer:
            rows.append(transfer)
        fee = _fee_row(
            tx_hash=tx_hash,
            timestamp=stamp,
            wallet=wallet,
            from_addr=str(tx.get("from") or ""),
            gas_used=tx.get("gasUsed"),
            gas_price=tx.get("gasPrice"),
            asset=native,
        )
        if fee:
            fee_by_hash[tx_hash] = fee

    for tx in internal_txs:
        if str(tx.get("isError") or "0") == "1":
            continue
        tx_hash = str(tx.get("hash") or "")
        stamp = _timestamp(tx.get("timeStamp"))
        transfer = _transfer_row(
            tx_hash=tx_hash,
            timestamp=stamp,
            wallet=wallet,
            from_addr=str(tx.get("from") or ""),
            to_addr=str(tx.get("to") or ""),
            amount=_eth_amount(tx.get("value")),
            asset=native,
        )
        if transfer:
            rows.append(transfer)

    for tx in token_txs:
        tx_hash = str(tx.get("hash") or "")
        stamp = _timestamp(tx.get("timeStamp"))
        transfer = _transfer_row(
            tx_hash=tx_hash,
            timestamp=stamp,
            wallet=wallet,
            from_addr=str(tx.get("from") or ""),
            to_addr=str(tx.get("to") or ""),
            amount=_token_amount(tx.get("value"), tx.get("tokenDecimal")),
            asset=_normalize_symbol(tx.get("tokenSymbol")),
            contract=str(tx.get("contractAddress") or "") or None,
        )
        if transfer:
            rows.append(transfer)

    rows.extend(fee_by_hash.values())
    rows.sort(key=lambda row: row["timestamp"])
    return rows


def _transfer_row(
    *,
    tx_hash: str,
    timestamp: datetime,
    wallet: str,
    from_addr: str,
    to_addr: str,
    amount: float,
    asset: str,
    contract: Optional[str] = None,
) -> Optional[dict]:
    if amount <= 0:
        return None
    from_addr = _normalize_address(from_addr)
    to_addr = _normalize_address(to_addr)
    if from_addr == wallet:
        flow = "out"
    elif to_addr == wallet:
        flow = "in"
    else:
        return None
    return {
        "hash": tx_hash,
        "timestamp": timestamp,
        "from": from_addr,
        "to": to_addr,
        "amount": amount,
        "asset": asset,
        "contract": contract,
        "flow": flow,
    }


def _fee_row(
    *,
    tx_hash: str,
    timestamp: datetime,
    wallet: str,
    from_addr: str,
    gas_used: object,
    gas_price: object,
    asset: str,
) -> Optional[dict]:
    if _normalize_address(from_addr) != wallet:
        return None
    try:
        fee_wei = int(str(gas_used)) * int(str(gas_price))
    except (TypeError, ValueError):
        return None
    amount = fee_wei / 1e18
    if amount <= 0:
        return None
    return {
        "kind": "fee",
        "hash": tx_hash,
        "timestamp": timestamp,
        "amount": amount,
        "asset": asset,
    }


def fetch_wallet_transactions(
    address: str,
    *,
    chain: EvmChain = "ethereum",
    max_rows: int = DEFAULT_MAX_ROWS,
) -> List[Transaction]:
    """Fetch and parse on-chain activity for an EVM wallet on one chain."""
    address = address.strip()
    if not is_valid_evm_address(address):
        raise ValueError("Invalid EVM wallet address (expected 0x + 40 hex chars).")

    config = CHAIN_CONFIG[chain]
    api_base = config["api_base"]
    chainid = config["chainid"]
    per_action_cap = max_rows

    normal_txs = _fetch_paginated(
        api_base,
        chainid=chainid,
        address=address,
        action="txlist",
        max_rows=per_action_cap,
    )
    time.sleep(REQUEST_DELAY_SEC)
    token_txs = _fetch_paginated(
        api_base,
        chainid=chainid,
        address=address,
        action="tokentx",
        max_rows=per_action_cap,
    )
    time.sleep(REQUEST_DELAY_SEC)
    internal_txs = _fetch_paginated(
        api_base,
        chainid=chainid,
        address=address,
        action="txlistinternal",
        max_rows=per_action_cap,
    )

    rows = etherscan_to_rows(
        address,
        chain=chain,
        normal_txs=normal_txs,
        token_txs=token_txs,
        internal_txs=internal_txs,
    )
    if not rows:
        return []

    return parse_evm_wallet(rows, wallet=address, chain=chain)


def fetch_wallet_transactions_multi(
    address: str,
    *,
    chains: tuple[EvmChain, ...] = EVM_AUTO_IMPORT_CHAINS,
    max_rows_per_chain: int = EVM_MULTI_IMPORT_MAX_ROWS,
) -> List[Transaction]:
    """Fetch the same 0x wallet across multiple EVM mainnets."""
    merged: List[Transaction] = []
    seen_ids: set[str] = set()
    skipped: list[str] = []

    for chain in chains:
        try:
            batch = fetch_wallet_transactions(
                address, chain=chain, max_rows=max_rows_per_chain
            )
        except ValueError:
            skipped.append(chain)
            continue
        for tx in batch:
            if tx.id in seen_ids:
                continue
            seen_ids.add(tx.id)
            merged.append(tx)
        time.sleep(REQUEST_DELAY_SEC)

    if not merged and skipped and len(skipped) == len(chains):
        raise ValueError(
            "Could not fetch EVM wallet history on any network. "
            "Check ETHERSCAN_API_KEY and rate limits."
        )

    merged.sort(key=lambda t: (t.timestamp, t.id))
    return merged
