"""On-chain transaction id backfill for explorer links."""

from __future__ import annotations

import re
from typing import List, Tuple

from .evm_chains import EVM_CHAIN_META
from .schemas import Transaction

_EVM_HASH_RE = re.compile(r"^0x[a-fA-F0-9]{64}$")
_EVM_PARTIAL_GROUP_RE = re.compile(r"^0x[a-fA-F0-9]{32}$")
_CDC_HASH_RE = re.compile(r"^cdc-(0x[a-fA-F0-9]+)-")


def _looks_like_solana_signature(value: str) -> bool:
    text = value.strip()
    return (
        len(text) >= 32
        and not text.startswith("0x")
        and text.isalnum()
        and "TSTRPC" not in text  # Kraken-style refs on some rows
    )


def infer_on_chain_tx_id(tx: Transaction) -> str | None:
    """Best-effort tx id for block explorer links."""
    if tx.on_chain_tx_id:
        return tx.on_chain_tx_id

    gid = (tx.trade_group_id or "").strip()
    if gid:
        if _EVM_HASH_RE.match(gid):
            return gid
        if (tx.source or "") in EVM_CHAIN_META and _EVM_PARTIAL_GROUP_RE.match(gid):
            return None
        if (tx.source or "") == "solana" and _looks_like_solana_signature(gid):
            return gid
        if (tx.source or "") == "bitcoin" and len(gid) >= 32:
            return gid
        if (tx.source or "") == "cardano" and len(gid) >= 32:
            return gid
        if (tx.source or "") == "celestia" and gid.upper().startswith("0X") is False:
            return gid

    m = _CDC_HASH_RE.match(tx.id)
    if m and len(m.group(1)) >= 42:
        return m.group(1)

    return None


def backfill_on_chain_tx_ids(
    transactions: List[Transaction],
) -> Tuple[List[Transaction], int]:
    """Populate ``on_chain_tx_id`` from trade groups and legacy ids."""
    changed = 0
    out: List[Transaction] = []
    for tx in transactions:
        if tx.on_chain_tx_id:
            out.append(tx)
            continue
        inferred = infer_on_chain_tx_id(tx)
        if inferred:
            out.append(tx.model_copy(update={"on_chain_tx_id": inferred}))
            changed += 1
        else:
            out.append(tx)
    return out, changed
