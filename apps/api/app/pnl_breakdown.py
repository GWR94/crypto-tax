"""Build per-asset P&L drill-down (open lots and realized disposals)."""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Tuple

from .config import TAX_JURISDICTION, is_stablecoin, reporting_currency_for
from .money import LOT_EPS, as_float_qty
from .schemas import (
    AccountingMethod,
    AssetPnlDetail,
    PnlBreakdown,
    PnlOpenLotLine,
    PnlRealizedDisposalLine,
    Transaction,
)
from .tax_engine import (
    _price_reporting,
    _run_engine,
)


def _merge_disposal(
    groups: Dict[Tuple[str, str], PnlRealizedDisposalLine],
    *,
    asset: str,
    disposal_id: str,
    disposed_at: datetime,
    quantity: float,
    proceeds: float,
    cost_basis: float,
    gain_loss: float,
) -> None:
    key = (asset, disposal_id)
    existing = groups.get(key)
    if existing is None:
        groups[key] = PnlRealizedDisposalLine(
            transaction_id=disposal_id,
            quantity=round(quantity, 8),
            proceeds=round(proceeds, 2),
            cost_basis=round(cost_basis, 2),
            gain_loss=round(gain_loss, 2),
            disposed_at=disposed_at,
        )
        return
    groups[key] = PnlRealizedDisposalLine(
        transaction_id=disposal_id,
        quantity=round(existing.quantity + quantity, 8),
        proceeds=round(existing.proceeds + proceeds, 2),
        cost_basis=round(existing.cost_basis + cost_basis, 2),
        gain_loss=round(existing.gain_loss + gain_loss, 2),
        disposed_at=existing.disposed_at,
    )


def build_pnl_breakdown(
    transactions: List[Transaction],
    method: AccountingMethod,
    prices_usd: Dict[str, float],
    *,
    tax_jurisdiction: str | None = None,
) -> PnlBreakdown:
    """Return open-lot and disposal lines grouped by asset."""
    jurisdiction = (tax_jurisdiction or TAX_JURISDICTION).upper()
    reporting_currency = reporting_currency_for(jurisdiction)
    by_asset: Dict[str, AssetPnlDetail] = {}
    disposal_groups: Dict[Tuple[str, str], PnlRealizedDisposalLine] = {}

    if jurisdiction == "UK":
        from .hmrc_cgt_engine import _all_disposal_rows, compute_uk_open_pool_details

        for row in _all_disposal_rows(transactions):
            if is_stablecoin(row.asset):
                continue
            _merge_disposal(
                disposal_groups,
                asset=row.asset,
                disposal_id=row.disposal_id,
                disposed_at=row.disposal_date,
                quantity=row.quantity,
                proceeds=row.proceeds,
                cost_basis=row.allowable_cost,
                gain_loss=row.gain,
            )

        for asset, (pool_qty, pool_cost, acquired_at) in compute_uk_open_pool_details(
            transactions
        ).items():
            if is_stablecoin(asset):
                continue
            current_price = _price_reporting(
                float(prices_usd.get(asset, 0.0)),
                reporting_currency=reporting_currency,
            )
            current_value = pool_qty * current_price
            detail = by_asset.setdefault(asset, AssetPnlDetail(asset=asset))
            detail.open_lots.append(
                PnlOpenLotLine(
                    transaction_id=f"section-104:{asset}",
                    quantity=round(pool_qty, 8),
                    cost_basis=round(pool_cost, 2),
                    current_value=round(current_value, 2),
                    unrealized_pnl=round(current_value - pool_cost, 2),
                    # Section 104 is an average-cost pool; show earliest contributing
                    # acquisition rather than "now".
                    acquired_at=acquired_at,
                    is_pooled=True,
                )
            )
    else:
        result = _run_engine(
            transactions, method, reporting_currency=reporting_currency
        )
        for row in result.rows:
            if is_stablecoin(row.asset):
                continue
            _merge_disposal(
                disposal_groups,
                asset=row.asset,
                disposal_id=row.disposal_id,
                disposed_at=row.date_sold,
                quantity=row.quantity,
                proceeds=row.proceeds,
                cost_basis=row.cost_basis,
                gain_loss=row.gain_loss,
            )

        for asset, lots in result.open_lots.items():
            if is_stablecoin(asset):
                continue
            current_price = _price_reporting(
                float(prices_usd.get(asset, 0.0)),
                reporting_currency=reporting_currency,
            )
            detail = by_asset.setdefault(asset, AssetPnlDetail(asset=asset))
            for lot in lots:
                if lot.quantity <= LOT_EPS:
                    continue
                cost = lot.remaining_cost_basis
                qty = as_float_qty(lot.quantity)
                current_value = qty * current_price
                detail.open_lots.append(
                    PnlOpenLotLine(
                        transaction_id=lot.source_id,
                        quantity=qty,
                        cost_basis=round(cost, 2),
                        current_value=round(current_value, 2),
                        unrealized_pnl=round(current_value - cost, 2),
                        acquired_at=lot.acquired_at,
                    )
                )
            detail.open_lots.sort(key=lambda line: line.acquired_at)

    for (_asset, _disposal_id), line in disposal_groups.items():
        detail = by_asset.setdefault(_asset, AssetPnlDetail(asset=_asset))
        detail.disposals.append(line)

    for detail in by_asset.values():
        detail.disposals.sort(key=lambda line: line.disposed_at, reverse=True)

    return PnlBreakdown(by_asset=by_asset)
