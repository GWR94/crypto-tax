"""Historical USD prices for wallet backfill (CoinGecko + disk cache)."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Dict, Optional

from .coingecko_client import coingecko_request
from .coingecko_registry import resolve_coingecko_id
from .price_resolver import COINGECKO_IDS, _fetch_coingecko, _normalize_asset

_CACHE_DIR = Path(
    os.environ.get(
        "CRYPTO_TAX_STATE_DIR",
        str(Path(__file__).resolve().parents[3] / "data"),
    )
)
_CACHE_FILE = _CACHE_DIR / "historical_prices_cache.json"
_REQUEST_DELAY_SEC = 0.35
_last_request_at = 0.0
_request_lock = Lock()


class HistoricalPriceCache:
    def __init__(self) -> None:
        self._lock = Lock()
        self._cache: Dict[str, float] = {}
        self._misses: set[str] = set()
        self._load()

    def _load(self) -> None:
        if not _CACHE_FILE.exists():
            return
        try:
            raw = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and "prices" in raw:
                self._cache = {
                    str(k): float(v)
                    for k, v in (raw.get("prices") or {}).items()
                    if v is not None and float(v) > 0
                }
                self._misses = {str(k) for k in raw.get("misses") or []}
                return
            self._cache = {str(k): float(v) for k, v in raw.items()}
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            self._cache = {}
            self._misses = set()

    def _persist(self) -> None:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(
            json.dumps(
                {"prices": self._cache, "misses": sorted(self._misses)},
                indent=2,
            ),
            encoding="utf-8",
        )

    def _key(self, coin_id: str, day: date) -> str:
        return f"{coin_id}:{day.isoformat()}"

    def known(self, coin_id: str, day: date) -> bool:
        key = self._key(coin_id, day)
        with self._lock:
            return key in self._cache or key in self._misses

    def get(self, coin_id: str, day: date) -> Optional[float]:
        key = self._key(coin_id, day)
        with self._lock:
            if key in self._misses:
                return None
            value = self._cache.get(key)
        return value if value and value > 0 else None

    def set(self, coin_id: str, day: date, price_usd: float) -> None:
        if price_usd <= 0:
            return
        key = self._key(coin_id, day)
        with self._lock:
            self._cache[key] = price_usd
            self._misses.discard(key)
            self._persist()

    def set_miss(self, coin_id: str, day: date) -> None:
        key = self._key(coin_id, day)
        with self._lock:
            self._misses.add(key)
            self._persist()


_cache = HistoricalPriceCache()


def _coingecko_history_date(day: date) -> str:
    return day.strftime("%d-%m-%Y")


def _throttle() -> None:
    global _last_request_at
    with _request_lock:
        now = time.time()
        wait = _REQUEST_DELAY_SEC - (now - _last_request_at)
        if wait > 0:
            time.sleep(wait)
        _last_request_at = time.time()


def _fetch_coingecko_history_usd(coin_id: str, day: date) -> Optional[float]:
    if _cache.known(coin_id, day):
        return _cache.get(coin_id, day)

    _throttle()
    path = (
        f"coins/{coin_id}/history?date={_coingecko_history_date(day)}&localization=false"
    )
    payload = coingecko_request(path, timeout=20)
    if not payload:
        _cache.set_miss(coin_id, day)
        return None
    try:
        market = payload.get("market_data") or {}
        current = market.get("current_price") or {}
        price = float(current.get("usd") or 0)
        if price > 0:
            _cache.set(coin_id, day, price)
            return price
        _cache.set_miss(coin_id, day)
    except (KeyError, TypeError, ValueError):
        _cache.set_miss(coin_id, day)
        return None
    return None


def _as_utc_day(when: datetime) -> date:
    if when.tzinfo is None:
        return when.date()
    return when.astimezone(timezone.utc).date()


def historical_usd_price(asset: str, when: datetime) -> Optional[float]:
    """USD price for ``asset`` on the calendar day of ``when`` (UTC)."""
    symbol = _normalize_asset(asset)
    coin_id = COINGECKO_IDS.get(symbol) or resolve_coingecko_id(asset)
    if not coin_id:
        return None

    day = _as_utc_day(when)
    today = datetime.now(timezone.utc).date()
    if day >= today:
        spot = _fetch_coingecko([coin_id]).get(coin_id, 0.0)
        return spot if spot > 0 else None

    return _fetch_coingecko_history_usd(coin_id, day)


def historical_usd_prices_for_transactions(
    pairs: list[tuple[str, datetime]],
) -> Dict[tuple[str, date], float]:
    """Batch-resolve unique (asset, day) pairs."""
    resolved: Dict[tuple[str, date], float] = {}
    for asset, when in pairs:
        day = _as_utc_day(when)
        key = (_normalize_asset(asset), day)
        if key in resolved:
            continue
        price = historical_usd_price(asset, when)
        if price is not None and price > 0:
            resolved[key] = price
    return resolved
