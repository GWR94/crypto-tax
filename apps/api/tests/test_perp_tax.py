"""Perp detection, exclusion guards, and perp PnL schedule tests."""

from __future__ import annotations

from datetime import datetime, timezone

from app.hmrc_cgt_engine import calculate_uk_cgt
from app.perp_tax import available_perp_periods, build_perp_tax_summary
from app.schemas import (
    AccountingMethod,
    Transaction,
    TransactionType,
    is_perp_transaction,
)
from app.tax_engine import calculate_realized_gains


def _perp(
    tx_id: str,
    when: str,
    ttype: TransactionType,
    amount: float,
    value: float,
    *,
    source: str = "hyperliquid",
    instrument_kind: str | None = "perp",
    realized_pnl: float | None = None,
    fee: float = 0.0,
) -> Transaction:
    return Transaction(
        id=tx_id,
        timestamp=datetime.fromisoformat(when).replace(tzinfo=timezone.utc),
        asset="BTC",
        transaction_type=ttype,
        amount=amount,
        fiat_value_at_trigger=value,
        fee_fiat=fee,
        fiat_currency="USD",
        source=source,
        instrument_kind=instrument_kind,
        realized_pnl=realized_pnl,
    )


def test_explicit_spot_overrides_legacy_source():
    spot = _perp("a", "2024-05-01T00:00:00", TransactionType.BUY, 1.0, 100.0, instrument_kind="spot")
    perp = _perp("b", "2024-05-01T00:00:00", TransactionType.BUY, 1.0, 100.0, instrument_kind="perp")
    assert not is_perp_transaction(spot)
    assert is_perp_transaction(perp)


def test_mexc_spot_rows_are_not_perps():
    """MEXC email imports are spot deposits/withdrawals — not legacy perps."""
    from app.schemas import Transaction, TransactionType, is_perp_transaction
    from datetime import datetime, timezone

    deposit = Transaction(
        id="mexc-dep",
        timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
        asset="ETH",
        transaction_type=TransactionType.TRANSFER,
        amount=1.0,
        fiat_value_at_trigger=0.0,
        source="mexc",
        transfer_direction="IN",
    )
    assert not is_perp_transaction(deposit)


def test_legacy_source_without_kind_is_perp():
    legacy = _perp("a", "2024-05-01T00:00:00", TransactionType.SELL, 1.0, 100.0, instrument_kind=None)
    assert is_perp_transaction(legacy)


def test_perps_excluded_from_uk_cgt():
    txs = [
        _perp("b", "2024-05-01T00:00:00", TransactionType.BUY, 1.0, 20000.0),
        _perp("s", "2024-06-01T00:00:00", TransactionType.SELL, 1.0, 30000.0, realized_pnl=10000.0),
    ]
    report = calculate_uk_cgt(txs, tax_year_label="2024/25")
    assert report.disposal_count == 0
    assert report.net_gain == 0.0


def test_perps_excluded_from_us_form_8949():
    txs = [
        _perp("b", "2024-05-01T00:00:00", TransactionType.BUY, 1.0, 20000.0),
        _perp("s", "2024-08-01T00:00:00", TransactionType.SELL, 1.0, 30000.0),
    ]
    report = calculate_realized_gains(txs, AccountingMethod.FIFO, tax_year=2024)
    assert report.rows == []
    assert report.total_gain == 0.0


def test_perp_income_schedule_uk_tax_year():
    txs = [
        _perp("s1", "2024-05-01T00:00:00", TransactionType.SELL, 1.0, 30000.0, realized_pnl=200.0, fee=10.0, source="hyperliquid"),
        _perp("s2", "2024-09-01T00:00:00", TransactionType.SELL, 1.0, 30000.0, realized_pnl=-50.0, source="hyperliquid"),
    ]
    summary = build_perp_tax_summary(
        txs, jurisdiction="UK", treatment="income", period_label="2024/25"
    )
    assert summary.event_count == 2
    # PnL is USD; identity-ish FX is not guaranteed, so just assert structure.
    assert summary.treatment == "income"
    assert summary.gains > 0
    assert summary.losses < 0


def test_available_perp_periods():
    txs = [
        _perp("s1", "2024-05-01T00:00:00", TransactionType.SELL, 1.0, 30000.0, realized_pnl=10.0),
        _perp("s2", "2025-05-01T00:00:00", TransactionType.SELL, 1.0, 30000.0, realized_pnl=10.0),
    ]
    uk = available_perp_periods(txs, "UK")
    us = available_perp_periods(txs, "US")
    assert "2024/25" in uk and "2025/26" in uk
    assert "2024" in us and "2025" in us
