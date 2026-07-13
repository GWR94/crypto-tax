"""Shared CoinGecko HTTP client (optional API key)."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Optional

_USER_AGENT = "crypto-tax-dashboard/1.0"


def coingecko_api_key() -> str:
    return (
        os.environ.get("COINGECKO_API_KEY", "").strip()
        or os.environ.get("CRYPTO_TAX_COINGECKO_API_KEY", "").strip()
    )


def coingecko_uses_pro() -> bool:
    return os.environ.get("COINGECKO_API_PLAN", "demo").strip().lower() == "pro"


def coingecko_api_base() -> str:
    if coingecko_uses_pro():
        return "https://pro-api.coingecko.com/api/v3"
    return "https://api.coingecko.com/api/v3"


def coingecko_has_api_key() -> bool:
    return bool(coingecko_api_key())


def coingecko_request(path: str, *, timeout: int = 30) -> Optional[dict[str, Any]]:
    """GET a CoinGecko API v3 path; returns parsed JSON or None on failure."""
    url = f"{coingecko_api_base()}/{path.lstrip('/')}"
    headers = {"User-Agent": _USER_AGENT}
    key = coingecko_api_key()
    if key:
        if coingecko_uses_pro():
            headers["x-cg-pro-api-key"] = key
        else:
            headers["x-cg-demo-api-key"] = key

    try:
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return payload if isinstance(payload, dict) else None
    except (urllib.error.URLError, json.JSONDecodeError, urllib.error.HTTPError):
        return None
