"""Tests for Drift collateral parsing and perp Data API import."""

from datetime import datetime, timezone

import pandas as pd

from app.drift import DRIFT_COLLATERAL_COUNTERPARTY, DRIFT_PROGRAM_ID, normalize_drift_collateral
from app.drift_fetch import _parse_funding_row, _parse_trade_row
from app.schemas import Transaction, TransactionType
from app.solana_fetch import helius_transactions_to_rows
from app.solana_wallet import parse_solana_wallet

WALLET = "4K4PdbMGiWt46LQcjDAuMzR8aKgpB2LNqPQaWL6NgEkS"
DRIFT_USER = "JCNCMFXo5M5qwUPg2Utu1u6YWp3MbygxqBsBeXXJfrw"
BSOL = "bSo13r4TkiE4KumL71LsHTPpL2euBYLFx6h9HP3piy1"
MSOL = "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So"


def test_parse_drift_bsol_deposit():
    sig = "2o3a9xrAvFpgoDcXuSDJrzpngJuoKMZJuCoT6FxRdJPWds725HUsE98CrgsL4S7St9W7kdtboJ8eZC5vLvjnXfg"
    rows = [
        {
            "Signature": sig,
            "Human Time": "2024-02-12 10:32:55",
            "Action": "transfer",
            "From": WALLET,
            "To": DRIFT_USER,
            "Amount": 0.7,
            "Flow": "out",
            "Value": 0,
            "Decimals": 0,
            "Multiplier": 1,
            "Token Address": BSOL,
            "Helius Source": "DRIFT",
            "Helius Type": "UNKNOWN",
        }
    ]
    txs = parse_solana_wallet(pd.DataFrame(rows), wallet=WALLET)
    assert len(txs) == 1
    tx = txs[0]
    assert tx.transaction_type == TransactionType.TRANSFER
    assert tx.transfer_direction == "OUT"
    assert tx.asset == "BSOL"
    assert abs(tx.amount - 0.7) < 1e-6
    assert tx.counterparty_address == DRIFT_COLLATERAL_COUNTERPARTY
    assert tx.venue_order_type == "drift_collateral"


def test_parse_drift_msol_withdraw():
    sig = "4W55aJRfBpWLRfu67fra5iBpEvUuF8u6CbLpmrf5UxmQs1WvQqEZVNJhYcGGZug2NEQTfGs1j3QoRvWqnn9Q8vdX"
    rows = [
        {
            "Signature": sig,
            "Human Time": "2024-02-12 10:44:17",
            "Action": "transfer",
            "From": DRIFT_USER,
            "To": WALLET,
            "Amount": 2.332187023,
            "Flow": "in",
            "Value": 0,
            "Decimals": 0,
            "Multiplier": 1,
            "Token Address": MSOL,
            "Helius Source": "DRIFT",
            "Helius Type": "UNKNOWN",
        }
    ]
    txs = parse_solana_wallet(pd.DataFrame(rows), wallet=WALLET)
    assert len(txs) == 1
    assert txs[0].transfer_direction == "IN"
    assert txs[0].asset == "MSOL"


def test_helius_drift_native_sol_withdraw():
    tx = {
        "signature": "5NohnVutff4UtPff2k9LVh5BW4ZAuzA3usfQyieA7tRcFwe3JCqsAdcZQyg8FUen2h1BeyF3VErppjccKjzZtVP8",
        "timestamp": 1719050473,
        "source": "DRIFT",
        "type": "UNKNOWN",
        "fee": 84455,
        "nativeTransfers": [],
        "tokenTransfers": [],
        "accountData": [
            {
                "account": WALLET,
                "nativeBalanceChange": 189322545,
            }
        ],
    }
    rows = helius_transactions_to_rows(WALLET, [tx])
    assert len(rows) == 1
    assert rows[0]["Flow"] == "in"
    assert abs(rows[0]["Amount"] - 189322545) < 1
    txs = parse_solana_wallet(pd.DataFrame(rows), wallet=WALLET)
    assert len(txs) == 1
    assert txs[0].asset == "SOL"
    assert txs[0].transfer_direction == "IN"
    assert abs(txs[0].amount - 0.189322545) < 1e-6


def test_normalize_drift_buy_to_transfer():
    gid = "drift-test-group"
    txs = [
        Transaction(
            id="buy-msol",
            timestamp=datetime(2024, 2, 12, tzinfo=timezone.utc),
            asset="MSOL",
            transaction_type=TransactionType.BUY,
            amount=2.33,
            fiat_value_at_trigger=0.0,
            fee_fiat=0.0,
            source="solana",
            counterparty_address=DRIFT_PROGRAM_ID,
            trade_group_id=gid,
        )
    ]
    fixed, changed = normalize_drift_collateral(txs)
    assert changed == 1
    assert fixed[0].transaction_type == TransactionType.TRANSFER
    assert fixed[0].transfer_direction == "IN"


def test_parse_drift_perp_trade_row():
    row = {
        "ts": 1700000000,
        "txSig": "abc123",
        "txSigIndex": 0,
        "marketType": "perp",
        "symbol": "SOL-PERP",
        "baseAssetAmountFilled": "1000000000",
        "quoteAssetAmountFilled": "123450000",
        "takerFee": "50000",
        "makerFee": "0",
        "taker": WALLET,
        "maker": "other",
        "user": WALLET,
        "takerOrderDirection": "long",
        "makerOrderDirection": "short",
        "takerOrderId": "99",
        "fillRecordId": "fill-1",
        "action": "fill",
        "actionExplanation": "orderFilledWithMatch",
    }
    tx = _parse_trade_row(row, WALLET)
    assert tx is not None
    assert tx.source == "drift"
    assert tx.instrument_kind == "perp"
    assert tx.asset == "SOL"
    assert tx.transaction_type == TransactionType.BUY
    assert abs(tx.amount - 1.0) < 1e-9
    assert tx.fiat_value_at_trigger == 123.45


def test_parse_drift_funding_row():
    row = {
        "ts": 1700000100,
        "txSig": "fund123",
        "txSigIndex": 1,
        "marketIndex": 0,
        "fundingPayment": "-2500000",
    }
    tx = _parse_funding_row(row, {0: "SOL-PERP"})
    assert tx is not None
    assert tx.venue_order_type == "funding"
    assert tx.realized_pnl == -2.5
    assert tx.fee_fiat == 2.5
