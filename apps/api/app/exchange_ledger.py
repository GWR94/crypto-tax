"""Binance / Crypto.com compatible transaction-history CSV parser.

Handles ledger-style exports with columns such as::

    User_ID, UTC_Time, Account, Operation, Coin, Change, Remark

Spot trades appear as multiple rows at the same timestamp (``Transaction Related``
legs plus optional fiat ``Deposit`` rows that net to zero).
"""

from __future__ import annotations

import re
import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Tuple

import pandas as pd

from .config import STABLECOIN_ASSETS
from .kraken import clean_columns, is_fiat, normalize_asset
from .schemas import Transaction, TransactionType

_EXPORT_OFFSET_RE = re.compile(
    r"(?:\(|\b)UTC([+-])(\d{1,2})(?::(\d{2}))?(?:\)|\b)",
    re.IGNORECASE,
)
_TIMEZONE_DUPE_MIN_SECONDS = 3500
_TIMEZONE_DUPE_MAX_SECONDS = 3700


def is_stablecoin(asset: str) -> bool:
    return normalize_asset(asset) in STABLECOIN_ASSETS


def _normalize_exchange_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Binance exports use ``Time``; Crypto.com often uses ``UTC_Time``."""
    out = clean_columns(df)
    if "time" in out.columns and "utc_time" not in out.columns:
        out = out.rename(columns={"time": "utc_time"})
    return out


def is_exchange_ledger(df: pd.DataFrame) -> bool:
    """True when the CSV matches the Binance / CDC transaction-history layout."""
    cols = set(_normalize_exchange_columns(df).columns)
    if not {"utc_time", "operation", "coin", "change"}.issubset(cols):
        return False
    # Kraken ledgers also carry ``time`` but never ``utc_time`` + ``operation``.
    return "txid" not in cols and "refid" not in cols


def _parse_time(raw: object) -> datetime:
    ts = pd.to_datetime(raw, utc=True, errors="coerce")
    if pd.isna(ts):
        raise ValueError(f"Unparseable timestamp: {raw!r}")
    return ts.to_pydatetime()


def parse_export_utc_offset(filename: str) -> timedelta | None:
    """Read Binance-style ``(UTC+1)`` hints from an export filename."""
    if not filename:
        return None
    match = _EXPORT_OFFSET_RE.search(filename)
    if not match:
        return None
    sign = 1 if match.group(1) == "+" else -1
    hours = int(match.group(2))
    minutes = int(match.group(3) or 0)
    return timedelta(hours=sign * hours, minutes=sign * minutes)


def _normalize_export_time(
    raw: object, export_offset: timedelta | None
) -> datetime:
    """Convert export-local timestamps to UTC."""
    ts = _parse_time(raw)
    if export_offset:
        return ts - export_offset
    return ts


def _hour_offset_duplicates(a: datetime, b: datetime) -> bool:
    delta = abs((a - b).total_seconds())
    return _TIMEZONE_DUPE_MIN_SECONDS <= delta <= _TIMEZONE_DUPE_MAX_SECONDS


def _float(raw: object) -> float:
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return 0.0
    return float(raw)


def _operation(raw: object) -> str:
    return str(raw or "").strip().lower()


def _infer_source(rows: List[dict]) -> str:
    for row in rows:
        remark = str(row.get("remark", "")).lower()
        if "binance" in remark:
            return "binance"
        if "crypto.com" in remark or "cryptocom" in remark:
            return "cryptocom"
    # Binance transaction-history exports include a User ID column.
    if rows and "user_id" in rows[0]:
        return "binance"
    return "exchange"


def _fiat_spent(fiat_rows: List[dict]) -> float:
    return sum(abs(_float(r["change"])) for r in fiat_rows if _float(r["change"]) < 0)


def _fiat_received(fiat_rows: List[dict]) -> float:
    return sum(_float(r["change"]) for r in fiat_rows if _float(r["change"]) > 0)


def _fiat_currency_from_rows(fiat_rows: List[dict]) -> str | None:
    for row in fiat_rows:
        asset = normalize_asset(str(row["coin"]))
        if is_fiat(asset):
            return asset
    return None


def _crypto_counter_value(crypto_rows: List[dict], current_change: float) -> float:
    others = [r for r in crypto_rows if _float(r["change"]) * current_change < 0]
    if not others:
        return 0.0
    return abs(_float(others[0]["change"]))


def _row_id(row: dict, timestamp: datetime, suffix: str = "") -> str:
    base = (
        f"{timestamp.isoformat()}-{row.get('account')}-"
        f"{row.get('coin')}-{row.get('change')}"
    )
    return f"{base}{suffix}" if suffix else str(uuid.uuid5(uuid.NAMESPACE_OID, base))


def _is_trade_batch(rows: List[dict]) -> bool:
    return any(_operation(r.get("operation")) == "transaction related" for r in rows)


def _parse_trade_batch(
    rows: List[dict], source: str, *, export_offset: timedelta | None
) -> List[Transaction]:
    timestamp = _normalize_export_time(rows[0]["utc_time"], export_offset)
    fiat_rows = [r for r in rows if is_fiat(normalize_asset(str(r["coin"])))]
    crypto_rows = [
        r
        for r in rows
        if not is_fiat(normalize_asset(str(r["coin"])))
        and _operation(r.get("operation")) == "transaction related"
    ]
    fiat_currency = _fiat_currency_from_rows(fiat_rows)

    transactions: List[Transaction] = []
    for row in crypto_rows:
        asset = normalize_asset(str(row["coin"]))
        change = _float(row["change"])
        qty = abs(change)
        if qty <= 0:
            continue

        if change > 0:
            tx_type = TransactionType.BUY
            fiat_value = _fiat_spent(fiat_rows)
            if fiat_value <= 0:
                fiat_value = _crypto_counter_value(crypto_rows, change)
        else:
            tx_type = TransactionType.SELL
            fiat_value = _fiat_received(fiat_rows)
            if fiat_value <= 0:
                fiat_value = _crypto_counter_value(crypto_rows, change)

        counter_asset = None
        if not fiat_currency:
            others = [r for r in crypto_rows if _float(r["change"]) * change < 0]
            if others:
                counter_asset = normalize_asset(str(others[0]["coin"]))

        transactions.append(
            Transaction(
                id=_row_id(row, timestamp),
                timestamp=timestamp,
                asset=asset,
                transaction_type=tx_type,
                amount=qty,
                fiat_value_at_trigger=round(fiat_value, 2),
                fee_fiat=0.0,
                fiat_currency=fiat_currency or counter_asset,
                source=source,
            )
        )
    return transactions


def _parse_single_row(
    row: dict, source: str, *, export_offset: timedelta | None
) -> Transaction | None:
    operation = _operation(row.get("operation"))
    asset = normalize_asset(str(row["coin"]))
    change = _float(row["change"])
    qty = abs(change)

    if qty <= 0 and operation not in {"fee"}:
        return None

    # Fiat balance movements fund the account — not crypto tax events.
    if is_fiat(asset) and operation in {"deposit", "withdraw", "fiat withdrawal"}:
        return None

    timestamp = _normalize_export_time(row["utc_time"], export_offset)
    fiat_currency: str | None = None
    transfer_direction: str | None = None

    if operation == "deposit" or operation == "crypto deposit":
        if is_fiat(asset):
            return None
        if is_stablecoin(asset):
            tx_type = TransactionType.BUY
            fiat_value = qty
            fiat_currency = asset
        else:
            tx_type = TransactionType.TRANSFER
            fiat_value = 0.0
            transfer_direction = "IN"
    elif operation in {"withdraw", "crypto withdrawal"}:
        if is_fiat(asset):
            return None
        tx_type = TransactionType.TRANSFER
        fiat_value = 0.0
        transfer_direction = "OUT"
    elif operation in {
        "staking rewards",
        "simple earn flexible interest",
        "simple earn locked interest",
        "earn",
        "referral commission",
        "distribution",
        "airdrop",
        "mission reward",
    }:
        tx_type = (
            TransactionType.AIRDROP
            if operation in {"airdrop", "mission reward"}
            else TransactionType.STAKING
        )
        fiat_value = qty if is_stablecoin(asset) else 0.0
        fiat_currency = asset if is_stablecoin(asset) else None
    elif operation in {
        "staking purchase",
        "simple earn flexible subscription",
        "simple earn locked subscription",
        "simple earn flexible redemption",
        "simple earn locked redemption",
        "savings purchase",
        "savings redemption",
    }:
        # Internal earn ↔ spot shuffles — not external wallet movements.
        return None
    elif operation in {"transaction fee", "fee"}:
        tx_type = TransactionType.FEE
        fiat_value = 0.0
    elif operation == "transaction related":
        # Trade legs are handled in batch parsing.
        return None
    else:
        # Unsupported operation types are skipped until explicitly mapped.
        return None

    return Transaction(
        id=_row_id(row, timestamp),
        timestamp=timestamp,
        asset=asset,
        transaction_type=tx_type,
        amount=qty,
        fiat_value_at_trigger=round(fiat_value, 2),
        fee_fiat=0.0,
        fiat_currency=fiat_currency,
        source=source,
        transfer_direction=transfer_direction,
    )


def collapse_exchange_timezone_duplicates(
    transactions: List[Transaction],
) -> Tuple[List[Transaction], int]:
    """Drop exchange rows duplicated by UTC vs UTC+1 export offsets."""
    exchange_sources = frozenset({"binance", "exchange"})
    by_fp: Dict[tuple, List[Transaction]] = defaultdict(list)

    for tx in transactions:
        if tx.source not in exchange_sources:
            continue
        by_fp[
            (
                tx.source,
                tx.asset,
                round(tx.amount, 8),
                tx.transaction_type,
                tx.transfer_direction or "",
                round(tx.fiat_value_at_trigger, 2),
                tx.fiat_currency or "",
            )
        ].append(tx)

    drop_ids: set[str] = set()
    for members in by_fp.values():
        if len(members) < 2:
            continue
        members.sort(key=lambda t: t.timestamp)
        kept: List[Transaction] = []
        for tx in members:
            if tx.id in drop_ids:
                continue
            duplicate_of = next(
                (
                    prev
                    for prev in kept
                    if _hour_offset_duplicates(prev.timestamp, tx.timestamp)
                ),
                None,
            )
            if duplicate_of is None:
                kept.append(tx)
                continue
            if tx.timestamp < duplicate_of.timestamp:
                drop_ids.add(duplicate_of.id)
                kept.remove(duplicate_of)
                kept.append(tx)
            else:
                drop_ids.add(tx.id)

    if not drop_ids:
        return transactions, 0
    return [tx for tx in transactions if tx.id not in drop_ids], len(drop_ids)


def parse_exchange_ledger(
    df: pd.DataFrame, *, filename: str = ""
) -> List[Transaction]:
    """Parse a Binance / CDC transaction-history CSV into unified transactions."""
    export_offset = parse_export_utc_offset(filename)
    normalised = _normalize_exchange_columns(df)
    records = normalised.to_dict(orient="records")
    source = _infer_source(records)

    groups: Dict[tuple[str, str], List[dict]] = defaultdict(list)
    for record in records:
        key = (str(record.get("utc_time", "")), str(record.get("account", "")))
        groups[key].append(record)

    transactions: List[Transaction] = []

    for rows in groups.values():
        if _is_trade_batch(rows):
            batch_txs = _parse_trade_batch(rows, source, export_offset=export_offset)
            transactions.extend(batch_txs)
        else:
            for row in rows:
                parsed = _parse_single_row(row, source, export_offset=export_offset)
                if parsed is not None:
                    transactions.append(parsed)

    transactions.sort(key=lambda t: (t.timestamp, t.id))
    return transactions
