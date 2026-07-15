"""Decimal helpers for tax-engine arithmetic.

Transaction / API surfaces stay as ``float``. Convert at the engine boundary with
:func:`D`, do pool/lot math in :class:`~decimal.Decimal`, then emit with
:func:`q_qty` / :func:`q_fiat`.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Union

Number = Union[int, float, str, Decimal]

# Quantity fully consumed / dust remaining (matches prior float ``1e-9``).
QTY_EPS = Decimal("1e-9")

# US lot loop stop (matches prior ``AMOUNT_MATCH_REL_TOL``).
LOT_EPS = Decimal("1e-6")

_QTY_PLACES = Decimal("0.00000001")
_FIAT_PLACES = Decimal("0.01")
_UNIT_PLACES = Decimal("0.0001")


def D(value: Number) -> Decimal:
    """Convert a numeric value to Decimal without binary-float artifacts.

    Prefer ``Decimal(str(float))`` over ``Decimal(float)`` so values that arrived
    as JSON floats (e.g. ``0.1``) become exact decimal tenths.
    """
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        return Decimal(int(value))
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value))
    return Decimal(str(value))


def q_qty(value: Number) -> Decimal:
    """Quantize an asset quantity to 8 decimal places."""
    return D(value).quantize(_QTY_PLACES, rounding=ROUND_HALF_UP)


def q_fiat(value: Number) -> Decimal:
    """Quantize fiat money to 2 decimal places (half-up)."""
    return D(value).quantize(_FIAT_PLACES, rounding=ROUND_HALF_UP)


def q_unit(value: Number) -> Decimal:
    """Quantize a per-unit cost for display (4 dp)."""
    return D(value).quantize(_UNIT_PLACES, rounding=ROUND_HALF_UP)


def as_float_qty(value: Number) -> float:
    return float(q_qty(value))


def as_float_fiat(value: Number) -> float:
    return float(q_fiat(value))


def is_dust_qty(value: Number, *, eps: Decimal = QTY_EPS) -> bool:
    return D(value) <= eps
