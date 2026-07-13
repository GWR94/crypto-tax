"""Tests for income event reclassification."""

from datetime import datetime, timezone

from app.hmrc_cgt_engine import calculate_uk_income
from app.income_classification import enrich_income_fiat_values, reclassify_income_events
from app.schemas import Transaction, TransactionType


def _ts():
    return datetime(2024, 1, 29, 10, 35, tzinfo=timezone.utc)


def test_cryptocom_earn_interest_becomes_staking():
    earn = Transaction(
        id="cdc-2021-12-09T01:13:08+00:00-crypto_earn_interest_paid-ETH",
        timestamp=_ts(),
        asset="ETH",
        transaction_type=TransactionType.AIRDROP,
        amount=0.0001726,
        fiat_value_at_trigger=0.57,
        fee_fiat=0.0,
        fiat_currency="GBP",
        source="cryptocom",
    )
    txs, n = reclassify_income_events([earn])
    assert n == 1
    assert txs[0].transaction_type == TransactionType.STAKING


def test_solana_wen_claim_becomes_airdrop():
    gid = "xKHxozCRyBrnZWoMrKeaVTAvXTVRTU7j"
    buy = Transaction(
        id="wen-buy",
        timestamp=_ts(),
        asset="WEN",
        transaction_type=TransactionType.BUY,
        amount=643652.0,
        fiat_value_at_trigger=3.24,
        fee_fiat=0.0,
        fiat_currency="USD",
        source="solana",
        counter_asset="SOL",
        trade_group_id=gid,
    )
    sell = Transaction(
        id="wen-sell",
        timestamp=_ts(),
        asset="SOL",
        transaction_type=TransactionType.SELL,
        amount=0.003654,
        fiat_value_at_trigger=0.53,
        fee_fiat=0.0,
        fiat_currency="USD",
        source="solana",
        counter_asset="WEN",
        trade_group_id=gid,
    )
    txs, n = reclassify_income_events([buy, sell])
    assert n == 2
    by_id = {t.id: t for t in txs}
    assert by_id["wen-buy"].transaction_type == TransactionType.AIRDROP
    assert by_id["wen-sell"].transaction_type == TransactionType.FEE


def test_solana_memecoin_buy_stays_buy_when_sol_out_is_material():
    """DEX buys (e.g. BOME) with meaningful SOL payment must not become airdrops."""
    gid = "3e9ANapf5bZxkKTaBfBmtT2abUfF5Z8KBrWY3FUw5qPfETMTpJohKNygLDh3teFqqt8K66dZmxJwkJGW1hd5hAts"
    buy = Transaction(
        id="bome-buy",
        timestamp=_ts(),
        asset="BOME",
        transaction_type=TransactionType.BUY,
        amount=6203.025688,
        fiat_value_at_trigger=0.0,
        fee_fiat=0.0,
        fiat_currency="USD",
        source="solana",
        counter_asset="SOL",
        trade_group_id=gid,
    )
    sell = Transaction(
        id="bome-sell",
        timestamp=_ts(),
        asset="SOL",
        transaction_type=TransactionType.SELL,
        amount=0.5352816,
        fiat_value_at_trigger=0.0,
        fee_fiat=0.0,
        fiat_currency="USD",
        source="solana",
        counter_asset="BOME",
        trade_group_id=gid,
    )
    txs, n = reclassify_income_events([buy, sell])
    assert n == 0
    assert txs[0].transaction_type == TransactionType.BUY
    assert txs[1].transaction_type == TransactionType.SELL


def test_revert_misclassified_bome_airdrop_back_to_buy():
    """If a BOME swap was wrongly stored as AIRDROP+FEE, restore BUY+SELL."""
    gid = "3e9ANapf5bZxkKTaBfBmtT2abUfF5Z8KBrWY3FUw5qPfETMTpJohKNygLDh3teFqqt8K66dZmxJwkJGW1hd5hAts"
    airdrop = Transaction(
        id="bome-airdrop",
        timestamp=_ts(),
        asset="BOME",
        transaction_type=TransactionType.AIRDROP,
        amount=6203.025688,
        fiat_value_at_trigger=0.0,
        fee_fiat=0.0,
        fiat_currency="USD",
        source="solana",
        trade_group_id=gid,
    )
    fee = Transaction(
        id="bome-fee",
        timestamp=_ts(),
        asset="SOL",
        transaction_type=TransactionType.FEE,
        amount=0.5352816,
        fiat_value_at_trigger=0.0,
        fee_fiat=0.0,
        fiat_currency="USD",
        source="solana",
        trade_group_id=gid,
    )
    txs, n = reclassify_income_events([airdrop, fee])
    assert n == 2
    by_id = {t.id: t for t in txs}
    assert by_id["bome-airdrop"].transaction_type == TransactionType.BUY
    assert by_id["bome-fee"].transaction_type == TransactionType.SELL
    assert by_id["bome-airdrop"].counter_asset == "SOL"


def test_enrich_zero_fiat_airdrop_for_uk_income():
    airdrop = Transaction(
        id="zero-arb",
        timestamp=datetime(2024, 5, 15, tzinfo=timezone.utc),
        asset="ARB",
        transaction_type=TransactionType.AIRDROP,
        amount=100.0,
        fiat_value_at_trigger=0.0,
        fee_fiat=0.0,
    )
    enriched, n = enrich_income_fiat_values([airdrop])
    assert n == 1
    assert enriched[0].fiat_value_at_trigger > 0.0
    assert enriched[0].fiat_currency == "USD"

    income = calculate_uk_income(enriched, tax_year_label="2024/25")
    assert income.airdrop_income > 0.0


def test_zero_fiat_dust_swap_without_sol_price_stays_buy():
    """Unpriced tiny-SOL legs must not be rewritten as airdrops (swap default)."""
    gid = "dust-swap-sig"
    buy = Transaction(
        id="token-buy",
        timestamp=_ts(),
        asset="BOME",
        transaction_type=TransactionType.BUY,
        amount=1000.0,
        fiat_value_at_trigger=0.0,
        fee_fiat=0.0,
        fiat_currency="USD",
        source="solana",
        counter_asset="SOL",
        trade_group_id=gid,
    )
    sell = Transaction(
        id="sol-sell",
        timestamp=_ts(),
        asset="SOL",
        transaction_type=TransactionType.SELL,
        amount=0.01,
        fiat_value_at_trigger=0.0,
        fee_fiat=0.0,
        fiat_currency="USD",
        source="solana",
        counter_asset="BOME",
        trade_group_id=gid,
    )
    txs, n = reclassify_income_events([buy, sell])
    assert n == 0
    assert txs[0].transaction_type == TransactionType.BUY
    assert txs[1].transaction_type == TransactionType.SELL
