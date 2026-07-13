"""Manual cost-basis overrides for orphaned exchange inflows.

When an import shows a deposit without purchase history (e.g. after an exchange
data purge), the user can supply historical acquisition data. Overrides are
injected as synthetic BUY rows and the anchored deposit is treated as
basis-neutral so coins are not double-counted.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

from .config import REPORTING_CURRENCY
from .schemas import ManualCostBasisOverride, Transaction, TransactionType

MANUAL_OVERRIDE_SOURCE = "manual_override"
_PAIR_PREFIX = "manual-basis-pair-"


def manual_override_pair_id(anchor_transaction_id: str) -> str:
    return f"{_PAIR_PREFIX}{anchor_transaction_id}"


def synthetic_buy_id(anchor_transaction_id: str) -> str:
    return f"manual-basis-{anchor_transaction_id}"


def build_synthetic_buy(override: ManualCostBasisOverride) -> Transaction:
    """Create a BUY row from a saved manual override."""
    unit = override.unit_cost
    if unit <= 0 and override.quantity > 0:
        unit = override.total_fiat_spent / override.quantity
    total = override.total_fiat_spent
    if total <= 0 and override.quantity > 0:
        total = unit * override.quantity
    return Transaction(
        id=synthetic_buy_id(override.anchor_transaction_id),
        timestamp=override.acquisition_date,
        asset=override.asset,
        transaction_type=TransactionType.BUY,
        amount=override.quantity,
        fiat_value_at_trigger=round(total, 2),
        fee_fiat=0.0,
        fiat_currency=override.reporting_currency or REPORTING_CURRENCY,
        source=MANUAL_OVERRIDE_SOURCE,
    )


def overrides_by_anchor(
    overrides: List[ManualCostBasisOverride],
) -> Dict[str, ManualCostBasisOverride]:
    return {o.anchor_transaction_id: o for o in overrides}


def prepare_tax_ledger(
    transactions: List[Transaction],
    overrides: List[ManualCostBasisOverride],
) -> List[Transaction]:
    """Return ledger rows with synthetic buys and paired orphan deposits."""
    if not overrides:
        return list(transactions)

    by_anchor = overrides_by_anchor(overrides)
    synthetics = [build_synthetic_buy(o) for o in overrides]
    enriched: List[Transaction] = []
    for tx in transactions:
        if tx.id in by_anchor:
            enriched.append(
                tx.model_copy(
                    update={
                        "transfer_pair_id": manual_override_pair_id(tx.id),
                    }
                )
            )
        else:
            enriched.append(tx)
    return synthetics + enriched


def resolve_unit_and_total(
    *,
    quantity: float,
    unit_cost: Optional[float] = None,
    total_fiat_spent: Optional[float] = None,
) -> tuple[float, float]:
    """Derive unit cost and total fiat from whichever field the user supplied."""
    if quantity <= 0:
        raise ValueError("Quantity must be positive.")

    unit = float(unit_cost or 0.0)
    total = float(total_fiat_spent or 0.0)

    if unit > 0 and total <= 0:
        total = round(unit * quantity, 2)
    elif total > 0 and unit <= 0:
        unit = round(total / quantity, 8)
    elif unit <= 0 and total <= 0:
        raise ValueError("Provide unit cost or total fiat spent.")

    return unit, total


def build_override_from_request(
    *,
    anchor: Transaction,
    acquisition_date: datetime,
    unit_cost: Optional[float] = None,
    total_fiat_spent: Optional[float] = None,
    notes: Optional[str] = None,
    existing: ManualCostBasisOverride | None = None,
) -> ManualCostBasisOverride:
    unit, total = resolve_unit_and_total(
        quantity=anchor.amount,
        unit_cost=unit_cost,
        total_fiat_spent=total_fiat_spent,
    )
    now = datetime.now(timezone.utc)
    return ManualCostBasisOverride(
        anchor_transaction_id=anchor.id,
        asset=anchor.asset,
        quantity=anchor.amount,
        acquisition_date=acquisition_date,
        unit_cost=unit,
        total_fiat_spent=total,
        reporting_currency=REPORTING_CURRENCY,
        notes=notes if notes is not None else (existing.notes if existing else None),
        created_at=existing.created_at if existing else now,
        updated_at=now,
    )
