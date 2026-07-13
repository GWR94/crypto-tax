"""Shared post-import ledger normalization for tax calculation."""

from __future__ import annotations

from typing import List

from .cryptocom import normalize_cryptocom_exchange_legs
from .drift import normalize_drift_collateral
from .exchange_ledger import collapse_exchange_timezone_duplicates
from .income_classification import enrich_income_fiat_values, reclassify_income_events
from .kamino_vault import normalize_kamino_vault
from .kraken import (
    normalize_exchange_asset_aliases,
    normalize_kraken_ledger,
)
from .ledger_filters import collapse_staking_echo_transfers
from .liquid_staking import normalize_liquid_staking
from .on_chain_links import backfill_on_chain_tx_ids
from .schemas import Transaction
from .solana_lending import normalize_lending_protocols
from .solana_wallet import (
    reclassify_disguised_solana_swaps,
    repair_mismatched_solana_trade_groups,
)
from .transaction_dedup import dedupe_transactions
from .transfer_matching import annotate_transfer_pairs
from .wallet_enrichment import enrich_fee_fiat_values


def normalize_tax_ledger(txs: List[Transaction]) -> tuple[List[Transaction], bool]:
    """Apply read-time ledger fixes; return (transactions, changed)."""
    txs, income_fix = reclassify_income_events(txs)
    txs, income_fiat = enrich_income_fiat_values(txs)
    txs, fee_fiat = enrich_fee_fiat_values(txs)
    txs, on_chain = backfill_on_chain_tx_ids(txs)
    txs, gid_fix = repair_mismatched_solana_trade_groups(txs)
    txs, alias_fix = normalize_exchange_asset_aliases(txs)
    txs, tz_dupes = collapse_exchange_timezone_duplicates(txs)
    txs, lst_fix = normalize_liquid_staking(txs)
    txs, kamino_fix = normalize_kamino_vault(txs)
    txs, lend_fix = normalize_lending_protocols(txs)
    txs, swap_fix = reclassify_disguised_solana_swaps(txs)
    txs, drift_fix = normalize_drift_collateral(txs)
    txs, cdc_fix = normalize_cryptocom_exchange_legs(txs)
    txs, kraken_fix_count = normalize_kraken_ledger(txs)
    kraken_fix = kraken_fix_count > 0
    transfer_before = {t.id: t.transfer_pair_id for t in txs}
    txs = annotate_transfer_pairs(txs)
    transfer_fix = any(
        transfer_before.get(t.id) != t.transfer_pair_id for t in txs
    )
    txs, staking_echo = collapse_staking_echo_transfers(txs)
    txs, dedup_stats = dedupe_transactions(txs)
    changed = bool(
        income_fix
        or income_fiat
        or fee_fiat
        or on_chain
        or gid_fix
        or alias_fix
        or tz_dupes
        or lst_fix
        or kamino_fix
        or lend_fix
        or swap_fix
        or drift_fix
        or cdc_fix
        or kraken_fix
        or transfer_fix
        or staking_echo
        or dedup_stats["skipped_id"]
        or dedup_stats["skipped_fingerprint"]
        or dedup_stats["skipped_on_chain"]
    )
    return txs, changed
