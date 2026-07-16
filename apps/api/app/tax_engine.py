"""Deterministic capital-gains tax engine.

This module contains pure, explicit, deterministic logic. No values are
estimated or predicted: every number is computed from the transaction ledger
using strict accounting rules (FIFO / LIFO / HIFO), IRS holding-period rules, internal
transfer matching, and dust filtering.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Dict, List, Tuple

from .config import (
    REPORTING_CURRENCY,
    TAX_JURISDICTION,
    UK_CGT_BASIC_RATE,
    UK_CGT_HIGHER_RATE,
    UK_UNUSED_BASIC_BAND_DEFAULT,
    US_LONG_TERM_CG_RATE,
    US_ORDINARY_INCOME_RATE,
    is_stablecoin,
    reporting_currency_for,
)
from .fx import fx, us_calendar_year
from .money import D, LOT_EPS, as_float_fiat, as_float_qty, q_unit
from .schemas import (
    ACQUISITION_TYPES,
    DISPOSAL_TYPES,
    INCOME_TYPES,
    AccountingMethod,
    Form8949Row,
    IncomeSummary,
    MissingCostBasisFlag,
    Position,
    RealizedGainsSummary,
    RealizedPnlRow,
    TaxHarvestRow,
    Transaction,
    TransactionType,
    is_perp_transaction,
)
from .transfer_matching import match_transfer_pairs

# --- Constants -------------------------------------------------------------

# IRS: long-term if held more than one year (day after the acquisition anniversary).
# Kept for reference / legacy callers; matching uses calendar anniversary logic.
LONG_TERM_THRESHOLD_DAYS = 365

# Internal-transfer matching window for mis-typed SELL+BUY legs.
# Keep tight: genuine wallet↔exchange moves usually clear in seconds, and a
# wider window falsely suppresses real market trades of the same size.
TRANSFER_MATCH_WINDOW = timedelta(minutes=5)

# Relative tolerance for "identical asset amounts" when matching transfers.
AMOUNT_MATCH_REL_TOL = float(LOT_EPS)

# Positions valued below this threshold (in reporting currency) are ignored.
DUST_THRESHOLD_REPORTING = 0.50


# --- Internal lot bookkeeping ----------------------------------------------


@dataclass
class Lot:
    """An open acquisition lot tracked per asset."""

    source_id: str
    asset: str
    acquired_at: datetime
    quantity: Decimal
    unit_cost: Decimal  # fiat cost basis per single unit, fee-inclusive

    @property
    def remaining_cost_basis(self) -> float:
        return as_float_fiat(self.quantity * self.unit_cost)


@dataclass
class EngineResult:
    """Everything computed in a single pass over the ledger."""

    rows: List[Form8949Row] = field(default_factory=list)
    open_lots: Dict[str, List[Lot]] = field(default_factory=dict)
    missing_cost_basis: List[MissingCostBasisFlag] = field(default_factory=list)
    income_by_asset: Dict[str, Dict[str, float]] = field(default_factory=dict)


# --- Helpers ---------------------------------------------------------------


def _amounts_match(a: float, b: float) -> bool:
    """True when two asset amounts are identical within tolerance."""
    if a == b:
        return True
    scale = max(abs(a), abs(b), 1e-12)
    return abs(a - b) / scale <= AMOUNT_MATCH_REL_TOL


def _sort_chronologically(transactions: List[Transaction]) -> List[Transaction]:
    def _sort_key(t: Transaction) -> tuple:
        # Within a swap group, acquire before dispose so cost basis exists.
        leg = 0 if t.transaction_type in ACQUISITION_TYPES else 1
        group = t.trade_group_id or ""
        return (t.timestamp, group, leg, t.id)

    return sorted(transactions, key=_sort_key)


def _as_utc_date(value: datetime) -> date:
    from .fx import fx_calendar_day

    return fx_calendar_day(value, reporting_currency="USD")


def _add_calendar_years(day: date, years: int) -> date:
    """Advance ``day`` by whole years; clamp Feb 29 → Feb 28 in non-leap years."""
    try:
        return day.replace(year=day.year + years)
    except ValueError:
        return day.replace(year=day.year + years, day=28)


def _holding_term(acquired_at: datetime, disposed_at: datetime) -> Tuple[str, int]:
    """Return ('SHORT'|'LONG', calendar holding_period_days).

    IRS holding period begins the day after acquisition and is long-term only
    when the asset is held *more than* one year — i.e. disposed on or after the
    day following the one-year anniversary (handles leap years correctly).
    """
    acquired = _as_utc_date(acquired_at)
    disposed = _as_utc_date(disposed_at)
    anniversary = _add_calendar_years(acquired, 1)
    long_start = anniversary + timedelta(days=1)
    term = "LONG" if disposed >= long_start else "SHORT"
    days = (disposed - acquired).days
    return term, days


def _select_lot_index(lots: List[Lot], method: AccountingMethod) -> int:
    """Pick the index of the next lot to consume for the given method.

    FIFO -> earliest ``acquired_at``.
    LIFO -> latest ``acquired_at``.
    HIFO -> highest ``unit_cost``.
    """
    if method == AccountingMethod.FIFO:
        # Earliest acquisition first; ties broken by lowest unit cost for stability.
        return min(
            range(len(lots)),
            key=lambda i: (lots[i].acquired_at, lots[i].unit_cost),
        )
    if method == AccountingMethod.LIFO:
        # Latest acquisition first; ties broken by highest unit cost for stability.
        return max(
            range(len(lots)),
            key=lambda i: (lots[i].acquired_at, lots[i].unit_cost),
        )
    # HIFO: highest unit cost first; ties broken by earliest acquisition.
    return max(
        range(len(lots)),
        key=lambda i: (lots[i].unit_cost, -lots[i].acquired_at.timestamp()),
    )


# --- Internal transfer matching --------------------------------------------


def _looks_like_market_trade(tx: Transaction) -> bool:
    """True when the row has markers of an exchange trade, not a wallet move."""
    if tx.counter_asset:
        return True
    if tx.venue_order_type:
        return True
    if tx.realized_pnl is not None:
        return True
    return False


def match_internal_transfers(
    transactions: List[Transaction],
) -> Tuple[List[Transaction], List[str]]:
    """Reclassify matched internal transfers as non-taxable ``TRANSFER`` events.

    A SELL on one ledger is matched with a BUY on a *different* ledger when both
    are for the identical asset and amount, occur within a short window, lack
    market-trade markers (counter asset / order type), and both have sources.
    Legs are reclassified to paired ``TRANSFER`` OUT/IN so cost basis carries.

    Returns the (possibly mutated copy of) transactions and the list of
    reclassified transaction ids.
    """
    ordered = _sort_chronologically(transactions)
    # Work on copies so the caller's objects are untouched.
    working = [t.model_copy(deep=True) for t in ordered]

    # Perp fills must never pair with spot legs as an internal transfer.
    sells = [
        t
        for t in working
        if t.transaction_type == TransactionType.SELL
        and not is_perp_transaction(t)
        and not _looks_like_market_trade(t)
    ]
    buys = [
        t
        for t in working
        if t.transaction_type == TransactionType.BUY
        and not is_perp_transaction(t)
        and not _looks_like_market_trade(t)
    ]
    consumed_buy_ids: set[str] = set()
    reclassified: List[str] = []

    for sell in sells:
        if not sell.source:
            continue
        for buy in buys:
            if buy.id in consumed_buy_ids:
                continue
            if not buy.source:
                continue
            if buy.asset != sell.asset:
                continue
            # Must originate from a different ledger to be an internal transfer.
            if sell.source == buy.source:
                continue
            if not _amounts_match(sell.amount, buy.amount):
                continue
            if abs(buy.timestamp - sell.timestamp) > TRANSFER_MATCH_WINDOW:
                continue

            # Match found: reclassify both legs as a paired non-taxable transfer.
            pair_id = f"pair-{sell.id}"
            sell.transaction_type = TransactionType.TRANSFER
            sell.transfer_direction = "OUT"
            sell.transfer_pair_id = pair_id
            buy.transaction_type = TransactionType.TRANSFER
            buy.transfer_direction = "IN"
            buy.transfer_pair_id = pair_id
            consumed_buy_ids.add(buy.id)
            reclassified.extend([sell.id, buy.id])
            break

    return working, reclassified


# --- Dust filtering --------------------------------------------------------


def is_dust(
    quantity: float,
    current_price_reporting: float,
    *,
    total_invested: float = 0.0,
) -> bool:
    """True when a position is too small to show — unless it has real cost basis."""
    market_value = quantity * current_price_reporting
    if total_invested >= DUST_THRESHOLD_REPORTING:
        return False
    return market_value < DUST_THRESHOLD_REPORTING


def _tx_value_reporting(
    tx: Transaction, *, reporting_currency: str = REPORTING_CURRENCY
) -> float:
    return fx.to_reporting(
        tx.fiat_value_at_trigger,
        tx.fiat_currency,
        tx.timestamp,
        tx.source,
        reporting_currency=reporting_currency,
    )


def _tx_fee_reporting(
    tx: Transaction, *, reporting_currency: str = REPORTING_CURRENCY
) -> float:
    return fx.to_reporting(
        tx.fee_fiat,
        tx.fiat_currency,
        tx.timestamp,
        tx.source,
        reporting_currency=reporting_currency,
    )


def _price_reporting(
    price_usd: float, *, reporting_currency: str = REPORTING_CURRENCY
) -> float:
    return fx.convert(
        price_usd, "USD", reporting_currency, datetime.now(timezone.utc)
    )


# --- Core lot-matching pass -------------------------------------------------


def _run_engine(
    transactions: List[Transaction],
    method: AccountingMethod,
    *,
    reporting_currency: str = REPORTING_CURRENCY,
) -> EngineResult:
    """Single chronological pass building lots and disposal rows.

    Paired internal TRANSFERs are basis-neutral (lots unchanged). Unpaired
    TRANSFER OUT is a taxable disposal (third-party send / unmatched move);
    unpaired TRANSFER IN establishes cost basis when FMV is known.
    """
    from .wallet_enrichment import enrich_fee_fiat_values

    reporting_currency = reporting_currency.upper()
    transactions, _ = enrich_fee_fiat_values(transactions)
    result = EngineResult()
    ordered = _sort_chronologically(transactions)
    paired_transfer_ids = set(match_transfer_pairs(ordered))

    for tx in ordered:
        asset = tx.asset

        # Perps never create spot FIFO lots. Callers normally pass
        # spot_transactions(...); guard here as defense in depth.
        if is_perp_transaction(tx):
            continue

        # Stablecoins are quote/cash — not capital-gains positions. SOL→USDC→GBP
        # is taxed on the SOL leg; USDC conversion is ignored (gain ≈ 0).
        if is_stablecoin(asset) and tx.transaction_type in (
            *ACQUISITION_TYPES,
            *DISPOSAL_TYPES,
            TransactionType.TRANSFER,
        ):
            continue

        if tx.transaction_type == TransactionType.TRANSFER:
            # An internal move between your own ledgers is basis-neutral: leave
            # the lots in the per-asset pool rather than removing then re-adding
            # them (which would otherwise reset cost basis to zero).
            if tx.id in paired_transfer_ids or tx.transfer_pair_id:
                continue
            if tx.transfer_direction == "OUT":
                # Third-party send / unmatched outbound — same as UK HMRC engine.
                _process_disposal(
                    tx, result, method, reporting_currency=reporting_currency
                )
            elif tx.transfer_direction == "IN" and tx.fiat_value_at_trigger > 0:
                # External wallet receipt with known FMV — establish cost basis.
                total_cost = D(
                    _tx_value_reporting(tx, reporting_currency=reporting_currency)
                ) + D(
                    _tx_fee_reporting(tx, reporting_currency=reporting_currency)
                )
                qty = D(tx.amount)
                unit_cost = total_cost / qty if qty > 0 else Decimal("0")
                result.open_lots.setdefault(asset, []).append(
                    Lot(
                        source_id=tx.id,
                        asset=asset,
                        acquired_at=tx.timestamp,
                        quantity=qty,
                        unit_cost=unit_cost,
                    )
                )
            elif tx.transfer_direction == "IN":
                # Wallet import without USD — track quantity; cost unknown.
                result.open_lots.setdefault(asset, []).append(
                    Lot(
                        source_id=tx.id,
                        asset=asset,
                        acquired_at=tx.timestamp,
                        quantity=D(tx.amount),
                        unit_cost=Decimal("0"),
                    )
                )
            # IN without value = cost basis unknown until priced import.
            continue

        if tx.transaction_type in ACQUISITION_TYPES:
            if tx.amount <= 0:
                continue
            # Cost basis includes fees. Airdrops/staking use fair-market value
            # at receipt (fiat_value_at_trigger), which is also ordinary income.
            total_cost = D(
                _tx_value_reporting(tx, reporting_currency=reporting_currency)
            ) + D(_tx_fee_reporting(tx, reporting_currency=reporting_currency))
            qty = D(tx.amount)
            unit_cost = total_cost / qty
            result.open_lots.setdefault(asset, []).append(
                Lot(
                    source_id=tx.id,
                    asset=asset,
                    acquired_at=tx.timestamp,
                    quantity=qty,
                    unit_cost=unit_cost,
                )
            )
            if tx.transaction_type in INCOME_TYPES:
                bucket = result.income_by_asset.setdefault(
                    asset, {"AIRDROP": 0.0, "STAKING": 0.0}
                )
                bucket[tx.transaction_type.value] += _tx_value_reporting(
                    tx, reporting_currency=reporting_currency
                )
            continue

        if tx.transaction_type in DISPOSAL_TYPES:
            _process_disposal(
                tx, result, method, reporting_currency=reporting_currency
            )
            continue

    return result


def _process_disposal(
    tx: Transaction,
    result: EngineResult,
    method: AccountingMethod,
    *,
    reporting_currency: str = REPORTING_CURRENCY,
) -> None:
    """Consume open lots to satisfy a disposal, emitting Form 8949 rows."""
    asset = tx.asset
    remaining_to_dispose = D(tx.amount)
    if remaining_to_dispose <= 0:
        return

    # FEE rows are the fee disposal itself: proceeds = FMV of crypto spent.
    # SELL/other disposals: net proceeds = gross value less incidental fiat fees.
    if tx.transaction_type == TransactionType.FEE:
        total_proceeds = D(
            _tx_value_reporting(tx, reporting_currency=reporting_currency)
        )
    else:
        total_proceeds = D(
            _tx_value_reporting(tx, reporting_currency=reporting_currency)
        ) - D(_tx_fee_reporting(tx, reporting_currency=reporting_currency))
    # Allocate proceeds proportionally across the consumed quantity.
    proceeds_per_unit = (
        total_proceeds / D(tx.amount) if tx.amount > 0 else Decimal("0")
    )

    lots = result.open_lots.setdefault(asset, [])

    while remaining_to_dispose > LOT_EPS and lots:
        idx = _select_lot_index(lots, method)
        lot = lots[idx]
        consumed = min(lot.quantity, remaining_to_dispose)

        cost_basis = consumed * lot.unit_cost
        proceeds = consumed * proceeds_per_unit
        term, days = _holding_term(lot.acquired_at, tx.timestamp)

        result.rows.append(
            Form8949Row(
                asset=asset,
                quantity=as_float_qty(consumed),
                date_acquired=lot.acquired_at,
                date_sold=tx.timestamp,
                proceeds=as_float_fiat(proceeds),
                cost_basis=as_float_fiat(cost_basis),
                gain_loss=as_float_fiat(proceeds - cost_basis),
                term=term,
                holding_period_days=days,
                disposal_id=tx.id,
                lot_source_id=lot.source_id,
                missing_cost_basis=False,
            )
        )

        lot.quantity -= consumed
        remaining_to_dispose -= consumed
        if lot.quantity <= LOT_EPS:
            lots.pop(idx)

    if remaining_to_dispose > LOT_EPS:
        # No acquisition history covers this portion of the disposal.
        uncovered = remaining_to_dispose
        proceeds = uncovered * proceeds_per_unit
        result.rows.append(
            Form8949Row(
                asset=asset,
                quantity=as_float_qty(uncovered),
                date_acquired=tx.timestamp,
                date_sold=tx.timestamp,
                proceeds=as_float_fiat(proceeds),
                cost_basis=0.0,
                gain_loss=as_float_fiat(proceeds),
                term="SHORT",
                holding_period_days=0,
                disposal_id=tx.id,
                lot_source_id="UNKNOWN",
                missing_cost_basis=True,
            )
        )
        result.missing_cost_basis.append(
            MissingCostBasisFlag(
                disposal_id=tx.id,
                asset=asset,
                timestamp=tx.timestamp,
                disposed_amount=tx.amount,
                uncovered_amount=as_float_qty(uncovered),
                message=(
                    f"SELL of {tx.amount} {asset} has no matching purchase "
                    f"history for {as_float_qty(uncovered)} {asset}. "
                    "Cost basis defaulted to $0 (full gain)."
                ),
            )
        )


# --- Public API -------------------------------------------------------------


def _income_summary_from_transactions(
    transactions: List[Transaction],
    *,
    reporting_currency: str = REPORTING_CURRENCY,
) -> IncomeSummary:
    """Sum airdrop and staking income without running the lot engine."""
    airdrop = 0.0
    staking = 0.0
    for tx in transactions:
        if tx.transaction_type == TransactionType.AIRDROP:
            airdrop += _tx_value_reporting(tx, reporting_currency=reporting_currency)
        elif tx.transaction_type == TransactionType.STAKING:
            staking += _tx_value_reporting(tx, reporting_currency=reporting_currency)
    return IncomeSummary(
        total_income=round(airdrop + staking, 2),
        airdrop_income=round(airdrop, 2),
        staking_income=round(staking, 2),
    )


def _income_by_asset(
    transactions: List[Transaction],
    *,
    reporting_currency: str = REPORTING_CURRENCY,
) -> Dict[str, float]:
    totals: Dict[str, float] = {}
    for tx in transactions:
        if tx.transaction_type not in INCOME_TYPES:
            continue
        totals[tx.asset] = totals.get(tx.asset, 0.0) + _tx_value_reporting(
            tx, reporting_currency=reporting_currency
        )
    return totals


def calculate_uk_positions(
    transactions: List[Transaction],
    prices: Dict[str, float],
    apply_dust_filter: bool = True,
) -> Tuple[List[Position], List[MissingCostBasisFlag], IncomeSummary]:
    """Holdings and unrealized PnL from Section 104 pools (HMRC)."""
    from .hmrc_cgt_engine import compute_uk_missing_cost_basis, compute_uk_open_pools

    reporting_currency = reporting_currency_for("UK")
    pools = compute_uk_open_pools(transactions)
    income_by_asset = _income_by_asset(
        transactions, reporting_currency=reporting_currency
    )
    positions: List[Position] = []

    for asset, (quantity, total_invested) in pools.items():
        current_price_usd = float(prices.get(asset, 0.0))
        current_price = _price_reporting(
            current_price_usd, reporting_currency=reporting_currency
        )
        current_value = quantity * current_price

        if quantity <= AMOUNT_MATCH_REL_TOL:
            continue
        if apply_dust_filter and is_dust(
            quantity, current_price, total_invested=total_invested
        ):
            continue

        avg_cost = total_invested / quantity if quantity > 0 else 0.0
        unrealized = current_value - total_invested
        unrealized_pct = (
            (unrealized / total_invested * 100.0) if total_invested > 0 else 0.0
        )

        positions.append(
            Position(
                asset=asset,
                quantity=round(quantity, 8),
                average_cost_basis=round(avg_cost, 4),
                current_price=round(current_price, 4),
                total_invested=round(total_invested, 2),
                current_value=round(current_value, 2),
                unrealized_pnl=round(unrealized, 2),
                unrealized_pnl_pct=round(unrealized_pct, 2),
                realized_income=round(income_by_asset.get(asset, 0.0), 2),
            )
        )

    positions.sort(key=lambda p: p.current_value, reverse=True)
    missing = compute_uk_missing_cost_basis(transactions)
    return (
        positions,
        missing,
        _income_summary_from_transactions(
            transactions, reporting_currency=reporting_currency
        ),
    )


def _uk_realized_gains(transactions: List[Transaction], jurisdiction: str) -> RealizedGainsSummary:
    """Map the HMRC CGT summary onto the legacy RealizedGainsSummary shape.

    Used by the portfolio KPI so the dashboard's realized-gain figure matches
    the HMRC tax report. Deferred import avoids a circular dependency.
    """
    from .hmrc_cgt_engine import calculate_uk_cgt

    uk = calculate_uk_cgt(transactions, tax_year_label=None)
    return RealizedGainsSummary(
        tax_year=0,
        method=AccountingMethod.SECTION_104,
        reporting_currency=reporting_currency_for(jurisdiction),
        tax_jurisdiction=jurisdiction,
        short_term_proceeds=uk.total_proceeds,
        short_term_cost_basis=uk.total_allowable_costs,
        short_term_gain=uk.net_gain,
        long_term_proceeds=0.0,
        long_term_cost_basis=0.0,
        long_term_gain=0.0,
        total_gain=uk.net_gain,
        rows=[],
    )


def calculate_realized_pnl_by_asset(
    transactions: List[Transaction],
    method: AccountingMethod,
    *,
    tax_jurisdiction: str | None = None,
) -> List[RealizedPnlRow]:
    """Aggregate lifetime realized gains per asset for the dashboard."""
    jurisdiction = (tax_jurisdiction or TAX_JURISDICTION).upper()
    buckets: Dict[str, Dict[str, float]] = {}

    if jurisdiction == "UK":
        from .hmrc_cgt_engine import _all_disposal_rows

        disposal_rows = _all_disposal_rows(transactions)
        for row in disposal_rows:
            if is_stablecoin(row.asset):
                continue
            bucket = buckets.setdefault(
                row.asset,
                {
                    "disposal_count": 0.0,
                    "quantity": 0.0,
                    "proceeds": 0.0,
                    "cost_basis": 0.0,
                    "realized_pnl": 0.0,
                },
            )
            bucket["disposal_count"] += 1
            bucket["quantity"] += row.quantity
            bucket["proceeds"] += row.proceeds
            bucket["cost_basis"] += row.allowable_cost
            bucket["realized_pnl"] += row.gain
    else:
        reporting_currency = reporting_currency_for(jurisdiction)
        result = _run_engine(
            transactions, method, reporting_currency=reporting_currency
        )
        for row in result.rows:
            if is_stablecoin(row.asset):
                continue
            bucket = buckets.setdefault(
                row.asset,
                {
                    "disposal_count": 0.0,
                    "quantity": 0.0,
                    "proceeds": 0.0,
                    "cost_basis": 0.0,
                    "realized_pnl": 0.0,
                },
            )
            bucket["disposal_count"] += 1
            bucket["quantity"] += row.quantity
            bucket["proceeds"] += row.proceeds
            bucket["cost_basis"] += row.cost_basis
            bucket["realized_pnl"] += row.gain_loss

    rows: List[RealizedPnlRow] = []
    for asset, totals in buckets.items():
        cost = totals["cost_basis"]
        pnl = totals["realized_pnl"]
        pnl_pct = (pnl / cost * 100.0) if cost > 0 else 0.0
        rows.append(
            RealizedPnlRow(
                asset=asset,
                disposal_count=int(totals["disposal_count"]),
                quantity_disposed=round(totals["quantity"], 8),
                proceeds=round(totals["proceeds"], 2),
                cost_basis=round(cost, 2),
                realized_pnl=round(pnl, 2),
                realized_pnl_pct=round(pnl_pct, 2),
            )
        )

    rows.sort(key=lambda r: abs(r.realized_pnl), reverse=True)
    return rows


def calculate_realized_gains(
    transactions: List[Transaction],
    method: AccountingMethod,
    tax_year: int | None = None,
    *,
    tax_jurisdiction: str | None = None,
) -> RealizedGainsSummary:
    """Compute realized capital gains, optionally filtered to a tax year.

    The full ledger is always processed so cost basis is correct; only the
    *reporting* of disposal rows is filtered to the requested tax year.

    Under UK jurisdiction this routes to the HMRC share-matching engine.
    """
    jurisdiction = (tax_jurisdiction or TAX_JURISDICTION).upper()
    if jurisdiction == "UK":
        return _uk_realized_gains(transactions, jurisdiction)

    reporting_currency = reporting_currency_for(jurisdiction)
    result = _run_engine(
        transactions, method, reporting_currency=reporting_currency
    )

    rows = result.rows
    if tax_year is not None:
        rows = [r for r in rows if us_calendar_year(r.date_sold) == tax_year]

    short = [r for r in rows if r.term == "SHORT"]
    long = [r for r in rows if r.term == "LONG"]

    st_proceeds = round(sum(r.proceeds for r in short), 2)
    st_cost = round(sum(r.cost_basis for r in short), 2)
    lt_proceeds = round(sum(r.proceeds for r in long), 2)
    lt_cost = round(sum(r.cost_basis for r in long), 2)
    st_gain = round(st_proceeds - st_cost, 2)
    lt_gain = round(lt_proceeds - lt_cost, 2)

    return RealizedGainsSummary(
        tax_year=tax_year if tax_year is not None else 0,
        method=method,
        reporting_currency=reporting_currency,
        tax_jurisdiction=jurisdiction,
        short_term_proceeds=st_proceeds,
        short_term_cost_basis=st_cost,
        short_term_gain=st_gain,
        long_term_proceeds=lt_proceeds,
        long_term_cost_basis=lt_cost,
        long_term_gain=lt_gain,
        total_gain=round(st_gain + lt_gain, 2),
        rows=rows,
    )


def calculate_positions(
    transactions: List[Transaction],
    method: AccountingMethod,
    prices: Dict[str, float],
    apply_dust_filter: bool = True,
    *,
    tax_jurisdiction: str | None = None,
) -> Tuple[List[Position], List[MissingCostBasisFlag], IncomeSummary]:
    """Compute current holdings, cost basis, and unrealized PnL per asset."""
    jurisdiction = (tax_jurisdiction or TAX_JURISDICTION).upper()
    if jurisdiction == "UK":
        return calculate_uk_positions(
            transactions, prices, apply_dust_filter=apply_dust_filter
        )

    reporting_currency = reporting_currency_for(jurisdiction)
    result = _run_engine(
        transactions, method, reporting_currency=reporting_currency
    )
    positions: List[Position] = []

    for asset, lots in result.open_lots.items():
        quantity = as_float_qty(sum((lot.quantity for lot in lots), Decimal("0")))
        total_invested = sum(lot.remaining_cost_basis for lot in lots)
        current_price_usd = float(prices.get(asset, 0.0))
        current_price = _price_reporting(
            current_price_usd, reporting_currency=reporting_currency
        )
        current_value = quantity * current_price

        if quantity <= AMOUNT_MATCH_REL_TOL:
            continue
        if apply_dust_filter and is_dust(
            quantity, current_price, total_invested=total_invested
        ):
            continue

        avg_cost = total_invested / quantity if quantity > 0 else 0.0
        unrealized = current_value - total_invested
        unrealized_pct = (
            (unrealized / total_invested * 100.0) if total_invested > 0 else 0.0
        )
        income_bucket = result.income_by_asset.get(asset, {})
        realized_income = sum(income_bucket.values())

        positions.append(
            Position(
                asset=asset,
                quantity=as_float_qty(quantity),
                average_cost_basis=float(q_unit(avg_cost)),
                current_price=float(q_unit(current_price)),
                total_invested=as_float_fiat(total_invested),
                current_value=as_float_fiat(current_value),
                unrealized_pnl=as_float_fiat(unrealized),
                unrealized_pnl_pct=round(unrealized_pct, 2),
                realized_income=as_float_fiat(realized_income),
            )
        )

    positions.sort(key=lambda p: p.current_value, reverse=True)
    income_summary = _income_summary(result)
    return positions, result.missing_cost_basis, income_summary


def _income_summary(result: EngineResult) -> IncomeSummary:
    airdrop = sum(b.get("AIRDROP", 0.0) for b in result.income_by_asset.values())
    staking = sum(b.get("STAKING", 0.0) for b in result.income_by_asset.values())
    return IncomeSummary(
        total_income=round(airdrop + staking, 2),
        airdrop_income=round(airdrop, 2),
        staking_income=round(staking, 2),
    )


def build_tax_harvest_matrix(
    positions: List[Position],
    *,
    tax_jurisdiction: str | None = None,
    transactions: List[Transaction] | None = None,
    method: AccountingMethod = AccountingMethod.FIFO,
    prices_usd: Dict[str, float] | None = None,
    as_of: datetime | None = None,
    uk_unused_basic_band: float | None = None,
    us_ordinary_rate: float | None = None,
    us_ltcg_rate: float | None = None,
) -> List[TaxHarvestRow]:
    """Filter positions to losers and estimate potential tax savings.

    UK: apply basic-rate CGT to losses until ``uk_unused_basic_band`` is
    exhausted, then higher-rate CGT on the remainder (largest losses first).

    US: when open lots are available, split each asset's unrealised PnL into
    short-term (ordinary) and long-term (LTCG) as if sold ``as_of`` today;
    savings = max(0, -(st_pnl × ordinary + lt_pnl × ltcg)).
    Without lots, fall back to flat LTCG on the net unrealised loss.
    """
    jurisdiction = (tax_jurisdiction or TAX_JURISDICTION).upper()
    losers = [p for p in positions if p.unrealized_pnl < 0]
    if not losers:
        return []

    if jurisdiction == "UK":
        return _uk_harvest_rows(
            losers,
            unused_basic_band=(
                UK_UNUSED_BASIC_BAND_DEFAULT
                if uk_unused_basic_band is None
                else max(0.0, float(uk_unused_basic_band))
            ),
        )

    return _us_harvest_rows(
        losers,
        transactions=transactions,
        method=method,
        prices_usd=prices_usd or {},
        as_of=as_of or datetime.now(timezone.utc),
        ordinary_rate=(
            US_ORDINARY_INCOME_RATE
            if us_ordinary_rate is None
            else float(us_ordinary_rate)
        ),
        ltcg_rate=(
            US_LONG_TERM_CG_RATE if us_ltcg_rate is None else float(us_ltcg_rate)
        ),
    )


def _uk_harvest_rows(
    losers: List[Position],
    *,
    unused_basic_band: float,
) -> List[TaxHarvestRow]:
    """Slice losses across unused basic-rate band, then higher rate."""
    ordered = sorted(losers, key=lambda p: abs(p.unrealized_pnl), reverse=True)
    remaining_band = max(0.0, unused_basic_band)
    rows: List[TaxHarvestRow] = []
    for pos in ordered:
        loss = abs(pos.unrealized_pnl)
        basic = min(loss, remaining_band)
        higher = loss - basic
        remaining_band -= basic
        savings = basic * UK_CGT_BASIC_RATE + higher * UK_CGT_HIGHER_RATE
        rows.append(
            TaxHarvestRow(
                asset=pos.asset,
                current_bags=pos.quantity,
                current_value=pos.current_value,
                unrealized_loss=round(loss, 2),
                potential_tax_savings=round(savings, 2),
                basic_rate_loss=round(basic, 2),
                higher_rate_loss=round(higher, 2),
            )
        )
    return rows


def _us_lot_term_pnl(
    transactions: List[Transaction],
    method: AccountingMethod,
    prices_usd: Dict[str, float],
    as_of: datetime,
) -> Dict[str, Tuple[float, float]]:
    """Per asset ``(short_term_pnl, long_term_pnl)`` if sold at ``as_of``."""
    reporting_currency = reporting_currency_for("US")
    result = _run_engine(
        transactions, method, reporting_currency=reporting_currency
    )
    out: Dict[str, Tuple[float, float]] = {}
    for asset, lots in result.open_lots.items():
        st = 0.0
        lt = 0.0
        price = _price_reporting(
            float(prices_usd.get(asset, 0.0)),
            reporting_currency=reporting_currency,
        )
        for lot in lots:
            if lot.quantity <= LOT_EPS:
                continue
            qty = as_float_qty(lot.quantity)
            cost = lot.remaining_cost_basis
            pnl = qty * price - cost
            term, _days = _holding_term(lot.acquired_at, as_of)
            if term == "LONG":
                lt += pnl
            else:
                st += pnl
        out[asset] = (st, lt)
    return out


def _us_harvest_rows(
    losers: List[Position],
    *,
    transactions: List[Transaction] | None,
    method: AccountingMethod,
    prices_usd: Dict[str, float],
    as_of: datetime,
    ordinary_rate: float,
    ltcg_rate: float,
) -> List[TaxHarvestRow]:
    term_pnl: Dict[str, Tuple[float, float]] = {}
    if transactions:
        term_pnl = _us_lot_term_pnl(transactions, method, prices_usd, as_of)

    rows: List[TaxHarvestRow] = []
    for pos in sorted(losers, key=lambda p: abs(p.unrealized_pnl), reverse=True):
        loss = abs(pos.unrealized_pnl)
        st_pnl, lt_pnl = term_pnl.get(pos.asset, (0.0, 0.0))
        if pos.asset in term_pnl:
            # Tax if the whole position were sold today (gains positive).
            tax_if_sold = st_pnl * ordinary_rate + lt_pnl * ltcg_rate
            savings = max(0.0, -tax_if_sold)
            st_loss = abs(min(0.0, st_pnl))
            lt_loss = abs(min(0.0, lt_pnl))
        else:
            # No lot detail — assume long-term.
            savings = loss * ltcg_rate
            st_loss = 0.0
            lt_loss = loss
        rows.append(
            TaxHarvestRow(
                asset=pos.asset,
                current_bags=pos.quantity,
                current_value=pos.current_value,
                unrealized_loss=round(loss, 2),
                potential_tax_savings=round(savings, 2),
                short_term_loss=round(st_loss, 2),
                long_term_loss=round(lt_loss, 2),
            )
        )
    return rows
