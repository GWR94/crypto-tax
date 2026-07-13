"""Resolve USD prices for ledger assets — live quotes plus fallbacks.

Priority:
1. Explicit overrides in :class:`~app.pricing.PriceStore`
2. **On-chain mint present** (wallet tokens): CoinGecko contract/mint API → DexScreener
   — never CoinGecko symbol search (avoids B→bitcoin-style collisions)
3. **No mint** (exchange imports): CoinGecko symbol / list / search
4. Liquid-staking receipts (mSOL, etc.): symbol quote fallback if mint path misses
5. Mint known but no quote → ``illiquid``
6. Carrying value at average cost basis only when no mint is on file
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Dict, Iterable, List, Literal, Optional, Set

from .coingecko_client import coingecko_request
from .coingecko_registry import resolve_coingecko_id
from .pricing import PriceStore
from .schemas import Transaction

PriceSource = Literal["market", "live", "dex", "illiquid", "cost_basis"]

# Re-export for tests / callers — ambiguous symbols only; everything else is auto-resolved.
from .coingecko_registry import _SYMBOL_OVERRIDES as COINGECKO_IDS

_COINGECKO_ID_ALIASES: Dict[str, str] = {
    "marinade-staked-sol": "msol",
    "airtor-protocol": "anyone-protocol",
}

_LST_ASSETS = frozenset({"MSOL", "JITOSOL", "BSOL"})

_CACHE_TTL_SECONDS = 300
_coingecko_cache: Dict[str, float] = {}
_coingecko_cache_at: float = 0.0
_dex_cache: Dict[str, float] = {}
_dex_cache_at: float = 0.0
_dex_checked: Set[str] = set()


@dataclass(frozen=True)
class ResolvedPrice:
    usd: float
    source: PriceSource


def _normalize_asset(asset: str) -> str:
    return asset.strip().upper()


def _coingecko_platform(address: str) -> Optional[str]:
    text = address.strip().lower()
    if text.startswith("0x") and len(text) == 42:
        return "ethereum"
    if len(text) >= 32 and not text.startswith("0x"):
        return "solana"
    return None


def _fetch_coingecko(ids: Iterable[str]) -> Dict[str, float]:
    """Return coingecko-id → USD price."""
    unique = sorted({coin_id for coin_id in ids if coin_id})
    if not unique:
        return {}

    global _coingecko_cache, _coingecko_cache_at
    now = time.time()
    if _coingecko_cache and now - _coingecko_cache_at < _CACHE_TTL_SECONDS:
        return dict(_coingecko_cache)

    path = "simple/price?ids=" + ",".join(unique) + "&vs_currencies=usd"
    payload = coingecko_request(path, timeout=15)
    if not payload:
        return dict(_coingecko_cache)

    fetched = {
        coin_id: float(row["usd"])
        for coin_id, row in payload.items()
        if isinstance(row, dict) and row.get("usd") is not None
    }
    for canonical, alias in _COINGECKO_ID_ALIASES.items():
        if canonical in fetched and alias not in fetched:
            fetched[alias] = fetched[canonical]
        elif alias in fetched and canonical not in fetched:
            fetched[canonical] = fetched[alias]
    if fetched:
        _coingecko_cache.update(fetched)
        _coingecko_cache_at = now
    return dict(_coingecko_cache)


def _fetch_coingecko_contracts(
    contracts: Iterable[tuple[str, str]],
) -> Dict[str, float]:
    """Return lowercase contract address → USD from CoinGecko token_price API."""
    by_platform: Dict[str, List[str]] = {}
    for platform, address in contracts:
        if not address:
            continue
        by_platform.setdefault(platform, []).append(address.lower())

    prices: Dict[str, float] = {}
    for platform, addresses in by_platform.items():
        unique = sorted(set(addresses))
        if not unique:
            continue
        # CoinGecko accepts comma-separated addresses per platform.
        for i in range(0, len(unique), 30):
            batch = unique[i : i + 30]
            path = (
                f"simple/token_price/{platform}"
                "?contract_addresses="
                + ",".join(batch)
                + "&vs_currencies=usd"
            )
            payload = coingecko_request(path, timeout=15)
            if not payload:
                continue

            for address, row in payload.items():
                if not isinstance(row, dict):
                    continue
                try:
                    price = float(row.get("usd") or 0)
                except (TypeError, ValueError):
                    continue
                if price > 0:
                    prices[address.lower()] = price
    return prices


def _contracts_for_assets(
    transactions: List[Transaction], assets: Iterable[str]
) -> Dict[str, tuple[str, str]]:
    """Map asset → (coingecko_platform, contract/mint) from the ledger."""
    wanted = {_normalize_asset(a) for a in assets}
    contracts: Dict[str, tuple[str, str]] = {}
    for tx in transactions:
        asset = _normalize_asset(tx.asset)
        if asset not in wanted or not tx.token_mint:
            continue
        platform = _coingecko_platform(tx.token_mint)
        if platform:
            contracts[asset] = (platform, tx.token_mint)
    return contracts


def _mints_for_assets(
    transactions: List[Transaction], assets: Iterable[str]
) -> Dict[str, str]:
    """Map asset symbol → most recent non-empty Solana mint from the ledger."""
    wanted = {_normalize_asset(a) for a in assets}
    mints: Dict[str, str] = {}
    for tx in transactions:
        asset = _normalize_asset(tx.asset)
        if asset not in wanted or not tx.token_mint:
            continue
        if _coingecko_platform(tx.token_mint) == "solana":
            mints[asset] = tx.token_mint
    return mints


def _fetch_dexscreener_prices(mints: Iterable[str]) -> Dict[str, float]:
    """Return mint → USD price from the highest-liquidity DexScreener pair."""
    global _dex_cache, _dex_cache_at, _dex_checked
    now = time.time()
    if now - _dex_cache_at > _CACHE_TTL_SECONDS:
        _dex_cache = {}
        _dex_checked = set()
        _dex_cache_at = now

    prices: Dict[str, float] = {}
    for mint in mints:
        if not mint or mint in _dex_checked:
            if mint in _dex_cache:
                prices[mint] = _dex_cache[mint]
            continue
        _dex_checked.add(mint)
        url = f"https://api.dexscreener.com/latest/dex/tokens/{mint}"
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": "crypto-tax-dashboard/1.0"}
            )
            with urllib.request.urlopen(request, timeout=15) as response:
                payload = json.loads(response.read().decode("utf-8"))
            pairs = payload.get("pairs") or []
            best_price = 0.0
            quotes: List[tuple[float, float]] = []
            for pair in pairs:
                if not isinstance(pair, dict):
                    continue
                try:
                    price = float(pair.get("priceUsd") or 0)
                    liq = float((pair.get("liquidity") or {}).get("usd") or 0)
                except (TypeError, ValueError):
                    continue
                if price <= 0 or liq < 1_000:
                    continue
                quotes.append((price, liq))
            if quotes:
                prices_only = sorted(p for p, _ in quotes)
                mid = prices_only[len(prices_only) // 2]
                in_band = [
                    (p, liq)
                    for p, liq in quotes
                    if mid <= 0 or (p >= mid / 5 and p <= mid * 5)
                ]
                pool = in_band or quotes
                total_liq = sum(liq for _, liq in pool)
                if total_liq > 0:
                    best_price = sum(p * liq for p, liq in pool) / total_liq
                else:
                    best_price = mid
            _dex_cache[mint] = best_price
            if best_price > 0:
                prices[mint] = best_price
        except (urllib.error.URLError, json.JSONDecodeError, ValueError):
            _dex_cache[mint] = 0.0
    return prices


def _assets_with_on_chain_mint(
    transactions: List[Transaction], assets: Iterable[str]
) -> Set[str]:
    """Ledger symbols that appear with a known on-chain mint/contract."""
    return set(_contracts_for_assets(transactions, assets))


def _apply_coingecko_symbol_prices(
    *,
    assets: Set[str],
    resolved: Dict[str, ResolvedPrice],
    wanted: Set[str],
) -> Dict[str, float]:
    """Resolve symbols via CoinGecko id lookup + simple/price batch."""
    live_by_id: Dict[str, float] = {}
    pending = assets - set(resolved)
    if not pending:
        return live_by_id

    id_by_asset: Dict[str, str] = {}
    for asset in pending:
        coin_id = resolve_coingecko_id(asset, token_mint=None)
        if coin_id:
            id_by_asset[asset] = coin_id

    cg_ids = set(id_by_asset.values())
    if (_LST_ASSETS & wanted) and "SOL" in COINGECKO_IDS:
        cg_ids.add(COINGECKO_IDS["SOL"])
    if cg_ids:
        live_by_id = _fetch_coingecko(cg_ids)

    for asset, coin_id in id_by_asset.items():
        if asset in resolved:
            continue
        price = live_by_id.get(coin_id, 0.0)
        if price <= 0:
            price = live_by_id.get(_COINGECKO_ID_ALIASES.get(coin_id, ""), 0.0)
        if price > 0:
            resolved[asset] = ResolvedPrice(usd=price, source="live")
    return live_by_id


def _apply_lst_sol_floor(
    *,
    resolved: Dict[str, ResolvedPrice],
    wanted: Set[str],
    live_by_id: Dict[str, float],
) -> None:
    sol_id = COINGECKO_IDS.get("SOL", "")
    sol_usd = (
        resolved["SOL"].usd
        if "SOL" in resolved
        else live_by_id.get(sol_id, 0.0)
    )
    for lst in _LST_ASSETS & wanted:
        quote = resolved.get(lst)
        if quote is None or sol_usd <= 0:
            continue
        if quote.usd < sol_usd * 0.9:
            resolved[lst] = ResolvedPrice(usd=sol_usd, source="live")


def resolve_prices(
    *,
    assets: Iterable[str],
    transactions: List[Transaction],
    store: PriceStore,
    cost_basis_usd: Optional[Dict[str, float]] = None,
) -> Dict[str, ResolvedPrice]:
    """Build a USD price map for the requested assets."""
    wanted = {_normalize_asset(a) for a in assets}
    if not wanted:
        return {}

    resolved: Dict[str, ResolvedPrice] = {}
    store_prices = store.all()
    for asset in wanted:
        manual = float(store_prices.get(asset, 0.0))
        if manual > 0:
            resolved[asset] = ResolvedPrice(usd=manual, source="market")

    remaining = wanted - set(resolved)
    minted_assets = _assets_with_on_chain_mint(transactions, remaining)
    exchange_assets = remaining - minted_assets

    # --- Wallet tokens: mint/contract → DexScreener (no symbol search) ---
    if minted_assets:
        contract_by_asset = _contracts_for_assets(transactions, minted_assets)
        if contract_by_asset:
            contract_prices = _fetch_coingecko_contracts(contract_by_asset.values())
            for asset, (_platform, address) in contract_by_asset.items():
                if asset in resolved:
                    continue
                price = contract_prices.get(address.lower(), 0.0)
                if price > 0:
                    resolved[asset] = ResolvedPrice(usd=price, source="live")

        mint_pending = minted_assets - set(resolved)
        if mint_pending:
            mint_by_asset = _mints_for_assets(transactions, mint_pending)
            dex_prices = _fetch_dexscreener_prices(mint_by_asset.values())
            for asset, mint in mint_by_asset.items():
                if asset in resolved:
                    continue
                price = dex_prices.get(mint, 0.0)
                if price > 0:
                    quote = ResolvedPrice(usd=price, source="dex")
                    resolved[asset] = quote
                    resolved[mint] = quote

        # LST: allow symbol quote if mint path did not produce a price.
        lst_fallback = (minted_assets & _LST_ASSETS) - set(resolved)
        live_by_id = _apply_coingecko_symbol_prices(
            assets=lst_fallback,
            resolved=resolved,
            wanted=wanted,
        )
        _apply_lst_sol_floor(resolved=resolved, wanted=wanted, live_by_id=live_by_id)

        for asset in minted_assets - set(resolved):
            mint = _mints_for_assets(transactions, [asset]).get(asset)
            if mint and mint not in resolved:
                resolved[mint] = ResolvedPrice(usd=0.0, source="illiquid")
            resolved[asset] = ResolvedPrice(usd=0.0, source="illiquid")

    # --- Exchange / no-mint assets: CoinGecko symbol search ---
    symbol_pending = exchange_assets - set(resolved)
    if symbol_pending:
        live_by_id = _apply_coingecko_symbol_prices(
            assets=symbol_pending,
            resolved=resolved,
            wanted=wanted,
        )
        _apply_lst_sol_floor(resolved=resolved, wanted=wanted, live_by_id=live_by_id)

    remaining = wanted - set(resolved)
    if remaining and cost_basis_usd:
        for asset in remaining:
            if asset in resolved:
                continue
            price = float(cost_basis_usd.get(asset, 0.0))
            if price > 0:
                resolved[asset] = ResolvedPrice(usd=price, source="cost_basis")

    return resolved


def merge_price_maps(
    store: PriceStore, resolved: Dict[str, ResolvedPrice]
) -> Dict[str, float]:
    """Merge resolved quotes into a flat USD map for the tax engine."""
    merged = store.all()
    for asset, quote in resolved.items():
        merged[asset] = quote.usd
    return merged
