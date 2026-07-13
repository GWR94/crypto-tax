"""Solana spam filter must not drop real memecoin swap legs."""

from datetime import datetime, timezone

from app.schemas import Transaction, TransactionType
from app.solana_wallet import is_solana_spam
from app.token_spam import strip_spam_transactions


def _swap_leg(**kwargs) -> Transaction:
    tx_id = kwargs.pop("id", "test-leg")
    defaults = dict(
        timestamp=datetime(2024, 3, 15, 20, 50, tzinfo=timezone.utc),
        amount=877.874752,
        fiat_value_at_trigger=0.0,
        fee_fiat=0.0,
        fiat_currency="USD",
        source="solana",
        trade_group_id="3Fm1wy8Xcsw8CsXKTLN1ykvGMDmcXRBC1XFkEZwLPc4Fm4tsx8cf6vEhrSmeACb49Dq8N2skKLTUR9AGuCRWyB6h",
        token_mint="ukHH6c7mMyiWCf1b9pnWe25TSpkDDt3H5pQZgZ74J82",
    )
    defaults.update(kwargs)
    return Transaction(id=tx_id, **defaults)


def test_bome_swap_legs_are_not_spam_before_fiat_backfill():
    buy = _swap_leg(
        id="bome-buy",
        asset="BOME",
        transaction_type=TransactionType.BUY,
        counter_asset="SOL",
        counter_amount=0.079377486,
    )
    sell = _swap_leg(
        id="sol-sell",
        asset="SOL",
        transaction_type=TransactionType.SELL,
        amount=0.079377486,
        counter_asset="BOME",
        counter_amount=877.874752,
    )
    assert not is_solana_spam(buy)
    assert not is_solana_spam(sell)
    kept, removed = strip_spam_transactions([buy, sell])
    assert removed == 0
    assert len(kept) == 2


def test_unpaired_routing_leg_still_spam():
    route = _swap_leg(
        id="route",
        asset="BOME",
        transaction_type=TransactionType.BUY,
        counter_asset=None,
    )
    assert is_solana_spam(route)
