"""Crypto.com app transaction-history CSV parser.

Handles exports with columns such as::

    Timestamp (UTC), Transaction Description, Currency, Amount,
    To Currency, To Amount, Native Currency, Native Amount,
    Native Amount (in USD), Transaction Kind, Transaction Hash
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple

import pandas as pd

from .config import is_stablecoin
from .kraken import clean_columns, is_fiat
from .schemas import Transaction, TransactionType

# Token swaps — sign of ``amount`` determines buy vs sell when both legs exist.
EXCHANGE_KINDS = frozenset(
    {
        "crypto_exchange",
        "crypto_viban_exchange",
        "trading",
        "trade",
    }
)

BUY_KINDS = frozenset(
    {
        "crypto_purchase",
        "crypto_exchange",
        "crypto_viban_exchange",
        "viban_purchase",
        "trading",
        "trade",
        "buy",
        "crypto_buy",
        "crypto_wallet_swap_credited",
        "recurring_buy",
    }
)

SELL_KINDS = frozenset(
    {
        "crypto_sale",
        "crypto_sold",
        "sell",
        "crypto_sell",
        "crypto_wallet_swap_debited",
    }
)

TRANSFER_IN_KINDS = frozenset(
    {
        "crypto_deposit",
        "crypto_transfer_in",
        "crypto_earn_program_withdrawn",
        "transfer_in",
        "deposit",
    }
)

TRANSFER_OUT_KINDS = frozenset(
    {
        "crypto_withdrawal",
        "crypto_transfer_out",
        "crypto_earn_program_created",
        "crypto_earn_program_deposit",
        "transfer_out",
        "withdrawal",
    }
)

INCOME_KINDS = frozenset(
    {
        "admin_wallet_credited",
        "referral_bonus",
        "referral_gift",
        "referral_card_cashback",
        "reimbursement",
        "airdrop",
    }
)

STAKING_INCOME_KINDS = frozenset(
    {
        "crypto_earn_interest_paid",
        "interest",
        "mco_stake_reward",
        "supercharger_reward",
        "reward",
    }
)

FEE_KINDS = frozenset({"crypto_withdrawal_fee", "fee", "admin_wallet_debited"})


def _squash_column(name: str) -> str:
    return (
        str(name)
        .lower()
        .replace(" ", "_")
        .replace("(", "")
        .replace(")", "")
        .replace("__", "_")
        .strip("_")
    )


def _prepare_df(df: pd.DataFrame) -> pd.DataFrame:
    out = clean_columns(df)
    out.columns = [_squash_column(c) for c in out.columns]
    return out


def is_cryptocom_export(df: pd.DataFrame) -> bool:
    """True when the CSV matches a Crypto.com app transaction export."""
    cols = set(_prepare_df(df).columns)
    if "transaction_kind" not in cols:
        return False
    if "native_currency" not in cols:
        return False
    return "timestamp_utc" in cols or (
        "transaction_description" in cols and "currency" in cols
    )


def _parse_time(raw: object) -> datetime:
    ts = pd.to_datetime(raw, utc=True, errors="coerce")
    if pd.isna(ts):
        raise ValueError(f"Unparseable timestamp: {raw!r}")
    return ts.to_pydatetime()


def _float(raw: object) -> float:
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return 0.0
    return float(raw)


def _normalize_asset(raw: object) -> str:
    return str(raw or "").strip().upper()


def _kind(raw: object) -> str:
    return str(raw or "").strip().lower()


def _description(raw: object) -> str:
    return str(raw or "").strip().lower()


def _native_value(row: dict) -> tuple[float, Optional[str]]:
    amount = _float(row.get("native_amount"))
    currency = _normalize_asset(row.get("native_currency"))
    if amount > 0 and currency:
        return amount, currency
    usd = _float(row.get("native_amount_in_usd"))
    if usd > 0:
        return usd, "USD"
    return 0.0, None


def _str_field(raw: object, default: str = "") -> str:
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return default
    text = str(raw).strip()
    return default if text.lower() == "nan" else text


def _row_id(row: dict, timestamp: datetime) -> str:
    txhash = _str_field(row.get("transaction_hash"))
    asset = _normalize_asset(row.get("currency"))
    kind = _kind(row.get("transaction_kind"))
    if txhash:
        return f"cdc-{txhash[:24]}-{kind}-{asset}"
    return f"cdc-{timestamp.isoformat()}-{kind}-{asset}"


def _classify_row(row: dict) -> Optional[TransactionType]:
    kind = _kind(row.get("transaction_kind"))
    desc = _description(row.get("transaction_description"))
    amount = _float(row.get("amount"))

    if kind in EXCHANGE_KINDS:
        if amount < 0:
            return TransactionType.SELL
        if amount > 0:
            return TransactionType.BUY

    if kind in BUY_KINDS:
        return TransactionType.BUY
    if kind in SELL_KINDS:
        return TransactionType.SELL
    if kind in TRANSFER_IN_KINDS:
        return TransactionType.TRANSFER
    if kind in TRANSFER_OUT_KINDS:
        return TransactionType.TRANSFER
    if kind in STAKING_INCOME_KINDS:
        return TransactionType.STAKING
    if kind in INCOME_KINDS or "adjustment (credit)" in desc:
        return TransactionType.AIRDROP
    if kind in FEE_KINDS or "fee" in desc:
        return TransactionType.FEE

    if "withdraw" in desc and amount < 0:
        return TransactionType.TRANSFER
    if "deposit" in desc and amount > 0:
        return TransactionType.TRANSFER
    if "earn withdrawal" in desc:
        return TransactionType.TRANSFER
    if "balance conversion" in desc:
        return TransactionType.SELL if amount < 0 else TransactionType.BUY

    # Purchases / sales from description when kind is generic.
    if "buy " in desc or "purchase" in desc:
        return TransactionType.BUY
    if "sell " in desc or "sold" in desc:
        return TransactionType.SELL

    return None


def _transfer_direction(
    tx_type: TransactionType, row: dict
) -> Optional[str]:
    if tx_type != TransactionType.TRANSFER:
        return None
    kind = _kind(row.get("transaction_kind"))
    amount = _float(row.get("amount"))
    desc = _description(row.get("transaction_description"))

    if kind in TRANSFER_IN_KINDS or "earn withdrawal" in desc:
        return "IN"
    if kind in TRANSFER_OUT_KINDS or "withdraw" in desc or amount < 0:
        return "OUT"
    if amount < 0:
        return "OUT"
    if amount > 0:
        return "IN"
    return None


def _round_fiat(value: float) -> float:
    if value <= 0:
        return 0.0
    return round(value, 2) if value >= 1 else round(value, 6)


def _parse_row(row: dict, *, skip_kinds: Optional[Set[str]] = None) -> Optional[Transaction]:
    kind = _kind(row.get("transaction_kind"))
    if skip_kinds and kind in skip_kinds:
        return None

    asset = _normalize_asset(row.get("currency"))
    raw_amount = _float(row.get("amount"))
    amount = abs(raw_amount)
    if amount <= 0:
        return None

    if is_fiat(asset):
        return None

    tx_type = _classify_row(row)
    if tx_type is None:
        return None

    # Stablecoin flows are cash — only track explicit crypto legs elsewhere.
    if is_stablecoin(asset) and tx_type in (
        TransactionType.BUY,
        TransactionType.SELL,
        TransactionType.TRANSFER,
    ):
        if tx_type == TransactionType.TRANSFER:
            pass  # allow USDC withdrawal to external wallet
        else:
            return None

    timestamp = _parse_time(row.get("timestamp_utc"))
    fiat_value, fiat_currency = _native_value(row)
    transfer_direction = _transfer_direction(tx_type, row)

    counter_asset = None
    counter_amount = None
    to_currency = _normalize_asset(row.get("to_currency"))
    to_amount = _float(row.get("to_amount"))
    if to_currency and to_amount > 0 and not is_fiat(to_currency):
        counter_asset = to_currency
        counter_amount = abs(to_amount)

    txhash = _str_field(row.get("transaction_hash"))
    on_chain = (
        txhash
        if txhash.startswith("0x") and len(txhash) >= 42
        else None
    )

    return Transaction(
        id=_row_id(row, timestamp),
        timestamp=timestamp,
        asset=asset,
        transaction_type=tx_type,
        amount=amount,
        fiat_value_at_trigger=_round_fiat(fiat_value),
        fee_fiat=0.0,
        fiat_currency=fiat_currency,
        counter_asset=counter_asset,
        counter_amount=counter_amount,
        source="cryptocom",
        transfer_direction=transfer_direction,
        on_chain_tx_id=on_chain,
    )


def _swap_skip_kinds(rows: List[dict]) -> Set[str]:
    """When swap credit/debit pairs share a timestamp, parse via grouped logic."""
    by_time: Dict[datetime, List[dict]] = defaultdict(list)
    for row in rows:
        kind = _kind(row.get("transaction_kind"))
        if kind in {"crypto_wallet_swap_credited", "crypto_wallet_swap_debited"}:
            by_time[_parse_time(row.get("timestamp_utc"))].append(row)

    skip: Set[str] = set()
    for group in by_time.values():
        if len(group) >= 2:
            skip.add("crypto_wallet_swap_credited")
            skip.add("crypto_wallet_swap_debited")
    return skip


def _parse_swap_groups(rows: List[dict]) -> List[Transaction]:
    """Emit explicit buy/sell rows for paired balance conversions."""
    by_time: Dict[datetime, List[dict]] = defaultdict(list)
    for row in rows:
        kind = _kind(row.get("transaction_kind"))
        if kind in {"crypto_wallet_swap_credited", "crypto_wallet_swap_debited"}:
            by_time[_parse_time(row.get("timestamp_utc"))].append(row)

    transactions: List[Transaction] = []
    for ts, group in by_time.items():
        if len(group) < 2:
            for row in group:
                tx = _parse_row(row)
                if tx:
                    transactions.append(tx)
            continue

        debits = [r for r in group if _float(r.get("amount")) < 0]
        credits = [r for r in group if _float(r.get("amount")) > 0]
        for debit in debits:
            asset = _normalize_asset(debit.get("currency"))
            qty = abs(_float(debit.get("amount")))
            fiat_value, fiat_currency = _native_value(debit)
            if not fiat_value and credits:
                fiat_value, fiat_currency = _native_value(credits[0])
            counter = _normalize_asset(credits[0].get("currency")) if credits else None
            counter_qty = abs(_float(credits[0].get("amount"))) if credits else None
            transactions.append(
                Transaction(
                    id=_row_id(debit, ts),
                    timestamp=ts,
                    asset=asset,
                    transaction_type=TransactionType.SELL,
                    amount=qty,
                    fiat_value_at_trigger=_round_fiat(fiat_value),
                    fee_fiat=0.0,
                    fiat_currency=fiat_currency,
                    counter_asset=counter,
                    counter_amount=counter_qty,
                    source="cryptocom",
                )
            )
        for credit in credits:
            asset = _normalize_asset(credit.get("currency"))
            qty = abs(_float(credit.get("amount")))
            fiat_value, fiat_currency = _native_value(credit)
            counter = _normalize_asset(debits[0].get("currency")) if debits else None
            counter_qty = abs(_float(debits[0].get("amount"))) if debits else None
            transactions.append(
                Transaction(
                    id=_row_id(credit, ts),
                    timestamp=ts,
                    asset=asset,
                    transaction_type=TransactionType.BUY,
                    amount=qty,
                    fiat_value_at_trigger=_round_fiat(fiat_value),
                    fee_fiat=0.0,
                    fiat_currency=fiat_currency,
                    counter_asset=counter,
                    counter_amount=counter_qty,
                    source="cryptocom",
                )
            )
    return transactions


def _is_exchange_tx(tx: Transaction) -> bool:
    return tx.source == "cryptocom" and any(f"-{kind}-" in tx.id for kind in EXCHANGE_KINDS)


def _counter_qty_from_row(row: dict, counter: str) -> float:
    to_currency = _normalize_asset(row.get("to_currency"))
    if to_currency == counter:
        return abs(_float(row.get("to_amount")))
    return 0.0


def _backfill_exchange_counter_amounts(
    transactions: List[Transaction],
) -> List[Transaction]:
    """Fill missing ``counter_amount`` on legacy Crypto.com exchange rows."""
    exchange_txs = [t for t in transactions if _is_exchange_tx(t)]
    by_ts: Dict[datetime, List[Transaction]] = defaultdict(list)
    for tx in exchange_txs:
        by_ts[tx.timestamp].append(tx)

    buys_by_asset: Dict[str, List[Transaction]] = defaultdict(list)
    for tx in transactions:
        if tx.transaction_type == TransactionType.BUY and tx.amount > 0:
            buys_by_asset[tx.asset].append(tx)
    for asset in buys_by_asset:
        buys_by_asset[asset].sort(key=lambda t: t.timestamp)

    def _prior_unit_price(asset: str, before: datetime) -> float:
        for buy in reversed(buys_by_asset.get(asset, [])):
            if buy.timestamp <= before and buy.fiat_value_at_trigger > 0:
                return buy.fiat_value_at_trigger / buy.amount
        return 0.0

    def _nearby_unit_price(asset: str, around: datetime) -> float:
        prior = _prior_unit_price(asset, around)
        if prior > 0:
            return prior
        window = 600  # seconds — fiat purchase often follows exchange on CDC
        for buy in buys_by_asset.get(asset, []):
            if buy.fiat_value_at_trigger <= 0:
                continue
            delta = (buy.timestamp - around).total_seconds()
            if 0 < delta <= window:
                return buy.fiat_value_at_trigger / buy.amount
        return 0.0

    patched: List[Transaction] = []
    for tx in transactions:
        if not _is_exchange_tx(tx) or tx.counter_asset is None or tx.counter_amount:
            patched.append(tx)
            continue

        counter = tx.counter_asset
        inferred = 0.0
        for peer in by_ts.get(tx.timestamp, []):
            if peer.id != tx.id and peer.asset == counter and peer.amount > 0:
                inferred = peer.amount
                break

        if inferred <= 0:
            unit = _nearby_unit_price(counter, tx.timestamp)
            if unit > 0 and tx.fiat_value_at_trigger > 0:
                inferred = tx.fiat_value_at_trigger / unit

        if inferred > 0:
            patched.append(
                tx.model_copy(update={"counter_amount": round(inferred, 8)})
            )
        else:
            patched.append(tx)
    return patched


def _complete_exchange_legs(
    transactions: List[Transaction], records: List[dict]
) -> List[Transaction]:
    """Emit missing swap legs when Crypto.com exports only one side of an exchange."""
    exchange_rows_by_ts: Dict[datetime, List[dict]] = defaultdict(list)
    for row in records:
        if _kind(row.get("transaction_kind")) in EXCHANGE_KINDS:
            exchange_rows_by_ts[_parse_time(row.get("timestamp_utc"))].append(row)

    sells_at = {
        (t.timestamp, t.asset)
        for t in transactions
        if t.transaction_type == TransactionType.SELL and t.source == "cryptocom"
    }
    buys_at = {
        (t.timestamp, t.asset)
        for t in transactions
        if t.transaction_type == TransactionType.BUY and t.source == "cryptocom"
    }

    extras: List[Transaction] = []
    group_ids: Dict[tuple[datetime, str], str] = {}

    def _group_id(ts: datetime) -> str:
        return f"cdc-xchg-{ts.isoformat()}"

    # Credited side exported alone → add counter SELL.
    for tx in transactions:
        if not _is_exchange_tx(tx) or tx.transaction_type != TransactionType.BUY:
            continue
        counter = tx.counter_asset
        if not counter or is_fiat(counter) or is_stablecoin(counter):
            continue
        if (tx.timestamp, counter) in sells_at:
            continue

        rows = exchange_rows_by_ts.get(tx.timestamp, [])
        match_row = next(
            (
                row
                for row in rows
                if _normalize_asset(row.get("currency")) == tx.asset
                and _float(row.get("amount")) > 0
            ),
            None,
        )
        to_amount = tx.counter_amount or 0.0
        if match_row is not None:
            to_currency = _normalize_asset(match_row.get("to_currency"))
            to_amount = _counter_qty_from_row(match_row, counter) or to_amount
            if to_currency != counter or to_amount <= 0:
                continue

        if to_amount <= 0:
            continue

        gid = _group_id(tx.timestamp)
        extras.append(
            Transaction(
                id=f"cdc-{tx.timestamp.isoformat()}-crypto_exchange-sell-{counter}",
                timestamp=tx.timestamp,
                asset=counter,
                transaction_type=TransactionType.SELL,
                amount=to_amount,
                fiat_value_at_trigger=tx.fiat_value_at_trigger,
                fee_fiat=0.0,
                fiat_currency=tx.fiat_currency,
                counter_asset=tx.asset,
                counter_amount=tx.amount,
                source="cryptocom",
                trade_group_id=gid,
            )
        )
        sells_at.add((tx.timestamp, counter))
        group_ids[(tx.timestamp, tx.asset)] = gid

    # Debited side exported alone → add counter BUY.
    for tx in transactions:
        if not _is_exchange_tx(tx) or tx.transaction_type != TransactionType.SELL:
            continue
        counter = tx.counter_asset
        if not counter or is_fiat(counter) or is_stablecoin(counter):
            continue
        if (tx.timestamp, counter) in buys_at:
            continue

        rows = exchange_rows_by_ts.get(tx.timestamp, [])
        match_row = next(
            (
                row
                for row in rows
                if _normalize_asset(row.get("currency")) == tx.asset
                and _float(row.get("amount")) < 0
            ),
            None,
        )
        to_amount = tx.counter_amount or 0.0
        if match_row is not None:
            to_currency = _normalize_asset(match_row.get("to_currency"))
            to_amount = _counter_qty_from_row(match_row, counter) or to_amount
            if to_currency != counter or to_amount <= 0:
                continue

        if to_amount <= 0:
            continue

        gid = group_ids.get((tx.timestamp, tx.asset)) or _group_id(tx.timestamp)
        extras.append(
            Transaction(
                id=f"cdc-{tx.timestamp.isoformat()}-crypto_exchange-buy-{counter}",
                timestamp=tx.timestamp,
                asset=counter,
                transaction_type=TransactionType.BUY,
                amount=to_amount,
                fiat_value_at_trigger=tx.fiat_value_at_trigger,
                fee_fiat=0.0,
                fiat_currency=tx.fiat_currency,
                counter_asset=tx.asset,
                counter_amount=tx.amount,
                source="cryptocom",
                trade_group_id=gid,
            )
        )
        buys_at.add((tx.timestamp, counter))
        group_ids[(tx.timestamp, tx.asset)] = gid

    patched: List[Transaction] = []
    for tx in transactions:
        group_id = group_ids.get((tx.timestamp, tx.asset))
        if group_id and not tx.trade_group_id:
            tx = tx.model_copy(update={"trade_group_id": group_id})
        patched.append(tx)

    return patched + extras


def normalize_cryptocom_exchange_legs(
    transactions: List[Transaction],
) -> Tuple[List[Transaction], int]:
    """Repair swap legs on already-imported Crypto.com rows (no CSV required)."""
    before = len(transactions)
    transactions = _backfill_exchange_counter_amounts(transactions)
    records: List[dict] = []
    for tx in transactions:
        if not _is_exchange_tx(tx):
            continue
        raw_amount = (
            tx.amount
            if tx.transaction_type == TransactionType.BUY
            else -tx.amount
        )
        records.append(
            {
                "timestamp_utc": tx.timestamp.isoformat(),
                "transaction_kind": "crypto_exchange",
                "currency": tx.asset,
                "amount": raw_amount,
                "to_currency": tx.counter_asset,
                "to_amount": tx.counter_amount or 0.0,
            }
        )
    repaired = _complete_exchange_legs(transactions, records)
    return repaired, max(0, len(repaired) - before)


def parse_cryptocom_export(df: pd.DataFrame) -> List[Transaction]:
    """Parse a Crypto.com app CSV export into unified transactions."""
    prepared = _prepare_df(df)
    records = prepared.to_dict(orient="records")
    skip_kinds = _swap_skip_kinds(records)

    transactions: List[Transaction] = []
    transactions.extend(_parse_swap_groups(records))

    for row in records:
        parsed = _parse_row(row, skip_kinds=skip_kinds)
        if parsed is not None:
            transactions.append(parsed)

    transactions = _complete_exchange_legs(transactions, records)

    # Deduplicate by id (exports sometimes repeat rows).
    seen: Set[str] = set()
    unique: List[Transaction] = []
    for tx in sorted(transactions, key=lambda t: (t.timestamp, t.id)):
        if tx.id in seen:
            continue
        seen.add(tx.id)
        unique.append(tx)

    return unique
