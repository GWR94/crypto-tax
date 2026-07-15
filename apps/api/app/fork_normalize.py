"""Hard-fork acquisition normalization.

When a configured fork date is reached and the ledger shows a net holding of the
parent asset, synthesize an acquisition of the forked coin (e.g. ETH → ETHW).
"""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Dict, List, Optional, Tuple

from .config import HARD_FORK_BASIS_POLICY, HARD_FORK_EVENTS
from .schemas import (
    ACQUISITION_TYPES,
    DISPOSAL_TYPES,
    Transaction,
    TransactionType,
    is_perp_transaction,
)

EVENT_HARD_FORK = "hard_fork"
_EPS = 1e-9


def _parse_fork_date(raw: object) -> date:
    text = str(raw).strip()
    return date.fromisoformat(text[:10])


def _net_parent_qty_before(
    transactions: List[Transaction], parent: str, fork_day: date
) -> float:
    """Net quantity of ``parent`` acquired on/before the fork date."""
    parent = parent.strip().upper()
    net = 0.0
    for tx in transactions:
        if is_perp_transaction(tx):
            continue
        if tx.asset.strip().upper() != parent:
            continue
        day = (
            tx.timestamp.astimezone(timezone.utc).date()
            if tx.timestamp.tzinfo
            else tx.timestamp.date()
        )
        if day > fork_day:
            continue
        if tx.transaction_type in ACQUISITION_TYPES:
            net += tx.amount
        elif tx.transaction_type in DISPOSAL_TYPES:
            net -= tx.amount
        elif tx.transaction_type == TransactionType.TRANSFER:
            if tx.transfer_direction == "IN":
                net += tx.amount
            elif tx.transfer_direction == "OUT":
                net -= tx.amount
    return net


def _fork_fmv_usd(asset: str, qty: float, when: datetime) -> float:
    if qty <= 0:
        return 0.0
    from .historical_prices import historical_usd_prices_for_transactions
    from .pricing import DEFAULT_PRICES
    from .wallet_enrichment import _normalize_asset_key, _tx_day

    day = _tx_day(when)
    key = _normalize_asset_key(asset)
    historical = historical_usd_prices_for_transactions([(asset, when)])
    unit = historical.get((key, day))
    if unit is None or unit <= 0:
        unit = float(DEFAULT_PRICES.get(key, 0.0) or 0.0)
    if unit <= 0:
        return 0.0
    return round(qty * unit, 2)


def normalize_hard_forks(
    transactions: List[Transaction],
    *,
    basis_policy: Optional[str] = None,
    fork_events: Optional[Dict[str, dict]] = None,
) -> Tuple[List[Transaction], int]:
    """Append synthetic hard-fork BUY rows for configured chain splits."""
    policy = (basis_policy or HARD_FORK_BASIS_POLICY).strip().lower()
    events = fork_events if fork_events is not None else HARD_FORK_EVENTS
    if not events:
        return transactions, 0

    existing_ids = {t.id for t in transactions}
    extra: List[Transaction] = []

    for fork_asset, meta in events.items():
        fork_asset = str(fork_asset).strip().upper()
        parent = str(meta.get("parent") or "").strip().upper()
        if not parent:
            continue
        fork_day = _parse_fork_date(meta.get("date"))
        ratio = float(meta.get("ratio") or 1.0)

        # Already booked (synthetic or user-imported with subtype).
        if any(
            t.asset.strip().upper() == fork_asset
            and (
                t.event_subtype == EVENT_HARD_FORK
                or t.id.startswith(f"hard-fork-{fork_asset.lower()}-")
            )
            for t in transactions
        ):
            continue

        net_parent = _net_parent_qty_before(transactions, parent, fork_day)
        if net_parent <= _EPS:
            continue

        qty = round(net_parent * ratio, 8)
        if qty <= _EPS:
            continue

        when = datetime.combine(fork_day, time(0, 0), tzinfo=timezone.utc)
        value = 0.0 if policy == "zero" else _fork_fmv_usd(fork_asset, qty, when)
        fork_id = f"hard-fork-{fork_asset.lower()}-{fork_day.isoformat()}"
        if fork_id in existing_ids:
            continue

        extra.append(
            Transaction(
                id=fork_id,
                timestamp=when,
                asset=fork_asset,
                transaction_type=TransactionType.BUY,
                amount=qty,
                fiat_value_at_trigger=value,
                fee_fiat=0.0,
                fiat_currency="USD" if value > 0 else None,
                source="hard_fork",
                event_subtype=EVENT_HARD_FORK,
                parent_asset=parent,
            )
        )

    if not extra:
        return transactions, 0

    merged = list(transactions) + extra
    merged.sort(key=lambda t: (t.timestamp, t.id))
    return merged, len(extra)
