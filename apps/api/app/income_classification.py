"""Fix misclassified staking rewards and Solana airdrop claims."""

from __future__ import annotations

from collections import defaultdict
from datetime import timezone
from typing import Dict, List, Tuple

from .config import is_stablecoin
from .ledger_filters import DUST_FIAT_VALUE
from .schemas import INCOME_TYPES, Transaction, TransactionType

_CORE_SOL_OUT = frozenset({"SOL", "WSOL", "JITOSOL", "MSOL", "BSOL"})
# Typical claim fee on Solana — real DEX buys spend far more SOL than this.
_AIRDROP_CLAIM_MAX_SOL = 0.015
# Tiny SOL leg paired with a token receipt — typical claim / airdrop pattern.
_AIRDROP_CLAIM_MAX_OUT_USD = 10.0


def _sol_out_usd(sell: Transaction) -> float:
    """Estimate USD value of the outbound SOL leg for airdrop-vs-trade checks."""
    if sell.fiat_value_at_trigger > 0:
        return sell.fiat_value_at_trigger
    if sell.asset.upper() not in _CORE_SOL_OUT or sell.amount <= 0:
        return 0.0
    from .historical_prices import historical_usd_prices_for_transactions

    day = (
        sell.timestamp.astimezone(timezone.utc).date()
        if sell.timestamp.tzinfo
        else sell.timestamp.date()
    )
    prices = historical_usd_prices_for_transactions([(sell.asset.upper(), sell.timestamp)])
    sol_usd = prices.get((sell.asset.upper(), day))
    if sol_usd and sol_usd > 0:
        return sell.amount * sol_usd
    return 0.0


def _is_airdrop_claim_group(group: List[Transaction]) -> bool:
    """One token in + dust SOL out (claim fee), not a real swap."""
    if not group or len(group) > 3:
        return False
    buys = [t for t in group if t.transaction_type == TransactionType.BUY]
    sells = [t for t in group if t.transaction_type == TransactionType.SELL]
    if len(buys) != 1 or len(sells) != 1:
        return False
    buy, sell = buys[0], sells[0]
    if sell.asset.upper() not in _CORE_SOL_OUT:
        return False
    if is_stablecoin(buy.asset) or buy.asset.upper() in _CORE_SOL_OUT:
        return False
    if sell.amount > _AIRDROP_CLAIM_MAX_SOL:
        return False
    sol_usd = _sol_out_usd(sell)
    if sol_usd > _AIRDROP_CLAIM_MAX_OUT_USD:
        return False
    # Without a priced SOL leg we cannot distinguish a claim fee from a swap.
    if sol_usd <= 0 and sell.fiat_value_at_trigger <= 0:
        return False
    return True


def _is_misclassified_airdrop_group(group: List[Transaction]) -> bool:
    """Previously labelled airdrop but SOL out is too large for a claim fee."""
    if not group or len(group) > 3:
        return False
    airdrops = [t for t in group if t.transaction_type == TransactionType.AIRDROP]
    sol_legs = [
        t
        for t in group
        if t.transaction_type == TransactionType.FEE
        and t.asset.upper() in _CORE_SOL_OUT
    ]
    if len(airdrops) != 1 or len(sol_legs) != 1:
        return False
    token, sol_leg = airdrops[0], sol_legs[0]
    if is_stablecoin(token.asset) or token.asset.upper() in _CORE_SOL_OUT:
        return False
    return (
        sol_leg.amount > _AIRDROP_CLAIM_MAX_SOL
        or _sol_out_usd(sol_leg) > _AIRDROP_CLAIM_MAX_OUT_USD
    )


def _reclassify_solana_airdrop_group(group: List[Transaction]) -> List[Transaction]:
    buy = next(t for t in group if t.transaction_type == TransactionType.BUY)
    sell = next(t for t in group if t.transaction_type == TransactionType.SELL)
    out: List[Transaction] = [
        buy.model_copy(
            update={
                "transaction_type": TransactionType.AIRDROP,
                "counter_asset": None,
            }
        ),
    ]
    if sell.amount > 0:
        out.append(
            sell.model_copy(
                update={
                    "transaction_type": TransactionType.FEE,
                    "counter_asset": None,
                }
            )
        )
    group_ids = {buy.id, sell.id}
    for tx in group:
        if tx.id not in group_ids:
            out.append(tx)
    return out


def _revert_misclassified_airdrop_group(group: List[Transaction]) -> List[Transaction]:
    """Restore a DEX buy mislabelled as airdrop + fee."""
    token = next(t for t in group if t.transaction_type == TransactionType.AIRDROP)
    sol_leg = next(
        t
        for t in group
        if t.transaction_type == TransactionType.FEE
        and t.asset.upper() in _CORE_SOL_OUT
    )
    sol_usd = _sol_out_usd(sol_leg)
    token_fiat = token.fiat_value_at_trigger
    if token_fiat <= 0 and sol_usd > 0:
        token_fiat = round(sol_usd, 2)

    out: List[Transaction] = [
        token.model_copy(
            update={
                "transaction_type": TransactionType.BUY,
                "counter_asset": sol_leg.asset.upper(),
                "counter_amount": sol_leg.amount,
                "fiat_value_at_trigger": token_fiat,
                "fiat_currency": token.fiat_currency or "USD",
            }
        ),
        sol_leg.model_copy(
            update={
                "transaction_type": TransactionType.SELL,
                "counter_asset": token.asset.upper(),
                "counter_amount": token.amount,
                "fiat_value_at_trigger": round(sol_usd, 2)
                if sol_usd > 0
                else sol_leg.fiat_value_at_trigger,
                "fiat_currency": sol_leg.fiat_currency or "USD",
            }
        ),
    ]
    patched_ids = {token.id, sol_leg.id}
    for tx in group:
        if tx.id not in patched_ids:
            out.append(tx)
    return out


def enrich_income_fiat_values(
    transactions: List[Transaction],
) -> Tuple[List[Transaction], int]:
    """Backfill USD FMV on income rows missing fiat (airdrops, staking rewards)."""
    candidates = [
        tx
        for tx in transactions
        if tx.transaction_type in INCOME_TYPES
        and tx.amount > 0
        and tx.fiat_value_at_trigger <= DUST_FIAT_VALUE
    ]
    if not candidates:
        return transactions, 0

    from .historical_prices import _as_utc_day, historical_usd_prices_for_transactions
    from .price_resolver import _normalize_asset
    from .pricing import DEFAULT_PRICES

    prices = historical_usd_prices_for_transactions(
        [(tx.asset, tx.timestamp) for tx in candidates]
    )

    patches: Dict[str, Transaction] = {}
    changed = 0
    for tx in candidates:
        day = _as_utc_day(tx.timestamp)
        key = (_normalize_asset(tx.asset), day)
        unit_usd = prices.get(key) or DEFAULT_PRICES.get(tx.asset.strip().upper(), 0.0)
        if unit_usd <= 0:
            continue
        total_usd = round(tx.amount * unit_usd, 2)
        if total_usd <= DUST_FIAT_VALUE:
            continue
        patches[tx.id] = tx.model_copy(
            update={
                "fiat_value_at_trigger": total_usd,
                "fiat_currency": "USD",
            }
        )
        changed += 1

    if not patches:
        return transactions, 0
    return [patches.get(tx.id, tx) for tx in transactions], changed


def reclassify_income_events(
    transactions: List[Transaction],
) -> Tuple[List[Transaction], int]:
    """Correct Crypto.com earn interest and Solana airdrop claim labels."""
    patches: Dict[str, Transaction] = {}
    changed = 0

    for tx in transactions:
        if (
            (tx.source or "") == "cryptocom"
            and tx.transaction_type == TransactionType.AIRDROP
            and "crypto_earn_interest_paid" in tx.id
        ):
            patches[tx.id] = tx.model_copy(
                update={"transaction_type": TransactionType.STAKING}
            )
            changed += 1

    by_group: Dict[str, List[Transaction]] = defaultdict(list)
    for tx in transactions:
        if (tx.source or "") == "solana" and tx.trade_group_id:
            by_group[tx.trade_group_id].append(tx)

    for group in by_group.values():
        if _is_misclassified_airdrop_group(group):
            for updated in _revert_misclassified_airdrop_group(group):
                orig = next(t for t in group if t.id == updated.id)
                if (
                    updated.transaction_type != orig.transaction_type
                    or updated.counter_asset != orig.counter_asset
                    or updated.fiat_value_at_trigger != orig.fiat_value_at_trigger
                ):
                    patches[updated.id] = updated
                    changed += 1
            continue
        if not _is_airdrop_claim_group(group):
            continue
        for updated in _reclassify_solana_airdrop_group(group):
            orig = next(t for t in group if t.id == updated.id)
            if (
                updated.transaction_type != orig.transaction_type
                or updated.counter_asset != orig.counter_asset
            ):
                patches[updated.id] = updated
                changed += 1

    if not patches:
        return transactions, 0

    return [patches.get(tx.id, tx) for tx in transactions], changed
