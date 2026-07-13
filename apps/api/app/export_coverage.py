"""Infer the date window an export is meant to cover (filter range vs row data)."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import List, Literal, Optional, Tuple

from .schemas import Transaction

CoverageFrom = Literal["export_filter", "transactions"]

_DATE_TOKEN = r"(\d{4}[-/]\d{2}[-/]\d{2}|\d{8})"
_FILENAME_RANGE_RE = re.compile(
    rf"{_DATE_TOKEN}.*?{_DATE_TOKEN}",
    re.IGNORECASE,
)
_FILTER_LINE_RE = re.compile(
    rf"(?:from|start|after|since)\s*[:=]?\s*{_DATE_TOKEN}.*?"
    rf"(?:to|until|through|end|before)\s*[:=]?\s*{_DATE_TOKEN}",
    re.IGNORECASE,
)
_PERIOD_LINE_RE = re.compile(
    rf"(?:period|range|export|filter|report)\s*[:=]?\s*{_DATE_TOKEN}.*?{_DATE_TOKEN}",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ExportCoverage:
    """Export window and the span of rows actually present."""

    coverage_start: datetime
    coverage_end: datetime
    data_start: datetime
    data_end: datetime
    coverage_from: CoverageFrom

    @property
    def uses_export_filter(self) -> bool:
        return self.coverage_from == "export_filter"


def _parse_date_token(token: str) -> Optional[datetime]:
    text = token.strip().replace("/", "-")
    if len(text) == 8 and text.isdigit():
        text = f"{text[0:4]}-{text[4:6]}-{text[6:8]}"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _pair_from_match(match: re.Match[str]) -> Optional[Tuple[datetime, datetime]]:
    start = _parse_date_token(match.group(1))
    end = _parse_date_token(match.group(2))
    if not start or not end:
        return None
    if end < start:
        start, end = end, start
    return start, end


def infer_filter_range_from_filename(filename: str) -> Optional[Tuple[datetime, datetime]]:
    if not filename:
        return None
    match = _FILENAME_RANGE_RE.search(filename)
    if not match:
        return None
    return _pair_from_match(match)


def infer_filter_range_from_preamble(content: bytes) -> Optional[Tuple[datetime, datetime]]:
    try:
        text = content.decode("utf-8-sig", errors="replace")
    except (UnicodeDecodeError, ValueError):
        return None

    for line in text.splitlines()[:12]:
        stripped = line.strip()
        if not stripped or stripped.startswith('"') and "," in stripped:
            # Likely reached the CSV header row.
            break
        for pattern in (_FILTER_LINE_RE, _PERIOD_LINE_RE):
            match = pattern.search(stripped)
            if match:
                pair = _pair_from_match(match)
                if pair:
                    return pair
        generic = _FILENAME_RANGE_RE.search(stripped)
        if generic:
            pair = _pair_from_match(generic)
            if pair:
                return pair
    return None


def transaction_date_range(
    transactions: List[Transaction],
) -> Tuple[Optional[datetime], Optional[datetime]]:
    if not transactions:
        return None, None
    timestamps = [tx.timestamp for tx in transactions]
    return min(timestamps), max(timestamps)


def infer_export_coverage(
    filename: str,
    content: bytes,
    transactions: List[Transaction],
) -> Optional[ExportCoverage]:
    """Best-effort export window: CSV filter metadata, else first/last row."""
    data_start, data_end = transaction_date_range(transactions)
    if not data_start or not data_end:
        return None

    filter_range = infer_filter_range_from_filename(filename)
    if not filter_range:
        filter_range = infer_filter_range_from_preamble(content)

    if filter_range:
        coverage_start, coverage_end = filter_range
        coverage_from: CoverageFrom = "export_filter"
    else:
        coverage_start, coverage_end = data_start, data_end
        coverage_from = "transactions"

    return ExportCoverage(
        coverage_start=coverage_start,
        coverage_end=coverage_end,
        data_start=data_start,
        data_end=data_end,
        coverage_from=coverage_from,
    )


def coverage_as_dict(coverage: ExportCoverage) -> dict[str, object]:
    return {
        "coverage_start": coverage.coverage_start.isoformat(),
        "coverage_end": coverage.coverage_end.isoformat(),
        "data_start": coverage.data_start.isoformat(),
        "data_end": coverage.data_end.isoformat(),
        "coverage_from": coverage.coverage_from,
    }


def as_coverage_date(when: datetime) -> date:
    if when.tzinfo is not None:
        return when.astimezone(timezone.utc).date()
    return when.date()
