"""Data Health Ledger — scan for orphaned inflows missing acquisition history."""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Set

from .config import is_stablecoin
from .cost_basis_overrides import overrides_by_anchor
from .ledger_filters import DUST_AMOUNT, DUST_FIAT_VALUE
from .schemas import (
    ACQUISITION_TYPES,
    DISPOSAL_TYPES,
    DataHealthSummary,
    ManualCostBasisOverride,
    OrphanedInflowFlag,
    Transaction,
    TransactionType,
)
from .transfer_matching import match_transfer_pairs

_EPS = 1e-9


def _is_orphan_candidate(tx: Transaction, pair_map: Dict[str, str]) -> bool:
    if tx.transaction_type != TransactionType.TRANSFER:
        return False
    if tx.transfer_direction != "IN":
        return False
    if tx.amount < DUST_AMOUNT:
        return False
    if is_stablecoin(tx.asset):
        return False
    if tx.id in pair_map or tx.transfer_pair_id:
        return False
    return True


def _prior_costed_quantity(
    asset: str,
    before: datetime,
    transactions: List[Transaction],
    pair_map: Dict[str, str],
) -> float:
    """Net quantity from costed acquisitions before *before*."""
    balance = 0.0
    ordered = sorted(transactions, key=lambda t: t.timestamp)
    for tx in ordered:
        if tx.timestamp >= before:
            break
        if tx.asset != asset:
            continue
        if tx.amount <= 0:
            continue

        if tx.transaction_type in DISPOSAL_TYPES:
            balance -= tx.amount
        elif tx.transaction_type == TransactionType.TRANSFER:
            if tx.transfer_direction == "OUT":
                balance -= tx.amount
            elif tx.transfer_direction == "IN":
                if tx.id in pair_map or tx.transfer_pair_id:
                    continue
                if tx.fiat_value_at_trigger > DUST_FIAT_VALUE:
                    balance += tx.amount
        elif tx.transaction_type in ACQUISITION_TYPES:
            if tx.fiat_value_at_trigger > DUST_FIAT_VALUE or tx.source == "manual_override":
                balance += tx.amount

    return max(0.0, balance)


def _orphan_reason(tx: Transaction, prior_costed: float) -> str:
    if tx.fiat_value_at_trigger <= DUST_FIAT_VALUE:
        return (
            f"{tx.amount:g} {tx.asset} arrived with no recorded purchase price. "
            "Common when an exchange API reports a balance after historical "
            "trade data was purged."
        )
    if prior_costed <= _EPS:
        return (
            f"{tx.amount:g} {tx.asset} deposit is the first costed appearance of "
            "this asset in your imports — earlier buy history may be missing."
        )
    return (
        f"{tx.amount:g} {tx.asset} inbound transfer has no matching acquisition "
        "history in earlier imports."
    )


def find_orphaned_inflows(
    transactions: List[Transaction],
    overrides: List[ManualCostBasisOverride] | None = None,
) -> List[OrphanedInflowFlag]:
    """Detect inbound transfers that lack explainable historical cost basis."""
    override_map = overrides_by_anchor(overrides or [])
    pair_map = match_transfer_pairs(transactions)
    flags: List[OrphanedInflowFlag] = []
    seen: Set[str] = set()

    for tx in sorted(transactions, key=lambda t: t.timestamp):
        if not _is_orphan_candidate(tx, pair_map):
            continue
        if tx.id in seen:
            continue
        seen.add(tx.id)

        has_override = tx.id in override_map
        prior = _prior_costed_quantity(tx.asset, tx.timestamp, transactions, pair_map)

        needs_basis = (
            tx.fiat_value_at_trigger <= DUST_FIAT_VALUE or prior <= _EPS
        )
        if not needs_basis and has_override:
            continue
        if has_override:
            continue

        if not needs_basis:
            continue

        flags.append(
            OrphanedInflowFlag(
                transaction_id=tx.id,
                asset=tx.asset,
                timestamp=tx.timestamp,
                quantity=tx.amount,
                source=tx.source,
                import_id=tx.import_id,
                fiat_value_at_trigger=tx.fiat_value_at_trigger,
                message=_orphan_reason(tx, prior),
                has_override=False,
            )
        )

    return flags


def build_data_health_summary(
    transactions: List[Transaction],
    overrides: List[ManualCostBasisOverride],
) -> DataHealthSummary:
    orphaned = find_orphaned_inflows(transactions, overrides)
    return DataHealthSummary(
        orphaned_inflows=orphaned,
        cost_basis_overrides=overrides,
    )
