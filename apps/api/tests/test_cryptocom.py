"""Crypto.com CSV parser tests."""

from __future__ import annotations

import pandas as pd

from app.cryptocom import parse_cryptocom_export
from app.schemas import TransactionType


def _export(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def test_crypto_exchange_credit_emits_counter_sell():
    """Single credited row (UNI→CRO) must create a UNI sell leg."""
    df = _export(
        [
            {
                "Timestamp (UTC)": "2021-03-08 16:29:04",
                "Transaction Description": "Exchange",
                "Currency": "CRO",
                "Amount": 345.65669442,
                "To Currency": "UNI",
                "To Amount": 1.759,
                "Native Currency": "GBP",
                "Native Amount": 38.79,
                "Native Amount (in USD)": 53.5,
                "Transaction Kind": "crypto_exchange",
                "Transaction Hash": "",
            }
        ]
    )
    txs = parse_cryptocom_export(df)
    by_asset = {(t.asset, t.transaction_type): t for t in txs}

    buy = by_asset[("CRO", TransactionType.BUY)]
    sell = by_asset[("UNI", TransactionType.SELL)]

    assert buy.amount == 345.65669442
    assert buy.counter_asset == "UNI"
    assert sell.amount == 1.759
    assert sell.counter_asset == "CRO"
    assert sell.fiat_value_at_trigger == buy.fiat_value_at_trigger
    assert buy.trade_group_id == sell.trade_group_id


def test_crypto_exchange_debit_emits_counter_buy():
    """Single debited row (ADA→CRO) must create a CRO buy leg."""
    df = _export(
        [
            {
                "Timestamp (UTC)": "2021-03-08 16:26:05",
                "Transaction Description": "Exchange",
                "Currency": "ADA",
                "Amount": -23.5,
                "To Currency": "CRO",
                "To Amount": 181.46,
                "Native Currency": "GBP",
                "Native Amount": 18.75,
                "Native Amount (in USD)": 26.0,
                "Transaction Kind": "crypto_exchange",
                "Transaction Hash": "",
            }
        ]
    )
    txs = parse_cryptocom_export(df)
    by_asset = {(t.asset, t.transaction_type): t for t in txs}

    sell = by_asset[("ADA", TransactionType.SELL)]
    buy = by_asset[("CRO", TransactionType.BUY)]

    assert sell.amount == 23.5
    assert sell.counter_asset == "CRO"
    assert buy.amount == 181.46
    assert buy.counter_asset == "ADA"
    assert buy.fiat_value_at_trigger == sell.fiat_value_at_trigger
    assert buy.trade_group_id == sell.trade_group_id


def test_crypto_exchange_dual_rows_no_synthetic_duplicate():
    """When debit and credit rows both exist, do not double-count the sell."""
    df = _export(
        [
            {
                "Timestamp (UTC)": "2021-03-08 16:29:04",
                "Transaction Description": "Exchange",
                "Currency": "UNI",
                "Amount": -1.759,
                "To Currency": "CRO",
                "To Amount": 345.65669442,
                "Native Currency": "GBP",
                "Native Amount": 38.79,
                "Transaction Kind": "crypto_exchange",
                "Transaction Hash": "",
            },
            {
                "Timestamp (UTC)": "2021-03-08 16:29:04",
                "Transaction Description": "Exchange",
                "Currency": "CRO",
                "Amount": 345.65669442,
                "To Currency": "UNI",
                "To Amount": 1.759,
                "Native Currency": "GBP",
                "Native Amount": 38.79,
                "Transaction Kind": "crypto_exchange",
                "Transaction Hash": "",
            },
        ]
    )
    txs = parse_cryptocom_export(df)
    uni_sells = [
        t for t in txs if t.asset == "UNI" and t.transaction_type == TransactionType.SELL
    ]
    assert len(uni_sells) == 1
    assert uni_sells[0].amount == 1.759
