"""Perp contract label helpers."""

from app.instruments import format_perp_contract, parse_exchange_instrument


def test_format_perp_contract():
    assert format_perp_contract("SOL", "USDC") == "SOL - USDC"
    assert format_perp_contract("SOL") == "SOL - USDC"
    assert format_perp_contract("", "USDC") == "USDC"
    assert format_perp_contract("nan", "USDC") == "USDC"


def test_parse_exchange_instrument():
    assert parse_exchange_instrument("PERP_BTC_USDT") == ("BTC", "USDT", "perp")
    assert parse_exchange_instrument("PERP_1000FLOKI_USDT") == (
        "1000FLOKI",
        "USDT",
        "perp",
    )
