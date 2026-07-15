"""UK tax-year timezone and US holding-period anniversary tests."""

from __future__ import annotations

from datetime import datetime, timezone

from app.schemas import AccountingMethod, Transaction, TransactionType
from app.tax_engine import _holding_term, calculate_realized_gains
from app.uk_tax_year import is_in_tax_year, uk_tax_year_label


def test_is_in_tax_year_uses_london_calendar_at_bst_boundary():
    """2024-04-05 23:30 UTC is already 6 Apr in London → start of 2024/25."""
    when = datetime(2024, 4, 5, 23, 30, tzinfo=timezone.utc)
    assert uk_tax_year_label(when) == "2024/25"
    assert is_in_tax_year(when, "2024/25")
    assert not is_in_tax_year(when, "2023/24")


def test_is_in_tax_year_end_of_prior_year_still_gmt():
    """Just before the London tax-year flip remains 2023/24."""
    when = datetime(2024, 4, 5, 22, 59, tzinfo=timezone.utc)  # 23:59 BST? Apr 5 is BST
    # 2024-04-05 22:59 UTC = 2024-04-05 23:59 BST → still 5 Apr → 2023/24
    assert uk_tax_year_label(when) == "2023/24"
    assert is_in_tax_year(when, "2023/24")
    assert not is_in_tax_year(when, "2024/25")


def test_holding_term_exactly_one_year_is_short():
    acquired = datetime(2023, 1, 1, tzinfo=timezone.utc)
    disposed = datetime(2024, 1, 1, tzinfo=timezone.utc)
    term, days = _holding_term(acquired, disposed)
    assert term == "SHORT"
    assert days == 365


def test_holding_term_day_after_anniversary_is_long():
    acquired = datetime(2023, 1, 1, tzinfo=timezone.utc)
    disposed = datetime(2024, 1, 2, tzinfo=timezone.utc)
    term, days = _holding_term(acquired, disposed)
    assert term == "LONG"
    assert days == 366


def test_holding_term_leap_year_anniversary_stays_short():
    """Jan 1 2024 → Jan 1 2025 is 366 calendar days but only one year → SHORT."""
    acquired = datetime(2024, 1, 1, tzinfo=timezone.utc)
    disposed = datetime(2025, 1, 1, tzinfo=timezone.utc)
    term, days = _holding_term(acquired, disposed)
    assert days == 366
    assert term == "SHORT"


def test_holding_term_leap_year_day_after_anniversary_is_long():
    acquired = datetime(2024, 1, 1, tzinfo=timezone.utc)
    disposed = datetime(2025, 1, 2, tzinfo=timezone.utc)
    term, _days = _holding_term(acquired, disposed)
    assert term == "LONG"


def test_form_8949_uses_anniversary_holding_rule():
    txs = [
        Transaction(
            id="b",
            timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
            asset="BTC",
            transaction_type=TransactionType.BUY,
            amount=1,
            fiat_value_at_trigger=10000,
            fiat_currency="USD",
            source="coinbase",
        ),
        Transaction(
            id="s",
            timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc),
            asset="BTC",
            transaction_type=TransactionType.SELL,
            amount=1,
            fiat_value_at_trigger=12000,
            fiat_currency="USD",
            source="coinbase",
        ),
    ]
    report = calculate_realized_gains(
        txs, AccountingMethod.FIFO, tax_year=2025, tax_jurisdiction="US"
    )
    assert len(report.rows) == 1
    assert report.rows[0].term == "SHORT"
    assert report.short_term_gain == 2000.0
    assert report.long_term_gain == 0.0


def test_us_tax_year_filter_uses_utc_calendar_not_offset_local_year():
    """A disposal that is still 2024-12-31 in UTC must not enter the 2025 report.

    ``datetime.year`` on a +05:00 stamp would say 2025; UTC date is still 2024.
    """
    from datetime import timedelta

    plus_five = timezone(timedelta(hours=5))
    txs = [
        Transaction(
            id="b",
            timestamp=datetime(2024, 6, 1, tzinfo=timezone.utc),
            asset="ETH",
            transaction_type=TransactionType.BUY,
            amount=1,
            fiat_value_at_trigger=1000,
            fiat_currency="USD",
            source="coinbase",
        ),
        Transaction(
            id="s",
            # 2025-01-01 02:00 +05:00 == 2024-12-31 21:00 UTC
            timestamp=datetime(2025, 1, 1, 2, 0, tzinfo=plus_five),
            asset="ETH",
            transaction_type=TransactionType.SELL,
            amount=1,
            fiat_value_at_trigger=1500,
            fiat_currency="USD",
            source="coinbase",
        ),
    ]
    y2024 = calculate_realized_gains(
        txs, AccountingMethod.FIFO, tax_year=2024, tax_jurisdiction="US"
    )
    y2025 = calculate_realized_gains(
        txs, AccountingMethod.FIFO, tax_year=2025, tax_jurisdiction="US"
    )
    assert len(y2024.rows) == 1
    assert y2024.total_gain == 500.0
    assert y2025.rows == []
    assert y2025.total_gain == 0.0
