"""Foreign-exchange conversion with historical rates and local caching.

Rates are fetched from the Frankfurter API (ECB data, no API key). Converted
amounts feed the tax engine in the jurisdiction reporting currency (GBP for
UK, USD for US Form 8949).

FX rate days align with tax calendar conventions: Europe/London for GBP
reporting (HMRC), UTC for USD reporting (Form 8949).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Dict, Optional

from .config import REPORTING_CURRENCY, STABLECOIN_ASSETS

FIAT_ISO = frozenset(
    {"USD", "EUR", "GBP", "CAD", "AUD", "JPY", "CHF", "NZD", "SGD", "HKD", "NOK", "SEK"}
)

# Fallback spot rates (1 unit of currency -> GBP) when offline.
_FALLBACK_TO_GBP: Dict[str, float] = {
    "GBP": 1.0,
    "USD": 0.79,
    "EUR": 0.86,
    "JPY": 0.0053,
    "CAD": 0.58,
    "AUD": 0.52,
    "CHF": 0.90,
}

_CACHE_DIR = Path(
    os.environ.get(
        "CRYPTO_TAX_STATE_DIR",
        str(Path(__file__).resolve().parents[3] / "data"),
    )
)
_CACHE_FILE = _CACHE_DIR / "fx_cache.json"


def fx_calendar_day(
    when: datetime | date,
    *,
    reporting_currency: Optional[str] = None,
) -> date:
    """Calendar day used to look up the historical FX rate.

    UK (GBP) reporting uses Europe/London dates so conversion matches HMRC
    same-day / tax-year boundaries. US (USD) reporting uses the UTC date.
    """
    if isinstance(when, date) and not isinstance(when, datetime):
        return when
    to_ccy = (reporting_currency or REPORTING_CURRENCY).upper()
    if to_ccy == "GBP":
        from .uk_tax_year import uk_calendar_date

        return uk_calendar_date(when)
    if when.tzinfo is None:
        return when.date()
    return when.astimezone(timezone.utc).date()


def us_calendar_year(value: datetime | date) -> int:
    """US Form 8949 / calendar-year bucket for a timestamp (UTC date)."""
    return fx_calendar_day(value, reporting_currency="USD").year


class FxService:
    """Thread-safe FX converter with on-disk rate cache."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._cache: Dict[str, float] = {}
        self._load_cache()

    def _load_cache(self) -> None:
        if _CACHE_FILE.exists():
            try:
                self._cache = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._cache = {}

    def _persist_cache(self) -> None:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(json.dumps(self._cache, indent=2), encoding="utf-8")

    def resolve_currency(self, currency: Optional[str], source: Optional[str] = None) -> str:
        """Map a transaction currency code to an ISO fiat code for FX."""
        if not currency:
            return "GBP" if source == "kraken" else "USD"
        code = currency.upper()
        if code in STABLECOIN_ASSETS:
            return "USD"
        if code in FIAT_ISO:
            return code
        # Crypto-denominated quote (e.g. SOL leg) — treat notional as USD.
        return "USD"

    def _cache_key(self, day: date, from_ccy: str, to_ccy: str) -> str:
        return f"{day.isoformat()}:{from_ccy}:{to_ccy}"

    def _fetch_rate(self, day: date, from_ccy: str, to_ccy: str) -> float:
        url = (
            f"https://api.frankfurter.app/{day.isoformat()}"
            f"?from={from_ccy}&to={to_ccy}"
        )
        with urllib.request.urlopen(url, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return float(payload["rates"][to_ccy])

    def _fallback_rate(self, from_ccy: str, to_ccy: str) -> float:
        """Convert via GBP pivot using static fallback table."""
        from_gbp = _FALLBACK_TO_GBP.get(from_ccy, _FALLBACK_TO_GBP["USD"])
        to_gbp = _FALLBACK_TO_GBP.get(to_ccy, _FALLBACK_TO_GBP["USD"])
        if to_gbp == 0:
            return 1.0
        return from_gbp / to_gbp

    def get_rate(self, from_ccy: str, to_ccy: str, when: date) -> float:
        """Return multiply factor: amount_in_from * rate = amount_in_to."""
        from_ccy = from_ccy.upper()
        to_ccy = to_ccy.upper()
        if from_ccy == to_ccy:
            return 1.0

        key = self._cache_key(when, from_ccy, to_ccy)
        with self._lock:
            if key in self._cache:
                return self._cache[key]

        try:
            rate = self._fetch_rate(when, from_ccy, to_ccy)
        except (urllib.error.URLError, KeyError, ValueError, TimeoutError):
            rate = self._fallback_rate(from_ccy, to_ccy)

        with self._lock:
            self._cache[key] = rate
            self._persist_cache()
        return rate

    def convert(
        self,
        amount: float,
        from_ccy: str,
        to_ccy: str,
        when: datetime | date,
        *,
        reporting_currency: Optional[str] = None,
    ) -> float:
        if amount == 0:
            return 0.0
        # When converting into a reporting currency, use that currency's tax day.
        day_ccy = (reporting_currency or to_ccy).upper()
        day = fx_calendar_day(when, reporting_currency=day_ccy)
        rate = self.get_rate(from_ccy, to_ccy, day)
        return amount * rate

    def to_reporting(
        self,
        amount: float,
        currency: Optional[str],
        when: datetime,
        source: Optional[str] = None,
        *,
        reporting_currency: Optional[str] = None,
    ) -> float:
        """Convert a transaction amount into the tax reporting currency."""
        from_ccy = self.resolve_currency(currency, source)
        to_ccy = (reporting_currency or REPORTING_CURRENCY).upper()
        return self.convert(
            amount,
            from_ccy,
            to_ccy,
            when,
            reporting_currency=to_ccy,
        )

    def reporting_to_display(
        self,
        amount: float,
        display_ccy: str,
        *,
        reporting_currency: Optional[str] = None,
    ) -> float:
        """Convert a tax-reporting amount to the dashboard display currency."""
        from_ccy = (reporting_currency or REPORTING_CURRENCY).upper()
        display_ccy = display_ccy.upper()
        if display_ccy == from_ccy:
            return amount
        today = datetime.now(timezone.utc)
        return self.convert(
            amount,
            from_ccy,
            display_ccy,
            today,
            reporting_currency=display_ccy,
        )


# Module singleton used by API + tax engine.
fx = FxService()
