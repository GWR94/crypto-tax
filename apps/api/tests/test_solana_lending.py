"""Tests for Kamino Lend and Marginfi lending protocol parsing."""

from datetime import datetime, timezone

import pandas as pd

from app.schemas import Transaction, TransactionType
from app.solana_lending import normalize_lending_protocols
from app.solana_wallet import parse_solana_wallet

WALLET = "4K4PdbMGiWt46LQcjDAuMzR8aKgpB2LNqPQaWL6NgEkS"
KAMINO_LEND = "9DrvZvyWh1HuAoZxvYWMvkf2XCzryCpGgHqrMjyDWpmo"
MSOL = "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So"
BSOL = "bSo13r4TkiE4KumL71LsHTPkpLWEywMUht6qBDkWeA"
COLLATERAL = "HTHAb6CigDQXtKuYX1Ta6s1BG3EUjiyFnw5QEc4rPw9u"


def test_parse_kamino_lend_deposit():
    sig = "2CsYiLomHzs3H6BS5mjUYw3CPa8cHQL2vR62PDptXYH9Cy8wwoCCXAqcwFvLRRhUz5aEsYYjyAf7bxhwjjH9H8Ra"
    rows = [
        {
            "Signature": sig,
            "Human Time": "2024-02-12 11:46:10",
            "Action": "transfer",
            "From": WALLET,
            "To": KAMINO_LEND,
            "Amount": 2021458952,
            "Flow": "out",
            "Value": 0,
            "Decimals": 9,
            "Multiplier": 1,
            "Token Address": MSOL,
            "Token": "MSOL",
            "Helius Source": "KAMINO_LEND",
            "Helius Type": "DEPOSIT_RESERVE_LIQUIDITY_AND_OBLIGATION_COLLATERAL",
        },
        {
            "Signature": sig,
            "Human Time": "2024-02-12 11:46:10",
            "Action": "transfer",
            "From": WALLET,
            "To": KAMINO_LEND,
            "Amount": 2020829545,
            "Flow": "out",
            "Value": 0,
            "Decimals": 6,
            "Multiplier": 1,
            "Token Address": COLLATERAL,
            "Token": COLLATERAL[:8],
            "Helius Source": "KAMINO_LEND",
            "Helius Type": "DEPOSIT_RESERVE_LIQUIDITY_AND_OBLIGATION_COLLATERAL",
        },
        {
            "Signature": sig,
            "Human Time": "2024-02-12 11:46:10",
            "Action": "transfer",
            "From": "",
            "To": WALLET,
            "Amount": 2020829545,
            "Flow": "in",
            "Value": 0,
            "Decimals": 6,
            "Multiplier": 1,
            "Token Address": COLLATERAL,
            "Token": COLLATERAL[:8],
            "Helius Source": "KAMINO_LEND",
            "Helius Type": "DEPOSIT_RESERVE_LIQUIDITY_AND_OBLIGATION_COLLATERAL",
        },
    ]
    txs = parse_solana_wallet(pd.DataFrame(rows), wallet=WALLET)
    assert len(txs) == 1
    tx = txs[0]
    assert tx.transaction_type == TransactionType.TRANSFER
    assert tx.transfer_direction == "OUT"
    assert tx.asset == "MSOL"
    assert abs(tx.amount - 2.021458952) < 1e-6
    assert tx.counterparty_address == KAMINO_LEND


def test_parse_kamino_lend_withdraw_and_borrow():
    withdraw_sig = "2asdQMjQGLdLHUdiKJF9withdrawsig000000000000000000000000000000000"
    borrow_sig = "65Zq9X8xUPhTyHZy2U42borrowsig00000000000000000000000000000000"
    withdraw_rows = [
        {
            "Signature": withdraw_sig,
            "Human Time": "2024-02-14 17:48:08",
            "Action": "transfer",
            "From": KAMINO_LEND,
            "To": WALLET,
            "Amount": 592201310,
            "Flow": "in",
            "Value": 0,
            "Decimals": 9,
            "Multiplier": 1,
            "Token Address": MSOL,
            "Token": "MSOL",
            "Helius Source": "KAMINO_LEND",
            "Helius Type": "WITHDRAW_OBLIGATION_COLLATERAL_AND_REDEEM_RESERVE_COLLATERAL",
        },
        {
            "Signature": withdraw_sig,
            "Human Time": "2024-02-14 17:48:08",
            "Action": "transfer",
            "From": WALLET,
            "To": "",
            "Amount": 592016353,
            "Flow": "out",
            "Value": 0,
            "Decimals": 6,
            "Multiplier": 1,
            "Token Address": COLLATERAL,
            "Token": COLLATERAL[:8],
            "Helius Source": "KAMINO_LEND",
            "Helius Type": "WITHDRAW_OBLIGATION_COLLATERAL_AND_REDEEM_RESERVE_COLLATERAL",
        },
    ]
    borrow_rows = [
        {
            "Signature": borrow_sig,
            "Human Time": "2024-02-12 11:48:02",
            "Action": "transfer",
            "From": KAMINO_LEND,
            "To": WALLET,
            "Amount": 700000000,
            "Flow": "in",
            "Value": 0,
            "Decimals": 9,
            "Multiplier": 1,
            "Token Address": BSOL,
            "Token": "BSOL",
            "Helius Source": "KAMINO_LEND",
            "Helius Type": "BORROW_OBLIGATION_LIQUIDITY",
        },
    ]
    withdraw_txs = parse_solana_wallet(pd.DataFrame(withdraw_rows), wallet=WALLET)
    borrow_txs = parse_solana_wallet(pd.DataFrame(borrow_rows), wallet=WALLET)
    assert len(withdraw_txs) == 1
    assert withdraw_txs[0].transfer_direction == "IN"
    assert withdraw_txs[0].asset == "MSOL"
    assert len(borrow_txs) == 1
    assert borrow_txs[0].transfer_direction == "IN"
    assert borrow_txs[0].asset == "BSOL"


def test_normalize_existing_kamino_lend_sell():
    sig = "deposit-sig"
    txs, n = normalize_lending_protocols(
        [
            Transaction(
                id="lend-sell",
                timestamp=datetime(2024, 2, 12, 11, 46, 10, tzinfo=timezone.utc),
                asset="MSOL",
                transaction_type=TransactionType.SELL,
                amount=2.021458952,
                fiat_value_at_trigger=0.0,
                fee_fiat=0.0,
                fiat_currency="USD",
                source="solana",
                trade_group_id=sig,
                on_chain_tx_id=sig,
                counter_asset="HTHAb6…Pw9u",
            )
        ]
    )
    assert n == 1
    assert txs[0].transaction_type == TransactionType.TRANSFER
    assert txs[0].transfer_direction == "OUT"
