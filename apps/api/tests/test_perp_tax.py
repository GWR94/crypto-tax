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


def test_perp_exclude_returns_empty_schedule():
    txs = [
        _perp(
            "s1",
            "2024-05-01T00:00:00",
            TransactionType.SELL,
            1.0,
            30000.0,
            realized_pnl=200.0,
        ),
    ]
    summary = build_perp_tax_summary(
        txs, jurisdiction="US", treatment="exclude", period_label="2024"
    )
    assert summary.event_count == 0
    assert summary.net_pnl == 0.0


def test_capital_gains_treatment_folds_into_uk_cgt():
    from app.perp_tax import merge_perp_into_uk_cgt

    spot = [
        Transaction(
            id="spot-buy",
            timestamp=datetime.fromisoformat("2024-04-10T00:00:00").replace(
                tzinfo=timezone.utc
            ),
            asset="ETH",
            transaction_type=TransactionType.BUY,
            amount=1.0,
            fiat_value_at_trigger=1000.0,
            fiat_currency="GBP",
            source="kraken",
        ),
        Transaction(
            id="spot-sell",
            timestamp=datetime.fromisoformat("2024-05-10T00:00:00").replace(
                tzinfo=timezone.utc
            ),
            asset="ETH",
            transaction_type=TransactionType.SELL,
            amount=1.0,
            fiat_value_at_trigger=1500.0,
            fiat_currency="GBP",
            source="kraken",
        ),
    ]
    perps = [
        _perp(
            "perp-win",
            "2024-06-01T00:00:00",
            TransactionType.SELL,
            1.0,
            0.0,
            realized_pnl=200.0,
            fee=0.0,
        ),
    ]
    # Force GBP reporting path: treat realized_pnl currency as GBP via fiat_currency.
    perps[0] = perps[0].model_copy(update={"fiat_currency": "GBP"})

    base = calculate_uk_cgt(spot, tax_year_label="2024/25")
    assert base.net_gain == 500.0
    assert all(r.match_type.value != "perp" for r in base.rows)

    income = merge_perp_into_uk_cgt(base, spot + perps, "income")
    assert income.net_gain == 500.0

    folded = merge_perp_into_uk_cgt(base, spot + perps, "capital_gains")
    assert folded.net_gain == 700.0
    perp_rows = [r for r in folded.rows if r.match_type.value == "perp"]
    assert len(perp_rows) == 1
    assert perp_rows[0].asset.startswith("PERP:")
    assert perp_rows[0].gain == 200.0
    # Spot BTC/ETH pools must stay untouched by raw perp fills.
    assert calculate_uk_cgt(spot + perps, tax_year_label="2024/25").net_gain == 500.0


def test_capital_gains_treatment_folds_into_us_form_8949():
    from app.perp_tax import merge_perp_into_us_realized

    spot = [
        Transaction(
            id="spot-buy",
            timestamp=datetime.fromisoformat("2024-01-01T00:00:00").replace(
                tzinfo=timezone.utc
            ),
            asset="ETH",
            transaction_type=TransactionType.BUY,
            amount=1.0,
            fiat_value_at_trigger=1000.0,
            fiat_currency="USD",
            source="coinbase",
        ),
        Transaction(
            id="spot-sell",
            timestamp=datetime.fromisoformat("2024-06-01T00:00:00").replace(
                tzinfo=timezone.utc
            ),
            asset="ETH",
            transaction_type=TransactionType.SELL,
            amount=1.0,
            fiat_value_at_trigger=1500.0,
            fiat_currency="USD",
            source="coinbase",
        ),
    ]
    perps = [
        _perp(
            "perp-loss",
            "2024-07-01T00:00:00",
            TransactionType.SELL,
            2.0,
            0.0,
            realized_pnl=-80.0,
            fee=20.0,
        ),
    ]

    base = calculate_realized_gains(
        spot, AccountingMethod.FIFO, tax_year=2024, tax_jurisdiction="US"
    )
    assert base.total_gain == 500.0
    assert len(base.rows) == 1

    folded = merge_perp_into_us_realized(base, spot + perps, "capital_gains")
    # Net perp = -80 - 20 = -100
    assert folded.total_gain == 400.0
    assert len(folded.rows) == 2
    perp_row = next(r for r in folded.rows if r.lot_source_id == "PERP")
    assert perp_row.asset.startswith("PERP:")
    assert perp_row.term == "SHORT"
    assert perp_row.gain_loss == -100.0
    assert perp_row.proceeds == 0.0
    assert perp_row.cost_basis == 100.0


def test_available_perp_periods():
    txs = [
        _perp("s1", "2024-05-01T00:00:00", TransactionType.SELL, 1.0, 30000.0, realized_pnl=10.0),
        _perp("s2", "2025-05-01T00:00:00", TransactionType.SELL, 1.0, 30000.0, realized_pnl=10.0),
    ]
    uk = available_perp_periods(txs, "UK")
    us = available_perp_periods(txs, "US")
    assert "2024/25" in uk and "2025/26" in uk
    assert "2024" in us and "2025" in us
