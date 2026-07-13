"""Solana SPL token metadata — Jupiter token list with local disk cache.

Resolves mint addresses to human-readable symbols/names for imports and UI.
The registry is optional at runtime: if the network fetch fails, hardcoded
mints and any existing cache file still work.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Dict, Optional

from .config import RESERVED_SYMBOLS, is_reserved_symbol

# Well-known mints (merged into every registry load).
KNOWN_MINTS: Dict[str, dict] = {
    "So11111111111111111111111111111111111111112": {
        "symbol": "SOL",
        "name": "Wrapped SOL",
        "decimals": 9,
    },
    "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So": {
        "symbol": "MSOL",
        "name": "Marinade staked SOL",
        "decimals": 9,
    },
    "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn": {
        "symbol": "JITOSOL",
        "name": "Jito Staked SOL",
        "decimals": 9,
    },
    "bSo13r4TkiE4KumL71LsHTPkpLWEywMUht6qBDkWeA": {
        "symbol": "BSOL",
        "name": "BlazeStake Staked SOL",
        "decimals": 9,
    },
    "jtojtome8JDRCnn4g1NSEzNoYRrGjoc15W3DjcnMnvJB": {
        "symbol": "JTO",
        "name": "Jito",
        "decimals": 9,
    },
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": {
        "symbol": "USDC",
        "name": "USD Coin",
        "decimals": 6,
    },
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": {
        "symbol": "USDT",
        "name": "Tether USD",
        "decimals": 6,
    },
    "FiM4VQdXXnTXL7GgChryf9zHNG9cmvKECwf34L2y3CkN": {
        "symbol": "KAMINOVAULT",
        "name": "Kamino Earn SOL Vault Shares",
        "decimals": 6,
    },
}

# Explorer CSVs often truncate mints to 8 chars; map to canonical mint when ambiguous.
FRAGMENT_ALIASES: Dict[str, str] = {
    "JTOJTOME": "jtojtome8JDRCnn4g1NSEzNoYRrGjoc15W3DjcnMnvJB",
    "BSO13R4T": "bSo13r4TkiE4KumL71LsHTPkpLWEywMUht6qBDkWeA",
    "GDFNESIA": "GDfnEsia2WLAW5t8yx2X5j2mkfA74i5kwGdDuZHt7XmG",
}

JUPITER_TOKEN_URL = "https://cache.jup.ag/tokens"
CACHE_TTL_SECONDS = 7 * 24 * 3600
_REPO_ROOT = Path(__file__).resolve().parents[3]
CACHE_PATH = _REPO_ROOT / "data" / "solana_tokens.json"


@dataclass(frozen=True)
class TokenInfo:
    mint: str
    symbol: str
    name: str
    decimals: Optional[int] = None


class SolanaTokenRegistry:
    """Mint → metadata lookup backed by Jupiter's public token list."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._by_mint: Dict[str, TokenInfo] = {}
        self._by_symbol: Dict[str, TokenInfo] = {}
        self._by_prefix8: Dict[str, TokenInfo] = {}
        self._loaded = False

    def _rebuild_prefix8(self) -> None:
        """Map first-8-char mint prefixes; well-known mints win over impostors."""
        self._by_prefix8 = {}
        for mint in KNOWN_MINTS:
            info = self._by_mint.get(mint)
            if info and len(mint) >= 8:
                self._by_prefix8[mint[:8].upper()] = info
        for mint, info in self._by_mint.items():
            if len(mint) >= 8:
                key = mint[:8].upper()
                if key not in self._by_prefix8:
                    self._by_prefix8[key] = info
        for fragment, canonical_mint in FRAGMENT_ALIASES.items():
            info = self._by_mint.get(canonical_mint)
            if info:
                self._by_prefix8[fragment.upper()] = info

    def _ingest(self, mint: str, symbol: str, name: str, decimals: Optional[int]) -> None:
        sym = symbol.strip().upper()
        if not sym:
            return
        info = TokenInfo(
            mint=mint,
            symbol=sym,
            name=(name or sym).strip(),
            decimals=decimals,
        )
        self._by_mint[mint] = info
        # Memecoins squat major tickers (500+ "BTC" on Solana) — never index those.
        if sym in RESERVED_SYMBOLS:
            return
        if sym not in self._by_symbol or mint in KNOWN_MINTS:
            self._by_symbol[sym] = info

    def _merge_known(self) -> None:
        for mint, meta in KNOWN_MINTS.items():
            self._ingest(mint, meta["symbol"], meta["name"], meta.get("decimals"))

    def load(self, *, force_refresh: bool = False) -> int:
        """Load registry from cache and optionally refresh from Jupiter."""
        with self._lock:
            self._by_mint.clear()
            self._by_symbol.clear()
            self._merge_known()

            cached = self._read_cache()
            if cached:
                for mint, meta in cached.get("tokens", {}).items():
                    self._ingest(
                        mint,
                        meta.get("symbol", ""),
                        meta.get("name", ""),
                        meta.get("decimals"),
                    )

            stale = (
                force_refresh
                or not cached
                or (time.time() - cached.get("fetched_at", 0)) > CACHE_TTL_SECONDS
            )
            if stale:
                fetched = self._fetch_jupiter()
                if fetched:
                    self._write_cache(fetched)

            self._rebuild_prefix8()
            self._loaded = True
            return len(self._by_mint)

    def ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    def lookup_mint(self, mint: str) -> Optional[TokenInfo]:
        self.ensure_loaded()
        return self._by_mint.get(mint) or self._by_mint.get(mint.lower())

    def lookup_symbol(self, symbol: str) -> Optional[TokenInfo]:
        self.ensure_loaded()
        sym = symbol.strip().upper()
        if is_reserved_symbol(sym):
            return None
        return self._by_symbol.get(sym)

    def lookup_mint_prefix(self, prefix: str) -> Optional[TokenInfo]:
        """Match explorer exports that truncate mint addresses (case-insensitive)."""
        self.ensure_loaded()
        text = prefix.strip()
        if len(text) < 4:
            return None
        upper = text.upper()
        if is_reserved_symbol(upper):
            return None

        alias_mint = FRAGMENT_ALIASES.get(upper)
        if alias_mint:
            info = self.lookup_mint(alias_mint)
            if info:
                return info

        if len(text) <= 8 and upper in self._by_prefix8:
            return self._by_prefix8[upper]

        lower = text.lower()
        matches = [m for m in self._by_mint if m.lower().startswith(lower)]
        if len(matches) == 1:
            return self._by_mint[matches[0]]
        if len(matches) > 1:
            known = [m for m in matches if m in KNOWN_MINTS]
            if known:
                return self._by_mint[known[0]]
        if len(text) >= 6:
            exact = [m for m in self._by_mint if m.lower()[: len(text)] == lower]
            if len(exact) == 1:
                return self._by_mint[exact[0]]
            if len(exact) > 1:
                known = [m for m in exact if m in KNOWN_MINTS]
                if known:
                    return self._by_mint[known[0]]
        return None

    def lookup_short_mint_label(self, asset: str) -> Optional[TokenInfo]:
        """Resolve compact mint labels such as ``FiM4…3CkN``."""
        parsed = parse_short_mint_label(asset)
        if not parsed:
            return None
        prefix, suffix = parsed
        matches = [
            m for m in self._by_mint if m.startswith(prefix) and m.endswith(suffix)
        ]
        if len(matches) == 1:
            return self._by_mint[matches[0]]
        if len(matches) > 1:
            known = [m for m in matches if m in KNOWN_MINTS]
            if len(known) == 1:
                return self._by_mint[known[0]]
        return None

    def resolve_asset(self, mint: str) -> tuple[str, str]:
        """Map a mint to (canonical_asset_ticker, full_mint).

        Falls back to a short mint label when the token is not in the registry.
        """
        info = self.lookup_mint(mint)
        if info:
            return info.symbol, info.mint
        return short_mint(mint), mint

    def _read_cache(self) -> Optional[dict]:
        if not CACHE_PATH.exists():
            return None
        try:
            return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def _write_cache(self, tokens: Dict[str, dict]) -> None:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {"fetched_at": time.time(), "tokens": tokens}
        CACHE_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _fetch_jupiter(self) -> Optional[Dict[str, dict]]:
        try:
            req = urllib.request.Request(
                JUPITER_TOKEN_URL,
                headers={"User-Agent": "crypto-tax-dashboard/1.0"},
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, json.JSONDecodeError, TimeoutError, OSError):
            return None

        if not isinstance(raw, list):
            return None

        tokens: Dict[str, dict] = {}
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            mint = str(entry.get("address", "")).strip()
            symbol = str(entry.get("symbol", "")).strip()
            if not mint or not symbol:
                continue
            tokens[mint] = {
                "symbol": symbol.upper(),
                "name": str(entry.get("name", symbol)).strip(),
                "decimals": entry.get("decimals"),
            }
            self._ingest(mint, symbol, str(entry.get("name", symbol)), entry.get("decimals"))

        return tokens


_registry = SolanaTokenRegistry()


def get_registry() -> SolanaTokenRegistry:
    return _registry


def short_mint(mint: str) -> str:
    """Compact mint label for unknown tokens, e.g. ``9LP1…QJKT``."""
    text = mint.strip()
    if len(text) <= 12:
        return text.upper()
    return f"{text[:4]}…{text[-4:]}"


def parse_short_mint_label(asset: str) -> Optional[tuple[str, str]]:
    """Split ``Abcd…Wxyz`` / ``Abcd...Wxyz`` into prefix and suffix."""
    text = asset.strip()
    if "\u2026" in text:
        parts = text.split("\u2026", 1)
    elif "..." in text:
        parts = text.split("...", 1)
    else:
        return None
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return None
    return parts[0], parts[1]


def is_short_mint_label(asset: str) -> bool:
    return parse_short_mint_label(asset) is not None


def looks_like_solana_mint(value: str) -> bool:
    """Heuristic: base58-ish string long enough to be a mint address."""
    text = value.strip()
    return 32 <= len(text) <= 48 and text.isalnum()


def looks_like_mint_fragment(asset: str) -> bool:
    """Short alphanumeric key from truncated Solana explorer exports."""
    text = asset.strip()
    return (
        4 <= len(text) <= 12
        and text.isalnum()
        and not looks_like_solana_mint(text)
        and text.upper() not in {"SOL", "MSOL", "BSOL", "JITOSOL", "JTO"}
    )
