"""Match internal wallet/exchange transfers.

When the same coins move between two ledgers you control (wallet -> exchange,
wallet -> wallet, exchange -> exchange), the outbound and inbound legs are
already typed as ``TRANSFER``. Pairing them lets the tax engines treat the move
as basis-neutral:

* The cost basis stays in the per-asset pool (no disposal, no new lot).
* A genuine external receipt (an unpaired ``TRANSFER IN`` with a known fiat
  value) instead establishes cost basis, so a later sale is not flagged as
  having no acquisition history.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from typing import Dict, List, Set

from .schemas import Transaction, TransactionType

# Maximum gap between the outbound and inbound legs of one transfer.
TRANSFER_PAIR_WINDOW = timedelta(hours=48)

# Earn subscriptions can sit for months before redemption (spot ↔ earn sub-account).
EARN_PAIR_WINDOW = timedelta(days=400)

# Crypto.com transaction_kind markers embedded in import ids.
_EARN_DEPOSIT_MARKERS = (
    "crypto_earn_program_created",
    "crypto_earn_program_deposit",
)
_EARN_WITHDRAWAL_MARKERS = ("crypto_earn_program_withdrawn",)

# The inbound amount is the outbound amount less a network fee. Allow the
# inbound leg to be up to 10% smaller (covers fees on small transfers) and a
# hair larger to absorb rounding.
PAIR_LOWER_RATIO = 0.90
PAIR_UPPER_RATIO = 1.0005


def _candidate_score(out_tx: Transaction, in_tx: Transaction) -> float:
    """Lower is a better match: prioritise amount then time proximity."""
    amount_diff = abs(out_tx.amount - in_tx.amount)
    time_diff = abs((in_tx.timestamp - out_tx.timestamp).total_seconds())
    return amount_diff * 1_000_000 + time_diff


def _amounts_compatible(out_amount: float, in_amount: float) -> bool:
    if out_amount <= 0 or in_amount <= 0:
        return False
    lower = out_amount * PAIR_LOWER_RATIO
    upper = out_amount * PAIR_UPPER_RATIO
    return lower <= in_amount <= upper


def _record_pair(
    pair_map: Dict[str, str], out_tx: Transaction, in_tx: Transaction
) -> None:
    pair_id = f"pair-{out_tx.id}"
    pair_map[out_tx.id] = pair_id
    pair_map[in_tx.id] = pair_id


def _id_contains(tx: Transaction, markers: tuple[str, ...]) -> bool:
    needle = tx.id.lower()
    return any(marker in needle for marker in markers)


def _is_exchange_earn_deposit_out(tx: Transaction) -> bool:
    """Spot → exchange earn sub-account (basis-neutral internal shuffle)."""
    if tx.transaction_type != TransactionType.TRANSFER:
        return False
    if tx.transfer_direction != "OUT":
        return False
    if tx.amount <= 0:
        return False
    return _id_contains(tx, _EARN_DEPOSIT_MARKERS)


def _is_exchange_earn_withdrawal_in(tx: Transaction) -> bool:
    """Earn sub-account → spot return leg."""
    if tx.transaction_type != TransactionType.TRANSFER:
        return False
    if tx.transfer_direction != "IN":
        return False
    if tx.amount <= 0:
        return False
    return _id_contains(tx, _EARN_WITHDRAWAL_MARKERS)


def _pair_on_chain_legs(
    transactions: List[Transaction],
    pair_map: Dict[str, str],
    used_out_ids: Set[str],
    used_in_ids: Set[str],
) -> None:
    """Pair IN/OUT legs that share the same on-chain signature.

    Two Solana wallet CSV imports of the same transfer (one per wallet) land as
    separate rows with the same ``trade_group_id`` but an identical ``source``.
    """
    by_gid: Dict[str, List[Transaction]] = defaultdict(list)
    for tx in transactions:
        if tx.id in pair_map:
            continue
        if tx.transaction_type != TransactionType.TRANSFER or tx.amount <= 0:
            continue
        gid = tx.on_chain_tx_id or tx.trade_group_id
        if not gid:
            continue
        by_gid[gid].append(tx)

    for group in by_gid.values():
        outs = [
            t
            for t in group
            if t.transfer_direction == "OUT" and t.id not in used_out_ids
        ]
        ins = [
            t
            for t in group
            if t.transfer_direction == "IN" and t.id not in used_in_ids
        ]
        for out_tx in sorted(outs, key=lambda t: t.timestamp):
            best: Transaction | None = None
            best_score = float("inf")
            for in_tx in ins:
                if in_tx.id in used_in_ids:
                    continue
                if in_tx.asset != out_tx.asset:
                    continue
                if not _amounts_compatible(out_tx.amount, in_tx.amount):
                    continue
                score = _candidate_score(out_tx, in_tx)
                if score < best_score:
                    best = in_tx
                    best_score = score
            if best is None:
                continue
            _record_pair(pair_map, out_tx, best)
            used_out_ids.add(out_tx.id)
            used_in_ids.add(best.id)


def _pair_cross_source_legs(
    transactions: List[Transaction],
    pair_map: Dict[str, str],
    used_out_ids: Set[str],
    used_in_ids: Set[str],
) -> None:
    """Pair transfers across different import sources (wallet -> exchange, etc.)."""
    outs = [
        t
        for t in transactions
        if t.id not in pair_map
        and t.transaction_type == TransactionType.TRANSFER
        and t.transfer_direction == "OUT"
        and t.amount > 0
    ]
    ins = [
        t
        for t in transactions
        if t.id not in pair_map
        and t.transaction_type == TransactionType.TRANSFER
        and t.transfer_direction == "IN"
        and t.amount > 0
    ]

    outs.sort(key=lambda t: t.timestamp)

    for out_tx in outs:
        if out_tx.id in used_out_ids:
            continue
        best: Transaction | None = None
        best_score = float("inf")
        for in_tx in ins:
            if in_tx.id in used_in_ids:
                continue
            if in_tx.asset != out_tx.asset:
                continue
            if in_tx.source and out_tx.source and in_tx.source == out_tx.source:
                continue
            if abs(in_tx.timestamp - out_tx.timestamp) > TRANSFER_PAIR_WINDOW:
                continue
            if not _amounts_compatible(out_tx.amount, in_tx.amount):
                continue
            score = _candidate_score(out_tx, in_tx)
            if score < best_score:
                best = in_tx
                best_score = score

        if best is not None:
            _record_pair(pair_map, out_tx, best)
            used_out_ids.add(out_tx.id)
            used_in_ids.add(best.id)


def _pair_exchange_earn_legs(
    transactions: List[Transaction],
    pair_map: Dict[str, str],
    used_out_ids: Set[str],
    used_in_ids: Set[str],
) -> None:
    """Pair earn deposit/withdrawal shuffles on the same exchange (e.g. Crypto.com).

    Cross-source pairing skips same-source legs, but spot ↔ earn moves never leave
    the exchange — they must still net to zero for holdings and cost basis.
    """
    outs = [
        t
        for t in transactions
        if t.id not in pair_map
        and _is_exchange_earn_deposit_out(t)
    ]
    ins = [
        t
        for t in transactions
        if t.id not in pair_map
        and _is_exchange_earn_withdrawal_in(t)
    ]
    outs.sort(key=lambda t: t.timestamp)

    for out_tx in outs:
        if out_tx.id in used_out_ids:
            continue
        best: Transaction | None = None
        best_score = float("inf")
        for in_tx in ins:
            if in_tx.id in used_in_ids:
                continue
            if in_tx.asset != out_tx.asset:
                continue
            if in_tx.source != out_tx.source:
                continue
            if in_tx.timestamp < out_tx.timestamp:
                continue
            if in_tx.timestamp - out_tx.timestamp > EARN_PAIR_WINDOW:
                continue
            if not _amounts_compatible(out_tx.amount, in_tx.amount):
                continue
            score = _candidate_score(out_tx, in_tx)
            if score < best_score:
                best = in_tx
                best_score = score

        if best is not None:
            _record_pair(pair_map, out_tx, best)
            used_out_ids.add(out_tx.id)
            used_in_ids.add(best.id)


def match_transfer_pairs(transactions: List[Transaction]) -> Dict[str, str]:
    """Return ``{transaction_id: pair_id}`` for matched internal transfer legs."""
    pair_map: Dict[str, str] = {}
    used_out_ids: Set[str] = set()
    used_in_ids: Set[str] = set()
    _pair_on_chain_legs(transactions, pair_map, used_out_ids, used_in_ids)
    _pair_exchange_earn_legs(transactions, pair_map, used_out_ids, used_in_ids)
    _pair_cross_source_legs(transactions, pair_map, used_out_ids, used_in_ids)
    return pair_map


def annotate_transfer_pairs(transactions: List[Transaction]) -> List[Transaction]:
    """Return copies of ``transactions`` with ``transfer_pair_id`` populated."""
    pair_map = match_transfer_pairs(transactions)
    if not pair_map:
        return transactions
    annotated: List[Transaction] = []
    for tx in transactions:
        pair_id = pair_map.get(tx.id)
        if pair_id and tx.transfer_pair_id != pair_id:
            annotated.append(tx.model_copy(update={"transfer_pair_id": pair_id}))
        else:
            annotated.append(tx)
    return annotated
