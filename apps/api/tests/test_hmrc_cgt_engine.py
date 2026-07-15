"""HMRC share-matching CGT engine tests.

Scenarios use GBP-denominated transactions so FX is the identity and the
expected gains are exact.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.hmrc_cgt_engine import calculate_uk_cgt, calculate_uk_income, compute_uk_open_pools
from app.schemas import AccountingMethod, CgtMatchType, Transaction, TransactionType
from app.tax_engine import calculate_realized_pnl_by_asset


def _tx(
    tx_id: str,
    when: str,
    asset: str,
    ttype: TransactionType,
    amount: float,
    value: float,
    *,
    fee: float = 0.0,
    direction: str | None = None,
    source: str | None = None,
) -> Transaction:
    return Transaction(
        id=tx_id,
        timestamp=datetime.fromisoformat(when).replace(tzinfo=timezone.utc),
        asset=asset,
        transaction_type=ttype,
        amount=amount,
        fiat_value_at_trigger=value,
        fee_fiat=fee,
        fiat_currency="GBP",
        source=source,
        transfer_direction=direction,
    )


def test_same_day_rule():
    txs = [
        _tx("b", "2024-05-01T09:00:00", "BTC", TransactionType.BUY, 1, 10000),
        _tx("s", "2024-05-01T15:00:00", "BTC", TransactionType.SELL, 1, 12000),
    ]
    report = calculate_uk_cgt(txs, tax_year_label="2024/25")
    assert len(report.rows) == 1
    row = report.rows[0]
    assert row.match_type == CgtMatchType.SAME_DAY
    assert row.gain == 2000.0
    assert report.net_gain == 2000.0


def test_thirty_day_bed_and_breakfast():
    txs = [
        _tx("b1", "2024-01-01T00:00:00", "BTC", TransactionType.BUY, 1, 10000),
        _tx("s1", "2024-06-01T00:00:00", "BTC", TransactionType.SELL, 1, 12000),
        _tx("b2", "2024-06-10T00:00:00", "BTC", TransactionType.BUY, 1, 11000),
    ]
    report = calculate_uk_cgt(txs)
    bnb = [r for r in report.rows if r.match_type == CgtMatchType.THIRTY_DAY]
    assert len(bnb) == 1
    # Disposal matched to the £11,000 repurchase, not the £10,000 original pool.
    assert bnb[0].allowable_cost == 11000.0
    assert bnb[0].gain == 1000.0


def test_section_104_average_cost():
    txs = [
        _tx("b1", "2024-01-01T00:00:00", "ETH", TransactionType.BUY, 1, 10000),
        _tx("b2", "2024-02-01T00:00:00", "ETH", TransactionType.BUY, 1, 20000),
        _tx("s1", "2024-12-01T00:00:00", "ETH", TransactionType.SELL, 1, 18000),
    ]
    report = calculate_uk_cgt(txs)
    assert len(report.rows) == 1
    row = report.rows[0]
    assert row.match_type == CgtMatchType.SECTION_104
    # Average pool cost = (10000 + 20000) / 2 = 15000.
    assert row.allowable_cost == 15000.0
    assert row.gain == 3000.0


def test_section_104_open_pool_after_partial_disposal():
    txs = [
        _tx("b1", "2024-01-01T00:00:00", "ETH", TransactionType.BUY, 1, 10000),
        _tx("b2", "2024-02-01T00:00:00", "ETH", TransactionType.BUY, 1, 20000),
        _tx("s1", "2024-12-01T00:00:00", "ETH", TransactionType.SELL, 1, 18000),
    ]
    pools = compute_uk_open_pools(txs)
    qty, cost = pools["ETH"]
    assert qty == 1.0
    assert cost == 15000.0


def test_crypto_to_crypto_swap_is_disposal():
    txs = [
        _tx("b", "2024-01-01T00:00:00", "AAA", TransactionType.BUY, 1, 1000),
        _tx("swap-sell", "2024-03-01T00:00:00", "AAA", TransactionType.SELL, 1, 1500),
        _tx("swap-buy", "2024-03-01T00:00:00", "BBB", TransactionType.BUY, 5, 1500),
    ]
    report = calculate_uk_cgt(txs)
    aaa = [r for r in report.rows if r.asset == "AAA"]
    assert len(aaa) == 1
    assert aaa[0].gain == 500.0
    # BBB only acquired, never disposed -> no rows.
    assert not [r for r in report.rows if r.asset == "BBB"]


def test_internal_transfers_ignored():
    txs = [
        _tx("b", "2024-01-01T00:00:00", "BTC", TransactionType.BUY, 1, 1000, source="kraken"),
        _tx("out", "2024-02-01T00:00:00", "BTC", TransactionType.TRANSFER, 1, 0, direction="OUT", source="kraken"),
        _tx("in", "2024-02-01T00:05:00", "BTC", TransactionType.TRANSFER, 1, 0, direction="IN", source="ledger"),
        _tx("s", "2024-06-01T00:00:00", "BTC", TransactionType.SELL, 1, 1500),
    ]
    report = calculate_uk_cgt(txs)
    assert report.disposal_count == 1
    assert len(report.rows) == 1
    assert report.rows[0].gain == 500.0


def test_unpaired_transfer_out_reduces_uk_pool():
    """Wallet sends that are not paired internal moves must leave the pool."""
    txs = [
        _tx("b", "2024-01-01T00:00:00", "SOL", TransactionType.BUY, 10, 1000),
        _tx(
            "send",
            "2024-06-01T00:00:00",
            "SOL",
            TransactionType.TRANSFER,
            8,
            900,
            direction="OUT",
            source="solana",
        ),
    ]
    pools = compute_uk_open_pools(txs)
    qty, _cost = pools["SOL"]
    assert qty == 2.0


def test_cross_year_boundary_filtering():
    txs = [
        _tx("b", "2023-01-01T00:00:00", "BTC", TransactionType.BUY, 2, 2000),
        _tx("s1", "2024-04-05T00:00:00", "BTC", TransactionType.SELL, 1, 1500),
        _tx("s2", "2024-04-06T00:00:00", "BTC", TransactionType.SELL, 1, 1600),
    ]
    prev = calculate_uk_cgt(txs, tax_year_label="2023/24")
    curr = calculate_uk_cgt(txs, tax_year_label="2024/25")
    assert prev.disposal_count == 1
    assert prev.rows[0].disposal_id == "s1"
    assert curr.disposal_count == 1
    assert curr.rows[0].disposal_id == "s2"


def test_cross_year_boundary_filtering_bst_utc_skew():
    """UTC evening of 5 Apr is already 6 Apr in London — belongs in 2024/25."""
    txs = [
        _tx("b", "2023-01-01T00:00:00", "BTC", TransactionType.BUY, 1, 1000),
        _tx("s", "2024-04-05T23:30:00Z", "BTC", TransactionType.SELL, 1, 1500),
    ]
    prev = calculate_uk_cgt(txs, tax_year_label="2023/24")
    curr = calculate_uk_cgt(txs, tax_year_label="2024/25")
    assert prev.disposal_count == 0
    assert curr.disposal_count == 1
    assert curr.rows[0].disposal_id == "s"


def test_annual_exempt_amount_applied():
    txs = [
        _tx("b", "2024-04-10T00:00:00", "BTC", TransactionType.BUY, 1, 1000),
        _tx("s", "2024-05-10T00:00:00", "BTC", TransactionType.SELL, 1, 6000),
    ]
    report = calculate_uk_cgt(txs, tax_year_label="2024/25")
    assert report.net_gain == 5000.0
    assert report.annual_exempt_amount == 3000.0
    # 5000 gain - 3000 allowance = 2000 taxable.
    assert report.taxable_gain_after_allowance == 2000.0


def test_unmatched_disposal_flagged():
    txs = [
        _tx("s", "2024-05-10T00:00:00", "BTC", TransactionType.SELL, 1, 6000),
    ]
    report = calculate_uk_cgt(txs)
    assert len(report.rows) == 1
    row = report.rows[0]
    assert row.match_type == CgtMatchType.UNMATCHED
    assert row.missing_cost_basis is True
    assert row.allowable_cost == 0.0
    assert row.gain == 6000.0


def test_disposal_fee_reduces_proceeds():
    txs = [
        _tx("b", "2024-05-01T09:00:00", "BTC", TransactionType.BUY, 1, 10000),
        _tx("s", "2024-05-01T15:00:00", "BTC", TransactionType.SELL, 1, 12000, fee=200),
    ]
    report = calculate_uk_cgt(txs, tax_year_label="2024/25")
    row = report.rows[0]
    # Proceeds net of the £200 fee.
    assert row.proceeds == 11800.0
    assert row.gain == 1800.0


def test_native_gas_fee_is_cgt_disposal():
    """Paying gas in ETH is a disposal under HMRC — must leave the S.104 pool."""
    txs = [
        _tx("b", "2024-05-01T09:00:00", "ETH", TransactionType.BUY, 1.0, 2000),
        _tx("gas", "2024-05-02T12:00:00", "ETH", TransactionType.FEE, 0.01, 25),
    ]
    report = calculate_uk_cgt(txs, tax_year_label="2024/25")
    assert report.disposal_count == 1
    row = report.rows[0]
    assert row.disposal_id == "gas"
    assert row.quantity == 0.01
    assert row.proceeds == 25.0
    # Allowable cost = 1% of £2000 pool = £20; gain = 25 - 20 = 5.
    assert row.allowable_cost == 20.0
    assert row.gain == 5.0

    pools = compute_uk_open_pools(txs)
    qty, cost = pools["ETH"]
    assert abs(qty - 0.99) < 1e-9
    assert abs(cost - 1980.0) < 1e-6


def test_sell_fee_larger_than_proceeds_increases_loss():
    """Incidental costs above gross proceeds must not be clamped to £0 proceeds."""
    txs = [
        _tx("b", "2024-05-01T09:00:00", "BTC", TransactionType.BUY, 1, 10000),
        _tx("s", "2024-05-01T15:00:00", "BTC", TransactionType.SELL, 1, 100, fee=250),
    ]
    report = calculate_uk_cgt(txs, tax_year_label="2024/25")
    row = report.rows[0]
    assert row.proceeds == -150.0
    assert row.gain == -10150.0


def test_income_summary():
    txs = [
        _tx("a", "2024-05-01T00:00:00", "UNI", TransactionType.AIRDROP, 10, 250),
        _tx("st", "2024-06-01T00:00:00", "ETH", TransactionType.STAKING, 0.1, 180),
        _tx("b", "2024-05-01T00:00:00", "BTC", TransactionType.BUY, 1, 1000),
    ]
    income = calculate_uk_income(txs, tax_year_label="2024/25")
    assert income.airdrop_income == 250.0
    assert income.staking_income == 180.0
    assert income.total_income == 430.0
    assert len(income.rows) == 2


def test_realized_pnl_by_asset_uk():
    txs = [
        _tx("b1", "2024-01-01T00:00:00", "ETH", TransactionType.BUY, 2, 20000),
        _tx("s1", "2024-06-01T00:00:00", "ETH", TransactionType.SELL, 1, 12000),
        _tx("b2", "2024-01-01T00:00:00", "BTC", TransactionType.BUY, 1, 10000),
        _tx("s2", "2024-06-01T00:00:00", "BTC", TransactionType.SELL, 1, 8000),
    ]
    rows = calculate_realized_pnl_by_asset(
        txs, AccountingMethod.SECTION_104, tax_jurisdiction="UK"
    )
    by_asset = {r.asset: r for r in rows}
    assert by_asset["ETH"].realized_pnl == 2000.0
    assert by_asset["BTC"].realized_pnl == -2000.0
    assert by_asset["ETH"].disposal_count == 1


def test_same_day_rule_uk_timezone_bst_boundary():
    """Same UK calendar evening across UTC/BST date boundaries."""
    txs = [
        _tx("b-pool", "2024-01-01T00:00:00", "BTC", TransactionType.BUY, 1, 10000),
        _tx("s", "2024-06-01T23:30:00Z", "BTC", TransactionType.SELL, 1, 12000),
        _tx("b-same", "2024-06-02T00:30:00+01:00", "BTC", TransactionType.BUY, 1, 11000),
    ]
    report = calculate_uk_cgt(txs, tax_year_label="2024/25")
    assert len(report.rows) == 1
    row = report.rows[0]
    assert row.match_type == CgtMatchType.SAME_DAY
    assert row.gain == 1000.0
