"""Tests for EVM staking withdrawal reclassification."""

from datetime import datetime, timezone

from app.evm_wallet import parse_evm_wallet
from app.schemas import Transaction, TransactionType
from app.staking_withdrawals import reclassify_staking_withdrawals


def _ts(y, m, d, h=0, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=timezone.utc)


def test_pure_inbound_not_buy():
    rows = [
        {
            "hash": "0xabc123",
            "timestamp": _ts(2026, 6, 23, 8, 44),
            "asset": "MUBI",
            "amount": 6292.38,
            "flow": "in",
            "contract": "0x38e382f74dfb84608f3c1f10187f6bef5951de93",
        },
        {
            "hash": "0xabc123",
            "timestamp": _ts(2026, 6, 23, 8, 44),
            "asset": "BSSB",
            "amount": 88.48,
            "flow": "in",
            "contract": "0xda31d0d1bc934fc34f7189e38a413ca0a5e8b44f",
        },
    ]
    txs = parse_evm_wallet(rows, wallet="0xwallet", chain="ethereum")
    assert all(t.transaction_type == TransactionType.TRANSFER for t in txs)
    assert all(t.transfer_direction == "IN" for t in txs)


def test_staking_withdrawal_principal_and_reward():
    prior_out = Transaction(
        id="stake-out",
        timestamp=_ts(2023, 12, 27, 23, 38),
        asset="MUBI",
        transaction_type=TransactionType.TRANSFER,
        amount=6292.38,
        fiat_value_at_trigger=0.0,
        fee_fiat=0.0,
        source="ethereum",
        transfer_direction="OUT",
        token_mint="0x38e382f74dfb84608f3c1f10187f6bef5951de93",
    )
    group_id = "0x368e7b03b70899b853f48ba9b47d8e"
    mubi_buy = Transaction(
        id="mubi",
        timestamp=_ts(2026, 6, 23, 8, 44),
        asset="MUBI",
        transaction_type=TransactionType.BUY,
        amount=6292.38,
        fiat_value_at_trigger=3.38,
        fee_fiat=0.0,
        source="ethereum",
        trade_group_id=group_id,
        token_mint="0x38e382f74dfb84608f3c1f10187f6bef5951de93",
    )
    bssb_buy = Transaction(
        id="bssb",
        timestamp=_ts(2026, 6, 23, 8, 44),
        asset="BSSB",
        transaction_type=TransactionType.BUY,
        amount=88.48,
        fiat_value_at_trigger=0.0,
        fee_fiat=0.0,
        source="ethereum",
        trade_group_id=group_id,
        token_mint="0xda31d0d1bc934fc34f7189e38a413ca0a5e8b44f",
    )
    txs, n = reclassify_staking_withdrawals([prior_out, mubi_buy, bssb_buy])
    assert n == 2
    mubi = next(t for t in txs if t.id == "mubi")
    bssb = next(t for t in txs if t.id == "bssb")
    assert mubi.transaction_type == TransactionType.TRANSFER
    assert mubi.transfer_direction == "IN"
    assert mubi.fiat_value_at_trigger == 0.0
    assert bssb.transaction_type == TransactionType.STAKING
    assert bssb.counter_asset == "MUBI"


def test_staking_withdrawal_without_prior_stake_out():
    """Two inbound legs in one group without a matching prior OUT should not crash."""
    group_id = "0xgroup-no-prior"
    principal = Transaction(
        id="principal",
        timestamp=_ts(2026, 6, 23, 8, 44),
        asset="MUBI",
        transaction_type=TransactionType.BUY,
        amount=6292.38,
        fiat_value_at_trigger=3.38,
        fee_fiat=0.0,
        source="ethereum",
        trade_group_id=group_id,
    )
    reward = Transaction(
        id="reward",
        timestamp=_ts(2026, 6, 23, 8, 44),
        asset="BSSB",
        transaction_type=TransactionType.BUY,
        amount=88.48,
        fiat_value_at_trigger=0.0,
        fee_fiat=0.0,
        source="ethereum",
        trade_group_id=group_id,
    )
    txs, n = reclassify_staking_withdrawals([principal, reward])
    assert n == 2
    p = next(t for t in txs if t.id == "principal")
    r = next(t for t in txs if t.id == "reward")
    assert p.transaction_type == TransactionType.TRANSFER
    assert p.transfer_direction == "IN"
    assert r.transaction_type == TransactionType.STAKING
    assert r.counter_asset == "MUBI"
