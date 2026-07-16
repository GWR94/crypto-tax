"""Jurisdiction-aware tax-loss harvest savings estimates."""

from datetime import datetime, timezone

from app.config import (
    UK_CGT_BASIC_RATE,
    UK_CGT_HIGHER_RATE,
    US_LONG_TERM_CG_RATE,
    US_ORDINARY_INCOME_RATE,
)
from app.schemas import AccountingMethod, Position, Transaction, TransactionType
from app.tax_engine import build_tax_harvest_matrix


def _loser(asset: str = "SOL", loss: float = 1000.0) -> Position:
    invested = 5000.0
    value = invested - loss
    return Position(
        asset=asset,
        quantity=10.0,
        average_cost_basis=invested / 10.0,
        current_price=value / 10.0,
        total_invested=invested,
        current_value=value,
        unrealized_pnl=-loss,
        unrealized_pnl_pct=round((-loss / invested) * 100, 2),
        realized_income=0.0,
    )


def _tx(
    tx_id: str,
    when: str,
    asset: str,
    ttype: TransactionType,
    amount: float,
    value: float,
) -> Transaction:
    return Transaction(
        id=tx_id,
        timestamp=datetime.fromisoformat(when).replace(tzinfo=timezone.utc),
        asset=asset,
        transaction_type=ttype,
        amount=amount,
        fiat_value_at_trigger=value,
        fiat_currency="USD",
        source="test",
    )


def test_uk_uses_basic_then_higher_across_band():
    # £30k unused band: first loser takes all basic, second is all higher.
    rows = build_tax_harvest_matrix(
        [_loser("AAA", 20000.0), _loser("BBB", 10000.0)],
        tax_jurisdiction="UK",
        uk_unused_basic_band=30000.0,
    )
    assert len(rows) == 2
    assert rows[0].asset == "AAA"
    assert rows[0].basic_rate_loss == 20000.0
    assert rows[0].higher_rate_loss == 0.0
    assert rows[0].potential_tax_savings == round(20000.0 * UK_CGT_BASIC_RATE, 2)

    assert rows[1].asset == "BBB"
    assert rows[1].basic_rate_loss == 10000.0
    assert rows[1].higher_rate_loss == 0.0
    assert rows[1].potential_tax_savings == round(10000.0 * UK_CGT_BASIC_RATE, 2)


def test_uk_spills_into_higher_rate_within_one_position():
    rows = build_tax_harvest_matrix(
        [_loser("ETH", 50000.0)],
        tax_jurisdiction="UK",
        uk_unused_basic_band=10000.0,
    )
    assert len(rows) == 1
    row = rows[0]
    assert row.basic_rate_loss == 10000.0
    assert row.higher_rate_loss == 40000.0
    expected = 10000.0 * UK_CGT_BASIC_RATE + 40000.0 * UK_CGT_HIGHER_RATE
    assert row.potential_tax_savings == round(expected, 2)


def test_uk_zero_unused_band_is_all_higher():
    rows = build_tax_harvest_matrix(
        [_loser(loss=1000.0)],
        tax_jurisdiction="UK",
        uk_unused_basic_band=0.0,
    )
    assert rows[0].basic_rate_loss == 0.0
    assert rows[0].higher_rate_loss == 1000.0
    assert rows[0].potential_tax_savings == round(1000.0 * UK_CGT_HIGHER_RATE, 2)


def test_us_short_and_long_term_lots():
    # Bought 2 years ago (LT) and recently (ST); both underwater at $50.
    txs = [
        _tx("lt", "2023-01-01T00:00:00", "SOL", TransactionType.BUY, 10.0, 2000.0),
        _tx("st", "2026-06-01T00:00:00", "SOL", TransactionType.BUY, 10.0, 1500.0),
    ]
    pos = Position(
        asset="SOL",
        quantity=20.0,
        average_cost_basis=175.0,
        current_price=50.0,
        total_invested=3500.0,
        current_value=1000.0,
        unrealized_pnl=-2500.0,
        unrealized_pnl_pct=-71.43,
        realized_income=0.0,
    )
    as_of = datetime(2026, 7, 15, tzinfo=timezone.utc)
    rows = build_tax_harvest_matrix(
        [pos],
        tax_jurisdiction="US",
        transactions=txs,
        method=AccountingMethod.FIFO,
        prices_usd={"SOL": 50.0},
        as_of=as_of,
        us_ordinary_rate=US_ORDINARY_INCOME_RATE,
        us_ltcg_rate=US_LONG_TERM_CG_RATE,
    )
    assert len(rows) == 1
    row = rows[0]
    # LT lot: cost 2000, value 500 → -1500; ST lot: cost 1500, value 500 → -1000
    assert row.long_term_loss == 1500.0
    assert row.short_term_loss == 1000.0
    expected = 1000.0 * US_ORDINARY_INCOME_RATE + 1500.0 * US_LONG_TERM_CG_RATE
    assert row.potential_tax_savings == round(expected, 2)


def test_us_fallback_without_lots_uses_ltcg():
    rows = build_tax_harvest_matrix(
        [_loser(loss=1000.0)],
        tax_jurisdiction="US",
    )
    assert rows[0].short_term_loss == 0.0
    assert rows[0].long_term_loss == 1000.0
    assert rows[0].potential_tax_savings == round(1000.0 * US_LONG_TERM_CG_RATE, 2)


def test_winners_excluded():
    winner = Position(
        asset="BTC",
        quantity=1.0,
        average_cost_basis=20000.0,
        current_price=30000.0,
        total_invested=20000.0,
        current_value=30000.0,
        unrealized_pnl=10000.0,
        unrealized_pnl_pct=50.0,
        realized_income=0.0,
    )
    assert build_tax_harvest_matrix([winner], tax_jurisdiction="UK") == []
