"""Tests for orphaned inflow detection and manual cost-basis overrides."""

from __future__ import annotations

from datetime import datetime, timezone

from app.cost_basis_overrides import build_override_from_request, prepare_tax_ledger
from app.data_health import find_orphaned_inflows
from app.hmrc_cgt_engine import calculate_uk_cgt
from app.schemas import Transaction, TransactionType
from app.tax_engine import calculate_realized_gains, AccountingMethod


def _inflow(
    tx_id: str,
    when: str,
    asset: str,
    amount: float,
    *,
    fiat: float = 0.0,
    source: str = "mexc",
) -> Transaction:
    return Transaction(
        id=tx_id,
        timestamp=datetime.fromisoformat(when).replace(tzinfo=timezone.utc),
        asset=asset,
        transaction_type=TransactionType.TRANSFER,
        amount=amount,
        fiat_value_at_trigger=fiat,
        fiat_currency="GBP",
        source=source,
        transfer_direction="IN",
    )


def _sell(tx_id: str, when: str, asset: str, amount: float, proceeds: float) -> Transaction:
    return Transaction(
        id=tx_id,
        timestamp=datetime.fromisoformat(when).replace(tzinfo=timezone.utc),
        asset=asset,
        transaction_type=TransactionType.SELL,
        amount=amount,
        fiat_value_at_trigger=proceeds,
        fiat_currency="GBP",
        source="mexc",
    )


def test_flags_unpaired_deposit_without_fiat():
    txs = [_inflow("dep", "2024-06-01T12:00:00", "ETH", 2.0)]
    flags = find_orphaned_inflows(txs)
    assert len(flags) == 1
    assert flags[0].transaction_id == "dep"
    assert "Missing" not in flags[0].message  # message describes purge scenario


def test_valued_first_deposit_is_not_orphan():
    """Valued unpaired IN already establishes basis — do not flag as orphan."""
    txs = [_inflow("dep", "2024-06-01T12:00:00", "ETH", 1.0, fiat=2500.0)]
    flags = find_orphaned_inflows(txs)
    assert flags == []

def test_manual_override_establishes_uk_cost_on_sell():
    txs = [
        _inflow("dep", "2024-06-01T12:00:00", "ETH", 2.0),
        _sell("sell", "2024-09-01T12:00:00", "ETH", 2.0, 5000.0),
    ]
    override = build_override_from_request(
        anchor=txs[0],
        acquisition_date=datetime(2023, 1, 15, tzinfo=timezone.utc),
        total_fiat_spent=3000.0,
    )
    tax_txs = prepare_tax_ledger(txs, [override])
    report = calculate_uk_cgt(tax_txs, tax_year_label="2024/25")
    assert report.disposal_count == 1
    row = report.rows[0]
    assert not row.missing_cost_basis
    assert row.allowable_cost == 3000.0
    assert row.gain == 2000.0


def test_manual_override_clears_orphan_flag():
    txs = [_inflow("dep", "2024-06-01T12:00:00", "ETH", 2.0)]
    override = build_override_from_request(
        anchor=txs[0],
        acquisition_date=datetime(2023, 1, 15, tzinfo=timezone.utc),
        unit_cost=1500.0,
    )
    flags = find_orphaned_inflows(txs, [override])
    assert flags == []


def test_manual_override_applies_to_us_fifo():
    txs = [
        _inflow("dep", "2024-06-01T12:00:00", "ETH", 1.0),
        _sell("sell", "2024-09-01T12:00:00", "ETH", 1.0, 4000.0).model_copy(
            update={"fiat_currency": "USD"}
        ),
    ]
    override = build_override_from_request(
        anchor=txs[0],
        acquisition_date=datetime(2023, 6, 1, tzinfo=timezone.utc),
        total_fiat_spent=2000.0,
    )
    override = override.model_copy(update={"reporting_currency": "USD"})
    tax_txs = prepare_tax_ledger(txs, [override])
    report = calculate_realized_gains(
        tax_txs, AccountingMethod.FIFO, tax_year=2024, tax_jurisdiction="US"
    )
    assert report.reporting_currency == "USD"
    assert report.total_gain == 2000.0
