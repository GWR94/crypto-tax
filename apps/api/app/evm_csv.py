"""Parse Etherscan / block-explorer EVM wallet CSV exports."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Dict, List, Optional

import pandas as pd

from .evm_wallet import EvmChain, parse_evm_wallet
from .kraken import clean_columns
from .schemas import Transaction

EVM_ADDRESS_RE = re.compile(r"0x[a-fA-F0-9]{40}")
FILENAME_ADDRESS_RE = re.compile(r"(0x[a-fA-F0-9]{40})")


def _squash(name: str) -> str:
    return (
        str(name)
        .lower()
        .strip()
        .replace(" ", "_")
        .replace("(", "")
        .replace(")", "")
        .replace("__", "_")
    )


def _prepare(df: pd.DataFrame) -> pd.DataFrame:
    out = clean_columns(df)
    out.columns = [_squash(c) for c in out.columns]
    return out


def is_evm_wallet_csv(df: pd.DataFrame) -> bool:
    """True for Etherscan-style wallet export CSVs (not Solana exports)."""
    cols = set(_prepare(df).columns)
    if "flow" in cols and "action" in cols:
        return False
    has_hash = bool(
        cols
        & {
            "transaction_hash",
            "txhash",
            "hash",
            "trans_id",
        }
    )
    return has_hash and "from" in cols and "to" in cols


def _wallet_from_filename(filename: str) -> Optional[str]:
    match = FILENAME_ADDRESS_RE.search(filename or "")
    return match.group(1).lower() if match else None


def _infer_wallet(records: List[dict]) -> Optional[str]:
    counts: Dict[str, int] = {}
    for row in records:
        for key in ("from", "to"):
            addr = str(row.get(key) or "").strip().lower()
            if EVM_ADDRESS_RE.fullmatch(addr):
                counts[addr] = counts.get(addr, 0) + 1
    if not counts:
        return None
    return max(counts, key=counts.get)


def _parse_timestamp(row: dict) -> Optional[datetime]:
    for key in (
        "datetime_utc",
        "human_time",
        "block_time",
        "unixtimestamp",
        "unix_timestamp",
        "timestamp",
        "date",
    ):
        raw = row.get(key)
        if raw is None or str(raw).strip().lower() in ("", "nan", "none"):
            continue
        ts = pd.to_datetime(raw, utc=True, errors="coerce")
        if not pd.isna(ts):
            return ts.to_pydatetime()
        try:
            stamp = int(float(str(raw)))
        except (TypeError, ValueError):
            continue
        if stamp > 0:
            return datetime.fromtimestamp(stamp, tz=timezone.utc)
    return None


def _field(row: dict, *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value is None or (isinstance(value, float) and pd.isna(value)):
            continue
        text = str(value).strip()
        if text and text.lower() != "nan":
            return text
    return ""


def _amount(row: dict) -> float:
    for key in ("amount", "value_in_eth", "value_out_eth", "value_eth", "value"):
        raw = row.get(key)
        if raw is None or (isinstance(raw, float) and pd.isna(raw)):
            continue
        try:
            amount = float(raw)
        except (TypeError, ValueError):
            continue
        if amount > 0:
            return amount
    return 0.0


def _usd_value(row: dict) -> float:
    for key in ("value_usd", "usd_value", "total_price"):
        raw = row.get(key)
        if raw is None or (isinstance(raw, float) and pd.isna(raw)):
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if value >= 0:
            return value
    return 0.0


def _asset(row: dict) -> str:
    symbol = _field(row, "token_symbol", "token", "asset", "symbol")
    if symbol:
        return symbol.upper()
    if _field(row, "contractaddress", "token_address", "contract_address"):
        return "UNKNOWN"
    return "ETH"


def _contract(row: dict) -> Optional[str]:
    contract = _field(row, "contractaddress", "token_address", "contract_address")
    return contract or None


def parse_evm_wallet_csv(
    df: pd.DataFrame,
    *,
    filename: str = "",
    chain: EvmChain = "ethereum",
) -> List[Transaction]:
    """Parse an Etherscan-style CSV into unified transactions."""
    prepared = _prepare(df)
    records = prepared.to_dict(orient="records")
    wallet = _wallet_from_filename(filename) or _infer_wallet(records)
    if not wallet:
        raise ValueError(
            "Could not determine wallet address for EVM CSV — "
            "include 0x… in the filename or ensure From/To columns are present."
        )

    rows: List[dict] = []
    for record in records:
        tx_hash = _field(
            record,
            "transaction_hash",
            "txhash",
            "hash",
            "trans_id",
        )
        if not tx_hash:
            continue
        timestamp = _parse_timestamp(record)
        if timestamp is None:
            continue

        from_addr = _field(record, "from", "from_address").lower()
        to_addr = _field(record, "to", "to_address").lower()
        amount = _amount(record)
        if amount <= 0:
            continue

        if from_addr == wallet:
            flow = "out"
        elif to_addr == wallet:
            flow = "in"
        else:
            continue

        rows.append(
            {
                "hash": tx_hash,
                "timestamp": timestamp,
                "from": from_addr,
                "to": to_addr,
                "amount": amount,
                "asset": _asset(record),
                "contract": _contract(record),
                "flow": flow,
            }
        )

    if not rows:
        return []

    return parse_evm_wallet(rows, wallet=wallet, chain=chain)
