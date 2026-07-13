"""Human-readable labels for ledger asset keys (Solana mints, symbols, etc.)."""

from __future__ import annotations

from typing import Dict, List, Optional, Set

from .config import NATIVE_ASSET_NAMES, is_reserved_symbol, native_asset_name
from .schemas import AssetLabel, Transaction
from .solana_tokens import (
    get_registry,
    is_short_mint_label,
    looks_like_mint_fragment,
    looks_like_solana_mint,
    short_mint,
)


def _display_name(symbol: str, fallback: str) -> str:
    """Prefer a catalog name when the fallback is just the ticker."""
    sym = symbol.strip().upper()
    catalog = native_asset_name(sym)
    if catalog.upper() != sym:
        return catalog
    if fallback.strip().upper() != sym:
        return fallback
    return catalog


def _is_suspicious_ticker(asset: str) -> bool:
    """Homoglyph / fake-stablecoin tickers (e.g. unicode lookalike USDT)."""
    text = asset.strip()
    if not text:
        return False
    if text.isascii() and text.isalnum():
        return False
    return any(ord(ch) > 127 for ch in text)


def _collect_asset_keys(transactions: List[Transaction]) -> Dict[str, Optional[str]]:
    """Map ledger asset keys to an optional token mint seen on any row."""
    keys: Dict[str, Optional[str]] = {}
    for tx in transactions:
        keys.setdefault(tx.asset, tx.token_mint)
        if tx.token_mint and tx.asset in keys and not keys[tx.asset]:
            keys[tx.asset] = tx.token_mint
        if tx.counter_asset:
            keys.setdefault(tx.counter_asset, None)
    return keys


def _label_asset_key(
    asset: str,
    *,
    mint: Optional[str],
    solana_symbol_lookup: bool,
    registry,
    labels: Dict[str, AssetLabel],
    add,
) -> None:
    if not asset or asset in labels:
        return

    if is_short_mint_label(asset):
        info = registry.lookup_short_mint_label(asset)
        if info:
            add(
                asset,
                symbol=info.symbol,
                name=_display_name(info.symbol, info.name),
                mint=info.mint,
            )
        else:
            add(asset, symbol=asset, name="Unknown SPL token")
        return

    if _is_suspicious_ticker(asset):
        add(asset, symbol=asset, name="Unverified token (possible scam)")
        return

    if mint:
        info = registry.lookup_mint(mint)
        if info:
            add(
                asset,
                symbol=info.symbol,
                name=_display_name(info.symbol, info.name),
                mint=info.mint,
            )
            return

    sym = asset.strip().upper()
    if sym in NATIVE_ASSET_NAMES and NATIVE_ASSET_NAMES[sym].upper() != sym:
        add(asset, symbol=sym, name=native_asset_name(sym))
        return

    if looks_like_solana_mint(asset):
        info = registry.lookup_mint(asset)
        if info:
            add(asset, symbol=info.symbol, name=_display_name(info.symbol, info.name), mint=info.mint)
        else:
            add(
                asset,
                symbol=short_mint(asset),
                name="Unknown SPL token",
                mint=asset,
            )
        return

    if is_reserved_symbol(asset) or sym in NATIVE_ASSET_NAMES:
        add(asset, symbol=sym, name=native_asset_name(sym))
        return

    if looks_like_mint_fragment(asset):
        info = registry.lookup_mint_prefix(asset)
        if info:
            add(
                asset,
                symbol=info.symbol,
                name=_display_name(info.symbol, info.name),
                mint=info.mint,
            )
            return

    if solana_symbol_lookup:
        info = registry.lookup_symbol(asset)
        if info:
            add(
                asset,
                symbol=info.symbol,
                name=_display_name(info.symbol, info.name),
                mint=info.mint,
            )
            return

    sym = asset.strip().upper()
    if sym in NATIVE_ASSET_NAMES:
        add(asset, symbol=sym, name=native_asset_name(sym))
        return

    add(asset, symbol=asset, name=_display_name(asset, asset))


def build_asset_labels(transactions: List[Transaction]) -> Dict[str, AssetLabel]:
    """Build a map from ledger ``asset`` keys to display metadata."""
    registry = get_registry()
    registry.ensure_loaded()

    labels: Dict[str, AssetLabel] = {}

    def add(key: str, *, symbol: str, name: str, mint: Optional[str] = None) -> None:
        if not key:
            return
        labels[key] = AssetLabel(symbol=symbol, name=name, mint=mint)

    keys = _collect_asset_keys(transactions)
    solana_keys: Set[str] = set()
    for tx in transactions:
        if tx.source != "solana":
            continue
        solana_keys.add(tx.asset)
        if tx.counter_asset:
            solana_keys.add(tx.counter_asset)

    for asset, mint in keys.items():
        _label_asset_key(
            asset,
            mint=mint,
            solana_symbol_lookup=asset in solana_keys,
            registry=registry,
            labels=labels,
            add=add,
        )

    return labels
