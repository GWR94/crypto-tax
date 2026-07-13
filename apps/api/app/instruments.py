"""Human-readable perp contract labels (e.g. ``SOL - USDC``)."""

from __future__ import annotations

import pandas as pd

from .kraken import normalize_asset

_INVALID = frozenset({"", "NAN", "NONE", "NULL"})


def _clean_symbol(raw: object) -> str:
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return ""
    text = str(raw).strip().upper()
    if text in _INVALID:
        return ""
    return normalize_asset(text)


def format_perp_contract(base: object, quote: object = "USDC") -> str:
    """``SOL`` + ``USDC`` → ``SOL - USDC``; quote-only → ``USDC``."""
    base_n = _clean_symbol(base)
    quote_n = _clean_symbol(quote) or "USDC"
    if base_n:
        return f"{base_n} - {quote_n}"
    return quote_n


def parse_exchange_instrument(raw: str) -> tuple[str, str, str]:
    """Parse exchange instrument ids like ``PERP_BTC_USDT`` → (BTC, USDT, perp)."""
    text = str(raw or "").strip().upper()
    kind = "perp" if text.startswith("PERP_") else "spot"
    parts = [p for p in text.split("_") if p]
    if len(parts) >= 3 and parts[0] == "PERP":
        return normalize_asset(parts[1]), normalize_asset(parts[-1]), kind
    if len(parts) >= 2:
        return normalize_asset(parts[0]), normalize_asset(parts[-1]), kind
    return normalize_asset(text), "USDT", kind
