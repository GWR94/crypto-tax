"""Read path for the active ledger (live imports vs bundled demo)."""

from __future__ import annotations

from typing import List

from .sample_data import default_transactions, without_sample
from .schemas import ManualCostBasisOverride, Transaction
from .state import state

SUPPORTED_DATA_MODES = frozenset({"live", "demo"})


def is_demo_mode() -> bool:
    return state.data_mode() == "demo"


def active_transactions() -> List[Transaction]:
    """Transactions visible in the current data mode."""
    if is_demo_mode():
        return default_transactions()
    return without_sample(state.transactions())


def active_cost_basis_overrides() -> List[ManualCostBasisOverride]:
    """Manual overrides apply to live data only."""
    if is_demo_mode():
        return []
    return state.cost_basis_overrides()
