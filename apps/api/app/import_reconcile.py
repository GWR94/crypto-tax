"""Rebuild import registry entries from ledger rows when metadata was lost."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Literal, Optional, Tuple

from . import ingestion
from .schemas import Transaction
from .wallet_detect import CHAIN_LABELS

WalletOrCsv = Literal["csv", "wallet"]

_CHAIN_SOURCES = frozenset(CHAIN_LABELS.keys())
_EXCHANGE_SOURCES = frozenset({"hyperliquid"})


def infer_orphan_import_metadata(
    import_txs: List[Transaction],
) -> Tuple[WalletOrCsv, str, Optional[str], Optional[str]]:
    """Infer kind, label, chain, and address for ledger rows missing registry metadata."""
    if not import_txs:
        return "csv", "Unknown import", None, None

    unique_sources = {tx.source for tx in import_txs if tx.source}
    chain_sources = unique_sources & _CHAIN_SOURCES
    on_chain_count = sum(1 for tx in import_txs if tx.on_chain_tx_id)
    on_chain_ratio = on_chain_count / len(import_txs)

    if len(chain_sources) >= 2 or (chain_sources and unique_sources & _EXCHANGE_SOURCES):
        chain = _dominant_chain(import_txs, chain_sources)
        return "wallet", _wallet_label(chain, unique_sources), chain, None

    if (
        len(chain_sources) == 1
        and on_chain_ratio >= 0.9
        and len(import_txs) >= 30
    ):
        chain = next(iter(chain_sources))
        return "wallet", _wallet_label(chain, unique_sources), chain, None

    parser_label = ingestion.primary_source_label(import_txs)
    label = parser_label or "Unknown import"
    if label and not label.lower().endswith(".csv"):
        label = f"{label} CSV"
    return "csv", label, None, None


def orphan_imported_at(import_txs: List[Transaction]) -> datetime:
    timestamps = [tx.timestamp for tx in import_txs if tx.timestamp]
    if timestamps:
        return min(timestamps)
    return datetime.now(timezone.utc)


def _dominant_chain(
    import_txs: List[Transaction], chain_sources: set[str]
) -> str:
    counts: dict[str, int] = {}
    for tx in import_txs:
        if tx.source in chain_sources:
            counts[tx.source] = counts.get(tx.source, 0) + 1
    if counts:
        return max(counts, key=counts.get)
    return sorted(chain_sources)[0]


def _wallet_label(chain: str, unique_sources: set[str | None]) -> str:
    if "hyperliquid" in unique_sources and chain in _CHAIN_SOURCES:
        return CHAIN_LABELS["ethereum"]
    base = CHAIN_LABELS.get(chain, chain.replace("_", " ").title())
    return f"{base} wallet"
