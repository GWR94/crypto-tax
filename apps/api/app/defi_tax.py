"""Tax-aware DeFi lending / vault normalization.

After protocol parsers collapse deposits to ``TRANSFER`` legs, this module
optionally reclassifies them as CGT disposals (deposit) and acquisitions
(withdraw) at fair-market value — matching HMRC CRYPTO guidance when beneficial
ownership of the crypto is relinquished to a protocol.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from .config import LENDING_DEPOSIT_TAX_TREATMENT
from .drift import is_drift_collateral_counterparty
from .kamino_vault import is_kamino_farms_authority, is_kamino_vault_share
from .schemas import Transaction, TransactionType
from .solana_lending import is_lending_protocol_authority

EVENT_LEND_DEPOSIT = "lend_deposit"
EVENT_LEND_WITHDRAW = "lend_withdraw"
EVENT_LP_ADD = "lp_add"
EVENT_LP_REMOVE = "lp_remove"

_MIN_FIAT_USD = 0.01


def is_defi_protocol_counterparty(addr: Optional[str]) -> bool:
    """True for known lending / vault / margin protocol counterparties."""
    if not addr:
        return False
    return (
        is_lending_protocol_authority(addr)
        or is_drift_collateral_counterparty(addr)
        or is_kamino_farms_authority(addr)
    )


def _needs_fmv(tx: Transaction) -> bool:
    return tx.amount > 0 and tx.fiat_value_at_trigger < _MIN_FIAT_USD


def _enrich_fmv(tx: Transaction) -> Transaction:
    """Price a zero-value DeFi leg from historical USD quotes when possible."""
    if not _needs_fmv(tx):
        return tx
    from .historical_prices import historical_usd_prices_for_transactions
    from .pricing import DEFAULT_PRICES
    from .wallet_enrichment import _normalize_asset_key, _tx_day

    day = _tx_day(tx.timestamp)
    asset_key = _normalize_asset_key(tx.asset)
    historical = historical_usd_prices_for_transactions([(tx.asset, tx.timestamp)])
    unit_usd = historical.get((asset_key, day))
    if unit_usd is None or unit_usd <= 0:
        unit_usd = float(DEFAULT_PRICES.get(asset_key, 0.0) or 0.0)
    if unit_usd <= 0:
        return tx
    total = round(tx.amount * unit_usd, 2)
    if total < _MIN_FIAT_USD:
        return tx
    return tx.model_copy(
        update={
            "fiat_value_at_trigger": total,
            "fiat_currency": tx.fiat_currency or "USD",
        }
    )


def _as_lend_deposit(tx: Transaction) -> Transaction:
    priced = _enrich_fmv(tx)
    return priced.model_copy(
        update={
            "transaction_type": TransactionType.SELL,
            "transfer_direction": None,
            "transfer_pair_id": None,
            "event_subtype": EVENT_LEND_DEPOSIT,
            "counter_asset": None,
            "counter_amount": None,
        }
    )


def _as_lend_withdraw(tx: Transaction) -> Transaction:
    priced = _enrich_fmv(tx)
    return priced.model_copy(
        update={
            "transaction_type": TransactionType.BUY,
            "transfer_direction": None,
            "transfer_pair_id": None,
            "event_subtype": EVENT_LEND_WITHDRAW,
            "counter_asset": None,
            "counter_amount": None,
        }
    )


def _is_lending_transfer(tx: Transaction) -> bool:
    if tx.transaction_type != TransactionType.TRANSFER:
        return False
    if tx.event_subtype in {EVENT_LEND_DEPOSIT, EVENT_LEND_WITHDRAW}:
        return True
    if is_defi_protocol_counterparty(tx.counterparty_address):
        return True
    # Vault share movements tagged via mint helper.
    if tx.token_mint and is_kamino_vault_share(tx.asset, tx.token_mint):
        return True
    return False


def normalize_lending_for_tax(
    transactions: List[Transaction],
    *,
    policy: Optional[str] = None,
) -> Tuple[List[Transaction], int]:
    """Reclassify DeFi lending/vault transfers as taxable SELL/BUY when configured.

    Deposit (TRANSFER OUT to protocol) → SELL ``lend_deposit`` at FMV.
    Withdraw (TRANSFER IN from protocol) → BUY ``lend_withdraw`` at FMV.
    """
    treatment = (policy or LENDING_DEPOSIT_TAX_TREATMENT).strip().lower()
    if treatment != "cgt_disposal":
        return transactions, 0

    patches: Dict[str, Transaction] = {}
    changed = 0

    for tx in transactions:
        if not _is_lending_transfer(tx):
            continue
        if tx.transfer_direction == "OUT":
            updated = _as_lend_deposit(tx)
            if updated != tx:
                patches[tx.id] = updated
                changed += 1
        elif tx.transfer_direction == "IN":
            updated = _as_lend_withdraw(tx)
            if updated != tx:
                patches[tx.id] = updated
                changed += 1

    if not patches:
        return transactions, 0
    return [patches.get(tx.id, tx) for tx in transactions], changed
