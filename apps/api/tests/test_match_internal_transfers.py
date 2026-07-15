"""Tests for mis-typed SELL+BUY internal-transfer matching."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.schemas import Transaction, TransactionType
from app.tax_engine import match_internal_transfers


def _tx(
    tx_id: str,
    when: str,
    ttype: TransactionType,
    amount: float,
    *,
    source: str | None,
    counter_asset: str | None = None,
    venue_order_type: str | None = None,
) -> Transaction:
    return Transaction(
        id=tx_id,
        timestamp=datetime.fromisoformat(when).replace(tzinfo=timezone.utc),
        asset="BTC",
        transaction_type=ttype,
        amount=amount,
        fiat_value_at_trigger=1000.0,
        fiat_currency="USD",
        source=source,
        counter_asset=counter_asset,
        venue_order_type=venue_order_type,
    )


def test_match_internal_transfer_reclassifies_plain_cross_source_legs():
    txs = [
        _tx("sell", "2024-05-01T12:00:00", TransactionType.SELL, 1.0, source="ledger"),
        _tx("buy", "2024-05-01T12:02:00", TransactionType.BUY, 1.0, source="kraken"),
    ]
    updated, ids = match_internal_transfers(txs)
    assert set(ids) == {"sell", "buy"}
    by_id = {t.id: t for t in updated}
    assert by_id["sell"].transaction_type == TransactionType.TRANSFER
    assert by_id["sell"].transfer_direction == "OUT"
    assert by_id["buy"].transaction_type == TransactionType.TRANSFER
    assert by_id["buy"].transfer_direction == "IN"
    assert by_id["sell"].transfer_pair_id == by_id["buy"].transfer_pair_id


def test_does_not_match_market_trades_with_counter_asset():
    """Same size SELL+BUY across venues within the window, but clearly trades."""
    txs = [
        _tx(
            "sell",
            "2024-05-01T12:00:00",
            TransactionType.SELL,
            1.0,
            source="binance",
            counter_asset="USDT",
            venue_order_type="MARKET",
        ),
        _tx(
            "buy",
            "2024-05-01T12:02:00",
            TransactionType.BUY,
            1.0,
            source="coinbase",
            counter_asset="USD",
            venue_order_type="LIMIT",
        ),
    ]
    _updated, ids = match_internal_transfers(txs)
    assert ids == []


def test_does_not_match_without_sources():
    txs = [
        _tx("sell", "2024-05-01T12:00:00", TransactionType.SELL, 1.0, source=None),
        _tx("buy", "2024-05-01T12:02:00", TransactionType.BUY, 1.0, source="kraken"),
    ]
    _updated, ids = match_internal_transfers(txs)
    assert ids == []


def test_does_not_match_outside_five_minute_window():
    txs = [
        _tx("sell", "2024-05-01T12:00:00", TransactionType.SELL, 1.0, source="ledger"),
        _tx("buy", "2024-05-01T12:06:00", TransactionType.BUY, 1.0, source="kraken"),
    ]
    _updated, ids = match_internal_transfers(txs)
    assert ids == []


def test_does_not_match_same_source():
    txs = [
        _tx("sell", "2024-05-01T12:00:00", TransactionType.SELL, 1.0, source="kraken"),
        _tx("buy", "2024-05-01T12:01:00", TransactionType.BUY, 1.0, source="kraken"),
    ]
    _updated, ids = match_internal_transfers(txs)
    assert ids == []
