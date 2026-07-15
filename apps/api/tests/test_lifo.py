"""US LIFO lot selection."""

from datetime import datetime, timezone

from app.schemas import AccountingMethod, Transaction, TransactionType
from app.tax_engine import calculate_realized_gains


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
        fee_fiat=0.0,
        fiat_currency="USD",
        source="test",
    )


def test_lifo_takes_newest_lot_not_highest_cost():
    """Expensive old lot + cheap new lot: LIFO ≠ HIFO ≠ FIFO on the sell."""
    txs = [
        _tx("old-expensive", "2024-01-01T00:00:00", "SOL", TransactionType.BUY, 1.0, 300.0),
        _tx("new-cheap", "2024-06-01T00:00:00", "SOL", TransactionType.BUY, 1.0, 100.0),
        _tx("sell", "2024-09-01T00:00:00", "SOL", TransactionType.SELL, 1.0, 200.0),
    ]

    fifo = calculate_realized_gains(
        txs, AccountingMethod.FIFO, tax_year=2024, tax_jurisdiction="US"
    )
    lifo = calculate_realized_gains(
        txs, AccountingMethod.LIFO, tax_year=2024, tax_jurisdiction="US"
    )
    hifo = calculate_realized_gains(
        txs, AccountingMethod.HIFO, tax_year=2024, tax_jurisdiction="US"
    )

    fifo_row = next(r for r in fifo.rows if r.asset == "SOL")
    lifo_row = next(r for r in lifo.rows if r.asset == "SOL")
    hifo_row = next(r for r in hifo.rows if r.asset == "SOL")

    # FIFO / HIFO consume the Jan lot (cost 300) → loss of 100.
    assert fifo_row.lot_source_id == "old-expensive"
    assert hifo_row.lot_source_id == "old-expensive"
    assert fifo_row.gain_loss == -100.0
    assert hifo_row.gain_loss == -100.0

    # LIFO consumes the June lot (cost 100) → gain of 100.
    assert lifo_row.lot_source_id == "new-cheap"
    assert lifo_row.cost_basis == 100.0
    assert lifo_row.gain_loss == 100.0
    assert lifo.total_gain != fifo.total_gain


def test_demo_bnb_lifo_matches_newest_lot():
    """On the demo BNB stack, LIFO takes the later (high) lot — same gain as HIFO."""
    from app.sample_data import default_transactions
    from app.schemas import spot_transactions

    spot = spot_transactions(default_transactions())
    lifo = calculate_realized_gains(
        spot, AccountingMethod.LIFO, tax_year=2024, tax_jurisdiction="US"
    )
    hifo = calculate_realized_gains(
        spot, AccountingMethod.HIFO, tax_year=2024, tax_jurisdiction="US"
    )
    lifo_bnb = next(r for r in lifo.rows if r.asset == "BNB")
    hifo_bnb = next(r for r in hifo.rows if r.asset == "BNB")
    assert lifo_bnb.lot_source_id == "demo-bnb-fifo-buy-high"
    assert lifo_bnb.gain_loss == hifo_bnb.gain_loss
