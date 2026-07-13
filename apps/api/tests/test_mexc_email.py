"""Tests for MEXC pasted-email parser."""

from app.mexc_email import parse_mexc_emails, split_email_blocks, transactions_to_csv
from app.schemas import TransactionType

DEPOSIT_GBP_FEE = """
Payment ID: pay_3tbaci2lxeeehhzmcpj5wiagh4
Bank card BIN/PAN: 5374******1879
Deposit Fiat Amount: 220.0 GBP
Received Crypto: 279.4193 USDT
Deposit Date: Sun, 16 Jul 2023 18:15:18 GMT
Processing Fee: 8.5825
"""

DEPOSIT_USDT_FEE = """
Payment ID: 1237210031634223104
Bank card BIN/PAN: 5374******9561
Deposit Fiat Amount: 1000 GBP
Received Crypto: 1234.3298 USDT
Deposit Date: Wed, 24 Jul 2024 16:23:59 GMT
Processing Fee: 58.1622 USDT
"""

WITHDRAWAL = """
Withdrawal Amount:
843.34 CROWN2
Withdrawal Time:
2025/07/29 19:42:57(UTC+8)
Withdrawal Address:
4K4PdbMGiWt46LQcjDAuMzR8aKgpB2LNqPQaWL6NgEkS
TxID:
3dMFBqvWjQrr9zA2h19e7T6Bt37aLh3sSGv86sgKzt29SQvLZhY1UncQ9YUAYnDRfqaGzmfSz1n1oNcaSNEKZwiX
"""

FUTURES = """
Your RNDR_USDT  futures SL order triggered has been filled completely at 2024-03-09 19:36:31. Number of cont. filled: 431.Average filled price: 12.6564.
"""

FUTURES_POSITION_SL = """
Your BTC_USDT futures position SL order triggered has been filled completely at \u200e2024-10-17 13:51:53\u200e. Number of cont. filled: 1157. Average filled price: 67050.2.
"""

ALL_EMAILS = DEPOSIT_GBP_FEE + "\n" + DEPOSIT_USDT_FEE + "\n" + WITHDRAWAL + "\n" + FUTURES + "\n" + FUTURES_POSITION_SL


def test_split_email_blocks():
    blocks = split_email_blocks(ALL_EMAILS)
    assert len(blocks) == 5


def test_parse_fiat_deposit_gbp_fee():
    result = parse_mexc_emails(DEPOSIT_GBP_FEE)
    assert len(result.transactions) == 1
    tx = result.transactions[0]
    assert tx.id == "mexc-deposit-pay_3tbaci2lxeeehhzmcpj5wiagh4"
    assert tx.transaction_type == TransactionType.BUY
    assert tx.asset == "USDT"
    assert abs(tx.amount - 279.4193) < 1e-6
    assert tx.fiat_value_at_trigger == 220.0
    assert tx.fee_fiat == 8.5825
    assert tx.fiat_currency == "GBP"


def test_parse_fiat_deposit_usdt_fee_imputed():
    result = parse_mexc_emails(DEPOSIT_USDT_FEE)
    assert len(result.transactions) == 1
    tx = result.transactions[0]
    assert tx.fiat_value_at_trigger == 1000.0
    expected_fee = 58.1622 * (1000.0 / 1234.3298)
    assert abs(tx.fee_fiat - expected_fee) < 0.01
    assert any("imputed" in note.lower() for note in result.warnings)


def test_parse_withdrawal():
    result = parse_mexc_emails(WITHDRAWAL)
    assert len(result.transactions) == 1
    tx = result.transactions[0]
    assert tx.transaction_type == TransactionType.TRANSFER
    assert tx.transfer_direction == "OUT"
    assert tx.asset == "CROWN"
    assert abs(tx.amount - 843.34) < 1e-6
    assert tx.on_chain_tx_id.startswith("3dMFBqvW")
    assert tx.counterparty_address == "4K4PdbMGiWt46LQcjDAuMzR8aKgpB2LNqPQaWL6NgEkS"
    assert tx.timestamp.hour == 11
    assert tx.timestamp.minute == 42


def test_parse_futures_sl_fill_skipped_with_warning():
    result = parse_mexc_emails(FUTURES)
    assert len(result.transactions) == 0
    assert len(result.skipped_blocks) == 0
    assert any("Skipped" in w and "RNDR" in w for w in result.warnings)


def test_parse_futures_position_sl_skipped_with_warning():
    result = parse_mexc_emails(FUTURES_POSITION_SL)
    assert len(result.transactions) == 0
    assert any("BTC" in w and "Skipped" in w for w in result.warnings)


def test_parse_multiple_emails_at_once():
    result = parse_mexc_emails(ALL_EMAILS)
    assert len(result.transactions) == 3
    assert any("RNDR" in w or "BTC" in w for w in result.warnings)
    csv_text = transactions_to_csv(result.transactions)
    assert "mexc-deposit-pay_3tbaci2lxeeehhzmcpj5wiagh4" in csv_text
    assert "mexc-withdraw-3dMFBqvW" in csv_text
