"""Detect wallet chain from address format."""

from __future__ import annotations

from typing import Literal, Optional

from .btc_fetch import is_valid_btc_address
from .cardano_fetch import is_valid_cardano_address
from .celestia_fetch import is_valid_celestia_address
from .evm_chains import EVM_CHAIN_META, EVM_AUTO_IMPORT_LABEL, EvmChain, is_evm_chain
from .evm_fetch import is_valid_evm_address
from .solana_fetch import is_valid_solana_address

WalletChain = Literal[
    "solana",
    "ethereum",
    "bitcoin",
    "cardano",
    "celestia",
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

CHAIN_LABELS: dict[str, str] = {
    "solana": "Solana",
    "ethereum": f"On-chain ({EVM_AUTO_IMPORT_LABEL}) + Hyperliquid",
    "bitcoin": "Bitcoin",
    "cardano": "Cardano",
    "celestia": "Celestia",
    **{slug: meta["label"] for slug, meta in EVM_CHAIN_META.items()},
}


def detect_wallet_chain(address: str) -> WalletChain:
    """Infer chain from address shape. Raises ValueError when unrecognized."""
    text = address.strip()
    if not text:
        raise ValueError("Wallet address is required.")

    lower = text.lower()

    if is_valid_evm_address(text):
        return "ethereum"

    if lower.startswith("celestia1"):
        if is_valid_celestia_address(lower):
            return "celestia"
        raise ValueError("Invalid Celestia address (expected celestia1…).")

    if lower.startswith("addr1") or lower.startswith("stake1"):
        if is_valid_cardano_address(lower):
            return "cardano"
        raise ValueError("Invalid Cardano address (expected addr1… or stake1…).")

    if is_valid_btc_address(text):
        return "bitcoin"

    if is_valid_solana_address(text):
        return "solana"

    raise ValueError(
        "Unrecognized wallet address. Supported formats: 0x… (EVM), Solana, "
        "Bitcoin, Cardano (addr1…), Celestia (celestia1…)."
    )


def normalize_wallet_address(address: str, chain: str) -> str:
    if chain in {"cardano", "celestia"}:
        return address.strip().lower()
    return address.strip()


def resolve_wallet_import(
    address: str,
    chain: Optional[WalletChain] = None,
) -> tuple[str, WalletChain]:
    """Return normalized address and chain (explicit chain must match address)."""
    text = address.strip()
    detected = detect_wallet_chain(text)
    chosen: WalletChain = chain or detected

    if chain is not None and chain != detected:
        if is_evm_chain(chain) and detected == "ethereum":
            chosen = chain
        else:
            raise ValueError(
                f"Address looks like {CHAIN_LABELS[detected]}, not {CHAIN_LABELS.get(chain, chain)}."
            )

    normalized = normalize_wallet_address(text, chosen)
    return normalized, chosen
