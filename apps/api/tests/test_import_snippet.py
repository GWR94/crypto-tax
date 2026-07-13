"""Connected import snippet tests."""

from datetime import datetime, timezone

from app.import_file_storage import read_import_file, save_import_file
from app.import_registry import registry
from app.ingestion import csv_text_snippet, ledger_snippet_from_transactions
from app.schemas import Transaction, TransactionType


def test_stored_csv_snippet_for_connected_source():
    registry.clear()
    import_id = registry.register("csv", label="sample.csv")
    save_import_file(
        import_id,
        b"id,asset,amount\n1,BTC,0.5\n2,ETH,1.0\n",
    )
    content = read_import_file(import_id)
    assert content is not None
    snippet = csv_text_snippet(content)
    assert snippet is not None
    assert snippet["columns"] == ["id", "asset", "amount"]
    assert snippet["rows"][0] == ["1", "BTC", "0.5"]


def test_ledger_fallback_snippet_for_connected_source():
    txs = [
        Transaction(
            id="1",
            timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
            asset="BTC",
            transaction_type=TransactionType.BUY,
            amount=0.5,
            fiat_value_at_trigger=100.0,
            fee_fiat=0.0,
            import_id="abc123",
            source="kraken",
        )
    ]
    snippet = ledger_snippet_from_transactions(txs)
    assert snippet["columns"][0] == "timestamp"
    assert snippet["rows"][0][1] == "BTC"
