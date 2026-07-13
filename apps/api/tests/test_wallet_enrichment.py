"""Wallet fiat enrichment tests."""

from datetime import datetime, timezone
from unittest.mock import patch

from app.pricing import PriceStore
from app.schemas import Transaction, TransactionType
from app.wallet_enrichment import (
    copy_fiat_from_transfer_pairs,
    enrich_fee_fiat_values,
    enrich_imported_fiat_values,
    enrich_staking_fiat_values,
)


def _btc_transfer(when: str, *, fiat: float = 0.0) -> Transaction:
    return Transaction(
        id=f"btc-{when}",
        timestamp=datetime.fromisoformat(when).replace(tzinfo=timezone.utc),
        asset="BTC",
        transaction_type=TransactionType.TRANSFER,
        amount=0.021076,
        fiat_value_at_trigger=fiat,
        fee_fiat=0.0,
        fiat_currency="USD" if fiat > 0 else None,
        source="bitcoin",
        transfer_direction="IN",
    )


@patch("app.wallet_enrichment.historical_usd_prices_for_transactions")
def test_enrich_uses_historical_not_spot(mock_hist):
    mock_hist.return_value = {("BTC", datetime(2022, 11, 14, tzinfo=timezone.utc).date()): 16500.0}
    txs, updated = enrich_imported_fiat_values(
        [_btc_transfer("2022-11-14T12:10:00")],
        store=PriceStore(),
    )
    assert updated == 1
    assert txs[0].fiat_value_at_trigger == round(0.021076 * 16500.0, 2)


@patch("app.wallet_enrichment.historical_usd_prices_for_transactions")
def test_reprices_existing_chain_indexer_estimate(mock_hist):
    mock_hist.return_value = {("BTC", datetime(2022, 11, 14, tzinfo=timezone.utc).date()): 16500.0}
    txs, updated = enrich_imported_fiat_values(
        [_btc_transfer("2022-11-14T12:10:00", fiat=1348.84)],
        store=PriceStore(),
    )
    assert updated == 1
    assert txs[0].fiat_value_at_trigger < 500


def test_copy_fiat_from_matched_transfer_pair():
    pair_id = "pair-cdc-btc"
    cdc_out = Transaction(
        id="cdc-out",
        timestamp=datetime(2022, 11, 14, 12, 0, tzinfo=timezone.utc),
        asset="BTC",
        transaction_type=TransactionType.TRANSFER,
        amount=0.021676,
        fiat_value_at_trigger=295.34,
        fee_fiat=0.0,
        fiat_currency="GBP",
        source="cryptocom",
        transfer_direction="OUT",
        transfer_pair_id=pair_id,
    )
    btc_in = Transaction(
        id="btc-in",
        timestamp=datetime(2022, 11, 14, 12, 10, tzinfo=timezone.utc),
        asset="BTC",
        transaction_type=TransactionType.TRANSFER,
        amount=0.021076,
        fiat_value_at_trigger=1348.84,
        fee_fiat=0.0,
        fiat_currency="USD",
        source="bitcoin",
        transfer_direction="IN",
        transfer_pair_id=pair_id,
    )
    txs, updated = copy_fiat_from_transfer_pairs([cdc_out, btc_in])
    assert updated == 1
    btc = next(t for t in txs if t.id == "btc-in")
    assert btc.fiat_currency == "GBP"
    assert btc.fiat_value_at_trigger == round(295.34 * (0.021076 / 0.021676), 2)


@patch("app.wallet_enrichment.historical_usd_prices_for_transactions")
def test_enrich_staking_fiat_for_exchange(mock_hist):
    mock_hist.return_value = {
        ("SOL", datetime(2022, 4, 6, tzinfo=timezone.utc).date()): 100.0
    }
    tx = Transaction(
        id="binance-sol-stake",
        timestamp=datetime(2022, 4, 6, 3, 22, 23, tzinfo=timezone.utc),
        asset="SOL",
        transaction_type=TransactionType.STAKING,
        amount=0.00073808,
        fiat_value_at_trigger=0.0,
        fee_fiat=0.0,
        source="binance",
    )
    txs, updated = enrich_staking_fiat_values([tx], store=PriceStore())
    assert updated == 1
    assert txs[0].fiat_value_at_trigger == round(0.00073808 * 100.0, 2)


@patch("app.wallet_enrichment.historical_usd_prices_for_transactions")
def test_enrich_fee_fiat_for_gas(mock_hist):
    mock_hist.return_value = {
        ("ETH", datetime(2024, 5, 2, tzinfo=timezone.utc).date()): 2500.0
    }
    tx = Transaction(
        id="ethereum-tx-fee",
        timestamp=datetime(2024, 5, 2, 12, 0, tzinfo=timezone.utc),
        asset="ETH",
        transaction_type=TransactionType.FEE,
        amount=0.01,
        fiat_value_at_trigger=0.0,
        fee_fiat=0.0,
        source="ethereum",
    )
    txs, updated = enrich_fee_fiat_values([tx])
    assert updated == 1
    assert txs[0].fiat_value_at_trigger == 25.0
    assert txs[0].fiat_currency == "USD"
