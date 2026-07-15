"""AMM liquidity-pool add/remove tax normalization.

Detects same-signature multi-asset pool deposits/withdrawals (Raydium / Orca /
Meteora-style) and books them as CGT disposals of contributed assets plus an
LP-share acquisition (add), or LP disposal plus re-acquisition of assets (remove).
"""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .config import LP_TAX_TREATMENT, is_stablecoin
from .defi_tax import (
    EVENT_LP_ADD,
    EVENT_LP_REMOVE,
    _enrich_fmv,
    is_defi_protocol_counterparty,
)
from .schemas import Transaction, TransactionType, is_perp_transaction
from .solana_lending import LENDING_PRINCIPAL_ASSETS

_MIN_FIAT = 0.01


def _sym(asset: str) -> str:
    return asset.strip().upper()


def _is_principal(asset: str, token_mint: Optional[str] = None) -> bool:
    """True for poolable base assets (majors / LSTs / stables), not LP receipts."""
    del token_mint  # mint used by callers for receipt detection elsewhere
    sym = _sym(asset)
    return sym in LENDING_PRINCIPAL_ASSETS or is_stablecoin(sym)


def _is_lp_share(tx: Transaction) -> bool:
    if tx.asset.upper().startswith("LP:"):
        return True
    return not _is_principal(tx.asset, tx.token_mint)


def _group_id(tx: Transaction) -> Optional[str]:
    return tx.trade_group_id or tx.on_chain_tx_id


def _synthetic_lp_asset(gid: str) -> str:
    # Transaction.asset is uppercased for non-mint tickers.
    return f"LP:{gid.strip().upper()}"


def _synthetic_lp_id(gid: str, kind: str) -> str:
    return f"lp-{kind}-{gid}"


def _leg_direction(tx: Transaction) -> Optional[str]:
    """Return OUT / IN for tax-relevant movement, else None."""
    if tx.transaction_type == TransactionType.TRANSFER:
        return tx.transfer_direction
    if tx.transaction_type == TransactionType.SELL:
        return "OUT"
    if tx.transaction_type in {
        TransactionType.BUY,
        TransactionType.AIRDROP,
        TransactionType.STAKING,
    }:
        return "IN"
    return None


def _already_tagged_lp(txs: Sequence[Transaction]) -> bool:
    return any((t.event_subtype or "") in {EVENT_LP_ADD, EVENT_LP_REMOVE} for t in txs)


def _looks_like_lending(txs: Sequence[Transaction]) -> bool:
    return any(
        (t.event_subtype or "") in {"lend_deposit", "lend_withdraw"}
        or is_defi_protocol_counterparty(t.counterparty_address)
        for t in txs
    )


def _classify_group(
    group: Sequence[Transaction],
) -> Optional[
    Tuple[str, List[Transaction], List[Transaction], List[Transaction], List[Transaction]]
]:
    """Return (kind, principal_out, principal_in, lp_out, lp_in) or None."""
    if len(group) < 2 or _already_tagged_lp(group) or _looks_like_lending(group):
        return None

    principal_out: List[Transaction] = []
    principal_in: List[Transaction] = []
    lp_out: List[Transaction] = []
    lp_in: List[Transaction] = []

    for tx in group:
        if is_perp_transaction(tx) or tx.transaction_type == TransactionType.FEE:
            continue
        direction = _leg_direction(tx)
        if direction is None:
            continue
        if _is_lp_share(tx):
            (lp_out if direction == "OUT" else lp_in).append(tx)
        else:
            (principal_out if direction == "OUT" else principal_in).append(tx)

    # Add: ≥2 principal assets leave the wallet (LP mint optional / often missing).
    if len(principal_out) >= 2 and not principal_in:
        return "add", principal_out, principal_in, lp_out, lp_in

    # Remove: ≥2 principal assets return, optionally burning an LP share.
    if len(principal_in) >= 2 and not principal_out:
        return "remove", principal_out, principal_in, lp_out, lp_in

    # Remove with explicit LP burn + at least one principal back.
    if lp_out and principal_in and not principal_out:
        return "remove", principal_out, principal_in, lp_out, lp_in

    # Add with explicit LP mint + at least one principal out (single-sided LP).
    if lp_in and principal_out and not principal_in:
        return "add", principal_out, principal_in, lp_out, lp_in

    return None


def _as_lp_disposal(tx: Transaction, subtype: str) -> Transaction:
    priced = _enrich_fmv(tx)
    return priced.model_copy(
        update={
            "transaction_type": TransactionType.SELL,
            "transfer_direction": None,
            "transfer_pair_id": None,
            "event_subtype": subtype,
            "counter_asset": None,
            "counter_amount": None,
        }
    )


def _as_lp_acquisition(
    tx: Transaction, subtype: str, fiat: Optional[float] = None
) -> Transaction:
    priced = _enrich_fmv(tx)
    value = priced.fiat_value_at_trigger if fiat is None else fiat
    return priced.model_copy(
        update={
            "transaction_type": TransactionType.BUY,
            "transfer_direction": None,
            "transfer_pair_id": None,
            "event_subtype": subtype,
            "counter_asset": None,
            "counter_amount": None,
            "fiat_value_at_trigger": round(max(0.0, value), 2),
            "fiat_currency": priced.fiat_currency
            or tx.fiat_currency
            or ("GBP" if value else None),
        }
    )


def _sum_fiat(txs: Iterable[Transaction]) -> float:
    total = 0.0
    for tx in txs:
        priced = _enrich_fmv(tx)
        total += max(0.0, priced.fiat_value_at_trigger)
    return round(total, 2)


def _currency_of(txs: Sequence[Transaction], fallback: str = "GBP") -> str:
    for tx in txs:
        if tx.fiat_currency:
            return tx.fiat_currency
    return fallback


def normalize_lp_for_tax(
    transactions: List[Transaction],
    *,
    policy: Optional[str] = None,
) -> Tuple[List[Transaction], int]:
    """Book AMM LP add/remove as CGT events when ``LP_TAX_TREATMENT=cgt_disposal``."""
    treatment = (policy or LP_TAX_TREATMENT).strip().lower()
    if treatment != "cgt_disposal":
        return transactions, 0

    by_gid: Dict[str, List[Transaction]] = defaultdict(list)
    for tx in transactions:
        gid = _group_id(tx)
        if gid:
            by_gid[gid].append(tx)

    patches: Dict[str, Transaction] = {}
    extras: List[Transaction] = []
    existing_ids = {t.id for t in transactions}
    changed = 0

    for gid, group in by_gid.items():
        classified = _classify_group(group)
        if not classified:
            continue
        kind, principal_out, principal_in, lp_out, lp_in = classified

        if kind == "add":
            disposals = [_as_lp_disposal(tx, EVENT_LP_ADD) for tx in principal_out]
            for original, updated in zip(principal_out, disposals):
                if updated != original:
                    patches[original.id] = updated
                    changed += 1
            cost = _sum_fiat(disposals)
            currency = _currency_of(disposals)

            if lp_in:
                for leg in lp_in:
                    updated = _as_lp_acquisition(
                        leg,
                        EVENT_LP_ADD,
                        fiat=cost if cost >= _MIN_FIAT else None,
                    )
                    if updated != leg:
                        patches[leg.id] = updated
                        changed += 1
            else:
                synth_id = _synthetic_lp_id(gid, "acquire")
                if synth_id not in existing_ids and not any(
                    t.id == synth_id for t in extras
                ):
                    anchor = disposals[0]
                    extras.append(
                        Transaction(
                            id=synth_id,
                            timestamp=anchor.timestamp,
                            asset=_synthetic_lp_asset(gid),
                            transaction_type=TransactionType.BUY,
                            amount=1.0,
                            fiat_value_at_trigger=cost,
                            fee_fiat=0.0,
                            fiat_currency=currency if cost > 0 else None,
                            source=anchor.source or "amm_lp",
                            trade_group_id=gid,
                            on_chain_tx_id=anchor.on_chain_tx_id or gid,
                            event_subtype=EVENT_LP_ADD,
                        )
                    )
                    changed += 1

        elif kind == "remove":
            acquisitions = [
                _as_lp_acquisition(tx, EVENT_LP_REMOVE) for tx in principal_in
            ]
            for original, updated in zip(principal_in, acquisitions):
                if updated != original:
                    patches[original.id] = updated
                    changed += 1
            proceeds = _sum_fiat(acquisitions)
            currency = _currency_of(acquisitions)

            # Close the LP lot only when a share burn/out leg is present (real mint
            # or synthetic ``LP:{add_gid}``). Do not invent a new LP:{remove_gid}
            # asset — that would never match the add acquisition.
            for leg in lp_out:
                updated = _as_lp_disposal(leg, EVENT_LP_REMOVE)
                if (
                    proceeds >= _MIN_FIAT
                    and updated.fiat_value_at_trigger < _MIN_FIAT
                ):
                    updated = updated.model_copy(
                        update={
                            "fiat_value_at_trigger": proceeds,
                            "fiat_currency": currency,
                        }
                    )
                if updated != leg:
                    patches[leg.id] = updated
                    changed += 1

    if not patches and not extras:
        return transactions, 0

    merged = [patches.get(tx.id, tx) for tx in transactions] + extras
    merged.sort(key=lambda t: (t.timestamp, t.id))
    return merged, changed
