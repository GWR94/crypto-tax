"""Ledger view filters and exchange-internal noise removal."""

from __future__ import annotations

from typing import List, Set

from .schemas import Transaction, TransactionType
from .staking_withdrawals import unstake_group_ids

# Exchange CSV sources where earn/staking sub-accounts create echo transfers.
_EXCHANGE_SOURCES = frozenset({"binance", "cryptocom", "exchange", "kraken"})

# Max hours after a staking credit for a matching transfer to be treated as echo.
_STAKING_ECHO_MAX_HOURS = 48.0
# Max seconds between a deposit transfer-in and a duplicate staking credit.
_DEPOSIT_STAKING_MAX_SEC = 300.0
_AMOUNT_REL_TOL = 0.02

# Negligible rows (e.g. Solana 1e-7 SOL rent refunds, Kraken 0 SOL placeholders).
DUST_AMOUNT = 1e-6
DUST_FIAT_VALUE = 0.50
# Tiny SOL receipts from closing ephemeral accounts (not real deposits).
_SOL_RENT_CRUMB_MAX = 0.0001


def is_sol_rent_crumb(tx: Transaction) -> bool:
    """True for zero-value SOL/WSOL transfer-in crumbs from account closures."""
    if tx.source != "solana":
        return False
    if tx.transaction_type != TransactionType.TRANSFER:
        return False
    if tx.transfer_direction != "IN":
        return False
    if tx.asset.upper() not in {"SOL", "WSOL"}:
        return False
    if tx.amount > _SOL_RENT_CRUMB_MAX:
        return False
    return tx.fiat_value_at_trigger <= 0


def is_solana_account_rent(tx: Transaction) -> bool:
    """Rent deposits or misclassified rent rows — not taxable fees or disposals."""
    if tx.source != "solana":
        return False
    if tx.asset.upper() not in {"SOL", "WSOL"}:
        return False
    if tx.fiat_value_at_trigger > 0:
        return False
    if tx.amount >= 0.05:
        return False
    if tx.transaction_type == TransactionType.FEE:
        return True
    if tx.transaction_type == TransactionType.TRANSFER and tx.transfer_direction == "OUT":
        return True
    if tx.transaction_type == TransactionType.SELL:
        return True
    return False


def is_dust_transaction(tx: Transaction) -> bool:
    """True for sub-dust amounts or trivial fiat value (matches dashboard ledger filter)."""
    # LP receipt/burn + pool-underlying legs are priced later — never dust.
    if tx.venue_order_type in {"amm_lp", "amm_lp_pool"}:
        return False
    if tx.amount < DUST_AMOUNT:
        return True
    if tx.fiat_value_at_trigger > 0 and tx.fiat_value_at_trigger < DUST_FIAT_VALUE:
        return True
    if is_sol_rent_crumb(tx):
        return True
    if is_solana_account_rent(tx):
        return True
    return False


def strip_dust_transactions(
    transactions: List[Transaction],
) -> tuple[List[Transaction], int]:
    """Drop negligible transfers/rewards that add noise without tax impact."""
    kept = [tx for tx in transactions if not is_dust_transaction(tx)]
    return kept, len(transactions) - len(kept)


def _amounts_close(a: float, b: float) -> bool:
    if a <= 0 or b <= 0:
        return False
    return abs(a - b) / max(a, b) <= _AMOUNT_REL_TOL


def is_deposit_staking_duplicate(staking: Transaction, transfer: Transaction) -> bool:
    """True when *staking* duplicates an inbound deposit on the same exchange.

    Kraken often logs both ``transfer in`` and ``staking`` for the same external
    deposit — only the transfer should remain (basis-neutral move).
    """
    if staking.transaction_type != TransactionType.STAKING:
        return False
    if transfer.transaction_type != TransactionType.TRANSFER:
        return False
    if transfer.transfer_direction != "IN":
        return False
    if (staking.source or "") not in _EXCHANGE_SOURCES:
        return False
    if (staking.source or "") != (transfer.source or ""):
        return False
    if staking.asset != transfer.asset:
        return False
    if not _amounts_close(staking.amount, transfer.amount):
        return False
    delta_sec = abs((transfer.timestamp - staking.timestamp).total_seconds())
    return delta_sec <= _DEPOSIT_STAKING_MAX_SEC


def is_staking_echo_transfer(staking: Transaction, transfer: Transaction) -> bool:
    """True when *transfer* is the exchange moving a staking credit to spot."""
    if staking.transaction_type != TransactionType.STAKING:
        return False
    if transfer.transaction_type != TransactionType.TRANSFER:
        return False
    if transfer.transfer_direction != "OUT":
        return False
    if (transfer.source or "") not in _EXCHANGE_SOURCES:
        return False
    if (staking.source or "") != (transfer.source or ""):
        return False
    if staking.asset != transfer.asset:
        return False
    if staking.amount <= 0 or transfer.amount <= 0:
        return False

    if not _amounts_close(staking.amount, transfer.amount):
        return False

    delta_h = (transfer.timestamp - staking.timestamp).total_seconds() / 3600.0
    return 0 <= delta_h <= _STAKING_ECHO_MAX_HOURS


def collapse_staking_echo_transfers(
    transactions: List[Transaction],
) -> tuple[List[Transaction], int]:
    """Drop exchange noise: earn→spot echo transfers and deposit/staking dupes."""
    staking_rows = [t for t in transactions if t.transaction_type == TransactionType.STAKING]
    if not staking_rows:
        return transactions, 0

    drop_ids: Set[str] = set()
    transfers = [
        t
        for t in transactions
        if t.transaction_type == TransactionType.TRANSFER
        and (t.source or "") in _EXCHANGE_SOURCES
    ]

    for transfer in transfers:
        for staking in staking_rows:
            if is_staking_echo_transfer(staking, transfer):
                drop_ids.add(transfer.id)
                break

    for staking in staking_rows:
        if staking.id in drop_ids:
            continue
        for transfer in transfers:
            if transfer.id in drop_ids:
                continue
            if is_deposit_staking_duplicate(staking, transfer):
                drop_ids.add(staking.id)
                break

    if not drop_ids:
        return transactions, 0

    return [t for t in transactions if t.id not in drop_ids], len(drop_ids)


def filter_exclude_staking(
    transactions: List[Transaction],
    *,
    exclude_echo_transfers: bool = True,
) -> List[Transaction]:
    """Remove staking income rows and optional echo transfers for ledger views."""
    keep_unstake = unstake_group_ids(transactions)

    staking_rows = [
        t
        for t in transactions
        if t.transaction_type == TransactionType.STAKING
        and (t.trade_group_id or "") not in keep_unstake
    ]
    drop_ids: Set[str] = {t.id for t in staking_rows}

    if not exclude_echo_transfers:
        return [t for t in transactions if t.id not in drop_ids]

    for transfer in transactions:
        if transfer.transaction_type != TransactionType.TRANSFER:
            continue
        for staking in staking_rows:
            if is_staking_echo_transfer(staking, transfer):
                drop_ids.add(transfer.id)
                break
            if is_deposit_staking_duplicate(staking, transfer):
                drop_ids.add(staking.id)
                break

    return [t for t in transactions if t.id not in drop_ids]
