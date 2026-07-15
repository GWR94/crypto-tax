"""Convert tax-reporting portfolio figures to dashboard display currency."""

from __future__ import annotations

from typing import List

from .config import REPORTING_CURRENCY, SUPPORTED_DISPLAY_CURRENCIES
from .fx import fx
from .schemas import (
    AccountingMethod,
    HoldingRow,
    IncomeSummary,
    MissingCostBasisFlag,
    PerpsSummary,
    PortfolioSummary,
    Position,
    RealizedPnlRow,
    TaxHarvestRow,
)


def _resolve_display(display: str, *, reporting_currency: str) -> str:
    code = display.upper()
    if code not in SUPPORTED_DISPLAY_CURRENCIES:
        return reporting_currency
    return code


def build_portfolio_summary(
    *,
    positions_reporting: List[Position],
    holdings_reporting: List[HoldingRow],
    income_reporting: IncomeSummary,
    harvest_reporting: List[TaxHarvestRow],
    realized_pnl_reporting: List[RealizedPnlRow],
    missing: List[MissingCostBasisFlag],
    method: AccountingMethod,
    total_value: float,
    total_invested: float,
    total_unrealized: float,
    total_realized: float,
    display_currency: str,
    tax_jurisdiction: str,
    reporting_currency: str = REPORTING_CURRENCY,
    perps_reporting: PerpsSummary | None = None,
) -> PortfolioSummary:
    """Map tax-reporting amounts to the requested dashboard display currency."""
    reporting_currency = reporting_currency.upper()
    display = _resolve_display(
        display_currency, reporting_currency=reporting_currency
    )

    def d_money(value: float) -> float:
        return round(
            fx.reporting_to_display(
                value, display, reporting_currency=reporting_currency
            ),
            2,
        )

    def d_unit(value: float) -> float:
        """Per-coin unit prices need more precision than portfolio totals."""
        return round(
            fx.reporting_to_display(
                value, display, reporting_currency=reporting_currency
            ),
            4,
        )

    positions = [
        Position(
            asset=p.asset,
            quantity=p.quantity,
            average_cost_basis=d_unit(p.average_cost_basis),
            current_price=d_unit(p.current_price),
            total_invested=d_money(p.total_invested),
            current_value=d_money(p.current_value),
            unrealized_pnl=d_money(p.unrealized_pnl),
            unrealized_pnl_pct=p.unrealized_pnl_pct,
            realized_income=d_money(p.realized_income),
        )
        for p in positions_reporting
    ]

    harvest = [
        TaxHarvestRow(
            asset=h.asset,
            current_bags=h.current_bags,
            current_value=d_money(h.current_value),
            unrealized_loss=d_money(h.unrealized_loss),
            potential_tax_savings=d_money(h.potential_tax_savings),
        )
        for h in harvest_reporting
    ]

    realized_pnl = [
        RealizedPnlRow(
            asset=r.asset,
            disposal_count=r.disposal_count,
            quantity_disposed=r.quantity_disposed,
            proceeds=d_money(r.proceeds),
            cost_basis=d_money(r.cost_basis),
            realized_pnl=d_money(r.realized_pnl),
            realized_pnl_pct=r.realized_pnl_pct,
        )
        for r in realized_pnl_reporting
    ]

    income = IncomeSummary(
        total_income=d_money(income_reporting.total_income),
        airdrop_income=d_money(income_reporting.airdrop_income),
        staking_income=d_money(income_reporting.staking_income),
    )

    holdings = [
        HoldingRow(
            asset=h.asset,
            quantity=h.quantity,
            average_cost_basis=d_unit(h.average_cost_basis),
            current_value=d_money(h.current_value),
            total_invested=d_money(h.total_invested),
            portfolio_pct=h.portfolio_pct,
            is_stablecoin=h.is_stablecoin,
            price_source=h.price_source,
            is_estimated=h.is_estimated,
            unrealized_pnl=d_money(h.unrealized_pnl),
            unrealized_pnl_pct=h.unrealized_pnl_pct,
        )
        for h in holdings_reporting
    ]

    perps = PerpsSummary()
    if perps_reporting is not None:
        perps = PerpsSummary(
            trade_count=perps_reporting.trade_count,
            closed_pnl=d_money(perps_reporting.closed_pnl),
            total_fees=d_money(perps_reporting.total_fees),
            total_notional=d_money(perps_reporting.total_notional),
            winning_closes=perps_reporting.winning_closes,
            losing_closes=perps_reporting.losing_closes,
        )

    return PortfolioSummary(
        total_portfolio_value=d_money(total_value),
        total_invested=d_money(total_invested),
        total_unrealized_gain=d_money(total_unrealized),
        total_realized_gain=d_money(total_realized),
        income_summary=income,
        positions=positions,
        holdings=holdings,
        tax_harvest=harvest,
        realized_pnl=realized_pnl,
        missing_cost_basis=missing,
        method=method,
        reporting_currency=reporting_currency,
        display_currency=display,
        tax_jurisdiction=tax_jurisdiction.upper(),
        perps=perps,
    )
