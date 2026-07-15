"""Verification tests for the bundled demo ledger.

Run after any change to ``sample_data.default_transactions()``::

    pytest tests/test_demo_ledger.py -v
"""

from __future__ import annotations

from app import demo_verification as expected
from app.data_health import find_orphaned_inflows
from app.hmrc_cgt_engine import (
    calculate_uk_cgt,
    calculate_uk_income,
    compute_uk_missing_cost_basis,
    compute_uk_open_pools,
)
from app.perp_tax import build_perp_tax_summary
from app.perps import build_perps_summary
from app.sample_data import default_transactions
from app.schemas import AccountingMethod, CgtMatchType, spot_transactions
from app.tax_engine import calculate_realized_gains


def _demo_spot():
    return spot_transactions(default_transactions())


def _demo_all():
    return default_transactions()


def test_demo_uk_cgt_legs_2024_25():
    report = calculate_uk_cgt(_demo_spot(), tax_year_label=expected.UK_TAX_YEAR)
    assert report.disposal_count == len(expected.UK_CGT_LEGS_2024_25)
    assert report.net_gain == expected.UK_CGT_NET_GAIN_2024_25
    assert report.annual_exempt_amount == expected.UK_CGT_ALLOWANCE_2024_25
    assert report.taxable_gain_after_allowance == expected.UK_CGT_TAXABLE_AFTER_ALLOWANCE_2024_25

    by_id = {row.disposal_id: row for row in report.rows}
    for leg in expected.UK_CGT_LEGS_2024_25:
        row = by_id[leg.disposal_id]
        assert row.asset == leg.asset
        assert row.match_type == CgtMatchType(leg.match_type)
        assert row.proceeds == leg.proceeds
        assert row.allowable_cost == leg.allowable_cost
        assert row.gain == leg.gain


def test_demo_uk_income_2024_25():
    income = calculate_uk_income(_demo_spot(), tax_year_label=expected.UK_TAX_YEAR)
    assert income.staking_income == expected.UK_INCOME_STAKING_2024_25
    assert income.airdrop_income == expected.UK_INCOME_AIRDROP_2024_25
    assert income.total_income == expected.UK_INCOME_TOTAL_2024_25


def test_demo_us_fifo_vs_hifo_bnb():
    """Demo ledger is GBP-denominated; US path reports in USD via FX.

    Assert currency + FIFO/HIFO ordering rather than hard GBP golden totals.
    """
    spot = _demo_spot()
    fifo = calculate_realized_gains(
        spot,
        AccountingMethod.FIFO,
        tax_year=expected.US_CALENDAR_YEAR,
        tax_jurisdiction="US",
    )
    hifo = calculate_realized_gains(
        spot,
        AccountingMethod.HIFO,
        tax_year=expected.US_CALENDAR_YEAR,
        tax_jurisdiction="US",
    )
    assert fifo.reporting_currency == "USD"
    assert hifo.reporting_currency == "USD"
    assert fifo.total_gain != hifo.total_gain

    fifo_bnb = next(r for r in fifo.rows if r.asset == "BNB")
    hifo_bnb = next(r for r in hifo.rows if r.asset == "BNB")
    # FIFO sells the cheap lot first → higher gain than HIFO on the same BNB sell.
    assert fifo_bnb.gain_loss > hifo_bnb.gain_loss
    assert fifo_bnb.gain_loss > 0
    assert hifo_bnb.gain_loss < 0


def test_demo_uk_section_104_pool_snapshot():
    pools = compute_uk_open_pools(_demo_spot())
    for asset, (qty, cost) in expected.UK_OPEN_POOLS.items():
        assert asset in pools
        assert pools[asset][0] == qty
        assert pools[asset][1] == cost


def test_demo_missing_cost_basis_flag():
    flags = compute_uk_missing_cost_basis(_demo_spot())
    assert len(flags) == expected.MISSING_COST_BASIS_COUNT
    assert flags[0].disposal_id == expected.MISSING_COST_BASIS_DISPOSAL_ID


def test_demo_orphaned_inflow():
    orphans = find_orphaned_inflows(_demo_spot())
    assert len(orphans) == expected.ORPHANED_INFLOW_COUNT
    assert orphans[0].transaction_id == expected.ORPHANED_INFLOW_ID


def test_demo_perps_by_source():
    txs = _demo_all()
    for source, want in expected.PERPS_BY_SOURCE.items():
        summary = build_perps_summary([t for t in txs if t.source == source])
        assert summary.closed_pnl == want["closed_pnl"]
        assert summary.total_fees == want["fees"]
        assert summary.winning_closes == want["wins"]
        assert summary.losing_closes == want["losses"]


def test_demo_perp_tax_schedule_2024_25():
    summary = build_perp_tax_summary(
        _demo_all(),
        jurisdiction="UK",
        treatment="income",
        period_label=expected.UK_TAX_YEAR,
    )
    assert summary.net_pnl == expected.PERP_TAX_NET_2024_25
    assert summary.event_count == 6


def test_demo_perps_excluded_from_uk_cgt():
    report = calculate_uk_cgt(_demo_all(), tax_year_label=expected.UK_TAX_YEAR)
    assert all("demo-hl" not in r.disposal_id for r in report.rows)
    assert all("demo-woox" not in r.disposal_id for r in report.rows)


def test_demo_hyperliquid_beats_variational_beats_woox():
    txs = _demo_all()
    hl = build_perps_summary([t for t in txs if t.source == "hyperliquid"]).closed_pnl
    var = build_perps_summary([t for t in txs if t.source == "variational"]).closed_pnl
    woo = build_perps_summary([t for t in txs if t.source == "woox"]).closed_pnl
    assert hl > var > woo
