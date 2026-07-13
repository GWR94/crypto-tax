"""WOO X CSV import tests."""

from pathlib import Path

from app.ingestion import parse_csv

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_woox_order_history_imports():
    content = (FIXTURES / "woox_sample.csv").read_bytes()
    txs = parse_csv(content, filename="woox_orders.csv")

    assert len(txs) == 6
    assert all(t.source == "woox" for t in txs)

    liquidate = next(t for t in txs if t.id == "woox-58916596505")
    assert liquidate.transaction_type.value == "SELL"
    assert liquidate.instrument_kind == "perp"
    assert liquidate.realized_pnl == -1.6488
    assert liquidate.asset == "1000FLOKI"
    assert liquidate.amount == 720.0
    assert liquidate.fiat_value_at_trigger == round(720 * 0.09756, 2)
    assert liquidate.counter_asset == "USDT"

    btc_buy = next(t for t in txs if t.id == "woox-57956972469")
    assert btc_buy.transaction_type.value == "BUY"
    assert btc_buy.asset == "BTC"
    assert btc_buy.amount == 0.03359
    assert btc_buy.fiat_value_at_trigger == round(0.03359 * 106806.58916344, 2)
    assert btc_buy.fee_fiat == 1.79381667
