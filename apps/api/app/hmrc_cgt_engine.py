"""HMRC Capital Gains Tax engine for UK crypto reporting.

Implements HMRC share-matching rules for crypto assets (per CRYPTO22000):

1. Same-day rule: disposals match acquisitions on the same day (after collapsing
   multiple same-day buys/sells of the same token into one event).
2. Bed-and-breakfast (30-day) rule: disposals match acquisitions made in the
   30 days following the disposal.
3. Section 104 pool: the remainder is matched against the running average-cost
   pool for the asset.

All values are computed in sterling (GBP) using historical FX at the date of
each event, reusing the conversion helpers from :mod:`app.tax_engine`.

Pool / matching arithmetic uses :class:`~decimal.Decimal` internally; API report
rows remain floats quantized to 8 dp (qty) / 2 dp (fiat).

This module is deliberately separate from the FIFO/HIFO engine so the US
(IRS Form 8949) path stays unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Dict, List, Optional

from .config import is_stablecoin
from .income_classification import enrich_income_fiat_values
from .money import D, as_float_fiat, as_float_qty, is_dust_qty
from .schemas import (
    ACQUISITION_TYPES,
    DISPOSAL_TYPES,
    INCOME_TYPES,
    CgtDisposalRow,
    CgtMatchType,
    MissingCostBasisFlag,
    Transaction,
    TransactionType,
    UkCgtSummary,
    UkIncomeRow,
    UkIncomeSummary,
    is_perp_transaction,
)
from .tax_engine import _tx_fee_reporting, _tx_value_reporting
from .transfer_matching import match_transfer_pairs
from .uk_tax_year import annual_exempt_amount, is_in_tax_year, uk_calendar_date

# HMRC bed-and-breakfast window: acquisitions within 30 days after a disposal.
_BNB_DAYS = 30


@dataclass
class _Acquisition:
    tx_id: str
    when: datetime
    quantity: Decimal
    cost: Decimal  # total GBP allowable cost for the full quantity
    remaining: Decimal = field(default_factory=lambda: Decimal("0"))

    def cost_for(self, matched: Decimal) -> Decimal:
        if self.quantity <= 0:
            return Decimal("0")
        return (self.cost * matched) / self.quantity


@dataclass
class _Disposal:
    tx_id: str
    when: datetime
    quantity: Decimal
    proceeds: Decimal  # total GBP proceeds for the full quantity
    remaining: Decimal = field(default_factory=lambda: Decimal("0"))

    def proceeds_for(self, matched: Decimal) -> Decimal:
        if self.quantity <= 0:
            return Decimal("0")
        return (self.proceeds * matched) / self.quantity


def _collect(transactions: List[Transaction]) -> Dict[str, Dict[str, list]]:
    """Group acquisitions and disposals per asset (excluding cash/stablecoins)."""
    per_asset: Dict[str, Dict[str, list]] = {}
    pair_map = match_transfer_pairs(transactions)

    for tx in transactions:
        asset = tx.asset
        if is_stablecoin(asset):
            continue
        if tx.amount <= 0:
            continue
        # Perps never enter the spot Section 104 pool. Callers normally pass
        # spot_transactions(...), but guard here as defense in depth.
        if is_perp_transaction(tx):
            continue

        bucket = per_asset.setdefault(asset, {"acq": [], "disp": []})
        qty = D(tx.amount)

        if tx.transaction_type in ACQUISITION_TYPES:
            cost = D(_tx_value_reporting(tx)) + D(_tx_fee_reporting(tx))
            bucket["acq"].append(
                _Acquisition(
                    tx_id=tx.id,
                    when=tx.timestamp,
                    quantity=qty,
                    cost=cost,
                    remaining=qty,
                )
            )
        elif tx.transaction_type in DISPOSAL_TYPES:
            # Native-asset FEE rows (gas, protocol fees paid in crypto) are
            # disposals. Their fiat_value_at_trigger is the FMV consideration —
            # do not subtract fee_fiat again (that field is for sell-side costs).
            # Incidental fiat fees on a SELL still reduce proceeds.
            if tx.transaction_type == TransactionType.FEE:
                proceeds = D(_tx_value_reporting(tx))
            else:
                proceeds = D(_tx_value_reporting(tx)) - D(_tx_fee_reporting(tx))
            bucket["disp"].append(
                _Disposal(
                    tx_id=tx.id,
                    when=tx.timestamp,
                    quantity=qty,
                    proceeds=proceeds,
                    remaining=qty,
                )
            )
        elif (
            tx.transaction_type == TransactionType.TRANSFER
            and tx.transfer_direction == "IN"
            and tx.id not in pair_map
            and not tx.transfer_pair_id
            and tx.fiat_value_at_trigger > 0
        ):
            # An external receipt with a known value that is not the inbound leg
            # of an internal move establishes cost basis under HMRC rules.
            cost = D(_tx_value_reporting(tx)) + D(_tx_fee_reporting(tx))
            bucket["acq"].append(
                _Acquisition(
                    tx_id=tx.id,
                    when=tx.timestamp,
                    quantity=qty,
                    cost=cost,
                    remaining=qty,
                )
            )
        elif (
            tx.transaction_type == TransactionType.TRANSFER
            and tx.transfer_direction == "OUT"
            and tx.id not in pair_map
            and not tx.transfer_pair_id
        ):
            # Outbound moves that are not the paired leg of an internal transfer
            # are disposals (swaps, sends to third parties, DeFi deposits, etc.).
            proceeds = D(_tx_value_reporting(tx)) - D(_tx_fee_reporting(tx))
            bucket["disp"].append(
                _Disposal(
                    tx_id=tx.id,
                    when=tx.timestamp,
                    quantity=qty,
                    proceeds=proceeds,
                    remaining=qty,
                )
            )
        # Paired internal transfers and value-less receipts leave the pool
        # untouched (basis carries over with the coins).

    return per_asset


def _aggregate_same_day_acquisitions(
    acquisitions: List[_Acquisition],
) -> List[_Acquisition]:
    """Collapse same-calendar-day buys into one acquisition (CRYPTO22250)."""
    if len(acquisitions) <= 1:
        return acquisitions

    by_day: Dict[date, List[_Acquisition]] = {}
    for acq in acquisitions:
        by_day.setdefault(uk_calendar_date(acq.when), []).append(acq)

    aggregated: List[_Acquisition] = []
    for day in sorted(by_day):
        group = sorted(by_day[day], key=lambda a: (a.when, a.tx_id))
        if len(group) == 1:
            aggregated.append(group[0])
            continue
        total_qty = sum((a.quantity for a in group), Decimal("0"))
        total_cost = sum((a.cost for a in group), Decimal("0"))
        aggregated.append(
            _Acquisition(
                tx_id="+".join(a.tx_id for a in group),
                when=group[0].when,
                quantity=total_qty,
                cost=total_cost,
                remaining=total_qty,
            )
        )
    return aggregated


def _aggregate_same_day_disposals(disposals: List[_Disposal]) -> List[_Disposal]:
    """Collapse same-calendar-day sells into one disposal (CRYPTO22250)."""
    if len(disposals) <= 1:
        return disposals

    by_day: Dict[date, List[_Disposal]] = {}
    for disp in disposals:
        by_day.setdefault(uk_calendar_date(disp.when), []).append(disp)

    aggregated: List[_Disposal] = []
    for day in sorted(by_day):
        group = sorted(by_day[day], key=lambda d: (d.when, d.tx_id))
        if len(group) == 1:
            aggregated.append(group[0])
            continue
        total_qty = sum((d.quantity for d in group), Decimal("0"))
        total_proceeds = sum((d.proceeds for d in group), Decimal("0"))
        aggregated.append(
            _Disposal(
                tx_id="+".join(d.tx_id for d in group),
                when=group[0].when,
                quantity=total_qty,
                proceeds=total_proceeds,
                remaining=total_qty,
            )
        )
    return aggregated


def _emit(
    rows: List[CgtDisposalRow],
    asset: str,
    disposal: _Disposal,
    matched: Decimal,
    proceeds: Decimal,
    cost: Decimal,
    match_type: CgtMatchType,
    acquisition: Optional[_Acquisition],
) -> None:
    rows.append(
        CgtDisposalRow(
            asset=asset,
            quantity=as_float_qty(matched),
            disposal_date=disposal.when,
            acquisition_date=acquisition.when if acquisition else None,
            proceeds=as_float_fiat(proceeds),
            allowable_cost=as_float_fiat(cost),
            gain=as_float_fiat(proceeds - cost),
            match_type=match_type,
            disposal_id=disposal.tx_id,
            acquisition_ids=[acquisition.tx_id] if acquisition else [],
            missing_cost_basis=match_type == CgtMatchType.UNMATCHED,
        )
    )


def _match_same_day(
    asset: str,
    acquisitions: List[_Acquisition],
    disposals: List[_Disposal],
    rows: List[CgtDisposalRow],
) -> None:
    for disposal in disposals:
        if is_dust_qty(disposal.remaining):
            continue
        for acq in acquisitions:
            if is_dust_qty(disposal.remaining):
                break
            if is_dust_qty(acq.remaining):
                continue
            if uk_calendar_date(acq.when) != uk_calendar_date(disposal.when):
                continue
            matched = min(disposal.remaining, acq.remaining)
            _emit(
                rows,
                asset,
                disposal,
                matched,
                disposal.proceeds_for(matched),
                acq.cost_for(matched),
                CgtMatchType.SAME_DAY,
                acq,
            )
            disposal.remaining -= matched
            acq.remaining -= matched


def _match_thirty_day(
    asset: str,
    acquisitions: List[_Acquisition],
    disposals: List[_Disposal],
    rows: List[CgtDisposalRow],
) -> None:
    # Disposals earliest first; each matched to the earliest later acquisitions.
    for disposal in sorted(disposals, key=lambda d: d.when):
        if is_dust_qty(disposal.remaining):
            continue
        later = sorted(
            (
                a
                for a in acquisitions
                if not is_dust_qty(a.remaining)
                and 0
                < (
                    uk_calendar_date(a.when) - uk_calendar_date(disposal.when)
                ).days
                <= _BNB_DAYS
            ),
            key=lambda a: a.when,
        )
        for acq in later:
            if is_dust_qty(disposal.remaining):
                break
            matched = min(disposal.remaining, acq.remaining)
            _emit(
                rows,
                asset,
                disposal,
                matched,
                disposal.proceeds_for(matched),
                acq.cost_for(matched),
                CgtMatchType.THIRTY_DAY,
                acq,
            )
            disposal.remaining -= matched
            acq.remaining -= matched


def _match_section_104(
    asset: str,
    acquisitions: List[_Acquisition],
    disposals: List[_Disposal],
    rows: List[CgtDisposalRow],
) -> tuple[Decimal, Decimal]:
    """Match remaining disposals against the Section 104 pool.

    Returns ``(pool_quantity, pool_cost)`` remaining after all events.
    """
    # Merge remaining events chronologically; acquisitions before disposals on ties.
    events: list[tuple[datetime, int, object]] = []
    for acq in acquisitions:
        if not is_dust_qty(acq.remaining):
            events.append((acq.when, 0, acq))
    for disposal in disposals:
        if not is_dust_qty(disposal.remaining):
            events.append((disposal.when, 1, disposal))
    events.sort(key=lambda e: (e[0], e[1]))

    pool_qty = Decimal("0")
    pool_cost = Decimal("0")

    for _when, kind, obj in events:
        if kind == 0:
            acq = obj  # type: ignore[assignment]
            pool_qty += acq.remaining
            pool_cost += acq.cost_for(acq.remaining)
            acq.remaining = Decimal("0")
            continue

        disposal = obj  # type: ignore[assignment]
        if not is_dust_qty(pool_qty):
            matched = min(disposal.remaining, pool_qty)
            avg_cost = pool_cost / pool_qty if pool_qty > 0 else Decimal("0")
            cost_share = avg_cost * matched
            _emit(
                rows,
                asset,
                disposal,
                matched,
                disposal.proceeds_for(matched),
                cost_share,
                CgtMatchType.SECTION_104,
                None,
            )
            pool_qty -= matched
            pool_cost -= cost_share
            disposal.remaining -= matched
            if is_dust_qty(pool_qty):
                pool_qty = Decimal("0")
                pool_cost = Decimal("0")

        if not is_dust_qty(disposal.remaining):
            # No acquisition history covers this portion of the disposal.
            _emit(
                rows,
                asset,
                disposal,
                disposal.remaining,
                disposal.proceeds_for(disposal.remaining),
                Decimal("0"),
                CgtMatchType.UNMATCHED,
                None,
            )
            disposal.remaining = Decimal("0")

    return pool_qty, pool_cost


def _all_disposal_rows(transactions: List[Transaction]) -> List[CgtDisposalRow]:
    # Price unvalued gas/protocol FEE legs so disposal proceeds are FMV, not £0.
    from .wallet_enrichment import enrich_fee_fiat_values

    transactions, _ = enrich_fee_fiat_values(transactions)
    per_asset = _collect(transactions)
    rows: List[CgtDisposalRow] = []

    for asset, bucket in per_asset.items():
        if not bucket["disp"]:
            continue
        asset_rows: List[CgtDisposalRow] = []
        acquisitions = _aggregate_same_day_acquisitions(list(bucket["acq"]))
        disposals = _aggregate_same_day_disposals(list(bucket["disp"]))
        _match_same_day(asset, acquisitions, disposals, asset_rows)
        _match_thirty_day(asset, acquisitions, disposals, asset_rows)
        _match_section_104(asset, acquisitions, disposals, asset_rows)
        rows.extend(asset_rows)

    rows.sort(key=lambda r: (r.disposal_date, r.asset))
    return rows


def _run_uk_matching_for_asset(
    asset: str,
    acquisitions: List[_Acquisition],
    disposals: List[_Disposal],
) -> tuple[Decimal, Decimal]:
    """Run HMRC matching and return the Section 104 pool balance."""
    rows: List[CgtDisposalRow] = []
    _match_same_day(asset, acquisitions, disposals, rows)
    _match_thirty_day(asset, acquisitions, disposals, rows)
    return _match_section_104(asset, acquisitions, disposals, rows)


def compute_uk_open_pools(
    transactions: List[Transaction],
) -> Dict[str, tuple[float, float]]:
    """Per-asset Section 104 pool balances after full HMRC share-matching."""
    return {
        asset: (qty, cost)
        for asset, (qty, cost, _acquired_at) in compute_uk_open_pool_details(
            transactions
        ).items()
    }


def compute_uk_open_pool_details(
    transactions: List[Transaction],
) -> Dict[str, tuple[float, float, datetime]]:
    """Open Section 104 pools as ``(qty, cost, earliest_acquisition)``.

    ``earliest_acquisition`` is the earliest buy that still had quantity after
    same-day / 30-day matching (i.e. that entered the average-cost pool).
    """
    from .wallet_enrichment import enrich_fee_fiat_values

    transactions, _ = enrich_fee_fiat_values(transactions)
    per_asset = _collect(transactions)
    pools: Dict[str, tuple[float, float, datetime]] = {}

    for asset, bucket in per_asset.items():
        acquisitions = _aggregate_same_day_acquisitions(list(bucket["acq"]))
        disposals = _aggregate_same_day_disposals(list(bucket["disp"]))
        if not acquisitions:
            continue

        if not disposals:
            pool_qty = sum((a.quantity for a in acquisitions), Decimal("0"))
            pool_cost = sum((a.cost for a in acquisitions), Decimal("0"))
            earliest = min(a.when for a in acquisitions)
        else:
            # Consume same-day / bed-and-breakfast matches first so earliest
            # reflects only lots that entered the Section 104 pool.
            scratch: List[CgtDisposalRow] = []
            _match_same_day(asset, acquisitions, disposals, scratch)
            _match_thirty_day(asset, acquisitions, disposals, scratch)
            pool_entries = [
                a for a in acquisitions if not is_dust_qty(a.remaining)
            ]
            if not pool_entries:
                continue
            earliest = min(a.when for a in pool_entries)
            pool_qty, pool_cost = _match_section_104(
                asset, acquisitions, disposals, scratch
            )

        if not is_dust_qty(pool_qty):
            pools[asset] = (
                as_float_qty(pool_qty),
                as_float_fiat(pool_cost),
                earliest,
            )

    return pools


def compute_uk_open_acquisitions(
    transactions: List[Transaction],
) -> Dict[str, List[_Acquisition]]:
    """Acquisition rows for assets with an open Section 104 pool (for drill-down)."""
    from .wallet_enrichment import enrich_fee_fiat_values

    transactions, _ = enrich_fee_fiat_values(transactions)
    per_asset = _collect(transactions)
    open_by_asset: Dict[str, List[_Acquisition]] = {}
    for asset, bucket in per_asset.items():
        acquisitions = _aggregate_same_day_acquisitions(list(bucket["acq"]))
        disposals = _aggregate_same_day_disposals(list(bucket["disp"]))
        pool_qty, _pool_cost = _run_uk_matching_for_asset(asset, acquisitions, disposals)
        if not is_dust_qty(pool_qty):
            open_by_asset[asset] = acquisitions
    return open_by_asset


def compute_uk_missing_cost_basis(
    transactions: List[Transaction],
) -> List[MissingCostBasisFlag]:
    """Missing-basis flags from HMRC disposal rows marked ``unmatched``."""
    flags: List[MissingCostBasisFlag] = []
    for row in _all_disposal_rows(transactions):
        if not row.missing_cost_basis:
            continue
        flags.append(
            MissingCostBasisFlag(
                disposal_id=row.disposal_id,
                asset=row.asset,
                timestamp=row.disposal_date,
                disposed_amount=row.quantity,
                uncovered_amount=row.quantity,
                message=(
                    f"SELL of {row.quantity} {row.asset} has no matching purchase "
                    f"history under HMRC rules. Cost basis defaulted to £0."
                ),
            )
        )
    return flags


def calculate_uk_cgt(
    transactions: List[Transaction],
    tax_year_label: Optional[str] = None,
) -> UkCgtSummary:
    """Compute an HMRC CGT summary, optionally filtered to a UK tax year.

    Matching always runs over the full ledger so the Section 104 pool stays
    continuous; only the *reported* disposal rows are filtered to the tax year.
    """
    rows = _all_disposal_rows(transactions)

    if tax_year_label:
        rows = [r for r in rows if is_in_tax_year(r.disposal_date, tax_year_label)]

    total_proceeds = as_float_fiat(sum((D(r.proceeds) for r in rows), Decimal("0")))
    total_costs = as_float_fiat(
        sum((D(r.allowable_cost) for r in rows), Decimal("0"))
    )
    total_gains = as_float_fiat(
        sum((D(r.gain) for r in rows if r.gain > 0), Decimal("0"))
    )
    total_losses = as_float_fiat(
        sum((-D(r.gain) for r in rows if r.gain < 0), Decimal("0"))
    )
    net_gain = as_float_fiat(D(total_gains) - D(total_losses))

    allowance = annual_exempt_amount(tax_year_label) if tax_year_label else 0.0
    taxable = as_float_fiat(max(Decimal("0"), D(net_gain) - D(allowance)))

    return UkCgtSummary(
        tax_year_label=tax_year_label,
        total_proceeds=total_proceeds,
        total_allowable_costs=total_costs,
        total_gains=total_gains,
        total_losses=total_losses,
        net_gain=net_gain,
        disposal_count=len({r.disposal_id for r in rows}),
        annual_exempt_amount=allowance,
        taxable_gain_after_allowance=taxable,
        rows=rows,
    )


def calculate_uk_income(
    transactions: List[Transaction],
    tax_year_label: Optional[str] = None,
) -> UkIncomeSummary:
    """Crypto income (airdrops, staking) valued in GBP for a UK tax year."""
    transactions, _ = enrich_income_fiat_values(transactions)
    rows: List[UkIncomeRow] = []

    for tx in transactions:
        if tx.transaction_type not in INCOME_TYPES:
            continue
        if tx.amount <= 0:
            continue
        if tax_year_label and not is_in_tax_year(tx.timestamp, tax_year_label):
            continue
        rows.append(
            UkIncomeRow(
                date=tx.timestamp,
                asset=tx.asset,
                kind=tx.transaction_type.value,
                quantity=as_float_qty(tx.amount),
                value_gbp=as_float_fiat(_tx_value_reporting(tx)),
                tx_id=tx.id,
            )
        )

    rows.sort(key=lambda r: (r.date, r.asset))
    airdrop = as_float_fiat(
        sum(
            (D(r.value_gbp) for r in rows if r.kind == TransactionType.AIRDROP.value),
            Decimal("0"),
        )
    )
    staking = as_float_fiat(
        sum(
            (D(r.value_gbp) for r in rows if r.kind == TransactionType.STAKING.value),
            Decimal("0"),
        )
    )
    return UkIncomeSummary(
        tax_year_label=tax_year_label,
        total_income=as_float_fiat(D(airdrop) + D(staking)),
        airdrop_income=airdrop,
        staking_income=staking,
        rows=rows,
    )
