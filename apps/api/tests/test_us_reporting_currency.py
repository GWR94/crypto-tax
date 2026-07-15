"""US Form 8949 must report in USD, not GBP."""

from __future__ import annotations

from datetime import datetime, timezone

from app.config import reporting_currency_for
from app.schemas import AccountingMethod, Transaction, TransactionType
from app.tax_engine import calculate_realized_gains


def _usd_tx(
    tx_id: str,
    when: str,
    asset: str,
    ttype: TransactionType,
    amount: float,
    value: float,
    *,
    fee: float = 0.0,
) -> Transaction:
    return Transaction(
        id=tx_id,
        timestamp=datetime.fromisoformat(when).replace(tzinfo=timezone.utc),
        asset=asset,
        transaction_type=ttype,
        amount=amount,
        fiat_value_at_trigger=value,
        fee_fiat=fee,
        fiat_currency="USD",
        source="coinbase",
    )


def test_reporting_currency_for_jurisdiction():
    assert reporting_currency_for("UK") == "GBP"
    assert reporting_currency_for("US") == "USD"
    assert reporting_currency_for("us") == "USD"


def test_us_form_8949_figures_are_usd():
    txs = [
        _usd_tx("b", "2023-01-01T00:00:00", "BTC", TransactionType.BUY, 1, 10000),
        _usd_tx("s", "2024-06-01T00:00:00", "BTC", TransactionType.SELL, 1, 15000),
    ]
    report = calculate_realized_gains(
        txs,
        AccountingMethod.FIFO,
        tax_year=2024,
        tax_jurisdiction="US",
    )
    assert report.reporting_currency == "USD"
    assert report.tax_jurisdiction == "US"
    assert len(report.rows) == 1
    row = report.rows[0]
    assert row.proceeds == 15000.0
    assert row.cost_basis == 10000.0
    assert row.gain_loss == 5000.0
    assert report.total_gain == 5000.0
    assert row.term == "LONG"


def test_uk_realized_summary_stays_gbp():
    txs = [
        _usd_tx("b", "2024-01-01T00:00:00", "ETH", TransactionType.BUY, 1, 2000),
        _usd_tx("s", "2024-06-01T00:00:00", "ETH", TransactionType.SELL, 1, 2500),
    ]
    # Force GBP-denominated legs so UK path needs no FX for the assertion.
    gbp_txs = [
        t.model_copy(update={"fiat_currency": "GBP", "fiat_value_at_trigger": v})
        for t, v in zip(txs, (2000.0, 2500.0))
    ]
    report = calculate_realized_gains(
        gbp_txs,
        AccountingMethod.SECTION_104,
        tax_jurisdiction="UK",
    )
    assert report.reporting_currency == "GBP"
    assert report.total_gain == 500.0
