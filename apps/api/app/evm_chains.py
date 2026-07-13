"""Etherscan API v2 chain registry for multichain 0x wallet import."""

from __future__ import annotations

from typing import Dict, Literal, Tuple

# slug → { chainid, label, native gas token }
EVM_CHAIN_META: Dict[str, dict] = {
    "ethereum": {"chainid": "1", "label": "Ethereum", "native": "ETH"},
    "arbitrum": {"chainid": "42161", "label": "Arbitrum", "native": "ETH"},
    "base": {"chainid": "8453", "label": "Base", "native": "ETH"},
    "optimism": {"chainid": "10", "label": "Optimism", "native": "ETH"},
    "polygon": {"chainid": "137", "label": "Polygon", "native": "POL"},
    "bsc": {"chainid": "56", "label": "BNB Chain", "native": "BNB"},
    "avalanche": {"chainid": "43114", "label": "Avalanche", "native": "AVAX"},
    "linea": {"chainid": "59144", "label": "Linea", "native": "ETH"},
    "blast": {"chainid": "81457", "label": "Blast", "native": "ETH"},
    "scroll": {"chainid": "534352", "label": "Scroll", "native": "ETH"},
}

EvmChain = Literal[
    "ethereum",
    "arbitrum",
    "base",
    "optimism",
    "polygon",
    "bsc",
    "avalanche",
    "linea",
    "blast",
    "scroll",
]

# Mainnets fetched automatically when a 0x address is imported.
# Focused on major ecosystems — not every niche L2.
EVM_AUTO_IMPORT_CHAINS: Tuple[EvmChain, ...] = (
    "ethereum",
    "bsc",
    "arbitrum",
    "base",
    "polygon",
    "optimism",
    "avalanche",
)

# Available for single-chain override but not auto-fetched on paste.
EVM_EXTENDED_CHAINS: Tuple[EvmChain, ...] = (
    "linea",
    "blast",
    "scroll",
)

EVM_AUTO_IMPORT_LABEL = ", ".join(
    EVM_CHAIN_META[slug]["label"] for slug in EVM_AUTO_IMPORT_CHAINS
)

# Per-chain cap when importing all EVM networks at once (avoids huge API usage).
EVM_MULTI_IMPORT_MAX_ROWS = 1_000


def native_asset_for(chain: str) -> str:
    meta = EVM_CHAIN_META.get(chain)
    if not meta:
        return "ETH"
    return str(meta["native"])


def chain_label(chain: str) -> str:
    meta = EVM_CHAIN_META.get(chain)
    return str(meta["label"]) if meta else chain.title()


def is_evm_chain(chain: str) -> bool:
    return chain in EVM_CHAIN_META
