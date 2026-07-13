"""Tests for P&L drill-down breakdown."""

from datetime import datetime, timezone

from app.pnl_breakdown import build_pnl_breakdown
from app.schemas import AccountingMethod, Transaction, TransactionType


def _tx(
    tx_id: str,
    when: str,
    asset: str,
    ttype: TransactionType,
    amount: float,
    value: float,
    *,
    source: str = "kraken",
) -> Transaction:
    return Transaction(
        id=tx_id,
        timestamp=datetime.fromisoformat(when).replace(tzinfo=timezone.utc),
        asset=asset,
        transaction_type=ttype,
        amount=amount,
        fiat_value_at_trigger=value,
        fiat_currency="GBP",
        source=source,
    )


def test_build_pnl_breakdown_groups_realized_disposals():
    txs = [
        _tx("buy", "2024-01-01T00:00:00", "BTC", TransactionType.BUY, 1.0, 20000.0),
        _tx("sell", "2024-06-01T00:00:00", "BTC", TransactionType.SELL, 1.0, 30000.0),
    ]
    breakdown = build_pnl_breakdown(
        txs,
        AccountingMethod.FIFO,
        {"BTC": 50000.0},
        tax_jurisdiction="US",
    )
    detail = breakdown.by_asset["BTC"]
    assert len(detail.disposals) == 1
    assert detail.disposals[0].transaction_id == "sell"
    assert detail.disposals[0].gain_loss == 10000.0
