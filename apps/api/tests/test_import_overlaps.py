"""Import overlap detection tests."""

from datetime import datetime, timezone

from app.import_overlaps import (
    count_ledger_duplicates,
    find_import_overlaps,
    format_fully_duplicate_rejection,
    ledger_dedup_keys,
    partition_novel_transactions,
)
from app.schemas import ImportSourceView, Transaction, TransactionType


def _source(
    source_id: str,
    label: str,
    *,
    parser_label: str,
    start: str,
    end: str,
    transaction_count: int = 10,
) -> ImportSourceView:
    coverage_start = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    coverage_end = datetime.fromisoformat(end).replace(tzinfo=timezone.utc)
    return ImportSourceView(
        id=source_id,
        kind="csv",
        label=label,
        parser_label=parser_label,
        coverage_start=coverage_start,
        coverage_end=coverage_end,
        date_start=coverage_start,
        date_end=coverage_end,
        transaction_count=transaction_count,
    )


def _tx(
    tx_id: str,
    *,
    import_id: str,
    timestamp: str = "2024-03-15T12:00:00+00:00",
) -> Transaction:
    return Transaction(
        id=tx_id,
        timestamp=datetime.fromisoformat(timestamp),
        asset="BTC",
        transaction_type=TransactionType.BUY,
        amount=1.0,
        fiat_value_at_trigger=100.0,
        fee_fiat=0.0,
        import_id=import_id,
        source="cryptocom",
    )


def test_same_platform_coverage_overlap():
    sources = [
        _source(
            "1",
            "cdc-jan-jun.csv",
            parser_label="Crypto.com",
            start="2024-01-01",
            end="2024-06-30",
        ),
        _source(
            "2",
            "cdc-mar-dec.csv",
            parser_label="Crypto.com",
            start="2024-03-01",
            end="2024-12-31",
        ),
    ]
    overlaps = find_import_overlaps(sources, [])
    assert len(overlaps) == 1
    assert overlaps[0]["same_platform"] is True
    assert overlaps[0]["overlap_days"] >= 120
    assert "cdc-jan-jun.csv" in overlaps[0]["message"]


def test_no_overlap_when_ranges_are_separate():
    sources = [
        _source(
            "1",
            "cdc-h1.csv",
            parser_label="Crypto.com",
            start="2024-01-01",
            end="2024-06-30",
        ),
        _source(
            "2",
            "kraken-h2.csv",
            parser_label="Kraken",
            start="2024-07-01",
            end="2024-12-31",
        ),
    ]
    assert find_import_overlaps(sources, []) == []


def test_shared_transactions_counted():
    sources = [
        _source(
            "1",
            "cdc-a.csv",
            parser_label="Crypto.com",
            start="2024-01-01",
            end="2024-06-30",
        ),
        _source(
            "2",
            "cdc-b.csv",
            parser_label="Crypto.com",
            start="2024-03-01",
            end="2024-12-31",
        ),
    ]
    txs = [
        _tx("shared", import_id="1"),
        _tx("shared", import_id="2"),
        _tx("only-a", import_id="1", timestamp="2024-02-01T12:00:00+00:00"),
    ]
    overlaps = find_import_overlaps(sources, txs)
    assert overlaps[0]["shared_transactions"] == 1


def test_redundant_zero_transaction_import():
    sources = [
        _source(
            "1",
            "cdc-original.csv",
            parser_label="Crypto.com",
            start="2024-01-01",
            end="2024-06-30",
            transaction_count=100,
        ),
        _source(
            "2",
            "cdc-reimport.csv",
            parser_label="Crypto.com",
            start="2024-01-01",
            end="2024-06-30",
            transaction_count=0,
        ),
    ]
    overlaps = find_import_overlaps(sources, [])
    kinds = {row["kind"] for row in overlaps}
    assert "redundant_import" in kinds


def test_redundant_imports_grouped_by_filename():
    sources = [
        _source(
            "1",
            "repeat.csv",
            parser_label="Crypto.com",
            start="2024-01-01",
            end="2024-06-30",
            transaction_count=0,
        ),
        _source(
            "2",
            "repeat.csv",
            parser_label="Crypto.com",
            start="2024-01-01",
            end="2024-06-30",
            transaction_count=0,
        ),
        _source(
            "3",
            "repeat.csv",
            parser_label="Crypto.com",
            start="2024-01-01",
            end="2024-06-30",
            transaction_count=0,
        ),
    ]
    overlaps = find_import_overlaps(sources, [])
    redundant = [row for row in overlaps if row["kind"] == "redundant_import"]
    assert len(redundant) == 1
    assert redundant[0]["duplicate_count"] == 3
    assert len(redundant[0]["import_ids"]) == 3


def test_different_wallets_same_chain_not_flagged():
    sources = [
        ImportSourceView(
            id="1",
            kind="wallet",
            label="Solana 7jEvut3C",
            parser_label="Solana",
            chain="solana",
            address="7jEvut3C1111111111111111111111111111111111",
            coverage_start=datetime.fromisoformat("2024-01-01").replace(tzinfo=timezone.utc),
            coverage_end=datetime.fromisoformat("2025-12-31").replace(tzinfo=timezone.utc),
            date_start=datetime.fromisoformat("2024-01-01").replace(tzinfo=timezone.utc),
            date_end=datetime.fromisoformat("2025-12-31").replace(tzinfo=timezone.utc),
            transaction_count=10,
        ),
        ImportSourceView(
            id="2",
            kind="wallet",
            label="Solana 4K4PdbMG",
            parser_label="Solana",
            chain="solana",
            address="4K4PdbMG2222222222222222222222222222222222",
            coverage_start=datetime.fromisoformat("2024-02-01").replace(tzinfo=timezone.utc),
            coverage_end=datetime.fromisoformat("2025-09-30").replace(tzinfo=timezone.utc),
            date_start=datetime.fromisoformat("2024-02-01").replace(tzinfo=timezone.utc),
            date_end=datetime.fromisoformat("2025-09-30").replace(tzinfo=timezone.utc),
            transaction_count=12,
        ),
    ]
    overlaps = find_import_overlaps(sources, [])
    assert [row for row in overlaps if row["kind"] == "coverage"] == []


def test_wallet_and_unrelated_csv_not_flagged():
    wallet_addr = "4K4PdbMG2222222222222222222222222222222222"
    sources = [
        ImportSourceView(
            id="1",
            kind="wallet",
            label="Solana 7jEvut3C",
            parser_label="Solana",
            chain="solana",
            address="7jEvut3C1111111111111111111111111111111111",
            coverage_start=datetime.fromisoformat("2024-01-01").replace(tzinfo=timezone.utc),
            coverage_end=datetime.fromisoformat("2025-12-31").replace(tzinfo=timezone.utc),
            date_start=datetime.fromisoformat("2024-01-01").replace(tzinfo=timezone.utc),
            date_end=datetime.fromisoformat("2025-12-31").replace(tzinfo=timezone.utc),
            transaction_count=10,
        ),
        ImportSourceView(
            id="2",
            kind="csv",
            label=f"export_transfer_{wallet_addr}_1755171233474.csv",
            parser_label="Solana",
            coverage_start=datetime.fromisoformat("2024-01-01").replace(tzinfo=timezone.utc),
            coverage_end=datetime.fromisoformat("2025-08-01").replace(tzinfo=timezone.utc),
            date_start=datetime.fromisoformat("2024-01-01").replace(tzinfo=timezone.utc),
            date_end=datetime.fromisoformat("2025-08-01").replace(tzinfo=timezone.utc),
            transaction_count=8,
        ),
    ]
    overlaps = find_import_overlaps(sources, [])
    assert [row for row in overlaps if row["kind"] == "coverage"] == []


def test_wallet_and_matching_csv_not_flagged_as_overlap():
    wallet_addr = "4K4PdbMG2222222222222222222222222222222222"
    sources = [
        ImportSourceView(
            id="1",
            kind="wallet",
            label="Solana 4K4PdbMG",
            parser_label="Solana",
            chain="solana",
            address=wallet_addr,
            coverage_start=datetime.fromisoformat("2024-01-01").replace(tzinfo=timezone.utc),
            coverage_end=datetime.fromisoformat("2025-12-31").replace(tzinfo=timezone.utc),
            date_start=datetime.fromisoformat("2024-01-01").replace(tzinfo=timezone.utc),
            date_end=datetime.fromisoformat("2025-12-31").replace(tzinfo=timezone.utc),
            transaction_count=10,
        ),
        ImportSourceView(
            id="2",
            kind="csv",
            label=f"export_transfer_{wallet_addr}_1755171233474.csv",
            parser_label="Solana",
            export_kind="transfers",
            coverage_start=datetime.fromisoformat("2024-01-01").replace(tzinfo=timezone.utc),
            coverage_end=datetime.fromisoformat("2025-08-01").replace(tzinfo=timezone.utc),
            date_start=datetime.fromisoformat("2024-01-01").replace(tzinfo=timezone.utc),
            date_end=datetime.fromisoformat("2025-08-01").replace(tzinfo=timezone.utc),
            transaction_count=8,
        ),
    ]
    overlaps = find_import_overlaps(sources, [])
    assert [row for row in overlaps if row["kind"] == "coverage"] == []


def test_variational_transfers_and_trades_not_flagged():
    sources = [
        _source(
            "1",
            "vari-transfers.csv",
            parser_label="Variational",
            start="2024-01-01",
            end="2024-12-31",
        ),
        _source(
            "2",
            "vari-trades.csv",
            parser_label="Variational",
            start="2024-01-01",
            end="2024-12-31",
        ),
    ]
    sources[0] = sources[0].model_copy(update={"export_kind": "transfers"})
    sources[1] = sources[1].model_copy(update={"export_kind": "trades"})
    overlaps = find_import_overlaps(sources, [])
    assert [row for row in overlaps if row["kind"] == "coverage"] == []


def test_same_variational_export_kind_still_flagged():
    sources = [
        _source(
            "1",
            "vari-transfers-jan.csv",
            parser_label="Variational",
            start="2024-01-01",
            end="2024-06-30",
        ),
        _source(
            "2",
            "vari-transfers-jul.csv",
            parser_label="Variational",
            start="2024-03-01",
            end="2024-12-31",
        ),
    ]
    sources[0] = sources[0].model_copy(update={"export_kind": "transfers"})
    sources[1] = sources[1].model_copy(update={"export_kind": "transfers"})
    overlaps = find_import_overlaps(sources, [])
    assert len([row for row in overlaps if row["kind"] == "coverage"]) == 1


def test_count_ledger_duplicates():
    existing = [_tx("a", import_id="existing-import")]
    batch = [
        _tx("a", import_id="new-import"),
        _tx(
            "b",
            import_id="new-import",
            timestamp="2024-04-01T12:00:00+00:00",
        ),
    ]
    count, matching = count_ledger_duplicates(batch, existing)
    assert count == 1
    assert matching == {"existing-import": 1}


def test_partition_novel_transactions():
    existing = [_tx("a", import_id="existing-import")]
    known_ids, known_fps = ledger_dedup_keys(existing)
    batch = [
        _tx("a", import_id="new-import"),
        _tx(
            "b",
            import_id="new-import",
            timestamp="2024-04-01T12:00:00+00:00",
        ),
    ]
    novel, duplicate_count = partition_novel_transactions(
        batch,
        known_ids=known_ids,
        known_fingerprints=known_fps,
    )
    assert duplicate_count == 1
    assert len(novel) == 1
    assert novel[0].id == "b"


def test_format_fully_duplicate_rejection():
    message = format_fully_duplicate_rejection(
        "binance.csv",
        10,
        ledger_labels=["binance.csv"],
    )
    assert "binance.csv" in message
    assert "10 transaction" in message
    assert "already in your ledger" in message

    same_upload = format_fully_duplicate_rejection(
        "binance.csv",
        10,
        ledger_labels=[],
        same_upload=True,
    )
    assert "another file in this upload" in same_upload
