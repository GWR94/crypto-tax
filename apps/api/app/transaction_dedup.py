"""Idempotent transaction deduplication.

Re-importing the same CSV (or fetching the same wallet twice) must never grow
the ledger. We dedupe in two passes:

1. By stable ``id`` — the primary key each parser assigns.
2. By a content fingerprint — a safety net for rows whose ``id`` drifted between
   imports (unstable exchange ids, formatting changes, etc.).
"""

from __future__ import annotations

from typing import Dict, List, Tuple

from .schemas import Transaction

Fingerprint = Tuple[object, ...]

_ON_CHAIN_SOURCES = frozenset({"solana", "ethereum", "bitcoin", "cardano", "celestia"})


def transaction_fingerprint(tx: Transaction) -> Fingerprint:
    """Content key identifying the same economic event across re-imports.

    The timestamp is included so two legitimately distinct trades with identical
    size and value on different days are not collapsed. Re-imports of the same
    row preserve their timestamp, so this still catches duplicates. Exchange
    timezone-offset duplicates (~1h apart) are handled separately by
    ``collapse_exchange_timezone_duplicates``.
    """
    return (
        tx.source or "",
        tx.on_chain_tx_id or tx.trade_group_id or "",
        tx.transaction_type,
        tx.asset,
        round(tx.amount, 8),
        tx.transfer_direction or "",
        tx.instrument_kind or "spot",
        round(tx.fiat_value_at_trigger, 2),
        tx.timestamp.isoformat(),
    )


def on_chain_event_fingerprint(tx: Transaction) -> Fingerprint | None:
    """Stable key for the same on-chain leg across re-imports.

    Overlapping Solana CSV exports often restate the same signature with a
    different ``id`` or FMV (USD vs GBP, refreshed price feeds). When a row
    carries an on-chain id, dedupe on the economic leg instead of the quoted
    value.
    """
    chain_id = tx.on_chain_tx_id
    if not chain_id and tx.source in _ON_CHAIN_SOURCES:
        chain_id = tx.trade_group_id
    if not chain_id:
        return None
    return (
        chain_id,
        tx.transaction_type,
        tx.asset,
        round(tx.amount, 8),
        tx.transfer_direction or "",
        tx.instrument_kind or "spot",
    )


def dedup_keys_for_transaction(tx: Transaction) -> List[Fingerprint]:
    """All fingerprints that should identify this row as already imported."""
    keys = [transaction_fingerprint(tx)]
    chain = on_chain_event_fingerprint(tx)
    if chain is not None:
        keys.append(chain)
    return keys


def _prefer(existing: Transaction, candidate: Transaction) -> Transaction:
    """Pick the richer of two duplicate rows.

    Prefer rows that carry an on-chain id, then a higher recorded fiat value,
    then the earlier timestamp for stability.
    """
    existing_has_chain = bool(existing.on_chain_tx_id)
    candidate_has_chain = bool(candidate.on_chain_tx_id)
    if existing_has_chain != candidate_has_chain:
        return existing if existing_has_chain else candidate

    if candidate.fiat_value_at_trigger != existing.fiat_value_at_trigger:
        return (
            candidate
            if candidate.fiat_value_at_trigger > existing.fiat_value_at_trigger
            else existing
        )

    return existing if existing.timestamp <= candidate.timestamp else candidate


def dedupe_transactions(
    transactions: List[Transaction],
) -> Tuple[List[Transaction], Dict[str, int]]:
    """Collapse duplicate rows by id then by content fingerprint.

    Returns the deduped list (original order preserved by first occurrence) and
    a stats dict: ``{kept, skipped_id, skipped_fingerprint, skipped_on_chain}``.
    """
    by_id: Dict[str, Transaction] = {}
    skipped_id = 0
    id_order: List[str] = []
    for tx in transactions:
        if tx.id in by_id:
            skipped_id += 1
            by_id[tx.id] = _prefer(by_id[tx.id], tx)
            continue
        by_id[tx.id] = tx
        id_order.append(tx.id)

    by_fp: Dict[Fingerprint, str] = {}
    skipped_fingerprint = 0
    kept_ids: List[str] = []
    for tx_id in id_order:
        tx = by_id[tx_id]
        fp = transaction_fingerprint(tx)
        if fp in by_fp:
            skipped_fingerprint += 1
            winner = _prefer(by_id[by_fp[fp]], tx)
            by_id[by_fp[fp]] = winner
            continue
        by_fp[fp] = tx_id
        kept_ids.append(tx_id)

    by_chain: Dict[Fingerprint, str] = {}
    skipped_on_chain = 0
    final_ids: List[str] = []
    for tx_id in kept_ids:
        tx = by_id[tx_id]
        chain_fp = on_chain_event_fingerprint(tx)
        if chain_fp is not None and chain_fp in by_chain:
            skipped_on_chain += 1
            winner = _prefer(by_id[by_chain[chain_fp]], tx)
            by_chain[chain_fp] = winner.id
            by_id[winner.id] = winner
            continue
        if chain_fp is not None:
            by_chain[chain_fp] = tx_id
        final_ids.append(tx_id)

    deduped = [by_id[tx_id] for tx_id in final_ids]
    stats = {
        "kept": len(deduped),
        "skipped_id": skipped_id,
        "skipped_fingerprint": skipped_fingerprint,
        "skipped_on_chain": skipped_on_chain,
    }
    return deduped, stats
