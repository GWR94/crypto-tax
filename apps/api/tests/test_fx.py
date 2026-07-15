"""FX calendar-day alignment with UK / US tax conventions."""

from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import patch

from app.fx import FxService, fx_calendar_day


def test_gbp_fx_day_uses_london_calendar():
    # 5 Apr 2024 23:30 UTC is already 6 Apr in London (BST).
    when = datetime(2024, 4, 5, 23, 30, tzinfo=timezone.utc)
    assert fx_calendar_day(when, reporting_currency="GBP") == date(2024, 4, 6)


def test_usd_fx_day_uses_utc_calendar():
    when = datetime(2024, 4, 5, 23, 30, tzinfo=timezone.utc)
    assert fx_calendar_day(when, reporting_currency="USD") == date(2024, 4, 5)


def test_to_reporting_gbp_looks_up_london_rate_day():
    service = FxService()
    when = datetime(2024, 4, 5, 23, 30, tzinfo=timezone.utc)
    with patch.object(service, "get_rate", return_value=0.8) as get_rate:
        converted = service.to_reporting(
            100.0,
            "USD",
            when,
            reporting_currency="GBP",
        )
    assert converted == 80.0
    get_rate.assert_called_once_with("USD", "GBP", date(2024, 4, 6))


def test_to_reporting_usd_looks_up_utc_rate_day():
    service = FxService()
    when = datetime(2024, 4, 5, 23, 30, tzinfo=timezone.utc)
    with patch.object(service, "get_rate", return_value=1.25) as get_rate:
        converted = service.to_reporting(
            80.0,
            "GBP",
            when,
            reporting_currency="USD",
        )
    assert converted == 100.0
    get_rate.assert_called_once_with("GBP", "USD", date(2024, 4, 5))
