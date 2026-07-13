"""Drift Protocol collateral normalization (spot margin deposits / withdrawals)."""

from __future__ import annotations

from typing import Dict, List, Optional, Set, Tuple

from .config import is_stablecoin
from .schemas import Transaction, TransactionType

DRIFT_PROGRAM_ID = "dRiftyHA39MWEi3m9aunc5MzRF1JYuBsbn6VPcn33UH"

DRIFT_HELIUS_SOURCES = frozenset({"DRIFT"})

# Standard counterparty tag on parsed Drift collateral rows (user PDAs vary).
DRIFT_COLLATERAL_COUNTERPARTY = DRIFT_PROGRAM_ID

_COLLATERAL_ASSETS = frozenset(
    {
        "SOL",
        "WSOL",
        "MSOL",
        "BSOL",
        "JITOSOL",
        "USDC",
        "USDT",
        "PYUSD",
        "USDG",
    }
)


def is_drift_helius_source(source: Optional[str]) -> bool:
    if not source:
        return False
    return source.strip().upper() in DRIFT_HELIUS_SOURCES


def is_drift_collateral_counterparty(addr: Optional[str]) -> bool:
    if not addr:
        return False
    return addr.strip() == DRIFT_COLLATERAL_COUNTERPARTY


def is_drift_collateral_principal(asset: str) -> bool:
    sym = asset.strip().upper()
    return sym in _COLLATERAL_ASSETS or is_stablecoin(sym)


def _as_transfer_in(tx: Transaction) -> Transaction:
    return tx.model_copy(
        update={
            "transaction_type": TransactionType.TRANSFER,
            "transfer_direction": "IN",
            "counter_asset": None,
            "counter_amount": None,
        }
    )


def _as_transfer_out(tx: Transaction) -> Transaction:
    return tx.model_copy(
        update={
            "transaction_type": TransactionType.TRANSFER,
            "transfer_direction": "OUT",
            "counter_asset": None,
            "counter_amount": None,
        }
    )


def _group_by_id(transactions: List[Transaction]) -> Dict[str, List[Transaction]]:
    groups: Dict[str, List[Transaction]] = {}
    for tx in transactions:
        if tx.trade_group_id:
            groups.setdefault(tx.trade_group_id, []).append(tx)
    return groups


def normalize_drift_collateral(
    transactions: List[Transaction],
) -> Tuple[List[Transaction], int]:
    """Reclassify Drift collateral BUY/SELL legs as internal transfers."""
    patches: Dict[str, Transaction] = {}
    changed = 0
    by_gid = _group_by_id(transactions)

    def touch(tx: Transaction) -> None:
        nonlocal changed
        if tx.id in patches:
            return
        if not is_drift_collateral_principal(tx.asset):
            return
        if tx.transaction_type == TransactionType.BUY:
            patches[tx.id] = _as_transfer_in(tx)
            changed += 1
        elif tx.transaction_type == TransactionType.SELL:
            patches[tx.id] = _as_transfer_out(tx)
            changed += 1

    for tx in transactions:
        if tx.source != "solana":
            continue
        if is_drift_collateral_counterparty(tx.counterparty_address):
            touch(tx)
            continue
        if tx.venue_order_type == "drift_collateral":
            touch(tx)

    for gid, group in by_gid.items():
        if not any(
            is_drift_collateral_counterparty(t.counterparty_address)
            or t.venue_order_type == "drift_collateral"
            for t in group
        ):
            continue
        for leg in group:
            if leg.source == "solana" and leg.transaction_type in (
                TransactionType.BUY,
                TransactionType.SELL,
            ):
                touch(leg)

    if not patches:
        return transactions, 0
    return ([patches.get(tx.id, tx) for tx in transactions], changed)
