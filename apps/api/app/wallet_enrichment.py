"""Post-import enrichment for wallet API rows (no USD from chain indexers)."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Dict, List, Set, Tuple

from .evm_chains import EVM_CHAIN_META
from .staking_withdrawals import reclassify_staking_withdrawals
from .historical_prices import historical_usd_prices_for_transactions
from .price_resolver import resolve_prices
from .pricing import PriceStore
from .config import is_stablecoin
from .schemas import Transaction, TransactionType

WALLET_SOURCES: Set[str] = frozenset(
    {"solana", "bitcoin", "cardano", "celestia", *EVM_CHAIN_META.keys()}
)
_EXCHANGE_SOURCES: Set[str] = frozenset(
    {"binance", "kraken", "cryptocom", "exchange", "coinbase"}
)
_CHAIN_INDEXER_SOURCES: Set[str] = frozenset(
    {"bitcoin", "cardano", "celestia", *EVM_CHAIN_META.keys()}
)
_MIN_FIAT_USD = 0.01
_STAKING_MIN_AMOUNT = 1e-6


def _tx_day(timestamp: datetime) -> date:
    if timestamp.tzinfo is None:
        return timestamp.date()
    return timestamp.astimezone(timezone.utc).date()


def _needs_wallet_fiat_estimate(tx: Transaction) -> bool:
    if tx.source not in WALLET_SOURCES or tx.amount <= 0:
        return False
    if tx.fiat_value_at_trigger <= 0:
        return True
    # Chain indexers never include fiat — refresh prior spot estimates.
    return tx.source in _CHAIN_INDEXER_SOURCES


def _staking_withdrawal_group_ids(transactions: List[Transaction]) -> Set[str]:
    """Groups where a reward leg (STAKING) shares a tx with principal (TRANSFER IN)."""
    by_group: Dict[str, List[Transaction]] = defaultdict(list)
    for tx in transactions:
        if tx.trade_group_id:
            by_group[tx.trade_group_id].append(tx)

    ids: Set[str] = set()
    for gid, group in by_group.items():
        has_staking = any(t.transaction_type == TransactionType.STAKING for t in group)
        has_transfer_in = any(
            t.transaction_type == TransactionType.TRANSFER and t.transfer_direction == "IN"
            for t in group
        )
        if has_staking and has_transfer_in:
            ids.add(gid)
    return ids


def infer_swap_cost_basis(
    transactions: List[Transaction],
) -> Tuple[List[Transaction], int]:
    """When a swap group has USD on one leg only, copy it to the other leg."""
    by_group: Dict[str, List[Transaction]] = defaultdict(list)
    for tx in transactions:
        if tx.trade_group_id:
            by_group[tx.trade_group_id].append(tx)

    patches: Dict[str, Transaction] = {}
    updated = 0

    for group in by_group.values():
        buys = [t for t in group if t.transaction_type == TransactionType.BUY]
        sells = [t for t in group if t.transaction_type == TransactionType.SELL]
        if not buys or not sells:
            continue

        sell_fiat = sum(t.fiat_value_at_trigger for t in sells)
        buy_fiat = sum(t.fiat_value_at_trigger for t in buys)

        if sell_fiat >= _MIN_FIAT_USD and buy_fiat < _MIN_FIAT_USD:
            zero_buys = [b for b in buys if b.fiat_value_at_trigger < _MIN_FIAT_USD]
            if not zero_buys:
                continue
            if len(zero_buys) == 1:
                target = zero_buys[0]
                patches[target.id] = target.model_copy(
                    update={
                        "fiat_value_at_trigger": round(sell_fiat, 2),
                        "fiat_currency": target.fiat_currency or "USD",
                    }
                )
                updated += 1
            else:
                total_qty = sum(b.amount for b in zero_buys)
                if total_qty <= 0:
                    continue
                for buy in zero_buys:
                    share = sell_fiat * (buy.amount / total_qty)
                    patches[buy.id] = buy.model_copy(
                        update={
                            "fiat_value_at_trigger": round(share, 2),
                            "fiat_currency": buy.fiat_currency or "USD",
                        }
                    )
                    updated += 1
        elif buy_fiat >= _MIN_FIAT_USD and sell_fiat < _MIN_FIAT_USD:
            zero_sells = [s for s in sells if s.fiat_value_at_trigger < _MIN_FIAT_USD]
            if not zero_sells:
                continue
            if len(zero_sells) == 1:
                target = zero_sells[0]
                patches[target.id] = target.model_copy(
                    update={
                        "fiat_value_at_trigger": round(buy_fiat, 2),
                        "fiat_currency": target.fiat_currency or "USD",
                    }
                )
                updated += 1

    if not patches:
        return transactions, 0

    return (
        [patches.get(tx.id, tx) for tx in transactions],
        updated,
    )


def enrich_imported_fiat_values(
    transactions: List[Transaction],
    *,
    store: PriceStore,
) -> Tuple[List[Transaction], int]:
    """Estimate fiat value for wallet imports using historical USD prices."""
    candidates = [tx for tx in transactions if _needs_wallet_fiat_estimate(tx)]
    if not candidates:
        return transactions, 0

    principal_groups = _staking_withdrawal_group_ids(transactions)
    to_price = [
        (tx.asset, tx.timestamp)
        for tx in candidates
        if not (
            tx.transaction_type == TransactionType.TRANSFER
            and tx.transfer_direction == "IN"
            and tx.trade_group_id in principal_groups
        )
    ]
    historical = historical_usd_prices_for_transactions(to_price)
    spot_fallback = resolve_prices(
        assets={tx.asset for tx in candidates},
        transactions=transactions,
        store=store,
        cost_basis_usd=None,
    )

    candidate_ids = {tx.id for tx in candidates}
    updated = 0
    enriched: List[Transaction] = []
    for tx in transactions:
        if tx.id not in candidate_ids:
            enriched.append(tx)
            continue

        if (
            tx.transaction_type == TransactionType.TRANSFER
            and tx.transfer_direction == "IN"
            and tx.trade_group_id in principal_groups
        ):
            enriched.append(tx)
            continue

        day = _tx_day(tx.timestamp)
        asset_key = _normalize_asset_key(tx.asset)
        quote_usd = historical.get((asset_key, day))
        if quote_usd is None and day >= datetime.now(timezone.utc).date():
            spot = spot_fallback.get(asset_key)
            if spot is None and tx.token_mint:
                spot = spot_fallback.get(tx.token_mint.strip())
            quote_usd = spot.usd if spot and spot.usd > 0 else None

        if quote_usd is None or quote_usd <= 0:
            enriched.append(tx)
            continue

        estimate = round(tx.amount * quote_usd, 2)
        if estimate <= 0:
            enriched.append(tx)
            continue

        enriched.append(
            tx.model_copy(
                update={
                    "fiat_value_at_trigger": estimate,
                    "fiat_currency": "USD",
                }
            )
        )
        updated += 1

    return enriched, updated


def enrich_fee_fiat_values(
    transactions: List[Transaction],
) -> Tuple[List[Transaction], int]:
    """Backfill USD FMV on crypto FEE disposals missing fiat (gas, protocol fees).

    Paying a fee in a crypto asset is a disposal of that asset. Proceeds are the
    fair-market value of the crypto spent; without a price the disposal would
    incorrectly show £0/$0 proceeds and a full capital loss.
    """
    candidates = [
        tx
        for tx in transactions
        if tx.transaction_type == TransactionType.FEE
        and tx.amount > 0
        and tx.fiat_value_at_trigger < _MIN_FIAT_USD
        and not is_stablecoin(tx.asset)
    ]
    if not candidates:
        return transactions, 0

    historical = historical_usd_prices_for_transactions(
        [(tx.asset, tx.timestamp) for tx in candidates]
    )
    from .pricing import DEFAULT_PRICES

    patches: Dict[str, Transaction] = {}
    changed = 0
    for tx in candidates:
        day = _tx_day(tx.timestamp)
        asset_key = _normalize_asset_key(tx.asset)
        unit_usd = historical.get((asset_key, day))
        if unit_usd is None or unit_usd <= 0:
            unit_usd = float(DEFAULT_PRICES.get(asset_key, 0.0) or 0.0)
        if unit_usd <= 0:
            continue
        total_usd = round(tx.amount * unit_usd, 2)
        if total_usd < _MIN_FIAT_USD:
            continue
        patches[tx.id] = tx.model_copy(
            update={
                "fiat_value_at_trigger": total_usd,
                "fiat_currency": tx.fiat_currency or "USD",
            }
        )
        changed += 1

    if not patches:
        return transactions, 0
    return [patches.get(tx.id, tx) for tx in transactions], changed


def enrich_staking_fiat_values(
    transactions: List[Transaction],
    *,
    store: PriceStore,
) -> Tuple[List[Transaction], int]:
    """Estimate USD value for exchange staking rows missing fiat in CSV imports."""
    candidates = [
        tx
        for tx in transactions
        if tx.transaction_type == TransactionType.STAKING
        and tx.amount >= _STAKING_MIN_AMOUNT
        and tx.fiat_value_at_trigger <= 0
        and (tx.source or "") in _EXCHANGE_SOURCES
    ]
    if not candidates:
        return transactions, 0

    historical = historical_usd_prices_for_transactions(
        [(tx.asset, tx.timestamp) for tx in candidates]
    )
    spot_fallback = resolve_prices(
        assets={tx.asset for tx in candidates},
        transactions=transactions,
        store=store,
        cost_basis_usd=None,
    )

    candidate_ids = {tx.id for tx in candidates}
    updated = 0
    enriched: List[Transaction] = []
    for tx in transactions:
        if tx.id not in candidate_ids:
            enriched.append(tx)
            continue

        day = _tx_day(tx.timestamp)
        asset_key = _normalize_asset_key(tx.asset)
        quote_usd = historical.get((asset_key, day))
        if quote_usd is None and day >= datetime.now(timezone.utc).date():
            spot = spot_fallback.get(asset_key)
            quote_usd = spot.usd if spot and spot.usd > 0 else None

        if quote_usd is None or quote_usd <= 0:
            enriched.append(tx)
            continue

        estimate = round(tx.amount * quote_usd, 2)
        if estimate <= 0:
            enriched.append(tx)
            continue

        enriched.append(
            tx.model_copy(
                update={
                    "fiat_value_at_trigger": estimate,
                    "fiat_currency": "USD",
                }
            )
        )
        updated += 1

    return enriched, updated


def _normalize_asset_key(asset: str) -> str:
    return asset.strip().upper()


def _should_copy_pair_fiat(recipient: Transaction, donor: Transaction) -> bool:
    if recipient.id == donor.id:
        return False
    if donor.fiat_value_at_trigger < _MIN_FIAT_USD:
        return False
    if donor.source in _CHAIN_INDEXER_SOURCES:
        return False
    if recipient.source not in _CHAIN_INDEXER_SOURCES:
        return recipient.fiat_value_at_trigger < _MIN_FIAT_USD
    return True


def copy_fiat_from_transfer_pairs(
    transactions: List[Transaction],
) -> Tuple[List[Transaction], int]:
    """Copy fiat from an exchange leg onto a paired chain-indexer leg."""
    by_pair: Dict[str, List[Transaction]] = defaultdict(list)
    for tx in transactions:
        if tx.transfer_pair_id:
            by_pair[tx.transfer_pair_id].append(tx)

    patches: Dict[str, Transaction] = {}
    updated = 0

    for group in by_pair.values():
        if len(group) < 2:
            continue
        donors = [t for t in group if t.fiat_value_at_trigger >= _MIN_FIAT_USD]
        if not donors:
            continue
        donor = max(
            donors,
            key=lambda t: (
                t.source not in _CHAIN_INDEXER_SOURCES,
                t.fiat_value_at_trigger,
            ),
        )
        for recipient in group:
            if not _should_copy_pair_fiat(recipient, donor):
                continue
            ratio = recipient.amount / donor.amount if donor.amount > 0 else 1.0
            patches[recipient.id] = recipient.model_copy(
                update={
                    "fiat_value_at_trigger": round(
                        donor.fiat_value_at_trigger * ratio, 2
                    ),
                    "fiat_currency": donor.fiat_currency
                    or recipient.fiat_currency
                    or "USD",
                }
            )
            updated += 1

    if not patches:
        return transactions, 0

    return [patches.get(tx.id, tx) for tx in transactions], updated


def backfill_wallet_cost_basis(
    transactions: List[Transaction],
    *,
    store: PriceStore,
) -> Tuple[List[Transaction], int]:
    """Infer swap notionals, historical prices, then paired transfer legs."""
    txs, stake_n = reclassify_staking_withdrawals(transactions)
    txs, swap_n = infer_swap_cost_basis(txs)
    txs, enrich_n = enrich_imported_fiat_values(txs, store=store)
    txs, fee_n = enrich_fee_fiat_values(txs)
    txs, staking_n = enrich_staking_fiat_values(txs, store=store)
    txs, pair_n = copy_fiat_from_transfer_pairs(txs)
    return txs, stake_n + swap_n + enrich_n + fee_n + staking_n + pair_n
