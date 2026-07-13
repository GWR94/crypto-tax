"""Normalize Solana liquid-staking (mSOL, JitoSOL, bSOL) wallet imports.

Marinade/Jito/Blaze unstake flows are often parsed as duplicate TRANSFER +
SELL legs, dual-SELL swap groups (dust SOL outbound), and miss the inbound SOL
receipt. This module:

1. Drops LST TRANSFER rows that duplicate a same-size SELL (pool double-count).
2. Reclassifies unstake groups: dust SOL SELL → FEE; inbound SOL → BUY.
3. Optionally books exchange-rate / yield uplift as STAKING income (configurable),
   shrinking the companion SOL BUY to principal so lots are not double-counted.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timezone
from typing import Dict, List, Optional, Set, Tuple

from .config import LIQUID_STAKING_YIELD_AS_INCOME, REPORTING_CURRENCY
from .fx import fx
from .schemas import Transaction, TransactionType

# Liquid-staking receipt tokens (not WSOL — that is native wrapped SOL).
LST_ASSETS = frozenset({"MSOL", "JITOSOL", "BSOL"})
SOL_ASSETS = frozenset({"SOL", "WSOL"})

_TIME_WINDOW_SEC = 3600
_UNSTAKE_LINK_SEC = 900
_AMOUNT_REL_TOL = 0.02
_DUST_SOL_FIAT_RATIO = 0.08
_DUST_SOL_MAX_USD = 15.0
_MIN_INCOME_SOL = 1e-6


def _sym(asset: str) -> str:
    return asset.strip().upper()


def _is_lst(asset: str) -> bool:
    return _sym(asset) in LST_ASSETS


def _is_sol(asset: str) -> bool:
    return _sym(asset) in SOL_ASSETS


def _amounts_close(a: float, b: float, rel_tol: float = _AMOUNT_REL_TOL) -> bool:
    if a <= 0 or b <= 0:
        return False
    return abs(a - b) / max(a, b) <= rel_tol


def _time_close(a: Transaction, b: Transaction, window_sec: int) -> bool:
    return abs(a.timestamp.timestamp() - b.timestamp.timestamp()) <= window_sec


def _sig_prefix(tx: Transaction) -> str:
    raw = tx.on_chain_tx_id or tx.trade_group_id or ""
    return str(raw)[:16]


def _group_by_id(transactions: List[Transaction]) -> Dict[str, List[Transaction]]:
    groups: Dict[str, List[Transaction]] = defaultdict(list)
    for tx in transactions:
        if tx.trade_group_id:
            groups[tx.trade_group_id].append(tx)
    return groups


def collapse_lst_transfer_sell_duplicates(
    transactions: List[Transaction],
) -> Tuple[List[Transaction], int]:
    """Drop LST TRANSFER OUT rows that duplicate a nearby SELL of the same size.

    Inbound TRANSFER IN before an unstake SELL is kept — it often represents
    mSOL returning to the wallet before the swap is netted into the SELL leg.
    """
    lst_sells = [
        tx
        for tx in transactions
        if tx.source == "solana"
        and tx.transaction_type == TransactionType.SELL
        and _is_lst(tx.asset)
    ]
    if not lst_sells:
        return transactions, 0

    drop_ids: Set[str] = set()
    for sell in lst_sells:
        for tx in transactions:
            if tx.id in drop_ids or tx.id == sell.id:
                continue
            if tx.source != "solana":
                continue
            if tx.transaction_type != TransactionType.TRANSFER:
                continue
            if tx.transfer_direction != "OUT":
                continue
            if not _is_lst(tx.asset) or _sym(tx.asset) != _sym(sell.asset):
                continue
            if not _amounts_close(tx.amount, sell.amount):
                continue
            same_sig = (
                sell.trade_group_id
                and tx.trade_group_id
                and sell.trade_group_id == tx.trade_group_id
            )
            same_sig_prefix = (
                _sig_prefix(sell) and _sig_prefix(tx) and _sig_prefix(sell) == _sig_prefix(tx)
            )
            if same_sig or same_sig_prefix or _time_close(tx, sell, _TIME_WINDOW_SEC):
                drop_ids.add(tx.id)

    if not drop_ids:
        return transactions, 0
    return [tx for tx in transactions if tx.id not in drop_ids], len(drop_ids)


def _sol_out_for_stake_group(
    transactions: List[Transaction], trade_group_id: Optional[str]
) -> float:
    if not trade_group_id:
        return 0.0
    return sum(
        g.amount
        for g in transactions
        if g.trade_group_id == trade_group_id
        and g.transaction_type == TransactionType.SELL
        and _is_sol(g.asset)
    )


def _build_lst_buy_sol_deposited_cache(
    transactions: List[Transaction],
) -> Dict[str, float]:
    """Precompute SOL deposited per LST buy (one batched price fetch)."""
    buys = [
        tx
        for tx in transactions
        if tx.source == "solana"
        and tx.transaction_type == TransactionType.BUY
        and _is_lst(tx.asset)
    ]
    if not buys:
        return {}

    infer_buys = [
        tx
        for tx in buys
        if _sol_out_for_stake_group(transactions, tx.trade_group_id) <= 0
        and tx.fiat_value_at_trigger > 0
    ]
    sol_usd_by_day: Dict[tuple[str, date], float] = {}
    if infer_buys:
        from .historical_prices import historical_usd_prices_for_transactions

        sol_usd_by_day = historical_usd_prices_for_transactions(
            [("SOL", tx.timestamp) for tx in infer_buys]
        )

    cache: Dict[str, float] = {}
    for tx in buys:
        sol_out = _sol_out_for_stake_group(transactions, tx.trade_group_id)
        if sol_out > 0:
            cache[tx.id] = sol_out
            continue
        if not infer_buys:
            continue
        if tx.fiat_value_at_trigger <= 0:
            continue
        day = (
            tx.timestamp.astimezone(timezone.utc).date()
            if tx.timestamp.tzinfo
            else tx.timestamp.date()
        )
        sol_usd = sol_usd_by_day.get(("SOL", day))
        if sol_usd and sol_usd > 0:
            cache[tx.id] = tx.fiat_value_at_trigger / sol_usd
    return cache


def _fifo_sol_deposited_for_lst_sell(
    transactions: List[Transaction],
    lst_sell: Transaction,
    deposit_cache: Dict[str, float],
) -> Optional[float]:
    """FIFO SOL deposited from prior stake swaps consumed by this LST sell."""
    asset = _sym(lst_sell.asset)
    lots: List[List[float]] = []

    ordered = sorted(
        (
            t
            for t in transactions
            if t.source == "solana"
            and _sym(t.asset) == asset
            and t.transaction_type in (TransactionType.BUY, TransactionType.SELL)
            and t.timestamp <= lst_sell.timestamp
        ),
        key=lambda t: (t.timestamp, t.id),
    )

    for tx in ordered:
        if tx.transaction_type == TransactionType.BUY:
            sol_dep = deposit_cache.get(tx.id, 0.0)
            if sol_dep > 0:
                lots.append([tx.amount, sol_dep])
            continue

        remaining = tx.amount
        sol_for_sell = 0.0
        while remaining > 1e-12 and lots:
            lot_lst, lot_sol = lots[0]
            if lot_lst <= 1e-12:
                lots.pop(0)
                continue
            take = min(remaining, lot_lst)
            fraction = take / lot_lst
            sol_for_sell += lot_sol * fraction
            lots[0][0] -= take
            lots[0][1] -= lot_sol * fraction
            remaining -= take
            if lots[0][0] <= 1e-12:
                lots.pop(0)

        if tx.id == lst_sell.id:
            return sol_for_sell if sol_for_sell > 0 else None

    return None


def _stake_sol_per_lst(
    transactions: List[Transaction], lst_asset: str, before: Transaction
) -> Optional[Tuple[float, float]]:
    """Return (lst_qty, sol_deposited) from the nearest prior SOL→LST stake swap."""
    candidates: List[Tuple[float, float, float]] = []
    target = _sym(lst_asset)

    for tx in transactions:
        if tx.timestamp >= before.timestamp:
            continue
        if tx.source != "solana":
            continue
        if tx.transaction_type != TransactionType.BUY or not _is_lst(tx.asset):
            continue
        if _sym(tx.asset) != target:
            continue
        if not tx.trade_group_id:
            continue
        group = [g for g in transactions if g.trade_group_id == tx.trade_group_id]
        sol_out = sum(
            g.amount
            for g in group
            if g.transaction_type == TransactionType.SELL and _is_sol(g.asset)
        )
        if sol_out <= 0:
            continue
        candidates.append((tx.timestamp.timestamp(), tx.amount, sol_out))

    if not candidates:
        return None
    _, lst_qty, sol_out = max(candidates, key=lambda row: row[0])
    return lst_qty, sol_out


def _reclassify_sol_leg(
    sol_tx: Transaction, *, as_fee: bool, counter: Optional[str]
) -> Transaction:
    if as_fee:
        return sol_tx.model_copy(
            update={
                "transaction_type": TransactionType.FEE,
                "counter_asset": None,
                "transfer_direction": None,
            }
        )
    return sol_tx.model_copy(
        update={
            "transaction_type": TransactionType.BUY,
            "counter_asset": counter,
            "transfer_direction": None,
        }
    )


def _reclassify_transfer_in_as_buy(
    tx: Transaction, *, fiat: float, counter: str, trade_group_id: Optional[str]
) -> Transaction:
    return tx.model_copy(
        update={
            "transaction_type": TransactionType.BUY,
            "transfer_direction": None,
            "fiat_value_at_trigger": round(max(0.0, fiat), 2),
            "fiat_currency": tx.fiat_currency or "USD",
            "counter_asset": counter,
            "trade_group_id": trade_group_id or tx.trade_group_id,
        }
    )


def reclassify_lst_unstake_swaps(
    transactions: List[Transaction],
) -> Tuple[List[Transaction], int]:
    """Fix unstake groups: dust SOL SELL→FEE, link inbound SOL TRANSFER→BUY."""
    patches: Dict[str, Transaction] = {}
    drop_ids: Set[str] = set()
    changed = 0
    by_gid = _group_by_id(transactions)

    for gid, group in by_gid.items():
        lst_sells = [
            t
            for t in group
            if t.transaction_type == TransactionType.SELL and _is_lst(t.asset)
        ]
        if not lst_sells:
            continue

        lst_sell = max(lst_sells, key=lambda t: t.fiat_value_at_trigger)
        sol_sells = [
            t
            for t in group
            if t.transaction_type == TransactionType.SELL and _is_sol(t.asset)
        ]
        has_buy_sol = any(
            t.transaction_type == TransactionType.BUY and _is_sol(t.asset) for t in group
        )

        for sol_tx in sol_sells:
            dust = sol_tx.fiat_value_at_trigger <= _DUST_SOL_MAX_USD and (
                lst_sell.fiat_value_at_trigger <= 0
                or sol_tx.fiat_value_at_trigger
                <= lst_sell.fiat_value_at_trigger * _DUST_SOL_FIAT_RATIO
            )
            if dust:
                patches[sol_tx.id] = _reclassify_sol_leg(
                    sol_tx, as_fee=True, counter=None
                )
                changed += 1
            elif not has_buy_sol:
                patches[sol_tx.id] = _reclassify_sol_leg(
                    sol_tx,
                    as_fee=False,
                    counter=_sym(lst_sell.asset),
                )
                changed += 1
                has_buy_sol = True

    # Link orphan TRANSFER IN SOL after an LST SELL (receipt outside swap group).
    lst_sells_all = [
        tx
        for tx in transactions
        if tx.source == "solana"
        and tx.transaction_type == TransactionType.SELL
        and _is_lst(tx.asset)
        and tx.id not in patches
    ]
    used_transfer_ids: Set[str] = set()

    for sell in lst_sells_all:
        gid = sell.trade_group_id
        if gid:
            group = by_gid.get(gid, [])
            if any(
                t.transaction_type == TransactionType.BUY and _is_sol(t.asset)
                for t in group
            ):
                continue
            if any(
                patches.get(t.id, t).transaction_type == TransactionType.BUY
                and _is_sol(t.asset)
                for t in group
            ):
                continue

        best: Optional[Transaction] = None
        best_score = float("inf")
        for tx in transactions:
            if tx.id in used_transfer_ids or tx.id in patches:
                continue
            if tx.source != "solana":
                continue
            if tx.transaction_type != TransactionType.TRANSFER:
                continue
            if tx.transfer_direction != "IN" or not _is_sol(tx.asset):
                continue
            if tx.timestamp < sell.timestamp:
                continue
            if not _time_close(tx, sell, _UNSTAKE_LINK_SEC):
                continue
            if tx.timestamp < sell.timestamp:
                continue
            if (
                sell.on_chain_tx_id
                and tx.on_chain_tx_id
                and sell.on_chain_tx_id != tx.on_chain_tx_id
            ):
                continue
            delta = tx.timestamp.timestamp() - sell.timestamp.timestamp()
            if delta < 0:
                continue
            if delta < best_score:
                best = tx
                best_score = delta

        if best is None:
            continue

        patches[best.id] = _reclassify_transfer_in_as_buy(
            best,
            fiat=sell.fiat_value_at_trigger,
            counter=_sym(sell.asset),
            trade_group_id=sell.trade_group_id,
        )
        used_transfer_ids.add(best.id)
        changed += 1

        # Marinade often emits a matching OUT leg for the same SOL receipt.
        for tx in transactions:
            if tx.id in patches or tx.id == best.id:
                continue
            if tx.source != "solana":
                continue
            if tx.transaction_type != TransactionType.TRANSFER:
                continue
            if tx.transfer_direction != "OUT" or not _is_sol(tx.asset):
                continue
            if not _time_close(tx, best, 30) and not _time_close(tx, sell, 30):
                continue
            if _amounts_close(tx.amount, best.amount):
                drop_ids.add(tx.id)
                changed += 1

    if not patches and not drop_ids:
        return transactions, 0
    return (
        [patches.get(tx.id, tx) for tx in transactions if tx.id not in drop_ids],
        changed,
    )


def _link_lst_transfer_in_before_sell(
    transactions: List[Transaction],
) -> Tuple[List[Transaction], int]:
    """Reclassify inbound LST TRANSFER before an unstake SELL as a BUY leg."""
    patches: Dict[str, Transaction] = {}
    changed = 0
    lst_sells = [
        tx
        for tx in transactions
        if tx.source == "solana"
        and tx.transaction_type == TransactionType.SELL
        and _is_lst(tx.asset)
    ]
    used: Set[str] = set()

    for sell in lst_sells:
        for tx in transactions:
            if tx.id in used or tx.id in patches:
                continue
            if tx.source != "solana":
                continue
            if tx.transaction_type != TransactionType.TRANSFER:
                continue
            if tx.transfer_direction != "IN" or not _is_lst(tx.asset):
                continue
            if _sym(tx.asset) != _sym(sell.asset):
                continue
            if tx.timestamp > sell.timestamp:
                continue
            if not _time_close(tx, sell, _TIME_WINDOW_SEC):
                continue
            if not _amounts_close(tx.amount, sell.amount, rel_tol=0.005):
                continue
            if (
                sell.on_chain_tx_id
                and tx.on_chain_tx_id
                and sell.on_chain_tx_id != tx.on_chain_tx_id
            ):
                continue
            patches[tx.id] = tx.model_copy(
                update={
                    "transaction_type": TransactionType.BUY,
                    "transfer_direction": None,
                    "fiat_value_at_trigger": round(sell.fiat_value_at_trigger, 2),
                    "fiat_currency": sell.fiat_currency or "USD",
                    "counter_asset": "SOL",
                    "trade_group_id": sell.trade_group_id or tx.trade_group_id,
                }
            )
            used.add(tx.id)
            changed += 1
            break

    if not patches:
        return transactions, 0
    return [patches.get(tx.id, tx) for tx in transactions], changed


def _to_reporting(amount_usd: float, when) -> float:
    if amount_usd <= 0:
        return 0.0
    return fx.convert(amount_usd, "USD", REPORTING_CURRENCY, when)


def split_lst_staking_income(
    transactions: List[Transaction],
) -> Tuple[List[Transaction], int]:
    """Book SOL yield above deposited principal as STAKING income on unstake.

    The companion SOL BUY is reduced to principal only so total SOL acquired
    equals ``principal BUY + yield STAKING`` (no double-count in lots / S.104).
    """
    if LIQUID_STAKING_YIELD_AS_INCOME == "off":
        return transactions, 0

    # Recompute synthetic yield rows each pass (FIFO matching may change).
    base = [t for t in transactions if not t.id.endswith("-lst-yield")]
    deposit_cache = _build_lst_buy_sol_deposited_cache(base)

    prior_yield_by_sell: Dict[str, Transaction] = {}
    for t in transactions:
        if (
            t.id.endswith("-lst-yield")
            and t.transaction_type == TransactionType.STAKING
        ):
            sell_id = t.id[: -len("-lst-yield")]
            prior_yield_by_sell[sell_id] = t

    extra: List[Transaction] = []
    patches: Dict[str, Transaction] = {}
    changed = 0
    existing_ids = {t.id for t in base}
    by_gid = _group_by_id(base)

    unstake_groups: List[Tuple[Transaction, Transaction]] = []
    seen_gids: Set[str] = set()

    for tx in base:
        if tx.source != "solana":
            continue
        if tx.transaction_type != TransactionType.SELL or not _is_lst(tx.asset):
            continue
        gid = tx.trade_group_id
        if not gid or gid in seen_gids:
            continue
        group = by_gid.get(gid, [])
        sol_buys = [
            t
            for t in group
            if t.transaction_type == TransactionType.BUY and _is_sol(t.asset)
        ]
        if not sol_buys:
            continue
        seen_gids.add(gid)
        unstake_groups.append((tx, max(sol_buys, key=lambda t: t.amount)))

    for lst_sell, sol_buy in unstake_groups:
        sol_deposited = _fifo_sol_deposited_for_lst_sell(base, lst_sell, deposit_cache)
        if sol_deposited is None:
            continue

        prior = prior_yield_by_sell.get(lst_sell.id)
        # If a prior pass already carved yield out of the BUY, reconstruct gross.
        if (
            prior is not None
            and prior.amount > 0
            and sol_buy.amount + _MIN_INCOME_SOL < sol_deposited + prior.amount
        ):
            sol_received = sol_buy.amount + prior.amount
            gross_fiat = sol_buy.fiat_value_at_trigger + prior.fiat_value_at_trigger
        else:
            sol_received = sol_buy.amount
            gross_fiat = sol_buy.fiat_value_at_trigger

        excess_sol = max(0.0, sol_received - sol_deposited)
        if excess_sol < _MIN_INCOME_SOL:
            continue

        unit_usd = gross_fiat / sol_received if sol_received > 0 else 0.0
        income_usd = excess_sol * unit_usd
        if income_usd < 0.01:
            continue

        income_reporting = _to_reporting(income_usd, lst_sell.timestamp)
        if income_reporting < 0.01:
            continue

        if LIQUID_STAKING_YIELD_AS_INCOME == "reporting":
            cap_proceeds_usd = max(0.0, lst_sell.fiat_value_at_trigger - income_usd)
            if cap_proceeds_usd != lst_sell.fiat_value_at_trigger:
                patches[lst_sell.id] = lst_sell.model_copy(
                    update={"fiat_value_at_trigger": round(cap_proceeds_usd, 2)}
                )

        principal_sol = sol_received - excess_sol
        principal_fiat = (
            round(gross_fiat * (principal_sol / sol_received), 2)
            if sol_received > 0
            else 0.0
        )
        if (
            abs(sol_buy.amount - principal_sol) > 1e-12
            or abs(sol_buy.fiat_value_at_trigger - principal_fiat) > 0.005
        ):
            patches[sol_buy.id] = sol_buy.model_copy(
                update={
                    "amount": round(principal_sol, 8),
                    "fiat_value_at_trigger": principal_fiat,
                }
            )
            changed += 1

        yield_id = f"{lst_sell.id}-lst-yield"
        if yield_id in existing_ids:
            continue

        extra.append(
            Transaction(
                id=yield_id,
                timestamp=lst_sell.timestamp,
                asset="SOL",
                transaction_type=TransactionType.STAKING,
                amount=round(excess_sol, 8),
                fiat_value_at_trigger=round(income_usd, 2),
                fee_fiat=0.0,
                fiat_currency="USD",
                source="solana",
                import_id=lst_sell.import_id,
                counter_asset=_sym(lst_sell.asset),
                trade_group_id=lst_sell.trade_group_id,
                on_chain_tx_id=lst_sell.on_chain_tx_id,
            )
        )
        changed += 1

    if not changed and not patches:
        return base, 0

    merged = [patches.get(tx.id, tx) for tx in base] + extra
    merged.sort(key=lambda t: (t.timestamp, t.id))

    prior_yields = {t.id: t for t in transactions if t.id.endswith("-lst-yield")}
    if (
        not patches
        and prior_yields
        and len(extra) == len(prior_yields)
        and all(
            y.id in prior_yields and prior_yields[y.id] == y for y in extra
        )
    ):
        return transactions, 0

    return merged, changed


def inherit_import_id_for_derived_lst_yield(
    transactions: List[Transaction],
) -> Tuple[List[Transaction], int]:
    """Backfill import_id on synthetic liquid-staking yield rows."""
    by_gid = _group_by_id(transactions)
    patches: Dict[str, Transaction] = {}
    changed = 0

    for tx in transactions:
        if tx.import_id or not tx.id.endswith("-lst-yield"):
            continue
        gid = tx.trade_group_id
        if not gid:
            continue
        donors = [t.import_id for t in by_gid.get(gid, []) if t.import_id]
        if not donors:
            continue
        patches[tx.id] = tx.model_copy(update={"import_id": donors[0]})
        changed += 1

    if not patches:
        return transactions, 0
    return [patches.get(t.id, t) for t in transactions], changed


def normalize_liquid_staking(
    transactions: List[Transaction],
) -> Tuple[List[Transaction], int]:
    """Run all liquid-staking normalizers in order."""
    total = 0
    txs, n = collapse_lst_transfer_sell_duplicates(transactions)
    total += n
    txs, n = _link_lst_transfer_in_before_sell(txs)
    total += n
    txs, n = reclassify_lst_unstake_swaps(txs)
    total += n
    txs, n = split_lst_staking_income(txs)
    total += n
    txs, n = inherit_import_id_for_derived_lst_yield(txs)
    total += n
    return txs, total
