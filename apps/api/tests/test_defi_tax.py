"""DeFi lending deposit / withdraw tax normalization."""

from datetime import datetime, timezone
from unittest.mock import patch

from app.defi_tax import EVENT_LEND_DEPOSIT, EVENT_LEND_WITHDRAW, normalize_lending_for_tax
from app.ledger_normalize import normalize_tax_ledger
from app.schemas import Transaction, TransactionType

KAMINO_LEND = "9DrvZvyWh1HuAoZxvYWMvkf2XCzryCpGgHqrMjyDWpmo"


def _tx(
    tx_id: str,
    when: str,
    asset: str,
    ttype: TransactionType,
    amount: float,
    value: float,
    *,
    direction: str | None = None,
    counterparty: str | None = None,
) -> Transaction:
    return Transaction(
        id=tx_id,
        timestamp=datetime.fromisoformat(when).replace(tzinfo=timezone.utc),
        asset=asset,
        transaction_type=ttype,
        amount=amount,
        fiat_value_at_trigger=value,
        fee_fiat=0.0,
        fiat_currency="GBP" if value else None,
        source="solana",
        transfer_direction=direction,
        counterparty_address=counterparty,
    )


def test_lend_deposit_becomes_sell_at_fmv():
    txs, n = normalize_lending_for_tax(
        [
            _tx(
                "dep",
                "2024-02-12T11:46:10",
                "MSOL",
                TransactionType.TRANSFER,
                2.0,
                210.0,
                direction="OUT",
                counterparty=KAMINO_LEND,
            )
        ]
    )
    assert n == 1
    assert txs[0].transaction_type == TransactionType.SELL
    assert txs[0].event_subtype == EVENT_LEND_DEPOSIT
    assert txs[0].fiat_value_at_trigger == 210.0
    assert txs[0].transfer_direction is None


def test_lend_withdraw_becomes_buy_at_fmv():
    txs, n = normalize_lending_for_tax(
        [
            _tx(
                "wd",
                "2024-03-01T10:00:00",
                "MSOL",
                TransactionType.TRANSFER,
                2.0,
                220.0,
                direction="IN",
                counterparty=KAMINO_LEND,
            )
        ]
    )
    assert n == 1
    assert txs[0].transaction_type == TransactionType.BUY
    assert txs[0].event_subtype == EVENT_LEND_WITHDRAW
    assert txs[0].fiat_value_at_trigger == 220.0


def test_basis_neutral_policy_keeps_transfers():
    raw = [
        _tx(
            "dep",
            "2024-02-12T11:46:10",
            "MSOL",
            TransactionType.TRANSFER,
            2.0,
            210.0,
            direction="OUT",
            counterparty=KAMINO_LEND,
        )
    ]
    txs, n = normalize_lending_for_tax(raw, policy="basis_neutral")
    assert n == 0
    assert txs[0].transaction_type == TransactionType.TRANSFER


@patch("app.historical_prices.historical_usd_prices_for_transactions")
def test_zero_fiat_deposit_enriched_from_history(mock_hist):
    mock_hist.return_value = {
        ("MSOL", datetime(2024, 2, 12, tzinfo=timezone.utc).date()): 105.0
    }
    txs, n = normalize_lending_for_tax(
        [
            _tx(
                "dep",
                "2024-02-12T11:46:10",
                "MSOL",
                TransactionType.TRANSFER,
                2.0,
                0.0,
                direction="OUT",
                counterparty=KAMINO_LEND,
            )
        ]
    )
    assert n == 1
    assert txs[0].transaction_type == TransactionType.SELL
    assert txs[0].fiat_value_at_trigger == 210.0


def test_ledger_normalize_wires_lend_tax():
    normalized, changed = normalize_tax_ledger(
        [
            _tx(
                "dep",
                "2024-02-12T11:46:10",
                "MSOL",
                TransactionType.TRANSFER,
                2.0,
                210.0,
                direction="OUT",
                counterparty=KAMINO_LEND,
            )
        ]
    )
    assert changed
    assert normalized[0].transaction_type == TransactionType.SELL
    assert normalized[0].event_subtype == EVENT_LEND_DEPOSIT
