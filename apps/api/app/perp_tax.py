"""Perpetual-futures PnL tax schedule.

Perps never enter spot lot pools (FIFO / Section 104). Depending on the
configured treatment, their exchange-reported ``realized_pnl`` is:

* ``exclude`` — dashboard reference only
* ``income`` — trading / ordinary income schedule by period
* ``capital_gains`` — folded into the main CGT / Form 8949 totals as synthetic
  disposal rows (still listed on the perps schedule for detail)

Treatment is a tax-policy choice the user sets per jurisdiction.
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable, List, Optional, Tuple

from .config import reporting_currency_for
from .fx import fx, us_calendar_year
from .instruments import format_perp_contract
from .money import D, as_float_fiat, as_float_qty
from .schemas import (
    CgtDisposalRow,
    CgtMatchType,
    Form8949Row,
    PerpTaxRow,
    PerpTaxSummary,
    RealizedGainsSummary,
    RealizedPnlRow,
    Transaction,
    UkCgtSummary,
    is_perp_transaction,
)
from .uk_tax_year import (
    annual_exempt_amount,
    available_tax_year_labels,
    is_in_tax_year,
    uk_tax_year_label,
)


def _period_label(when: datetime, jurisdiction: str) -> str:
    if jurisdiction.upper() == "UK":
        return uk_tax_year_label(when)
    return str(us_calendar_year(when))


def _in_period(when: datetime, jurisdiction: str, period_label: str) -> bool:
    if jurisdiction.upper() == "UK":
        return is_in_tax_year(when, period_label)
    return str(us_calendar_year(when)) == str(period_label)


def _contract_label(tx: Transaction) -> str:
    if tx.instrument:
        return tx.instrument
    return format_perp_contract(tx.asset)


def _perp_asset_label(tx: Transaction) -> str:
    """Synthetic asset id so perp PnL never mixes with spot tickers."""
    return f"PERP:{_contract_label(tx)}"


def _net_proceeds_and_cost(net: float) -> Tuple[float, float, float]:
    """Map a signed net PnL onto proceeds / cost / gain for CG schedules."""
    net = as_float_fiat(net)
    if net >= 0:
        return net, 0.0, net
    loss = as_float_fiat(-D(net))
    return 0.0, loss, net


def iter_perp_pnl_events(
    transactions: Iterable[Transaction],
    *,
    jurisdiction: str,
    period_label: Optional[str] = None,
) -> List[Tuple[Transaction, float, float]]:
    """Return ``(tx, net_pnl, fee)`` in reporting currency for each perp close."""
    jurisdiction = jurisdiction.upper()
    reporting_currency = reporting_currency_for(jurisdiction)
    events: List[Tuple[Transaction, float, float]] = []

    for tx in transactions:
        if not is_perp_transaction(tx) or tx.realized_pnl is None:
            continue
        if period_label is not None and not _in_period(
            tx.timestamp, jurisdiction, period_label
        ):
            continue

        pnl = fx.to_reporting(
            tx.realized_pnl,
            tx.fiat_currency or tx.counter_asset,
            tx.timestamp,
            tx.source,
            reporting_currency=reporting_currency,
        )
        fee = fx.to_reporting(
            tx.fee_fiat,
            tx.fiat_currency or tx.counter_asset,
            tx.timestamp,
            tx.source,
            reporting_currency=reporting_currency,
        )
        events.append((tx, as_float_fiat(D(pnl) - D(fee)), as_float_fiat(fee)))

    events.sort(key=lambda item: (item[0].timestamp, item[0].id))
    return events


def available_perp_periods(
    transactions: List[Transaction], jurisdiction: str
) -> List[str]:
    """Distinct period labels (newest first) that contain perp PnL events."""
    timestamps = [
        t.timestamp
        for t in transactions
        if is_perp_transaction(t) and t.realized_pnl is not None
    ]
    if not timestamps:
        return []
    if jurisdiction.upper() == "UK":
        return available_tax_year_labels(timestamps)
    return sorted({str(us_calendar_year(ts)) for ts in timestamps}, reverse=True)


def build_perp_tax_summary(
    transactions: List[Transaction],
    *,
    jurisdiction: str,
    treatment: str,
    period_label: Optional[str] = None,
) -> PerpTaxSummary:
    """Aggregate perp realized PnL for a period under the chosen treatment."""
    jurisdiction = jurisdiction.upper()
    reporting_currency = reporting_currency_for(jurisdiction)
    rows: List[PerpTaxRow] = []
    total_pnl = 0.0
    total_fees = 0.0
    gains = 0.0
    losses = 0.0

    if treatment.strip().lower() == "exclude":
        return PerpTaxSummary(
            period_label=period_label,
            treatment=treatment,
            tax_jurisdiction=jurisdiction,
            reporting_currency=reporting_currency,
            total_realized_pnl=0.0,
            total_fees=0.0,
            net_pnl=0.0,
            gains=0.0,
            losses=0.0,
            event_count=0,
            rows=[],
        )

    for tx, net, fee in iter_perp_pnl_events(
        transactions, jurisdiction=jurisdiction, period_label=period_label
    ):
        # Reconstruct gross pnl = net + fee for schedule totals.
        total_pnl += net + fee
        total_fees += fee
        if net > 0:
            gains += net
        elif net < 0:
            losses += net

        rows.append(
            PerpTaxRow(
                date=tx.timestamp,
                contract=_contract_label(tx),
                asset=tx.asset,
                source=tx.source,
                realized_pnl=round(net, 2),
                fee=round(fee, 2),
                tx_id=tx.id,
            )
        )

    return PerpTaxSummary(
        period_label=period_label,
        treatment=treatment,
        tax_jurisdiction=jurisdiction,
        reporting_currency=reporting_currency,
        total_realized_pnl=round(total_pnl, 2),
        total_fees=round(total_fees, 2),
        net_pnl=round(total_pnl - total_fees, 2),
        gains=round(gains, 2),
        losses=round(losses, 2),
        event_count=len(rows),
        rows=rows,
    )


def perp_cgt_disposal_rows(
    transactions: List[Transaction],
    *,
    jurisdiction: str = "UK",
    period_label: Optional[str] = None,
) -> List[CgtDisposalRow]:
    """Synthetic HMRC disposal rows from perp net PnL (no share matching)."""
    rows: List[CgtDisposalRow] = []
    for tx, net, _fee in iter_perp_pnl_events(
        transactions, jurisdiction=jurisdiction, period_label=period_label
    ):
        proceeds, cost, gain = _net_proceeds_and_cost(net)
        qty = as_float_qty(tx.amount) if tx.amount > 0 else 1.0
        rows.append(
            CgtDisposalRow(
                asset=_perp_asset_label(tx),
                quantity=qty,
                disposal_date=tx.timestamp,
                acquisition_date=tx.timestamp,
                proceeds=proceeds,
                allowable_cost=cost,
                gain=gain,
                match_type=CgtMatchType.PERP,
                disposal_id=tx.id,
                acquisition_ids=[],
                missing_cost_basis=False,
            )
        )
    return rows


def perp_form8949_rows(
    transactions: List[Transaction],
    *,
    jurisdiction: str = "US",
    tax_year: Optional[int] = None,
) -> List[Form8949Row]:
    """Synthetic Form 8949 rows from perp net PnL (always short-term)."""
    period = str(tax_year) if tax_year is not None else None
    rows: List[Form8949Row] = []
    for tx, net, _fee in iter_perp_pnl_events(
        transactions, jurisdiction=jurisdiction, period_label=period
    ):
        proceeds, cost, gain = _net_proceeds_and_cost(net)
        qty = as_float_qty(tx.amount) if tx.amount > 0 else 1.0
        rows.append(
            Form8949Row(
                asset=_perp_asset_label(tx),
                quantity=qty,
                date_acquired=tx.timestamp,
                date_sold=tx.timestamp,
                proceeds=proceeds,
                cost_basis=cost,
                gain_loss=gain,
                term="SHORT",
                holding_period_days=0,
                disposal_id=tx.id,
                lot_source_id="PERP",
                missing_cost_basis=False,
            )
        )
    return rows


def merge_perp_into_uk_cgt(
    summary: UkCgtSummary,
    transactions: List[Transaction],
    treatment: str,
) -> UkCgtSummary:
    """Fold perp PnL into a UK CGT summary when treatment is capital_gains."""
    if treatment.strip().lower() != "capital_gains":
        return summary

    extra = perp_cgt_disposal_rows(
        transactions,
        jurisdiction="UK",
        period_label=summary.tax_year_label,
    )
    if not extra:
        return summary

    rows = sorted(
        list(summary.rows) + extra,
        key=lambda r: (r.disposal_date, r.asset, r.disposal_id),
    )
    total_proceeds = as_float_fiat(sum((D(r.proceeds) for r in rows), D(0)))
    total_costs = as_float_fiat(sum((D(r.allowable_cost) for r in rows), D(0)))
    total_gains = as_float_fiat(
        sum((D(r.gain) for r in rows if r.gain > 0), D(0))
    )
    total_losses = as_float_fiat(
        sum((-D(r.gain) for r in rows if r.gain < 0), D(0))
    )
    net_gain = as_float_fiat(D(total_gains) - D(total_losses))
    allowance = (
        annual_exempt_amount(summary.tax_year_label)
        if summary.tax_year_label
        else summary.annual_exempt_amount
    )
    taxable = as_float_fiat(max(D(0), D(net_gain) - D(allowance)))

    return summary.model_copy(
        update={
            "total_proceeds": total_proceeds,
            "total_allowable_costs": total_costs,
            "total_gains": total_gains,
            "total_losses": total_losses,
            "net_gain": net_gain,
            "disposal_count": len({r.disposal_id for r in rows}),
            "annual_exempt_amount": allowance,
            "taxable_gain_after_allowance": taxable,
            "rows": rows,
        }
    )


def merge_perp_into_us_realized(
    summary: RealizedGainsSummary,
    transactions: List[Transaction],
    treatment: str,
) -> RealizedGainsSummary:
    """Fold perp PnL into a US Form 8949 summary when treatment is capital_gains."""
    if treatment.strip().lower() != "capital_gains":
        return summary
    if summary.tax_jurisdiction.upper() == "UK":
        return summary

    tax_year = summary.tax_year if summary.tax_year else None
    extra = perp_form8949_rows(
        transactions,
        jurisdiction=summary.tax_jurisdiction or "US",
        tax_year=tax_year,
    )
    if not extra:
        return summary

    rows = sorted(
        list(summary.rows) + extra,
        key=lambda r: (r.date_sold, r.asset, r.disposal_id),
    )
    short = [r for r in rows if r.term == "SHORT"]
    long = [r for r in rows if r.term == "LONG"]

    st_proceeds = round(sum(r.proceeds for r in short), 2)
    st_cost = round(sum(r.cost_basis for r in short), 2)
    lt_proceeds = round(sum(r.proceeds for r in long), 2)
    lt_cost = round(sum(r.cost_basis for r in long), 2)
    st_gain = round(st_proceeds - st_cost, 2)
    lt_gain = round(lt_proceeds - lt_cost, 2)

    return summary.model_copy(
        update={
            "short_term_proceeds": st_proceeds,
            "short_term_cost_basis": st_cost,
            "short_term_gain": st_gain,
            "long_term_proceeds": lt_proceeds,
            "long_term_cost_basis": lt_cost,
            "long_term_gain": lt_gain,
            "total_gain": round(st_gain + lt_gain, 2),
            "rows": rows,
        }
    )


def merge_perp_into_realized_pnl_by_asset(
    rows: List[RealizedPnlRow],
    transactions: List[Transaction],
    *,
    jurisdiction: str,
    treatment: str,
) -> List[RealizedPnlRow]:
    """Append per-contract perp CG buckets when treatment is capital_gains."""
    if treatment.strip().lower() != "capital_gains":
        return rows

    buckets: dict[str, dict[str, float]] = {
        r.asset: {
            "disposal_count": float(r.disposal_count),
            "quantity": r.quantity_disposed,
            "proceeds": r.proceeds,
            "cost_basis": r.cost_basis,
            "realized_pnl": r.realized_pnl,
        }
        for r in rows
    }

    # Lifetime (no period filter) — matches calculate_realized_pnl_by_asset.
    for tx, net, _fee in iter_perp_pnl_events(
        transactions, jurisdiction=jurisdiction, period_label=None
    ):
        asset = _perp_asset_label(tx)
        proceeds, cost, gain = _net_proceeds_and_cost(net)
        qty = as_float_qty(tx.amount) if tx.amount > 0 else 1.0
        bucket = buckets.setdefault(
            asset,
            {
                "disposal_count": 0.0,
                "quantity": 0.0,
                "proceeds": 0.0,
                "cost_basis": 0.0,
                "realized_pnl": 0.0,
            },
        )
        bucket["disposal_count"] += 1
        bucket["quantity"] += qty
        bucket["proceeds"] += proceeds
        bucket["cost_basis"] += cost
        bucket["realized_pnl"] += gain

    merged = [
        RealizedPnlRow(
            asset=asset,
            disposal_count=int(vals["disposal_count"]),
            quantity_disposed=round(vals["quantity"], 8),
            proceeds=round(vals["proceeds"], 2),
            cost_basis=round(vals["cost_basis"], 2),
            realized_pnl=round(vals["realized_pnl"], 2),
            realized_pnl_pct=round(
                (
                    vals["realized_pnl"] / vals["cost_basis"] * 100.0
                    if vals["cost_basis"] > 0
                    else 0.0
                ),
                2,
            ),
        )
        for asset, vals in buckets.items()
    ]
    merged.sort(key=lambda r: abs(r.realized_pnl), reverse=True)
    return merged
