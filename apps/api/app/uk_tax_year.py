"""UK tax-year helpers.

The UK Capital Gains Tax year runs from 6 April to 5 April of the following
calendar year. A tax year is labelled like ``2024/25`` (6 Apr 2024 to 5 Apr
2025).
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Iterable, List, Tuple
from zoneinfo import ZoneInfo

from .config import UK_CGT_ANNUAL_EXEMPT_AMOUNT, UK_CGT_DEFAULT_ALLOWANCE

UK_TIMEZONE = ZoneInfo("Europe/London")

# UK tax year starts on 6 April.
_TAX_YEAR_START = (4, 6)


def _as_date(value: datetime | date) -> date:
    return value.date() if isinstance(value, datetime) else value


def uk_calendar_date(value: datetime | date) -> date:
    """Calendar date in the UK (Europe/London) for HMRC same-day / 30-day rules."""
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    when = value if isinstance(value, datetime) else datetime.combine(value, datetime.min.time())
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(UK_TIMEZONE).date()


def uk_tax_year_start_year(value: datetime | date) -> int:
    """Return the calendar year in which the date's UK tax year begins."""
    d = _as_date(value)
    return d.year if (d.month, d.day) >= _TAX_YEAR_START else d.year - 1


def label_from_start_year(start_year: int) -> str:
    """``2024 -> "2024/25"``."""
    return f"{start_year}/{(start_year + 1) % 100:02d}"


def uk_tax_year_label(value: datetime | date) -> str:
    """Return the UK tax-year label (e.g. ``2024/25``) containing the date."""
    return label_from_start_year(uk_tax_year_start_year(value))


def start_year_from_label(label: str) -> int:
    """Parse the starting calendar year from a ``2024/25`` style label."""
    head = label.strip().split("/", 1)[0]
    return int(head)


def uk_tax_year_range(label: str) -> Tuple[date, date]:
    """Return the inclusive ``(start, end)`` dates for a tax-year label."""
    start_year = start_year_from_label(label)
    return date(start_year, 4, 6), date(start_year + 1, 4, 5)


def is_in_tax_year(value: datetime | date, label: str) -> bool:
    """True when the date falls within the given UK tax-year label."""
    start, end = uk_tax_year_range(label)
    d = _as_date(value)
    return start <= d <= end


def available_tax_year_labels(timestamps: Iterable[datetime | date]) -> List[str]:
    """Distinct UK tax-year labels for a set of timestamps, newest first."""
    labels = {uk_tax_year_label(ts) for ts in timestamps}
    return sorted(labels, key=start_year_from_label, reverse=True)


def annual_exempt_amount(label: str) -> float:
    """Annual CGT exempt amount (in GBP) for a tax-year label."""
    return float(UK_CGT_ANNUAL_EXEMPT_AMOUNT.get(label, UK_CGT_DEFAULT_ALLOWANCE))
