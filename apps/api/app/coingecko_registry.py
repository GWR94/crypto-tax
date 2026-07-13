"""Resolve ledger symbols to CoinGecko coin ids (cached list + search)."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from threading import Lock
from typing import Dict, List, Optional

from .coingecko_client import coingecko_request

_CACHE_DIR = Path(
    os.environ.get(
        "CRYPTO_TAX_STATE_DIR",
        str(Path(__file__).resolve().parents[3] / "data"),
    )
)
_LIST_FILE = _CACHE_DIR / "coingecko_coins_list.json"
_SYMBOL_FILE = _CACHE_DIR / "coingecko_symbol_cache.json"
_LIST_TTL_SECONDS = 7 * 24 * 3600
_SEARCH_DELAY_SEC = 0.35

# Symbols that collide on CoinGecko — always use these ids.
_SYMBOL_OVERRIDES: Dict[str, str] = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "ADA": "cardano",
    "MATIC": "matic-network",
    "POL": "polygon-ecosystem-token",
    "DOGE": "dogecoin",
    "LINK": "chainlink",
    "AVAX": "avalanche-2",
    "DOT": "polkadot",
    "USDC": "usd-coin",
    "USDT": "tether",
    "ARB": "arbitrum",
    "OP": "optimism",
    "UNI": "uniswap",
    "CRO": "crypto-com-chain",
    "CHZ": "chiliz",
    "LUNC": "terra-luna",
    "LUNA2": "terra-luna-2",
    "LUNA": "terra-luna-2",
    "ETHW": "ethereum-pow-iou",
    "TIA": "celestia",
    "LTC": "litecoin",
    "BNB": "binancecoin",
    "VET": "vechain",
    "MSOL": "marinade-staked-sol",
    "JITOSOL": "jito-staked-sol",
    "BSOL": "blazestake-staked-sol",
    "WEN": "wen-4",
}

# CoinGecko ids that must NEVER be used for a ledger symbol (search collisions).
# Example: ticker "B" must not resolve to "bitcoin".
_SYMBOL_REJECTED_COIN_IDS: Dict[str, set[str]] = {
    "OMNI": {"omni-network"},
    "B": {"bitcoin"},
}

# Never attach blue-chip CoinGecko ids to meme / ambiguous tickers.
_MAJOR_COIN_IDS = frozenset(
    {
        "bitcoin",
        "ethereum",
        "solana",
        "tether",
        "usd-coin",
        "binancecoin",
        "ripple",
        "cardano",
        "dogecoin",
        "litecoin",
    }
)

# Symbols this short collide with unrelated CoinGecko search hits (e.g. B → bitcoin).
_MIN_SYMBOL_LEN_FOR_SEARCH = 4

_lock = Lock()
_last_search_at = 0.0
_symbol_cache: Dict[str, Optional[str]] = {}
_list_loaded_at = 0.0
_by_symbol: Dict[str, List[dict]] = {}
_by_mint: Dict[str, str] = {}


def _normalize_symbol(asset: str) -> str:
    text = asset.strip().upper()
    if text.startswith("$"):
        text = text[1:]
    return text


def _is_pump_fun_mint(token_mint: Optional[str]) -> bool:
    return bool(token_mint and token_mint.strip().lower().endswith("pump"))


def _reject_implausible_coin_id(
    symbol: str,
    coin_id: str,
    *,
    token_mint: Optional[str] = None,
) -> bool:
    """True when a symbol→id pairing is almost certainly a search collision."""
    if coin_id in _SYMBOL_REJECTED_COIN_IDS.get(symbol, set()):
        return True
    if symbol in _SYMBOL_OVERRIDES and _SYMBOL_OVERRIDES[symbol] == coin_id:
        return False
    if coin_id in _MAJOR_COIN_IDS and symbol not in _SYMBOL_OVERRIDES:
        return True
    if _is_pump_fun_mint(token_mint) and coin_id in _MAJOR_COIN_IDS:
        return True
    return False


def _load_symbol_cache() -> None:
    global _symbol_cache
    if not _SYMBOL_FILE.exists():
        _symbol_cache = {}
        return
    try:
        raw = json.loads(_SYMBOL_FILE.read_text(encoding="utf-8"))
        _symbol_cache = {
            str(k).upper(): (str(v) if v else None) for k, v in raw.items()
        }
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        _symbol_cache = {}


def _cache_get(symbol: str, *, token_mint: Optional[str] = None) -> Optional[str]:
    """Return a cached coin id, dropping entries that fail plausibility checks."""
    if symbol not in _symbol_cache:
        return None
    cached = _symbol_cache[symbol]
    if cached and _reject_implausible_coin_id(symbol, cached, token_mint=token_mint):
        del _symbol_cache[symbol]
        _persist_symbol_cache()
        return None
    return cached


def _cache_set(symbol: str, coin_id: Optional[str], *, token_mint: Optional[str] = None) -> None:
    if coin_id and _reject_implausible_coin_id(symbol, coin_id, token_mint=token_mint):
        coin_id = None
    _symbol_cache[symbol] = coin_id
    _persist_symbol_cache()


def _persist_symbol_cache() -> None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _SYMBOL_FILE.write_text(
        json.dumps(_symbol_cache, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _ensure_list() -> None:
    global _list_loaded_at, _by_symbol, _by_mint
    now = time.time()
    if _by_symbol and now - _list_loaded_at < _LIST_TTL_SECONDS:
        return

    if _LIST_FILE.exists():
        try:
            meta = json.loads(_LIST_FILE.read_text(encoding="utf-8"))
            if (
                isinstance(meta, dict)
                and now - float(meta.get("fetched_at", 0)) < _LIST_TTL_SECONDS
            ):
                _ingest_list(meta.get("coins") or [])
                _list_loaded_at = float(meta["fetched_at"])
                return
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            pass

    url_path = "coins/list?include_platform=true"
    try:
        coins = coingecko_request(url_path, timeout=60)
        if not isinstance(coins, list):
            if _by_symbol:
                return
            raise urllib.error.URLError("CoinGecko coins/list returned no data")
    except urllib.error.URLError:
        if _by_symbol:
            return
        raise

    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _LIST_FILE.write_text(
        json.dumps({"fetched_at": now, "coins": coins}, indent=0),
        encoding="utf-8",
    )
    _ingest_list(coins)
    _list_loaded_at = now


def _ingest_list(coins: list) -> None:
    global _by_symbol, _by_mint
    by_symbol: Dict[str, List[dict]] = {}
    by_mint: Dict[str, str] = {}
    for row in coins:
        if not isinstance(row, dict):
            continue
        coin_id = str(row.get("id") or "")
        symbol = str(row.get("symbol") or "").upper()
        if not coin_id or not symbol:
            continue
        by_symbol.setdefault(symbol, []).append(row)
        platforms = row.get("platforms") or {}
        if isinstance(platforms, dict):
            for _platform, address in platforms.items():
                if address:
                    by_mint[str(address).lower()] = coin_id
    _by_symbol = by_symbol
    _by_mint = by_mint


def _search_coingecko_id(
    symbol: str,
    *,
    token_mint: Optional[str] = None,
) -> Optional[str]:
    if len(symbol) < _MIN_SYMBOL_LEN_FOR_SEARCH:
        return None
    if _is_pump_fun_mint(token_mint):
        return None

    global _last_search_at
    with _lock:
        now = time.time()
        wait = _SEARCH_DELAY_SEC - (now - _last_search_at)
        if wait > 0:
            time.sleep(wait)
        _last_search_at = time.time()

    payload = coingecko_request(
        "search?query=" + urllib.request.quote(symbol), timeout=15
    )
    if not payload:
        return None

    rejected = _SYMBOL_REJECTED_COIN_IDS.get(symbol, set())
    for row in payload.get("coins") or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("symbol") or "").upper() != symbol:
            continue
        coin_id = str(row.get("id") or "")
        if not coin_id or coin_id in rejected:
            continue
        if _reject_implausible_coin_id(symbol, coin_id, token_mint=token_mint):
            continue
        return coin_id
    return None


def resolve_coingecko_id(
    asset: str,
    *,
    token_mint: Optional[str] = None,
) -> Optional[str]:
    """Best-effort CoinGecko coin id for a ledger symbol."""
    symbol = _normalize_symbol(asset)
    if not symbol:
        return None

    if symbol in _SYMBOL_OVERRIDES:
        return _SYMBOL_OVERRIDES[symbol]

    if not _symbol_cache:
        _load_symbol_cache()
    cached = _cache_get(symbol, token_mint=token_mint)
    if cached is not None or symbol in _symbol_cache:
        return cached

    try:
        _ensure_list()
    except (urllib.error.URLError, urllib.error.HTTPError):
        pass

    rejected = _SYMBOL_REJECTED_COIN_IDS.get(symbol, set())

    if token_mint:
        mint_key = token_mint.strip().lower()
        coin_id = _by_mint.get(mint_key)
        if coin_id and coin_id not in rejected:
            if not _reject_implausible_coin_id(symbol, coin_id, token_mint=token_mint):
                _cache_set(symbol, coin_id, token_mint=token_mint)
                return coin_id

    candidates = _by_symbol.get(symbol) or []
    candidates = [c for c in candidates if c.get("id") not in rejected]
    if len(candidates) == 1:
        coin_id = str(candidates[0]["id"])
        if not _reject_implausible_coin_id(symbol, coin_id, token_mint=token_mint):
            _cache_set(symbol, coin_id, token_mint=token_mint)
            return coin_id

    if token_mint and candidates:
        mint_key = token_mint.strip().lower()
        for row in candidates:
            platforms = row.get("platforms") or {}
            if not isinstance(platforms, dict):
                continue
            for address in platforms.values():
                if address and str(address).lower() == mint_key:
                    coin_id = str(row["id"])
                    if not _reject_implausible_coin_id(
                        symbol, coin_id, token_mint=token_mint
                    ):
                        _cache_set(symbol, coin_id, token_mint=token_mint)
                        return coin_id

    searched = _search_coingecko_id(symbol, token_mint=token_mint)
    _cache_set(symbol, searched, token_mint=token_mint)
    return searched
