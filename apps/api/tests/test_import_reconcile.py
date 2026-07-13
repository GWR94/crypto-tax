"""Import registry reconciliation from orphaned ledger rows."""

from datetime import datetime, timezone

from app.import_reconcile import infer_orphan_import_metadata
from app.import_registry import registry
from app.main import _build_import_sources
from app.schemas import Transaction, TransactionType


def _tx(
    *,
    id: str,
    import_id: str,
    source: str,
    on_chain_tx_id: str | None = None,
) -> Transaction:
    return Transaction(
        id=id,
        timestamp=datetime(2024, 6, 1, tzinfo=timezone.utc),
        asset="SOL",
        transaction_type=TransactionType.TRANSFER,
        amount=1.0,
        fiat_value_at_trigger=100.0,
        fee_fiat=0.0,
        import_id=import_id,
        source=source,
        on_chain_tx_id=on_chain_tx_id,
    )


def test_infer_orphan_wallet_from_multi_chain_batch():
    txs = [
        _tx(id="1", import_id="abc", source="ethereum", on_chain_tx_id="0x1"),
        _tx(id="2", import_id="abc", source="hyperliquid"),
        _tx(id="3", import_id="abc", source="arbitrum", on_chain_tx_id="0x2"),
    ]
    kind, label, chain, address = infer_orphan_import_metadata(txs)
    assert kind == "wallet"
    assert chain == "ethereum"
    assert "Ethereum" in label
    assert address is None


def test_infer_orphan_csv_from_exchange_batch():
    txs = [
        _tx(id="1", import_id="abc", source="binance"),
        _tx(id="2", import_id="abc", source="binance"),
    ]
    kind, label, chain, address = infer_orphan_import_metadata(txs)
    assert kind == "csv"
    assert "Binance" in label
    assert chain is None
    assert address is None


def test_reconcile_orphans_restores_import_sources():
    registry.clear()
    import_id = "orphan1234567890abcdef1234567890ab"
    txs = [
        _tx(id="1", import_id=import_id, source="kraken"),
        _tx(id="2", import_id=import_id, source="kraken"),
    ]
    recovered = registry.reconcile_orphans(txs)
    assert recovered == 1
    sources = registry.all()
    assert len(sources) == 1
    assert sources[0].id == import_id
    assert sources[0].kind == "csv"
    assert "Kraken" in sources[0].label


def test_build_import_sources_includes_orphan_batches():
    registry.clear()
    import_id = "orphan1234567890abcdef1234567890ac"
    txs = [
        _tx(id="1", import_id=import_id, source="kraken"),
        _tx(id="2", import_id=import_id, source="kraken"),
    ]
    views = _build_import_sources(txs)
    assert any(view.id == import_id and view.transaction_count == 2 for view in views)
