"""Idempotent import / deduplication tests."""

from __future__ import annotations

from datetime import datetime, timezone

from app.schemas import Transaction, TransactionType
from app.transaction_dedup import dedupe_transactions, transaction_fingerprint


def _sol_transfer(
    tx_id: str,
    sig: str,
    *,
    amount: float = 2.9995,
    value: float = 80.01,
    direction: str = "IN",
    import_id: str = "csv-a",
) -> Transaction:
    return Transaction(
        id=tx_id,
        timestamp=datetime.fromisoformat("2023-07-15T22:47:38").replace(
            tzinfo=timezone.utc
        ),
        asset="SOL",
        transaction_type=TransactionType.TRANSFER,
        amount=amount,
        fiat_value_at_trigger=value,
        fiat_currency="USD" if value > 70 else "GBP",
        source="solana",
        import_id=import_id,
        transfer_direction=direction,
        trade_group_id=sig,
        on_chain_tx_id=sig,
    )


def _tx(
    tx_id: str,
    when: str = "2024-05-01T09:00:00",
    *,
    asset: str = "BTC",
    ttype: TransactionType = TransactionType.SELL,
    amount: float = 1.0,
    value: float = 10000.0,
    source: str = "kraken",
    trade_group_id: str | None = None,
) -> Transaction:
    return Transaction(
        id=tx_id,
        timestamp=datetime.fromisoformat(when).replace(tzinfo=timezone.utc),
        asset=asset,
        transaction_type=ttype,
        amount=amount,
        fiat_value_at_trigger=value,
        source=source,
        trade_group_id=trade_group_id,
    )


def test_dedupe_collapses_repeated_ids():
    rows = [_tx("a"), _tx("a"), _tx("a")]
    deduped, stats = dedupe_transactions(rows)
    assert len(deduped) == 1
    assert stats["skipped_id"] == 2
    assert stats["skipped_fingerprint"] == 0


def test_dedupe_collapses_same_content_distinct_ids():
    # Same economic event re-imported with a drifted id.
    rows = [_tx("a", trade_group_id="ref1"), _tx("b", trade_group_id="ref1")]
    deduped, stats = dedupe_transactions(rows)
    assert len(deduped) == 1
    assert stats["skipped_fingerprint"] == 1


def test_dedupe_keeps_distinct_trades_on_different_days():
    # Identical size/value but different timestamps must NOT collapse.
    rows = [
        _tx("a", "2024-05-01T09:00:00"),
        _tx("b", "2024-05-02T09:00:00"),
    ]
    deduped, _ = dedupe_transactions(rows)
    assert len(deduped) == 2


def test_dedupe_keeps_distinct_amounts():
    rows = [_tx("a", amount=1.0), _tx("b", amount=2.0)]
    deduped, _ = dedupe_transactions(rows)
    assert len(deduped) == 2


def test_reimport_is_idempotent():
    batch = [_tx("a"), _tx("b", asset="ETH", amount=2.0, value=4000.0)]
    once, _ = dedupe_transactions(batch)
    twice, stats = dedupe_transactions(once + batch)
    assert len(twice) == len(once) == 2
    assert stats["skipped_id"] == 2


def test_fingerprint_includes_timestamp():
    a = _tx("a", "2024-05-01T09:00:00")
    b = _tx("b", "2024-05-02T09:00:00")
    assert transaction_fingerprint(a) != transaction_fingerprint(b)


def test_dedupe_collapses_same_on_chain_leg_distinct_fmv():
    """Overlapping Solana CSVs restate the same signature with different FMV."""
    sig = (
        "2wfenxs9q6GXxTFKbzc6sSSRPoq4fQey59NSy12vPJch7qM3y658XMusMgmvD3bVgeh"
        "5ev4B3deEC8Mt8GCNzaJw"
    )
    rows = [
        _sol_transfer(
            "sol-transfer-in-SOL",
            sig,
            value=80.01,
            import_id="csv-v1",
        ),
        _sol_transfer(
            "sol-transfer-in-wsol-mint",
            sig,
            value=61.2,
            import_id="csv-old",
        ),
    ]
    deduped, stats = dedupe_transactions(rows)
    assert len(deduped) == 1
    assert stats["skipped_on_chain"] == 1
    assert deduped[0].fiat_value_at_trigger == 80.01
