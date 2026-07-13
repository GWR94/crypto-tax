"""Reclassify EVM staking withdrawals mis-parsed as swaps."""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Optional, Set, Tuple

from .evm_chains import EVM_CHAIN_META
from .schemas import Transaction, TransactionType

EVM_SOURCES = frozenset(EVM_CHAIN_META.keys())
_AMOUNT_REL_TOL = 0.005


def is_unstake_reward(tx: Transaction, group: List[Transaction]) -> bool:
    """Staking reward leg that shares a tx with principal returned from a stake."""
    if tx.transaction_type != TransactionType.STAKING:
        return False
    if not tx.trade_group_id:
        return False
    return any(
        other.transaction_type == TransactionType.TRANSFER
        and other.transfer_direction == "IN"
        and other.id != tx.id
        for other in group
    )


def unstake_group_ids(transactions: List[Transaction]) -> Set[str]:
    """trade_group_id values that pair a principal return with a reward leg."""
    by_group: Dict[str, List[Transaction]] = defaultdict(list)
    for tx in transactions:
        if tx.trade_group_id:
            by_group[tx.trade_group_id].append(tx)

    ids: Set[str] = set()
    for gid, group in by_group.items():
        has_principal = any(
            t.transaction_type == TransactionType.TRANSFER and t.transfer_direction == "IN"
            for t in group
        )
        has_reward = any(t.transaction_type == TransactionType.STAKING for t in group)
        if has_principal and has_reward:
            ids.add(gid)
    return ids


def _amount_key(asset: str, amount: float, token_mint: Optional[str]) -> tuple:
    return (asset.strip().upper(), round(amount, 8), (token_mint or "").lower())


def _matches_amount(a: float, b: float) -> bool:
    if a <= 0 or b <= 0:
        return False
    return abs(a - b) / max(a, b) <= _AMOUNT_REL_TOL


def _find_prior_stake_out(
    inbound: Transaction, outs_by_key: Dict[tuple, List[Transaction]]
) -> Optional[Transaction]:
    key = _amount_key(inbound.asset, inbound.amount, inbound.token_mint)
    candidates = [
        o
        for o in outs_by_key.get(key, [])
        if o.timestamp < inbound.timestamp
        and (o.source or "") == (inbound.source or "")
        and _matches_amount(o.amount, inbound.amount)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda o: o.timestamp)


def reclassify_staking_withdrawals(
    transactions: List[Transaction],
) -> Tuple[List[Transaction], int]:
    """Turn swap-shaped staking exits into TRANSFER IN (principal) + STAKING (rewards)."""
    outs_by_key: Dict[tuple, List[Transaction]] = defaultdict(list)
    for tx in transactions:
        if (tx.source or "") not in EVM_SOURCES:
            continue
        if tx.transaction_type != TransactionType.TRANSFER:
            continue
        if tx.transfer_direction != "OUT":
            continue
        outs_by_key[_amount_key(tx.asset, tx.amount, tx.token_mint)].append(tx)

    by_group: Dict[str, List[Transaction]] = defaultdict(list)
    for tx in transactions:
        if tx.trade_group_id and (tx.source or "") in EVM_SOURCES:
            by_group[tx.trade_group_id].append(tx)

    patches: Dict[str, Transaction] = {}
    changed = 0

    for group in by_group.values():
        sells = [t for t in group if t.transaction_type == TransactionType.SELL]
        if sells:
            continue

        receipt_legs = [
            t
            for t in group
            if t.transaction_type in {TransactionType.BUY, TransactionType.TRANSFER}
            and (
                t.transaction_type == TransactionType.BUY
                or t.transfer_direction == "IN"
            )
        ]
        if len(receipt_legs) < 1:
            continue

        principal_legs: List[Transaction] = []
        for leg in receipt_legs:
            prior_out = _find_prior_stake_out(leg, outs_by_key)
            if prior_out:
                principal_legs.append(leg)

        principal_ids = {leg.id for leg in principal_legs}
        if not principal_ids and len(receipt_legs) < 2:
            continue

        if not principal_legs:
            # No matching prior stake-out; largest inbound leg is likely principal.
            principal_legs = [max(receipt_legs, key=lambda t: t.amount)]
            principal_ids = {principal_legs[0].id}

        principal_asset = principal_legs[0].asset
        prior_out = _find_prior_stake_out(principal_legs[0], outs_by_key)
        counterparty = principal_legs[0].counterparty_address or (
            prior_out.counterparty_address if prior_out else None
        )

        for leg in receipt_legs:
            if leg.id in principal_ids:
                prior_out = _find_prior_stake_out(leg, outs_by_key)
                cp = leg.counterparty_address or (
                    prior_out.counterparty_address if prior_out else None
                )
                if leg.transaction_type != TransactionType.TRANSFER:
                    patches[leg.id] = leg.model_copy(
                        update={
                            "transaction_type": TransactionType.TRANSFER,
                            "transfer_direction": "IN",
                            "fiat_value_at_trigger": 0.0,
                            "fiat_currency": None,
                            "counter_asset": None,
                            "counterparty_address": cp,
                        }
                    )
                    changed += 1
                elif leg.fiat_value_at_trigger > 0 or not leg.counterparty_address:
                    updates: dict = {}
                    if leg.fiat_value_at_trigger > 0:
                        updates["fiat_value_at_trigger"] = 0.0
                        updates["fiat_currency"] = None
                    if cp and not leg.counterparty_address:
                        updates["counterparty_address"] = cp
                    if updates:
                        patches[leg.id] = leg.model_copy(update=updates)
                        changed += 1
            else:
                if leg.transaction_type == TransactionType.STAKING:
                    if principal_asset and not leg.counter_asset:
                        patches[leg.id] = leg.model_copy(
                            update={
                                "counter_asset": principal_asset,
                                "counterparty_address": counterparty,
                            }
                        )
                        changed += 1
                    continue
                patches[leg.id] = leg.model_copy(
                    update={
                        "transaction_type": TransactionType.STAKING,
                        "transfer_direction": None,
                        "counter_asset": principal_asset,
                        "counterparty_address": counterparty,
                    }
                )
                changed += 1

        for leg in group:
            if leg.transaction_type != TransactionType.STAKING:
                continue
            if not principal_asset:
                continue
            updates: dict = {}
            if not leg.counter_asset:
                updates["counter_asset"] = principal_asset
            if counterparty and not leg.counterparty_address:
                updates["counterparty_address"] = counterparty
            if updates:
                patches[leg.id] = leg.model_copy(update=updates)
                changed += 1

        if counterparty:
            for leg in group:
                if (
                    leg.transaction_type == TransactionType.TRANSFER
                    and leg.transfer_direction == "IN"
                    and leg.id in principal_ids
                    and not leg.counterparty_address
                ):
                    patches[leg.id] = leg.model_copy(
                        update={"counterparty_address": counterparty}
                    )
                    changed += 1
                prior_out = (
                    _find_prior_stake_out(leg, outs_by_key)
                    if leg.id in principal_ids
                    else None
                )
                if prior_out and not prior_out.counterparty_address:
                    cp_out = prior_out.counterparty_address or counterparty
                    if cp_out:
                        patches[prior_out.id] = prior_out.model_copy(
                            update={"counterparty_address": cp_out}
                        )
                        changed += 1

    if not patches:
        return transactions, 0

    return [patches.get(tx.id, tx) for tx in transactions], changed
