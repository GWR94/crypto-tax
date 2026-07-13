"""Deterministic capital-gains tax engine.

This module contains pure, explicit, deterministic logic. No values are
estimated or predicted: every number is computed from the transaction ledger
using strict accounting rules (FIFO / HIFO), IRS holding-period rules, internal
transfer matching, and dust filtering.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Tuple

from .config import REPORTING_CURRENCY, TAX_JURISDICTION, is_stablecoin
from .fx import fx
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

# IRS rule: an asset held for MORE THAN one year is long-term.
LONG_TERM_THRESHOLD_DAYS = 365

# Internal-transfer matching window.
TRANSFER_MATCH_WINDOW = timedelta(minutes=15)

# Relative tolerance for "identical asset amounts" when matching transfers.
AMOUNT_MATCH_REL_TOL = 1e-6

# Wallet deposit/withdraw pairs (e.g. game custody) may report slightly different
# inbound vs outbound amounts when protocol fees are taken in-token.
WALLET_TRANSFER_PAIR_WINDOW = timedelta(hours=48)
WALLET_TRANSFER_REL_TOL = 0.01  # 1%
WALLET_TRANSFER_ABS_TOL = 1.0  # whole tokens

# Positions valued below this threshold (in reporting currency) are ignored.
DUST_THRESHOLD_REPORTING = 0.50

# Flat estimated capital-gains tax rate used for tax-loss-harvesting savings.
TAX_LOSS_HARVEST_RATE = 0.20


# --- Internal lot bookkeeping ----------------------------------------------


@dataclass
class Lot:
    """An open acquisition lot tracked per asset."""

    source_id: str
    asset: str
    acquired_at: datetime
    quantity: float
    unit_cost: float  # fiat cost basis per single unit, fee-inclusive

    @property
    def remaining_cost_basis(self) -> float:
        return self.quantity * self.unit_cost


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


def _amounts_near_match(a: float, b: float) -> bool:
    """True when two amounts are close enough to be the same wallet movement."""
    if _amounts_match(a, b):
        return True
    diff = abs(a - b)
    scale = max(abs(a), abs(b), 1e-12)
    return diff <= WALLET_TRANSFER_ABS_TOL or diff / scale <= WALLET_TRANSFER_REL_TOL


def _sort_chronologically(transactions: List[Transaction]) -> List[Transaction]:
    def _sort_key(t: Transaction) -> tuple:
        # Within a swap group, acquire before dispose so cost basis exists.
        leg = 0 if t.transaction_type in ACQUISITION_TYPES else 1
        group = t.trade_group_id or ""
        return (t.timestamp, group, leg, t.id)

    return sorted(transactions, key=_sort_key)


def _holding_term(acquired_at: datetime, disposed_at: datetime) -> Tuple[str, int]:
    """Return ('SHORT'|'LONG', holding_period_days)."""
    days = (disposed_at - acquired_at).days
    term = "LONG" if days > LONG_TERM_THRESHOLD_DAYS else "SHORT"
    return term, days


def _select_lot_index(lots: List[Lot], method: AccountingMethod) -> int:
    """Pick the index of the next lot to consume for the given method.

    FIFO -> earliest ``acquired_at``. HIFO -> highest ``unit_cost``.
    """
    if method == AccountingMethod.FIFO:
        # Earliest acquisition first; ties broken by lowest unit cost for stability.
        return min(
            range(len(lots)),
            key=lambda i: (lots[i].acquired_at, lots[i].unit_cost),
        )
    # HIFO: highest unit cost first; ties broken by earliest acquisition.
    return max(
        range(len(lots)),
        key=lambda i: (lots[i].unit_cost, -lots[i].acquired_at.timestamp()),
    )


# --- Internal transfer matching --------------------------------------------


def match_internal_transfers(
    transactions: List[Transaction],
) -> Tuple[List[Transaction], List[str]]:
    """Reclassify matched internal transfers as non-taxable ``TRANSFER`` events.

    A SELL on one ledger is matched with a BUY on a *different* ledger when both
    are for the identical asset and amount and occur within a 15-minute window.
    Both legs are reclassified to ``TRANSFER`` so the engine preserves the
    original cost-basis continuity instead of realizing a gain.

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
        if t.transaction_type == TransactionType.SELL and not is_perp_transaction(t)
    ]
    buys = [
        t
        for t in working
        if t.transaction_type == TransactionType.BUY and not is_perp_transaction(t)
    ]
    consumed_buy_ids: set[str] = set()
    reclassified: List[str] = []

    for sell in sells:
        for buy in buys:
            if buy.id in consumed_buy_ids:
                continue
            if buy.asset != sell.asset:
                continue
            # Must originate from a different ledger to be an internal transfer.
            if sell.source is not None and buy.source is not None:
                if sell.source == buy.source:
                    continue
            if not _amounts_match(sell.amount, buy.amount):
                continue
            if abs(buy.timestamp - sell.timestamp) > TRANSFER_MATCH_WINDOW:
                continue

            # Match found: reclassify both legs as a non-taxable transfer.
            sell.transaction_type = TransactionType.TRANSFER
            buy.transaction_type = TransactionType.TRANSFER
            consumed_buy_ids.add(buy.id)
            reclassified.extend([sell.id, buy.id])
            break

    return working, reclassified


def _paired_inbound_transfer(
    out_tx: Transaction, prior: List[Transaction]
) -> Transaction | None:
    """Find the inbound leg that pairs with an outbound wallet transfer."""
    for candidate in reversed(prior):
        if candidate.asset != out_tx.asset:
            continue
        if candidate.source and out_tx.source and candidate.source != out_tx.source:
            continue
        if abs(out_tx.timestamp - candidate.timestamp) > WALLET_TRANSFER_PAIR_WINDOW:
            continue

        is_inbound = (
            candidate.transaction_type == TransactionType.TRANSFER
            and candidate.transfer_direction == "IN"
        ) or candidate.transaction_type == TransactionType.BUY
        if not is_inbound:
            continue
        if not _amounts_near_match(candidate.amount, out_tx.amount):
            continue
        if candidate.amount <= out_tx.amount + AMOUNT_MATCH_REL_TOL:
            continue
        return candidate
    return None


def _transfer_fee_delta(out_tx: Transaction, prior: List[Transaction]) -> float:
    """Token quantity lost to fees between a paired inbound and outbound transfer."""
    inbound = _paired_inbound_transfer(out_tx, prior)
    if inbound is None:
        return 0.0
    return inbound.amount - out_tx.amount


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


def _tx_value_reporting(tx: Transaction) -> float:
    return fx.to_reporting(
        tx.fiat_value_at_trigger, tx.fiat_currency, tx.timestamp, tx.source
    )


def _tx_fee_reporting(tx: Transaction) -> float:
    return fx.to_reporting(tx.fee_fiat, tx.fiat_currency, tx.timestamp, tx.source)


def _price_reporting(price_usd: float) -> float:
    return fx.convert(
        price_usd, "USD", REPORTING_CURRENCY, datetime.now(timezone.utc)
    )


# --- Core lot-matching pass -------------------------------------------------


def _run_engine(
    transactions: List[Transaction], method: AccountingMethod
) -> EngineResult:
    """Single chronological pass building lots and disposal rows.

    TRANSFER events are intentionally skipped for gain calculations so that the
    cost basis of moved coins carries over untouched.
    """
    from .wallet_enrichment import enrich_fee_fiat_values

    transactions, _ = enrich_fee_fiat_values(transactions)
    result = EngineResult()
    ordered = _sort_chronologically(transactions)
    paired_transfer_ids = set(match_transfer_pairs(ordered))

    for idx, tx in enumerate(ordered):
        asset = tx.asset
        prior = ordered[:idx]

        # Perps never create spot FIFO lots. Callers normally pass
        # spot_transactions(...); guard here as defense in depth.
        if is_perp_transaction(tx):
            continue

        # Stablecoins are quote/cash — not capital-gains positions. SOL→USDC→GBP
        # is taxed on the SOL leg; USDC conversion is ignored (gain ≈ 0).
        if is_stablecoin(asset) and tx.transaction_type in (
            *ACQUISITION_TYPES,
            *DISPOSAL_TYPES,
        ):
            continue

        if tx.transaction_type == TransactionType.TRANSFER:
            # An internal move between your own ledgers is basis-neutral: leave
            # the lots in the per-asset pool rather than removing then re-adding
            # them (which would otherwise reset cost basis to zero).
            if tx.id in paired_transfer_ids or tx.transfer_pair_id:
                continue
            if tx.transfer_direction == "OUT":
                _process_transfer_out(tx, result, method)
                fee_qty = _transfer_fee_delta(tx, prior)
                if fee_qty > AMOUNT_MATCH_REL_TOL:
                    # Paired deposit/withdraw often reports less on the outbound
                    # leg than was received — the gap is protocol/game fees.
                    _process_disposal(
                        Transaction(
                            id=f"{tx.id}-transfer-fee",
                            timestamp=tx.timestamp,
                            asset=tx.asset,
                            transaction_type=TransactionType.FEE,
                            amount=fee_qty,
                            fiat_value_at_trigger=0.0,
                            fee_fiat=0.0,
                            fiat_currency=tx.fiat_currency,
                            source=tx.source,
                            token_mint=tx.token_mint,
                        ),
                        result,
                        method,
                    )
            elif tx.transfer_direction == "IN" and tx.fiat_value_at_trigger > 0:
                # External wallet receipt with known FMV — establish cost basis.
                total_cost = _tx_value_reporting(tx) + _tx_fee_reporting(tx)
                unit_cost = total_cost / tx.amount if tx.amount > 0 else 0.0
                result.open_lots.setdefault(asset, []).append(
                    Lot(
                        source_id=tx.id,
                        asset=asset,
                        acquired_at=tx.timestamp,
                        quantity=tx.amount,
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
                        quantity=tx.amount,
                        unit_cost=0.0,
                    )
                )
            # IN without value = cost basis unknown until priced import.
            continue

        if tx.transaction_type in ACQUISITION_TYPES:
            if tx.amount <= 0:
                continue
            # Cost basis includes fees. Airdrops/staking use fair-market value
            # at receipt (fiat_value_at_trigger), which is also ordinary income.
            total_cost = _tx_value_reporting(tx) + _tx_fee_reporting(tx)
            unit_cost = total_cost / tx.amount
            result.open_lots.setdefault(asset, []).append(
                Lot(
                    source_id=tx.id,
                    asset=asset,
                    acquired_at=tx.timestamp,
                    quantity=tx.amount,
                    unit_cost=unit_cost,
                )
            )
            if tx.transaction_type in INCOME_TYPES:
                bucket = result.income_by_asset.setdefault(
                    asset, {"AIRDROP": 0.0, "STAKING": 0.0}
                )
                bucket[tx.transaction_type.value] += _tx_value_reporting(tx)
            continue

        if tx.transaction_type in DISPOSAL_TYPES:
            _process_disposal(tx, result, method)
            continue

    return result


def _process_transfer_out(
    tx: Transaction, result: EngineResult, method: AccountingMethod
) -> None:
    """Remove coins from open lots for a non-taxable outbound wallet transfer."""
    remaining = tx.amount
    if remaining <= 0:
        return

    lots = result.open_lots.setdefault(tx.asset, [])
    while remaining > AMOUNT_MATCH_REL_TOL and lots:
        idx = _select_lot_index(lots, method)
        lot = lots[idx]
        consumed = min(lot.quantity, remaining)
        lot.quantity -= consumed
        remaining -= consumed
        if lot.quantity <= AMOUNT_MATCH_REL_TOL:
            lots.pop(idx)


def _process_disposal(
    tx: Transaction, result: EngineResult, method: AccountingMethod
) -> None:
    """Consume open lots to satisfy a disposal, emitting Form 8949 rows."""
    asset = tx.asset
    remaining_to_dispose = tx.amount
    if remaining_to_dispose <= 0:
        return

    # FEE rows are the fee disposal itself: proceeds = FMV of crypto spent.
    # SELL/other disposals: net proceeds = gross value less incidental fiat fees.
    if tx.transaction_type == TransactionType.FEE:
        total_proceeds = _tx_value_reporting(tx)
    else:
        total_proceeds = _tx_value_reporting(tx) - _tx_fee_reporting(tx)
    # Allocate proceeds proportionally across the consumed quantity.
    proceeds_per_unit = (
        total_proceeds / tx.amount if tx.amount > 0 else 0.0
    )

    lots = result.open_lots.setdefault(asset, [])

    while remaining_to_dispose > AMOUNT_MATCH_REL_TOL and lots:
        idx = _select_lot_index(lots, method)
        lot = lots[idx]
        consumed = min(lot.quantity, remaining_to_dispose)

        cost_basis = consumed * lot.unit_cost
        proceeds = consumed * proceeds_per_unit
        term, days = _holding_term(lot.acquired_at, tx.timestamp)

        result.rows.append(
            Form8949Row(
                asset=asset,
                quantity=consumed,
                date_acquired=lot.acquired_at,
                date_sold=tx.timestamp,
                proceeds=round(proceeds, 2),
                cost_basis=round(cost_basis, 2),
                gain_loss=round(proceeds - cost_basis, 2),
                term=term,
                holding_period_days=days,
                disposal_id=tx.id,
                lot_source_id=lot.source_id,
                missing_cost_basis=False,
            )
        )

        lot.quantity -= consumed
        remaining_to_dispose -= consumed
        if lot.quantity <= AMOUNT_MATCH_REL_TOL:
            lots.pop(idx)

    if remaining_to_dispose > AMOUNT_MATCH_REL_TOL:
        # No acquisition history covers this portion of the disposal.
        uncovered = remaining_to_dispose
        proceeds = uncovered * proceeds_per_unit
        result.rows.append(
            Form8949Row(
                asset=asset,
                quantity=uncovered,
                date_acquired=tx.timestamp,
                date_sold=tx.timestamp,
                proceeds=round(proceeds, 2),
                cost_basis=0.0,
                gain_loss=round(proceeds, 2),
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
                uncovered_amount=round(uncovered, 8),
                message=(
                    f"SELL of {tx.amount} {asset} has no matching purchase "
                    f"history for {round(uncovered, 8)} {asset}. "
                    "Cost basis defaulted to $0 (full gain)."
                ),
            )
        )


# --- Public API -------------------------------------------------------------


def _income_summary_from_transactions(
    transactions: List[Transaction],
) -> IncomeSummary:
    """Sum airdrop and staking income without running the lot engine."""
    airdrop = 0.0
    staking = 0.0
    for tx in transactions:
        if tx.transaction_type == TransactionType.AIRDROP:
            airdrop += _tx_value_reporting(tx)
        elif tx.transaction_type == TransactionType.STAKING:
            staking += _tx_value_reporting(tx)
    return IncomeSummary(
        total_income=round(airdrop + staking, 2),
        airdrop_income=round(airdrop, 2),
        staking_income=round(staking, 2),
    )


def _income_by_asset(transactions: List[Transaction]) -> Dict[str, float]:
    totals: Dict[str, float] = {}
    for tx in transactions:
        if tx.transaction_type not in INCOME_TYPES:
            continue
        totals[tx.asset] = totals.get(tx.asset, 0.0) + _tx_value_reporting(tx)
    return totals


def calculate_uk_positions(
    transactions: List[Transaction],
    prices: Dict[str, float],
    apply_dust_filter: bool = True,
) -> Tuple[List[Position], List[MissingCostBasisFlag], IncomeSummary]:
    """Holdings and unrealized PnL from Section 104 pools (HMRC)."""
    from .hmrc_cgt_engine import compute_uk_missing_cost_basis, compute_uk_open_pools

    pools = compute_uk_open_pools(transactions)
    income_by_asset = _income_by_asset(transactions)
    positions: List[Position] = []

    for asset, (quantity, total_invested) in pools.items():
        current_price_usd = float(prices.get(asset, 0.0))
        current_price = _price_reporting(current_price_usd)
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
    return positions, missing, _income_summary_from_transactions(transactions)


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
        reporting_currency=REPORTING_CURRENCY,
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
        result = _run_engine(transactions, method)
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

    result = _run_engine(transactions, method)

    rows = result.rows
    if tax_year is not None:
        rows = [r for r in rows if r.date_sold.year == tax_year]

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
        reporting_currency=REPORTING_CURRENCY,
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

    result = _run_engine(transactions, method)
    positions: List[Position] = []

    for asset, lots in result.open_lots.items():
        quantity = sum(lot.quantity for lot in lots)
        total_invested = sum(lot.remaining_cost_basis for lot in lots)
        current_price_usd = float(prices.get(asset, 0.0))
        current_price = _price_reporting(current_price_usd)
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
                quantity=round(quantity, 8),
                average_cost_basis=round(avg_cost, 4),
                current_price=round(current_price, 4),
                total_invested=round(total_invested, 2),
                current_value=round(current_value, 2),
                unrealized_pnl=round(unrealized, 2),
                unrealized_pnl_pct=round(unrealized_pct, 2),
                realized_income=round(realized_income, 2),
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


def build_tax_harvest_matrix(positions: List[Position]) -> List[TaxHarvestRow]:
    """Filter positions to losers and compute potential tax savings."""
    rows: List[TaxHarvestRow] = []
    for pos in positions:
        if pos.unrealized_pnl < 0:
            loss = abs(pos.unrealized_pnl)
            rows.append(
                TaxHarvestRow(
                    asset=pos.asset,
                    current_bags=pos.quantity,
                    current_value=pos.current_value,
                    unrealized_loss=round(loss, 2),
                    potential_tax_savings=round(loss * TAX_LOSS_HARVEST_RATE, 2),
                )
            )
    rows.sort(key=lambda r: r.unrealized_loss, reverse=True)
    return rows
