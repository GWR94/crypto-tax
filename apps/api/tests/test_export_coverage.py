"""Export coverage inference tests."""

from datetime import datetime, timezone

from app.export_coverage import (
    infer_export_coverage,
    infer_filter_range_from_filename,
    infer_filter_range_from_preamble,
)
from app.schemas import Transaction, TransactionType


def _tx(when: str) -> Transaction:
    return Transaction(
        id="t1",
        timestamp=datetime.fromisoformat(when).replace(tzinfo=timezone.utc),
        asset="USDC",
        transaction_type=TransactionType.TRANSFER,
        amount=1.0,
        fiat_value_at_trigger=1.0,
        source="variational",
    )


def test_filename_filter_range():
    pair = infer_filter_range_from_filename(
        "binance-spot-2024-01-01-to-2024-12-31.csv"
    )
    assert pair is not None
    assert pair[0].date().isoformat() == "2024-01-01"
    assert pair[1].date().isoformat() == "2024-12-31"


def test_preamble_filter_range():
    content = b"Export period: 2024-03-01 to 2024-06-30\nid,created_at,qty\n"
    pair = infer_filter_range_from_preamble(content)
    assert pair is not None
    assert pair[0].month == 3
    assert pair[1].month == 6


def test_infer_export_coverage_prefers_filter_over_row_span():
    content = b"id,created_at\n"
    txs = [_tx("2024-04-15T12:00:00"), _tx("2024-05-20T12:00:00")]
    coverage = infer_export_coverage(
        "export_2024-01-01_2024-12-31.csv",
        content,
        txs,
    )
    assert coverage is not None
    assert coverage.coverage_from == "export_filter"
    assert coverage.coverage_start.date().isoformat() == "2024-01-01"
    assert coverage.coverage_end.date().isoformat() == "2024-12-31"
    assert coverage.data_start.date().isoformat() == "2024-04-15"
