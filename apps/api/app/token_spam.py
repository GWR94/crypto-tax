"""Cross-chain phishing / spam token detection (URLs in ticker names, etc.)."""

from __future__ import annotations

import re
from typing import List

from .config import is_stablecoin
from .schemas import Transaction

MIN_SPAM_FIAT_USD = 0.01

_CORE_CRYPTO_ASSETS = frozenset(
    {"SOL", "MSOL", "BSOL", "JITOSOL", "JTO", "WSOL", "ETH", "WETH"}
)

_SCAM_LABEL_RE = re.compile(
    r"https?://|www\.|\.(?:org|com|io|xyz|app|net|top)\b",
    re.IGNORECASE,
)


def is_scam_token_label(text: str) -> bool:
    """True for phishing-style token names (URLs, claim-reward spam, etc.)."""
    label = text.strip()
    if not label:
        return False
    if _is_core_crypto_asset(label):
        return False
    upper = label.upper()
    if "HTTP://" in upper or "HTTPS://" in upper:
        return True
    if _SCAM_LABEL_RE.search(label):
        return True
    if len(label) > 32:
        return True
    if "CLAIM" in upper and any(
        kw in upper for kw in ("REWARD", "HTTP", "VISIT", "FREE", "AIRDROP")
    ):
        return True
    if "REWARD" in upper and "HTTP" in upper:
        return True
    return False


def _is_core_crypto_asset(asset: str) -> bool:
    sym = asset.strip().upper()
    return sym in _CORE_CRYPTO_ASSETS or is_stablecoin(sym)


def is_phishing_transaction(tx: Transaction) -> bool:
    """Phishing airdrops on any chain (Ethereum, Solana, etc.)."""
    if is_scam_token_label(tx.asset):
        return True
    if tx.counter_asset and is_scam_token_label(tx.counter_asset):
        return True
    return False


def strip_spam_transactions(
    transactions: List[Transaction],
) -> tuple[List[Transaction], int]:
    """Remove phishing tokens and Solana routing noise from the ledger."""
    from .solana_wallet import is_phantom_solana_leg

    kept: List[Transaction] = []
    removed = 0
    for tx in transactions:
        if is_phishing_transaction(tx) or is_phantom_solana_leg(tx):
            removed += 1
        else:
            kept.append(tx)
    return kept, removed
