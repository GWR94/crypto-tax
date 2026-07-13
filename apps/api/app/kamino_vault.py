"""Normalize Kamino Earn (Kvault) and Kamino Farms stake flows on Solana.

Kamino vault deposits burn SOL/WSOL and mint vault share tokens; withdrawals
burn shares and return SOL/WSOL. Kamino Farms deposits swap/stake principal
tokens (e.g. JTO) and mint farm receipt tokens. Wallet CSV exports often list
both WSOL and native SOL unwrap legs, which the generic swap grouper
double-counts as a taxable BUY/SELL. These flows are principal movement, not
disposals/acquisitions.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Set, Tuple

from .schemas import Transaction, TransactionType
from .solana_tokens import get_registry, parse_short_mint_label

KVAULT_PROGRAM_ID = "KvauGMspG5k6rtzrqqn7WNn3oZdyKqLKwK2XWQ8FLjd"

# Kamino Earn SOL vault share mint (short label FiM4…3CkN in explorer CSVs).
KAMINO_VAULT_SHARE_MINTS = frozenset(
    {
        "FiM4VQdXXnTXL7GgChryf9zHNG9cmvKECwf34L2y3CkN",
    }
)

# Kamino Farms vault authorities (pool-specific).
KAMINO_FARMS_AUTHORITIES = frozenset(
    {
        "FTj3SbJuawWT42Wj2GWxmbqLXNpXFE4ypE1bN17Sh5J5",  # JTO farm
    }
)

SOL_ASSETS = frozenset({"SOL", "WSOL"})

# Principal assets staked in Kamino Farms (not receipt/share tokens).
FARMS_PRINCIPAL_ASSETS = frozenset(
    {"SOL", "WSOL", "JTO", "JITOSOL", "MSOL", "BSOL", "KMNO"}
)

def _sym(asset: str) -> str:
    return asset.strip().upper()


def _is_sol(asset: str) -> bool:
    return _sym(asset) in SOL_ASSETS


def _vault_share_mint(asset: str, token_mint: Optional[str]) -> Optional[str]:
    if token_mint and token_mint in KAMINO_VAULT_SHARE_MINTS:
        return token_mint
    parsed = parse_short_mint_label(asset)
    if parsed:
        prefix, suffix = parsed
        matches = [
            m
            for m in KAMINO_VAULT_SHARE_MINTS
            if m.startswith(prefix) and m.endswith(suffix)
        ]
        if len(matches) == 1:
            return matches[0]
    info = get_registry().lookup_short_mint_label(asset)
    if info and info.mint in KAMINO_VAULT_SHARE_MINTS:
        return info.mint
    return None


def is_kamino_vault_share(asset: str, token_mint: Optional[str] = None) -> bool:
    return _vault_share_mint(asset, token_mint) is not None


def is_kamino_vault_counter(counter: Optional[str]) -> bool:
    if not counter:
        return False
    return is_kamino_vault_share(counter)


def is_kamino_farms_authority(addr: Optional[str]) -> bool:
    if not addr:
        return False
    return addr.strip() in KAMINO_FARMS_AUTHORITIES


def _is_farms_principal(asset: str) -> bool:
    return _sym(asset) in FARMS_PRINCIPAL_ASSETS


def is_kamino_farms_receipt(asset: str, token_mint: Optional[str] = None) -> bool:
    """Farm receipt/share token — bookkeeping only, not a portfolio asset."""
    if _is_farms_principal(asset):
        return False
    if is_kamino_vault_share(asset, token_mint):
        return False
    if token_mint and token_mint in KAMINO_VAULT_SHARE_MINTS:
        return False
    # Unlisted mint labels in a farms tx are receipt tokens.
    if token_mint and len(token_mint) >= 32 and not get_registry().lookup_symbol(asset):
        return True
    parsed = parse_short_mint_label(asset)
    if parsed and not get_registry().lookup_symbol(asset):
        return True
    return False


def is_kamino_farms_counter(counter: Optional[str]) -> bool:
    if not counter:
        return False
    return is_kamino_farms_receipt(counter)


def _unwrap_double_sol(amount: float, fiat: float) -> Tuple[float, float]:
    """Halve SOL qty/fiat when WSOL + native unwrap were summed in one leg."""
    if amount <= 0:
        return amount, fiat
    half = amount / 2
    if half <= 0:
        return amount, fiat
    return half, round(fiat / 2, 2)


def _as_transfer_in(tx: Transaction) -> Transaction:
    amount, fiat = _unwrap_double_sol(tx.amount, tx.fiat_value_at_trigger)
    return tx.model_copy(
        update={
            "transaction_type": TransactionType.TRANSFER,
            "transfer_direction": "IN",
            "amount": amount,
            "fiat_value_at_trigger": fiat,
            "counter_asset": None,
            "counter_amount": None,
        }
    )


def _as_transfer_out(tx: Transaction) -> Transaction:
    amount, fiat = _unwrap_double_sol(tx.amount, tx.fiat_value_at_trigger)
    return tx.model_copy(
        update={
            "transaction_type": TransactionType.TRANSFER,
            "transfer_direction": "OUT",
            "amount": amount,
            "fiat_value_at_trigger": fiat,
            "counter_asset": None,
            "counter_amount": None,
        }
    )


def _group_by_id(transactions: List[Transaction]) -> Dict[str, List[Transaction]]:
    groups: Dict[str, List[Transaction]] = {}
    for tx in transactions:
        if tx.trade_group_id:
            groups.setdefault(tx.trade_group_id, []).append(tx)
    return groups


def _as_farms_transfer_in(tx: Transaction) -> Transaction:
    return tx.model_copy(
        update={
            "transaction_type": TransactionType.TRANSFER,
            "transfer_direction": "IN",
            "counter_asset": None,
            "counter_amount": None,
        }
    )


def _as_farms_transfer_out(tx: Transaction) -> Transaction:
    return tx.model_copy(
        update={
            "transaction_type": TransactionType.TRANSFER,
            "transfer_direction": "OUT",
            "counter_asset": None,
            "counter_amount": None,
        }
    )


def normalize_kamino_vault(transactions: List[Transaction]) -> Tuple[List[Transaction], int]:
    """Reclassify Kamino vault/farms swaps as internal transfers; drop share legs."""
    patches: Dict[str, Transaction] = {}
    drop_ids: Set[str] = set()
    changed = 0
    by_gid = _group_by_id(transactions)

    def touch_vault_group(gid: str) -> None:
        nonlocal changed
        group = by_gid.get(gid, [])
        if not group:
            return

        sol_legs = [
            t
            for t in group
            if _is_sol(t.asset)
            and t.transaction_type in (TransactionType.BUY, TransactionType.SELL)
        ]
        share_legs = [
            t
            for t in group
            if is_kamino_vault_share(t.asset, t.token_mint)
            and t.transaction_type in (TransactionType.BUY, TransactionType.SELL)
        ]

        if not sol_legs and not share_legs:
            return

        for share in share_legs:
            drop_ids.add(share.id)
            changed += 1

        for sol in sol_legs:
            if sol.transaction_type == TransactionType.BUY:
                patches[sol.id] = _as_transfer_in(sol)
            else:
                patches[sol.id] = _as_transfer_out(sol)
            changed += 1

    def touch_farms_group(gid: str) -> None:
        nonlocal changed
        group = by_gid.get(gid, [])
        if not group:
            return

        principal_legs = [
            t
            for t in group
            if not is_kamino_farms_receipt(t.asset, t.token_mint)
            and t.transaction_type in (TransactionType.BUY, TransactionType.SELL)
        ]
        receipt_legs = [
            t
            for t in group
            if is_kamino_farms_receipt(t.asset, t.token_mint)
            and t.transaction_type in (TransactionType.BUY, TransactionType.SELL)
        ]

        if not principal_legs and not receipt_legs:
            return

        for receipt in receipt_legs:
            drop_ids.add(receipt.id)
            changed += 1

        for leg in principal_legs:
            if leg.transaction_type == TransactionType.BUY:
                patches[leg.id] = _as_farms_transfer_in(leg)
            else:
                patches[leg.id] = _as_farms_transfer_out(leg)
            changed += 1

    for gid, group in by_gid.items():
        if any(
            is_kamino_vault_share(t.asset, t.token_mint)
            or is_kamino_vault_counter(t.counter_asset)
            for t in group
        ):
            touch_vault_group(gid)
        if any(
            is_kamino_farms_receipt(t.asset, t.token_mint)
            and t.transaction_type in (TransactionType.BUY, TransactionType.SELL)
            for t in group
        ):
            touch_farms_group(gid)

    # Orphan swap legs whose counter is a vault/farms receipt (receipt skipped as spam).
    for tx in transactions:
        if tx.id in patches or tx.id in drop_ids:
            continue
        if tx.source != "solana":
            continue
        if is_kamino_vault_counter(tx.counter_asset) and _is_sol(tx.asset):
            if tx.transaction_type == TransactionType.BUY:
                patches[tx.id] = _as_transfer_in(tx)
                changed += 1
            elif tx.transaction_type == TransactionType.SELL:
                patches[tx.id] = _as_transfer_out(tx)
                changed += 1
            continue
        if is_kamino_farms_counter(tx.counter_asset) and _is_farms_principal(tx.asset):
            if tx.transaction_type == TransactionType.BUY:
                patches[tx.id] = _as_farms_transfer_in(tx)
                changed += 1
            elif tx.transaction_type == TransactionType.SELL:
                patches[tx.id] = _as_farms_transfer_out(tx)
                changed += 1

    if not patches and not drop_ids:
        return transactions, 0
    return (
        [patches.get(tx.id, tx) for tx in transactions if tx.id not in drop_ids],
        changed,
    )
