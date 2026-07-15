"""Data Health Ledger — scan for orphaned inflows missing acquisition history."""

from __future__ import annotations

from typing import Dict, List, Set

from .config import is_stablecoin
from .cost_basis_overrides import overrides_by_anchor
from .ledger_filters import DUST_AMOUNT, DUST_FIAT_VALUE
from .schemas import (
    DataHealthSummary,
    ManualCostBasisOverride,
    OrphanedInflowFlag,
    Transaction,
    TransactionType,
)
from .transfer_matching import match_transfer_pairs


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


def _orphan_reason(tx: Transaction) -> str:
    return (
        f"{tx.amount:g} {tx.asset} arrived with no recorded purchase price. "
        "Common when an exchange API reports a balance after historical "
        "trade data was purged."
    )


def find_orphaned_inflows(
    transactions: List[Transaction],
    overrides: List[ManualCostBasisOverride] | None = None,
) -> List[OrphanedInflowFlag]:
    """Detect inbound transfers that lack explainable historical cost basis.

    Only unpaired ``TRANSFER IN`` rows with missing/zero fiat are flagged.
    A valued unpaired receipt already establishes cost basis in the tax
    engines, so those are not treated as orphans.
    """
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

        if tx.id in override_map:
            continue

        # Valued receipts already seed cost basis — not an orphan.
        if tx.fiat_value_at_trigger > DUST_FIAT_VALUE:
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
                message=_orphan_reason(tx),
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
