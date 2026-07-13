"""Tests for Binance / Crypto.com transaction-history CSV parsing."""

from datetime import datetime, timezone

from app.exchange_ledger import collapse_exchange_timezone_duplicates
from app.ingestion import parse_csv
from app.schemas import Transaction, TransactionType


BINANCE_SAMPLE = """User ID,Time,Account,Operation,Coin,Change,Remark
149531592,2021-05-21 09:46:10,Spot,Deposit,LTC,1.149,
149531592,2021-05-21 09:46:53,Spot,Transaction Related,ETH,0.07779,
149531592,2021-05-21 09:46:53,Spot,Transaction Related,GBP,-147.
"""


def test_binance_time_column_header():
    txs = parse_csv(BINANCE_SAMPLE.encode(), "binance-transaction-history.csv")
    assert len(txs) == 2

    deposit = next(t for t in txs if t.asset == "LTC")
    assert deposit.transaction_type.value == "TRANSFER"
    assert deposit.transfer_direction == "IN"
    assert deposit.amount == 1.149
    assert deposit.source == "binance"

    buy = next(t for t in txs if t.asset == "ETH")
    assert buy.transaction_type.value == "BUY"
    assert buy.amount == 0.07779
    assert buy.fiat_value_at_trigger == 147.0
    assert buy.fiat_currency == "GBP"
    assert buy.source == "binance"


def test_binance_utc_plus_one_filename_shifts_to_utc():
    txs = parse_csv(
        BINANCE_SAMPLE.encode(),
        "Binance-Transaction-History-202606241540(UTC+1)-part1-of1.csv",
    )
    deposit = next(t for t in txs if t.asset == "LTC")
    assert deposit.timestamp == datetime(2021, 5, 21, 8, 46, 10, tzinfo=timezone.utc)


def test_binance_utc_and_utc_plus_one_exports_share_ids():
    utc = parse_csv(BINANCE_SAMPLE.replace("09:46", "08:46").encode(), "binance-utc.csv")
    local = parse_csv(
        BINANCE_SAMPLE.encode(),
        "Binance-Transaction-History(UTC+1).csv",
    )
    utc_ids = {t.id for t in utc}
    local_ids = {t.id for t in local}
    assert utc_ids == local_ids


def test_collapse_exchange_timezone_duplicates():
    earlier = Transaction(
        id="a",
        timestamp=datetime(2021, 5, 21, 8, 46, 10, tzinfo=timezone.utc),
        asset="LTC",
        transaction_type=TransactionType.TRANSFER,
        amount=1.149,
        fiat_value_at_trigger=0.0,
        fee_fiat=0.0,
        source="binance",
        transfer_direction="IN",
    )
    later = earlier.model_copy(
        update={
            "id": "b",
            "timestamp": datetime(2021, 5, 21, 9, 46, 10, tzinfo=timezone.utc),
        }
    )
    cleaned, removed = collapse_exchange_timezone_duplicates([earlier, later])
    assert removed == 1
    assert len(cleaned) == 1
    assert cleaned[0].id == "a"

