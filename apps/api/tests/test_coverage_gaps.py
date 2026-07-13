"""Coverage gap detection tests."""

from datetime import datetime, timezone

from app.coverage_gaps import find_coverage_gaps, find_ledger_coverage_gaps
from app.schemas import ImportSourceView


def _source(
    source_id: str,
    label: str,
    *,
    parser_label: str,
    start: str,
    end: str,
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
        transaction_count=1,
    )


def test_ledger_gap_between_different_sources():
    sources = [
        _source("1", "vari-jan.csv", parser_label="Variational", start="2024-01-01", end="2024-03-31"),
        _source("2", "sol-jul.csv", parser_label="Solana", start="2024-07-01", end="2024-12-31"),
    ]
    gaps = find_ledger_coverage_gaps(sources, min_gap_days=7)
    assert len(gaps) == 1
    assert gaps[0]["kind"] == "ledger"
    assert gaps[0]["gap_days"] >= 90
    assert "Variational" in gaps[0]["message"]
    assert "Solana" in gaps[0]["message"]


def test_no_gap_when_import_ranges_overlap():
    sources = [
        _source("1", "vari-h1.csv", parser_label="Variational", start="2024-01-01", end="2024-06-30"),
        _source("2", "kraken-h2.csv", parser_label="Kraken", start="2024-06-01", end="2024-12-31"),
    ]
    gaps = find_coverage_gaps(sources, min_gap_days=7)
    assert gaps == []


def test_gap_between_two_imports_same_platform():
    sources = [
        _source("1", "vari-jan.csv", parser_label="Variational", start="2024-01-01", end="2024-03-31"),
        _source("2", "vari-jul.csv", parser_label="Variational", start="2024-07-01", end="2024-12-31"),
    ]
    gaps = find_ledger_coverage_gaps(sources, min_gap_days=7)
    assert len(gaps) == 1
