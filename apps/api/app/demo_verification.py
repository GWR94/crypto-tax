"""Expected tax figures for the bundled demo ledger (GBP, identity FX).

All values are asserted in ``tests/test_demo_ledger.py``. Update this module
whenever ``sample_data.default_transactions()`` changes.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DemoCgtLeg:
    disposal_id: str
    asset: str
    match_type: str
    proceeds: float
    allowable_cost: float
    gain: float


UK_TAX_YEAR = "2024/25"

# Disposal legs in the 2024/25 UK tax year (newest first — same order as CGT report).
UK_CGT_LEGS_2024_25: tuple[DemoCgtLeg, ...] = (
    DemoCgtLeg("demo-ada-bb-sell", "ADA", "thirty_day", 1200.0, 1100.0, 100.0),
    DemoCgtLeg("demo-xrp-sameday-sell", "XRP", "same_day", 1500.0, 1000.0, 500.0),
    DemoCgtLeg("demo-doge-sell", "DOGE", "unmatched", 158.0, 0.0, 158.0),
    DemoCgtLeg("demo-avax-pool-sell", "AVAX", "section_104", 1250.0, 1000.0, 250.0),
    DemoCgtLeg("demo-bnb-fifo-sell", "BNB", "section_104", 1500.0, 1500.0, 0.0),
)

UK_CGT_NET_GAIN_2024_25 = 1008.0
UK_CGT_ALLOWANCE_2024_25 = 3000.0
UK_CGT_TAXABLE_AFTER_ALLOWANCE_2024_25 = 0.0

UK_INCOME_STAKING_2024_25 = 50.0
UK_INCOME_AIRDROP_2024_25 = 500.0
UK_INCOME_TOTAL_2024_25 = 550.0

US_CALENDAR_YEAR = 2024
US_FIFO_TOTAL_GAIN_2024 = 1608.0
US_HIFO_TOTAL_GAIN_2024 = 608.0
US_FIFO_BNB_GAIN_2024 = 500.0
US_HIFO_BNB_GAIN_2024 = -500.0

MISSING_COST_BASIS_COUNT = 1
MISSING_COST_BASIS_DISPOSAL_ID = "demo-doge-sell"

ORPHANED_INFLOW_COUNT = 1
ORPHANED_INFLOW_ID = "demo-mexc-deposit"

PERPS_BY_SOURCE = {
    "hyperliquid": {"closed_pnl": 1200.0, "fees": 30.0, "wins": 2, "losses": 0},
    "variational": {"closed_pnl": 100.0, "fees": 10.0, "wins": 1, "losses": 1},
    "woox": {"closed_pnl": -500.0, "fees": 25.0, "wins": 0, "losses": 2},
}

PERP_TAX_NET_2024_25 = 735.0

# Section 104 pool after full ledger matching (quantity, total cost GBP).
UK_OPEN_POOLS: dict[str, tuple[float, float]] = {
    "ARB": (625.0, 500.0),
    "ADA": (2000.0, 1000.0),
    "BNB": (2.0, 1500.0),
    "BTC": (0.2, 8010.0),
    "ETH": (2.02, 5065.0),
    "LINK": (100.0, 1460.0),
    "SOL": (50.0, 9515.0),
}
