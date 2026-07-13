"""Tests for staking ledger filters."""

from datetime import datetime, timezone

from app.exchange_ledger import parse_exchange_ledger
from app.ledger_filters import (
    collapse_staking_echo_transfers,
    filter_exclude_staking,
    is_dust_transaction,
    strip_dust_transactions,
)
from app.schemas import Transaction, TransactionType
import pandas as pd


def _tx(**kwargs) -> Transaction:
    defaults = {
        "id": "x",
        "timestamp": datetime(2022, 11, 9, 5, 10, tzinfo=timezone.utc),
        "asset": "ETH",
        "transaction_type": TransactionType.STAKING,
        "amount": 2.57e-06,
        "fiat_value_at_trigger": 0.0,
        "fee_fiat": 0.0,
        "source": "binance",
    }
    defaults.update(kwargs)
    return Transaction(**defaults)


def test_collapse_staking_echo_transfer():
    staking = _tx(id="stk")
    echo = _tx(
        id="echo",
        timestamp=datetime(2022, 11, 9, 18, 16, tzinfo=timezone.utc),
        transaction_type=TransactionType.TRANSFER,
        transfer_direction="OUT",
    )
    real = _tx(
        id="real",
        timestamp=datetime(2022, 11, 9, 20, 0, tzinfo=timezone.utc),
        transaction_type=TransactionType.TRANSFER,
        amount=1.0,
        transfer_direction="OUT",
    )
    kept, removed = collapse_staking_echo_transfers([staking, echo, real])
    assert removed == 1
    assert {t.id for t in kept} == {"stk", "real"}


def test_collapse_kraken_deposit_staking_duplicate():
    when = datetime(2025, 8, 1, 15, 6, 45, tzinfo=timezone.utc)
    staking = _tx(
        id="kraken-stk",
        timestamp=when,
        asset="SOL",
        amount=1.86821034,
        source="kraken",
    )
    transfer = _tx(
        id="kraken-in",
        timestamp=when,
        asset="SOL",
        amount=1.86821034,
        transaction_type=TransactionType.TRANSFER,
        transfer_direction="IN",
        source="kraken",
    )
    kept, removed = collapse_staking_echo_transfers([staking, transfer])
    assert removed == 1
    assert len(kept) == 1
    assert kept[0].id == "kraken-in"


def test_filter_exclude_staking():
    staking = _tx(id="stk")
    echo = _tx(
        id="echo",
        timestamp=datetime(2022, 11, 9, 18, 16, tzinfo=timezone.utc),
        transaction_type=TransactionType.TRANSFER,
        transfer_direction="OUT",
    )
    filtered = filter_exclude_staking([staking, echo])
    assert filtered == []


def test_is_dust_transaction():
    assert is_dust_transaction(_tx(amount=1e-7)) is True
    assert is_dust_transaction(_tx(amount=0.01, fiat_value_at_trigger=0.1)) is True
    assert is_dust_transaction(_tx(amount=1.0, fiat_value_at_trigger=100.0)) is False


def test_strip_dust_transactions():
    dust_transfer = _tx(
        id="dust",
        amount=1e-7,
        transaction_type=TransactionType.TRANSFER,
        transfer_direction="IN",
        source="solana",
    )
    real = _tx(
        id="real",
        amount=1.0,
        transaction_type=TransactionType.TRANSFER,
        transfer_direction="IN",
        source="solana",
    )
    kept, removed = strip_dust_transactions([dust_transfer, real])
    assert removed == 1
    assert [t.id for t in kept] == ["real"]


def test_sol_rent_crumb_skipped():
    crumb = _tx(
        id="crumb",
        asset="SOL",
        amount=1e-5,
        transaction_type=TransactionType.TRANSFER,
        transfer_direction="IN",
        source="solana",
        fiat_value_at_trigger=0.0,
    )
    kept, removed = strip_dust_transactions([crumb])
    assert removed == 1
    assert kept == []


def test_binance_earn_redemption_not_imported():
    csv = """User ID,Time,Account,Operation,Coin,Change,Remark
1,2022-11-09 05:10:01,Spot,Staking Rewards,ETH,0.00000257,
1,2022-11-09 18:16:10,Spot,Simple Earn Flexible Redemption,ETH,-0.00000257,
"""
    df = pd.read_csv(pd.io.common.StringIO(csv))
    txs = parse_exchange_ledger(df)
    assert len(txs) == 1
    assert txs[0].transaction_type == TransactionType.STAKING
