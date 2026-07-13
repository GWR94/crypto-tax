"""Perpetual-futures summary (separate from spot portfolio)."""

from __future__ import annotations

from typing import List

from .schemas import PerpsSummary, Transaction, TransactionType, is_perp_transaction


def _is_perp_fill(tx: Transaction) -> bool:
    return tx.transaction_type in (TransactionType.BUY, TransactionType.SELL) and tx.amount > 0


def _trade_notional(tx: Transaction) -> float:
    if not _is_perp_fill(tx) or tx.fiat_value_at_trigger <= 0:
        return 0.0
    return tx.fiat_value_at_trigger


def build_perps_summary(transactions: List[Transaction]) -> PerpsSummary:
    """Aggregate exchange-reported perp activity."""
    perps = [t for t in transactions if is_perp_transaction(t)]
    if not perps:
        return PerpsSummary()

    closed_pnl = 0.0
    winning = 0
    losing = 0
    for tx in perps:
        if tx.realized_pnl is None:
            continue
        closed_pnl += tx.realized_pnl
        if not _is_perp_fill(tx):
            continue
        if tx.realized_pnl > 0:
            winning += 1
        elif tx.realized_pnl < 0:
            losing += 1

    fills = [t for t in perps if _is_perp_fill(t)]

    return PerpsSummary(
        trade_count=len(fills),
        closed_pnl=round(closed_pnl, 2),
        total_fees=round(sum(t.fee_fiat for t in perps), 2),
        total_notional=round(sum(_trade_notional(t) for t in perps), 2),
        winning_closes=winning,
        losing_closes=losing,
    )
