"""Historical price resolver tests."""

from datetime import date, datetime, timezone
from unittest.mock import patch

from app.historical_prices import _fetch_coingecko_history_usd, historical_usd_price


@patch("app.historical_prices._cache.known", return_value=False)
@patch("app.historical_prices._cache.get", return_value=None)
@patch("app.historical_prices._cache.set")
@patch(
    "app.historical_prices.coingecko_request",
    return_value={"market_data": {"current_price": {"usd": 16605.2}}},
)
def test_fetch_coingecko_history_parses_usd(
    _mock_request, _mock_set, _mock_get, _mock_known
):
    price = _fetch_coingecko_history_usd("bitcoin", date(2022, 11, 13))
    assert price == 16605.2


@patch("app.historical_prices._fetch_coingecko_history_usd", return_value=16605.2)
def test_historical_usd_price_uses_history_for_past_dates(mock_hist):
    when = datetime(2022, 11, 13, 19, 45, tzinfo=timezone.utc)
    assert historical_usd_price("BTC", when) == 16605.2
    mock_hist.assert_called_once()
