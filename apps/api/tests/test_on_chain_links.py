"""Tests for on-chain tx id backfill."""

from datetime import datetime, timezone

from app.on_chain_links import backfill_on_chain_tx_ids, infer_on_chain_tx_id
from app.schemas import Transaction, TransactionType


def _tx(**kwargs) -> Transaction:
    base = dict(
        id="x",
        timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
        asset="SOL",
        transaction_type=TransactionType.BUY,
        amount=1.0,
        fiat_value_at_trigger=0.0,
        fee_fiat=0.0,
        source="solana",
    )
    base.update(kwargs)
    return Transaction(**base)


def test_infer_solana_trade_group():
    sig = "3DSLdLzRF73fMnBoAtNctjwg8L9E8yGx"
    tx = _tx(trade_group_id=sig)
    assert infer_on_chain_tx_id(tx) == sig


def test_backfill_populates_field():
    sig = "3DSLdLzRF73fMnBoAtNctjwg8L9E8yGx"
    txs, n = backfill_on_chain_tx_ids([_tx(trade_group_id=sig)])
    assert n == 1
    assert txs[0].on_chain_tx_id == sig
