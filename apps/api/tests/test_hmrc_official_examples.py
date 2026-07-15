"""HMRC Cryptoassets Manual goldens + narrative gap coverage.

Official pooling examples: CRYPTO22251–CRYPTO22257 (totals).

Run::

    npm run test:api -- tests/test_hmrc_official_examples.py
"""

from __future__ import annotations

import pytest

from app.fork_normalize import EVENT_HARD_FORK, normalize_hard_forks
from app.hmrc_cgt_engine import (
    calculate_uk_cgt,
    calculate_uk_income,
    compute_uk_open_pools,
)
from app.hmrc_official_examples import (
    FIXTURE_BUILDERS,
    NARRATIVE_CASES,
    OFFICIAL_CASES,
    Provenance,
    fixture_crypto22257,
)
from app.schemas import CgtMatchType, TransactionType


@pytest.mark.parametrize("case", OFFICIAL_CASES, ids=lambda c: c.case_id)
def test_official_catalogue_metadata(case) -> None:
    assert case.provenance == Provenance.OFFICIAL
    assert case.hmrc_ref.startswith("CRYPTO")
    assert case.url.startswith("https://www.gov.uk/")
    assert case.case_id in FIXTURE_BUILDERS


def test_crypto22251_basic_section_104() -> None:
    txs = FIXTURE_BUILDERS["crypto22251"]()
    report = calculate_uk_cgt(txs)
    assert report.net_gain == 258_000.0
    assert len(report.rows) == 1
    assert report.rows[0].match_type == CgtMatchType.SECTION_104
    assert report.rows[0].allowable_cost == 42_000.0
    assert compute_uk_open_pools(txs)["TOKA"] == (100.0, 84_000.0)


def test_crypto22252_same_day() -> None:
    txs = FIXTURE_BUILDERS["crypto22252"]()
    report = calculate_uk_cgt(txs)
    # Exact half-pennies; HMRC published £462 after rounding £937.50 → £938.
    assert report.net_gain == 462.5
    assert report.total_proceeds == 1400.0
    assert all(r.match_type == CgtMatchType.SAME_DAY for r in report.rows)
    assert compute_uk_open_pools(txs)["TOKB"] == (5100.0, 562.5)


def test_crypto22253_thirty_day() -> None:
    txs = FIXTURE_BUILDERS["crypto22253"]()
    report = calculate_uk_cgt(txs)
    assert report.net_gain == 185.0  # £165 + £20
    assert all(r.match_type == CgtMatchType.THIRTY_DAY for r in report.rows)
    assert compute_uk_open_pools(txs)["TOKC"] == (2200.0, 1060.0)


def test_crypto22254_same_day_and_section_104() -> None:
    txs = FIXTURE_BUILDERS["crypto22254"]()
    report = calculate_uk_cgt(txs)
    assert report.total_proceeds == 642.0
    assert report.net_gain == 79.5  # HMRC £79 after rounding S.104 slice
    assert any(r.match_type == CgtMatchType.SAME_DAY for r in report.rows)
    assert any(r.match_type == CgtMatchType.SECTION_104 for r in report.rows)
    assert compute_uk_open_pools(txs)["TOKD"] == (7500.0, 937.5)


def test_crypto22255_thirty_day_and_section_104() -> None:
    txs = FIXTURE_BUILDERS["crypto22255"]()
    report = calculate_uk_cgt(txs)
    assert report.net_gain == 92_500.0
    by_type = {r.match_type for r in report.rows}
    assert by_type == {CgtMatchType.THIRTY_DAY, CgtMatchType.SECTION_104}
    assert compute_uk_open_pools(txs)["TOKE"] == (10_500.0, 150_000.0)


def test_crypto22256_all_three_rules() -> None:
    txs = FIXTURE_BUILDERS["crypto22256"]()
    report = calculate_uk_cgt(txs)
    assert report.total_gains == 25_000.0  # 15k + 10k
    assert abs(report.total_losses - 163_636.36) < 0.01
    assert abs(report.net_gain - (-138_636.36)) < 0.01
    types = {r.match_type for r in report.rows}
    assert CgtMatchType.SAME_DAY in types
    assert CgtMatchType.THIRTY_DAY in types
    assert CgtMatchType.SECTION_104 in types
    qty, cost = compute_uk_open_pools(txs)["TOKF"]
    assert qty == 10_000.0
    assert abs(cost - 31_363.64) < 0.01


def test_crypto22257_crypto_crypto() -> None:
    """Same-day aggregation + cross-asset consideration (CRYPTO22257)."""
    txs = fixture_crypto22257()
    report = calculate_uk_cgt(txs)
    # HMRC published: gains 280+267+18=565, losses 120+1417=1537, net −972.
    # Per-leg presentation differs (we split the 31 Aug G disposal across match
    # types) and mid-row fiat rounding leaves gains/losses at 564.67 / 1536.67,
    # but headline net and closing pools match the manual.
    assert report.net_gain == -972.0
    assert abs(report.total_gains - 564.67) < 0.01
    assert abs(report.total_losses - 1536.67) < 0.01

    pools = compute_uk_open_pools(txs)
    assert "TOKH" not in pools
    assert pools["TOKG"] == (99_730.0, 298_890.0)


@pytest.mark.parametrize("case", NARRATIVE_CASES, ids=lambda c: c.case_id)
def test_narrative_catalogue_metadata(case) -> None:
    assert case.provenance in {Provenance.NARRATIVE, Provenance.ENGINE_POLICY}
    assert case.case_id in FIXTURE_BUILDERS


def test_narrative_staking_income_and_basis() -> None:
    txs = FIXTURE_BUILDERS["narrative-staking-income"]()
    income = calculate_uk_income(txs)
    assert income.staking_income == 200.0
    report = calculate_uk_cgt(txs)
    # Dispose 0.1 of the reward lot / pooled mix: S.104 avg = 2200/1.1 = 2000/unit
    # → cost 200, proceeds 250, gain 50
    assert report.rows[0].proceeds == 250.0
    assert report.rows[0].allowable_cost == 200.0
    assert report.rows[0].gain == 50.0


def test_narrative_airdrop_income_and_pool() -> None:
    txs = FIXTURE_BUILDERS["narrative-airdrop-income"]()
    income = calculate_uk_income(txs)
    assert income.airdrop_income == 500.0
    report = calculate_uk_cgt(txs)
    assert report.rows[0].gain == 150.0  # 400 − 250
    assert compute_uk_open_pools(txs)["AIR"] == (50.0, 250.0)


def test_narrative_fee_disposal_against_pool() -> None:
    txs = FIXTURE_BUILDERS["narrative-fee-disposal"]()
    report = calculate_uk_cgt(txs)
    assert len(report.rows) == 1
    assert report.rows[0].disposal_id == "fee-gas"
    assert report.rows[0].proceeds == 20.0
    assert report.rows[0].allowable_cost == 20.0  # 2000 * 0.01/1
    assert report.rows[0].gain == 0.0
    assert compute_uk_open_pools(txs)["ETH"] == (0.99, 1980.0)


def test_policy_hard_fork_fmv_documents_divergence() -> None:
    raw = FIXTURE_BUILDERS["policy-hard-fork-fmv"]()
    txs, n = normalize_hard_forks(raw, basis_policy="fmv")
    assert n == 1
    fork = next(t for t in txs if t.event_subtype == EVENT_HARD_FORK)
    assert fork.asset == "ETHW"
    assert fork.transaction_type == TransactionType.BUY
    assert fork.parent_asset == "ETH"
    assert fork.amount == 2.0
    # FMV may be 0 if ETHW unpriced — acquisition row still present (policy).
    case = next(c for c in NARRATIVE_CASES if c.case_id == "policy-hard-fork-fmv")
    assert "cost split" in case.description.lower() or "CRYPTO22300" in case.hmrc_ref
