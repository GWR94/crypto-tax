"""Current market price provider.

To keep the application fully self-hosted and deterministic with no mandatory
network dependency, prices live in an in-memory map seeded with sensible
defaults. The REST API exposes endpoints to read and override these prices so
the dashboard can reflect live numbers when the user supplies them.
"""

from __future__ import annotations

from threading import Lock
from typing import Dict

# Seed prices (USD). These are static defaults and can be overridden at runtime
# via the /api/prices endpoint. They are NOT predictions or live quotes.
DEFAULT_PRICES: Dict[str, float] = {
    "BTC": 64000.0,
    "ETH": 3400.0,
    "SOL": 145.0,
    "ADA": 0.45,
    "MATIC": 0.72,
    "DOGE": 0.16,
    "LINK": 14.5,
    "AVAX": 28.0,
    "DOT": 6.8,
    "USDC": 1.0,
    "USDT": 1.0,
    "ARB": 0.95,
    "OP": 1.8,
    "UNI": 7.2,
}


class PriceStore:
    """Thread-safe in-memory store of current asset prices."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._prices: Dict[str, float] = dict(DEFAULT_PRICES)

    def all(self) -> Dict[str, float]:
        with self._lock:
            return dict(self._prices)

    def get(self, asset: str) -> float:
        with self._lock:
            return self._prices.get(asset.upper(), 0.0)

    def set(self, asset: str, price: float) -> None:
        with self._lock:
            self._prices[asset.upper()] = float(price)

    def update_many(self, prices: Dict[str, float]) -> None:
        with self._lock:
            for asset, price in prices.items():
                self._prices[asset.upper()] = float(price)
