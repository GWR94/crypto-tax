"""Perpetual-futures PnL tax schedule.

Perps are kept out of the spot CGT / Form 8949 engines. Depending on the
configured treatment, their realized PnL can instead be reported as trading or
ordinary income. This module aggregates exchange-reported ``realized_pnl`` by
period (UK tax year or US calendar year), converted into the reporting
currency.

Treatment is a tax-policy choice the user sets per jurisdiction; this module
only does the arithmetic and grouping.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from .config import REPORTING_CURRENCY
from .fx import fx
from .instruments import format_perp_contract
from .schemas import PerpTaxRow, PerpTaxSummary, Transaction, is_perp_transaction
from .uk_tax_year import available_tax_year_labels, is_in_tax_year, uk_tax_year_label


def _period_label(when: datetime, jurisdiction: str) -> str:
    if jurisdiction.upper() == "UK":
        return uk_tax_year_label(when)
    return str(when.year)


def _in_period(when: datetime, jurisdiction: str, period_label: str) -> bool:
    if jurisdiction.upper() == "UK":
        return is_in_tax_year(when, period_label)
    return str(when.year) == str(period_label)


def _contract_label(tx: Transaction) -> str:
    if tx.instrument:
        return tx.instrument
    return format_perp_contract(tx.asset)


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
    return sorted({str(ts.year) for ts in timestamps}, reverse=True)


def build_perp_tax_summary(
    transactions: List[Transaction],
    *,
    jurisdiction: str,
    treatment: str,
    period_label: Optional[str] = None,
) -> PerpTaxSummary:
    """Aggregate perp realized PnL for a period under the chosen treatment."""
    jurisdiction = jurisdiction.upper()
    rows: List[PerpTaxRow] = []
    total_pnl = 0.0
    total_fees = 0.0
    gains = 0.0
    losses = 0.0

    for tx in transactions:
        if not is_perp_transaction(tx) or tx.realized_pnl is None:
            continue
        if period_label is not None and not _in_period(
            tx.timestamp, jurisdiction, period_label
        ):
            continue

        pnl = fx.to_reporting(
            tx.realized_pnl, tx.fiat_currency or tx.counter_asset, tx.timestamp, tx.source
        )
        fee = fx.to_reporting(
            tx.fee_fiat, tx.fiat_currency or tx.counter_asset, tx.timestamp, tx.source
        )
        net = pnl - fee
        total_pnl += pnl
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

    rows.sort(key=lambda r: r.date)

    return PerpTaxSummary(
        period_label=period_label,
        treatment=treatment,
        tax_jurisdiction=jurisdiction,
        reporting_currency=REPORTING_CURRENCY,
        total_realized_pnl=round(total_pnl, 2),
        total_fees=round(total_fees, 2),
        net_pnl=round(total_pnl - total_fees, 2),
        gains=round(gains, 2),
        losses=round(losses, 2),
        event_count=len(rows),
        rows=rows,
    )
