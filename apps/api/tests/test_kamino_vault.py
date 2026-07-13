"""Tests for Kamino Earn (Kvault) and Kamino Farms normalization."""

from datetime import datetime, timezone

import pandas as pd

from app.kamino_vault import normalize_kamino_vault
from app.schemas import Transaction, TransactionType
from app.solana_wallet import parse_solana_wallet

KVAULT = "KvauGMspG5k6rtzrqqn7WNn3oZdyKqLKwK2XWQ8FLjd"
SHARE_MINT = "FiM4VQdXXnTXL7GgChryf9zHNG9cmvKECwf34L2y3CkN"
WSOL = "So11111111111111111111111111111111111111112"
WALLET = "4K4PdbMGiWt46LQcjDAuMzR8aKgpB2LNqPQaWL6NgEkS"
SIG = "5XRKXDFqsqhLH4TRR3YuMjX3PjfDggQiD4mpFvUnxY66KvFNKsMhbq6oe853QFKzNAXrtQ6yhKWQ4hU6BYgw2QAR"

JTO_MINT = "jtojtomepa8beP8AuQc6eXt5FriJwfFMwQx2v2f9mCL"
JITOSOL_MINT = "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn"
KAMINO_JTO_FARM = "FTj3SbJuawWT42Wj2GWxmbqLXNpXFE4ypE1bN17Sh5J5"
JTO_SWAP_SIG = (
    "4VUzJvDN27B3mz9AdA1KBCkpuT5UZDUWA6Zyr42cDyxpv63cDn4Zsm13JxrmUhKvfbyaEz2TCFhQWaF9qpNyXoLo"
)
RECEIPT_MINT = "5N5E1SAD8KaminoJTOFarmReceiptMintToken1234567890"
ROUTE_POOL = "BDxWKPm7X8v9QZ2L5xKf9mNw3Jy4hT6CpR8uV1aE2bQcD"


def _tx(**kwargs) -> Transaction:
    defaults = {
        "timestamp": datetime(2025, 11, 18, 9, 36, 11, tzinfo=timezone.utc),
        "asset": "SOL",
        "transaction_type": TransactionType.BUY,
        "amount": 8.033449754,
        "fiat_value_at_trigger": 1164.85,
        "fee_fiat": 0.0,
        "fiat_currency": "USD",
        "source": "solana",
        "trade_group_id": SIG,
        "on_chain_tx_id": SIG,
        "counter_asset": "FiM4\u20263CkN",
        "token_mint": WSOL,
    }
    defaults.update(kwargs)
    tx_id = defaults.pop("id", "test-id")
    return Transaction(id=tx_id, **defaults)


def test_normalize_existing_kamino_withdraw_buy():
    txs, n = normalize_kamino_vault([_tx(id="kamino-buy")])
    assert n == 1
    assert len(txs) == 1
    tx = txs[0]
    assert tx.transaction_type == TransactionType.TRANSFER
    assert tx.transfer_direction == "IN"
    assert abs(tx.amount - 4.016724877) < 0.001
    assert abs(tx.fiat_value_at_trigger - 582.42) < 1.0
    assert tx.counter_asset is None


def test_normalize_existing_kamino_deposit_sell():
    txs, n = normalize_kamino_vault(
        [
            _tx(
                id="kamino-sell",
                transaction_type=TransactionType.SELL,
                amount=7.974790304,
                fiat_value_at_trigger=1156.34,
                timestamp=datetime(2025, 10, 2, 10, 31, 30, tzinfo=timezone.utc),
                trade_group_id="deposit-sig",
                on_chain_tx_id="deposit-sig",
            )
        ]
    )
    assert n == 1
    tx = txs[0]
    assert tx.transaction_type == TransactionType.TRANSFER
    assert tx.transfer_direction == "OUT"
    assert abs(tx.amount - 3.987395152) < 0.001


def test_parse_kamino_withdraw_csv_group():
    rows = [
        {
            "Signature": SIG,
            "Human Time": "2025-11-18 09:36:11",
            "Action": "transfer",
            "From": KVAULT,
            "To": WALLET,
            "Amount": 4016724879,
            "Flow": "in",
            "Value": 582.42,
            "Decimals": 9,
            "Multiplier": 1,
            "Token Address": WSOL,
            "Token": "WSOL",
        },
        {
            "Signature": SIG,
            "Human Time": "2025-11-18 09:36:11",
            "Action": "transfer",
            "From": KVAULT,
            "To": WALLET,
            "Amount": 4016699054,
            "Flow": "in",
            "Value": 0,
            "Decimals": 9,
            "Multiplier": 1,
            "Token": "SOL",
        },
        {
            "Signature": SIG,
            "Human Time": "2025-11-18 09:36:11",
            "Action": "transfer",
            "From": WALLET,
            "To": KVAULT,
            "Amount": 3885254677,
            "Flow": "out",
            "Value": 582.42,
            "Decimals": 6,
            "Multiplier": 1,
            "Token Address": SHARE_MINT,
            "Token": SHARE_MINT[:8],
        },
    ]
    df = pd.DataFrame(rows)
    txs = parse_solana_wallet(df, wallet=WALLET)
    assert len(txs) == 1
    tx = txs[0]
    assert tx.transaction_type == TransactionType.TRANSFER
    assert tx.transfer_direction == "IN"
    assert abs(tx.amount - 4.016724879) < 0.001
    assert tx.fiat_value_at_trigger == 582.42


def test_parse_jto_swap_then_kamino_farms_deposit():
    """Jupiter swap into JTO then immediate Kamino Farms deposit in one signature."""
    rows = [
        {
            "Signature": JTO_SWAP_SIG,
            "Human Time": "2024-02-22 18:28:36",
            "Action": "transfer",
            "From": WALLET,
            "To": ROUTE_POOL,
            "Amount": 1434749320,
            "Flow": "out",
            "Value": 165.0,
            "Decimals": 9,
            "Multiplier": 1,
            "Token Address": JITOSOL_MINT,
            "Token": "JITOSOL",
        },
        {
            "Signature": JTO_SWAP_SIG,
            "Human Time": "2024-02-22 18:28:36",
            "Action": "transfer",
            "From": WALLET,
            "To": ROUTE_POOL,
            "Amount": 986275666,
            "Flow": "out",
            "Value": 112.48,
            "Decimals": 9,
            "Multiplier": 1,
            "Token Address": JITOSOL_MINT,
            "Token": "JITOSOL",
        },
        {
            "Signature": JTO_SWAP_SIG,
            "Human Time": "2024-02-22 18:28:36",
            "Action": "transfer",
            "From": ROUTE_POOL,
            "To": WALLET,
            "Amount": 76147689091,
            "Flow": "in",
            "Value": 277.48,
            "Decimals": 9,
            "Multiplier": 1,
            "Token Address": JTO_MINT,
            "Token": "JTO",
        },
        {
            "Signature": JTO_SWAP_SIG,
            "Human Time": "2024-02-22 18:28:36",
            "Action": "transfer",
            "From": WALLET,
            "To": ROUTE_POOL,
            "Amount": 76147689068,
            "Flow": "out",
            "Value": 277.48,
            "Decimals": 9,
            "Multiplier": 1,
            "Token Address": JTO_MINT,
            "Token": "JTO",
        },
        {
            "Signature": JTO_SWAP_SIG,
            "Human Time": "2024-02-22 18:28:36",
            "Action": "transfer",
            "From": "",
            "To": WALLET,
            "Amount": 423336220706,
            "Flow": "in",
            "Value": 0,
            "Decimals": 6,
            "Multiplier": 1,
            "Token Address": RECEIPT_MINT,
            "Token": RECEIPT_MINT[:8],
        },
        {
            "Signature": JTO_SWAP_SIG,
            "Human Time": "2024-02-22 18:28:36",
            "Action": "transfer",
            "From": WALLET,
            "To": KAMINO_JTO_FARM,
            "Amount": 423336220706,
            "Flow": "out",
            "Value": 0,
            "Decimals": 6,
            "Multiplier": 1,
            "Token Address": RECEIPT_MINT,
            "Token": RECEIPT_MINT[:8],
        },
    ]
    txs = parse_solana_wallet(pd.DataFrame(rows), wallet=WALLET)
    by_type = {(t.transaction_type, t.asset): t for t in txs}
    assert (TransactionType.SELL, "JITOSOL") in by_type
    assert (TransactionType.BUY, "JTO") in by_type

    sell = by_type[(TransactionType.SELL, "JITOSOL")]
    buy = by_type[(TransactionType.BUY, "JTO")]
    xfer = [t for t in txs if t.transaction_type == TransactionType.TRANSFER and t.asset == "JTO"][0]

    assert abs(sell.amount - 2.421024986) < 0.0001
    assert abs(buy.amount - 76.147689091) < 0.0001
    assert buy.fiat_value_at_trigger == 277.48
    assert xfer.transfer_direction == "OUT"
    assert xfer.counterparty_address == KAMINO_JTO_FARM
    assert abs(xfer.amount - 76.147689091) < 0.0001
