"""Infer the role of a CSV within a platform (transfers vs trades, etc.)."""

from __future__ import annotations

import re
from typing import List, Optional, Set

from .schemas import Transaction, TransactionType

_TRANSFER_ORDER_TYPES = frozenset(
    {
        "deposit",
        "withdrawal",
        "funding",
        "realized_pnl",
        "fee",
        "transfer",
    }
)

_FILENAME_KIND_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"export_transfer", re.I), "transfers"),
    (re.compile(r"export_swap", re.I), "swaps"),
    (re.compile(r"export_defi", re.I), "defi"),
    (re.compile(r"order[-_]history", re.I), "orders"),
    (re.compile(r"transaction[-_]history", re.I), "transactions"),
    (re.compile(r"transfer", re.I), "transfers"),
    (re.compile(r"trade", re.I), "trades"),
    (re.compile(r"funding", re.I), "funding"),
    (re.compile(r"ledger", re.I), "ledger"),
    (re.compile(r"deposit", re.I), "deposits"),
    (re.compile(r"withdraw", re.I), "withdrawals"),
)


def _slug(value: str) -> str:
    return value.strip().lower().replace(" ", "_")


def _kind_from_transactions(transactions: List[Transaction]) -> Optional[str]:
    if not transactions:
        return None

    sources = {_slug(t.source) for t in transactions if t.source}
    order_types: Set[str] = {
        _slug(t.venue_order_type)
        for t in transactions
        if t.venue_order_type
    }

    if "variational" in sources:
        if order_types & _TRANSFER_ORDER_TYPES:
            return "transfers"
        if any(
            t.transaction_type in (TransactionType.BUY, TransactionType.SELL)
            and t.trade_group_id
            for t in transactions
        ):
            return "trades"
        return "transfers" if order_types else "trades"

    actions = {
        _slug(t.venue_order_type or "")
        for t in transactions
        if t.venue_order_type
    }
    if actions & {"swap", "route"}:
        return "swaps"
    if all(t.transaction_type == TransactionType.TRANSFER for t in transactions):
        return "transfers"

    return None


def infer_csv_export_kind(
    filename: str,
    transactions: Optional[List[Transaction]] = None,
) -> Optional[str]:
    """Best-effort export category for distinguishing CSVs from one platform."""
    name = filename.lower()
    for pattern, kind in _FILENAME_KIND_PATTERNS:
        if pattern.search(name):
            return kind

    if transactions:
        return _kind_from_transactions(transactions)

    return None


def export_kind_label(kind: Optional[str]) -> Optional[str]:
    if not kind:
        return None
    labels = {
        "transfers": "Transfers",
        "trades": "Trades",
        "orders": "Order history",
        "transactions": "Transaction history",
        "swaps": "Swaps",
        "defi": "DeFi activity",
        "funding": "Funding",
        "ledger": "Ledger",
        "deposits": "Deposits",
        "withdrawals": "Withdrawals",
    }
    return labels.get(kind, kind.replace("_", " ").title())
