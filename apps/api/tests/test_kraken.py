"""Kraken ledger parsing and normalisation."""

from datetime import datetime, timezone

from app.hmrc_cgt_engine import calculate_uk_cgt, compute_uk_missing_cost_basis
from app.ingestion import parse_csv
from app.kraken import normalize_kraken_ledger
from app.schemas import Transaction, TransactionType, spot_transactions

PARTIAL_FILL_CSV = '''"txid","refid","time","type","subtype","aclass","subclass","asset","wallet","amount","fee","balance"
"TX-BTC-1","REF-SELL-1","2026-06-14 08:45:00","trade","tradespot","currency","crypto","BTC","spot / main",-0.00300000,0.00000339,0.50000000
"TX-BTC-2","REF-SELL-1","2026-06-14 08:45:00","trade","tradespot","currency","crypto","BTC","spot / main",-0.00200000,0.00000044,0.49700000
"TX-GBP-1","REF-SELL-1","2026-06-14 08:45:00","trade","tradespot","currency","fiat","GBP","spot / main",73.56000000,0.00000000,1000.00000000
'''

RECEIVE_THEN_SELL_CSV = '''"txid","refid","time","type","subtype","aclass","subclass","asset","wallet","amount","fee","balance"
"TX-IN","REF-RCV","2026-05-01 07:49:15","receive","","currency","crypto","BTC","spot / main",0.00720000,0.00000000,0.00720000
"TX-OUT","REF-SEL","2026-05-01 08:09:35","trade","tradespot","currency","crypto","BTC","spot / main",-0.00720000,0.00000000,0.00000000
"TX-GBP","REF-SEL","2026-05-01 08:09:35","trade","tradespot","currency","fiat","GBP","spot / main",555.28000000,0.00000000,555.28000000
'''

BUY_CRYPTO_SPEND_CSV = '''"txid","refid","time","type","subtype","aclass","subclass","asset","wallet","amount","fee","balance"
"LP3QYV-U2MKC-34Q4QP","TICRR7-6AGEB-BYBGDU","2026-06-14 08:45:58","spend","","currency","crypto","BTC","spot / main",-0.00135661,65.07000000,0.01000000
'''

BUY_CRYPTO_PAIR_CSV = '''"txid","refid","time","type","subtype","aclass","subclass","asset","wallet","amount","fee","balance"
"TX-SPEND","REF-BUY","2026-06-14 08:45:58","spend","","currency","crypto","BTC","spot / main",-0.00100000,50.00000000,0.01000000
"TX-RECV","REF-BUY","2026-06-14 08:45:58","receive","","currency","crypto","ETH","spot / main",0.02000000,0.00000000,0.02000000
'''

ORPHAN_FEE_SELLS = [
    Transaction(
        id="fee-1",
        timestamp=datetime(2026, 6, 14, 7, 42, 35, tzinfo=timezone.utc),
        asset="BTC",
        transaction_type=TransactionType.SELL,
        amount=0.0018,
        fiat_value_at_trigger=0.0,
        fee_fiat=0.0,
        fiat_currency="GBP",
        source="kraken",
        trade_group_id="TECEB5-SHTUE-42ZVDS",
    ),
    Transaction(
        id="fee-2",
        timestamp=datetime(2026, 6, 14, 7, 42, 38, tzinfo=timezone.utc),
        asset="BTC",
        transaction_type=TransactionType.SELL,
        amount=0.00038638,
        fiat_value_at_trigger=0.0,
        fee_fiat=0.0,
        fiat_currency="GBP",
        source="kraken",
        trade_group_id="TJE57N-NQU7G-WZ4FIZ",
    ),
]


def test_kraken_partial_fills_merge_per_refid():
    txs = parse_csv(PARTIAL_FILL_CSV.encode(), "kraken-ledgers.csv")
    sells = [t for t in txs if t.transaction_type.value == "SELL" and t.asset == "BTC"]
    assert len(sells) == 1
    sell = sells[0]
    assert sell.amount == 0.005
    assert sell.fiat_value_at_trigger == 73.56
    assert sell.trade_group_id == "REF-SELL-1"


def test_kraken_receive_paired_with_follow_up_sell():
    txs = parse_csv(RECEIVE_THEN_SELL_CSV.encode(), "kraken-ledgers.csv")
    buys = [t for t in txs if t.asset == "BTC" and t.transaction_type == TransactionType.BUY]
    sells = [t for t in txs if t.asset == "BTC" and t.transaction_type == TransactionType.SELL]
    assert len(buys) == 1
    assert len(sells) == 1
    assert buys[0].fiat_value_at_trigger == 555.28
    report = calculate_uk_cgt(spot_transactions(txs), tax_year_label="2025/26")
    assert report.net_gain == 0.0


def test_kraken_spend_is_disposal_not_fee():
    txs = parse_csv(BUY_CRYPTO_SPEND_CSV.encode(), "kraken-ledgers.csv")
    assert len(txs) == 1
    row = txs[0]
    assert row.transaction_type == TransactionType.SELL
    assert row.asset == "BTC"
    assert row.amount == 0.00135661
    assert row.fiat_value_at_trigger == 65.07
    assert row.fee_fiat == 0.0
    assert row.trade_group_id == "TICRR7-6AGEB-BYBGDU"


def test_kraken_spend_receive_paired_as_swap():
    txs = parse_csv(BUY_CRYPTO_PAIR_CSV.encode(), "kraken-ledgers.csv")
    sells = [t for t in txs if t.asset == "BTC" and t.transaction_type == TransactionType.SELL]
    buys = [t for t in txs if t.asset == "ETH" and t.transaction_type == TransactionType.BUY]
    assert len(sells) == 1
    assert len(buys) == 1
    assert sells[0].fiat_value_at_trigger == 50.0
    assert buys[0].fiat_value_at_trigger == 50.0
    assert sells[0].counter_asset == "ETH"


def test_kraken_legacy_spend_fees_reclassified_to_sell():
    legacy = [
        Transaction(
            id="LP3QYV-U2MKC-34Q4QP",
            timestamp=datetime(2026, 6, 14, 7, 45, 58, tzinfo=timezone.utc),
            asset="BTC",
            transaction_type=TransactionType.FEE,
            amount=0.00135661,
            fiat_value_at_trigger=0.0,
            fee_fiat=65.07,
            fiat_currency="GBP",
            source="kraken",
            trade_group_id="TICRR7-6AGEB-BYBGDU",
        ),
    ]
    txs, changed = normalize_kraken_ledger(legacy)
    assert changed >= 1
    row = txs[0]
    assert row.transaction_type == TransactionType.SELL
    assert row.fiat_value_at_trigger == 65.07
    assert row.fee_fiat == 0.0
    assert row.counter_asset == "GBP"


def test_kraken_large_fee_fiat_without_spend_ratio_stays_fee():
    legacy = [
        Transaction(
            id="kraken-real-fee",
            timestamp=datetime(2026, 6, 14, 7, 45, 58, tzinfo=timezone.utc),
            asset="BTC",
            transaction_type=TransactionType.FEE,
            amount=0.1,
            fiat_value_at_trigger=0.0,
            fee_fiat=80.0,
            fiat_currency="GBP",
            source="kraken",
            trade_group_id="TREF-FEE-ONLY",
        ),
    ]
    txs, changed = normalize_kraken_ledger(legacy)
    assert changed == 0
    assert txs[0].transaction_type == TransactionType.FEE


def test_mexc_deposit_fee_not_touched_by_kraken_spend_fix():
    deposit = Transaction(
        id="mexc-deposit-1",
        timestamp=datetime(2025, 9, 18, 9, 33, 15, tzinfo=timezone.utc),
        asset="USDT",
        transaction_type=TransactionType.BUY,
        amount=1000.0,
        fiat_value_at_trigger=1000.0,
        fee_fiat=80.0,
        fiat_currency="GBP",
        source="mexc",
        trade_group_id="pay-123",
        venue_order_type="fiat_deposit",
    )
    txs, changed = normalize_kraken_ledger([deposit])
    assert changed == 0
    assert txs[0].transaction_type == TransactionType.BUY
    assert txs[0].fee_fiat == 80.0


def test_kraken_micro_btc_sells_without_buys_become_fees():
    txs, changed = normalize_kraken_ledger(ORPHAN_FEE_SELLS)
    assert changed >= 2
    fees = [t for t in txs if t.transaction_type == TransactionType.FEE and t.asset == "BTC"]
    assert len(fees) == 2
    assert fees[0].fee_fiat == 0.0
    missing = compute_uk_missing_cost_basis(spot_transactions(txs))
    assert not any(m.asset == "BTC" for m in missing)


def test_kraken_spend_with_fiat_notional_stays_sell():
    txs, changed = normalize_kraken_ledger(
        parse_csv(BUY_CRYPTO_SPEND_CSV.encode(), "kraken-ledgers.csv")
    )
    assert changed == 0
    row = txs[0]
    assert row.transaction_type == TransactionType.SELL
    assert row.fiat_value_at_trigger == 65.07
