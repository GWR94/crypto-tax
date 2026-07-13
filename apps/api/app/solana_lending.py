"""Kamino Lend (Klend) and Marginfi lending deposit/withdraw/borrow normalization."""

from __future__ import annotations

from typing import Dict, List, Optional, Set, Tuple

from .config import is_stablecoin
from .schemas import Transaction, TransactionType
from .solana_tokens import get_registry, parse_short_mint_label

KLEND_PROGRAM_ID = "KLend2g3cP87fffoy8q1mQqGKjrxjC8boSyAYavgmjD"

# Kamino Lend market / lending program authority seen in wallet imports.
KAMINO_LEND_AUTHORITIES = frozenset(
    {
        "9DrvZvyWh1HuAoZxvYWMvkf2XCzryCpGgHqrMjyDWpmo",
    }
)

MARGINFI_PROGRAM_ID = "MFv2hWf31Z9kbCa1snEPYctwafyhdvnV7FZnsebVacA"
MARGINFI_GROUP_MAIN = "4qp6Fx6tnZkY5Wropq9wUYgtFxXKwE6viZxFHg3rdAG8"

MARGINFI_HELIUS_SOURCES = frozenset({"MARGINFI", "MARGIN_FI", "MRGN"})

LENDING_PRINCIPAL_ASSETS = frozenset(
    {
        "SOL",
        "WSOL",
        "MSOL",
        "BSOL",
        "JITOSOL",
        "JTO",
        "USDC",
        "USDT",
        "PYUSD",
        "USDG",
        "KMNO",
    }
)


def _sym(asset: str) -> str:
    return asset.strip().upper()


def _is_lending_principal(asset: str) -> bool:
    sym = _sym(asset)
    return sym in LENDING_PRINCIPAL_ASSETS or is_stablecoin(sym)


def is_kamino_lend_authority(addr: Optional[str]) -> bool:
    if not addr:
        return False
    return addr.strip() in KAMINO_LEND_AUTHORITIES


def is_marginfi_authority(addr: Optional[str]) -> bool:
    if not addr:
        return False
    addr = addr.strip()
    return addr in {MARGINFI_PROGRAM_ID, MARGINFI_GROUP_MAIN}


def is_lending_protocol_authority(addr: Optional[str]) -> bool:
    return is_kamino_lend_authority(addr) or is_marginfi_authority(addr)


def is_lending_receipt(asset: str, token_mint: Optional[str] = None) -> bool:
    """Collateral / obligation receipt tokens — not portfolio assets."""
    if _is_lending_principal(asset):
        return False
    if token_mint and len(token_mint) >= 32 and not get_registry().lookup_symbol(asset):
        return True
    parsed = parse_short_mint_label(asset)
    if parsed and not get_registry().lookup_symbol(asset):
        return True
    return False


def is_lending_receipt_counter(counter: Optional[str]) -> bool:
    if not counter:
        return False
    return is_lending_receipt(counter)


def _group_by_id(transactions: List[Transaction]) -> Dict[str, List[Transaction]]:
    groups: Dict[str, List[Transaction]] = {}
    for tx in transactions:
        if tx.trade_group_id:
            groups.setdefault(tx.trade_group_id, []).append(tx)
    return groups


def _group_shares_on_chain_id(group: List[Transaction]) -> bool:
    """True when every leg belongs to the same on-chain signature."""
    ids = {t.on_chain_tx_id for t in group if t.on_chain_tx_id}
    return len(ids) <= 1


def _as_transfer_in(tx: Transaction) -> Transaction:
    return tx.model_copy(
        update={
            "transaction_type": TransactionType.TRANSFER,
            "transfer_direction": "IN",
            "counter_asset": None,
            "counter_amount": None,
        }
    )


def _as_transfer_out(tx: Transaction) -> Transaction:
    return tx.model_copy(
        update={
            "transaction_type": TransactionType.TRANSFER,
            "transfer_direction": "OUT",
            "counter_asset": None,
            "counter_amount": None,
        }
    )


def normalize_lending_protocols(
    transactions: List[Transaction],
) -> Tuple[List[Transaction], int]:
    """Reclassify Kamino Lend / Marginfi swap legs as internal transfers; drop receipts."""
    patches: Dict[str, Transaction] = {}
    drop_ids: Set[str] = set()
    changed = 0
    by_gid = _group_by_id(transactions)

    def touch_group(gid: str) -> None:
        nonlocal changed
        group = by_gid.get(gid, [])
        if not group:
            return

        principal_legs = [
            t
            for t in group
            if not is_lending_receipt(t.asset, t.token_mint)
            and t.transaction_type in (TransactionType.BUY, TransactionType.SELL)
        ]
        receipt_legs = [
            t
            for t in group
            if is_lending_receipt(t.asset, t.token_mint)
            and t.transaction_type in (TransactionType.BUY, TransactionType.SELL)
        ]

        if not principal_legs and not receipt_legs:
            return

        for receipt in receipt_legs:
            drop_ids.add(receipt.id)
            changed += 1

        for leg in principal_legs:
            if leg.transaction_type == TransactionType.BUY:
                patches[leg.id] = _as_transfer_in(leg)
            else:
                patches[leg.id] = _as_transfer_out(leg)
            changed += 1

    for gid, group in by_gid.items():
        if not _group_shares_on_chain_id(group):
            continue
        if not any(
            is_lending_receipt(t.asset, t.token_mint)
            or is_lending_receipt_counter(t.counter_asset)
            or is_lending_protocol_authority(t.counterparty_address)
            for t in group
        ):
            continue
        touch_group(gid)

    for tx in transactions:
        if tx.id in patches or tx.id in drop_ids:
            continue
        if tx.source != "solana":
            continue
        if not is_lending_receipt_counter(tx.counter_asset):
            continue
        if not _is_lending_principal(tx.asset):
            continue
        if tx.transaction_type == TransactionType.BUY:
            patches[tx.id] = _as_transfer_in(tx)
            changed += 1
        elif tx.transaction_type == TransactionType.SELL:
            patches[tx.id] = _as_transfer_out(tx)
            changed += 1

    if not patches and not drop_ids:
        return transactions, 0
    return (
        [patches.get(tx.id, tx) for tx in transactions if tx.id not in drop_ids],
        changed,
    )
