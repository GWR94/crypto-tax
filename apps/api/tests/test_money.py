"""Decimal helpers for tax-engine arithmetic."""

from decimal import Decimal

from app.money import D, as_float_fiat, as_float_qty, is_dust_qty, q_fiat, q_qty


def test_d_avoids_binary_float_artifacts():
    assert D(0.1) + D(0.2) == Decimal("0.3")
    assert D("0.1") + D("0.2") == Decimal("0.3")


def test_quantize_qty_and_fiat():
    assert q_qty("1.123456789") == Decimal("1.12345679")
    assert q_fiat("10.005") == Decimal("10.01")
    assert as_float_qty("2.0") == 2.0
    assert as_float_fiat("99.999") == 100.0


def test_dust_qty():
    assert is_dust_qty("1e-10")
    assert not is_dust_qty("1e-8")
