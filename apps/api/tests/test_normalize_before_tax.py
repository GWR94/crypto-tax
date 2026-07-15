"""Portfolio/tax reads must normalize even without GET /transactions first."""

from __future__ import annotations

from datetime import datetime, timezone

from app.defi_tax import EVENT_LEND_DEPOSIT
from app.hmrc_cgt_engine import calculate_uk_cgt
from app.main import _ensure_normalized_active_ledger, _spot_tax_ledger
from app.schemas import Transaction, TransactionType, spot_transactions
from app.state import state

KAMINO_LEND = "9DrvZvyWh1HuAoZxvYWMvkf2XCzryCpGgHqrMjyDWpmo"


def _lend_deposit_raw() -> Transaction:
    return Transaction(
        id="test-normalize-before-tax-lend",
        timestamp=datetime.fromisoformat("2024-02-12T11:46:10").replace(
            tzinfo=timezone.utc
        ),
        asset="MSOL",
        transaction_type=TransactionType.TRANSFER,
        amount=2.0,
        fiat_value_at_trigger=210.0,
        fee_fiat=0.0,
        fiat_currency="GBP",
        source="solana",
        transfer_direction="OUT",
        counterparty_address=KAMINO_LEND,
    )


def test_spot_tax_ledger_normalizes_lending_without_transactions_get():
    """Import-shaped TRANSFER OUT must become lend_deposit before CGT math."""
    prior_mode = state.data_mode()
    prior = state.transactions()
    try:
        state.set_data_mode("live")
        state.replace_all(
            [
                Transaction(
                    id="test-normalize-before-tax-buy",
                    timestamp=datetime.fromisoformat("2024-01-01T00:00:00").replace(
                        tzinfo=timezone.utc
                    ),
                    asset="MSOL",
                    transaction_type=TransactionType.BUY,
                    amount=2.0,
                    fiat_value_at_trigger=200.0,
                    fiat_currency="GBP",
                    source="solana",
                ),
                _lend_deposit_raw(),
            ]
        )

        # Raw state still has TRANSFER (no GET /transactions yet).
        raw = next(t for t in state.transactions() if t.id.endswith("-lend"))
        assert raw.transaction_type == TransactionType.TRANSFER

        tax_txs = _spot_tax_ledger()
        lend = next(t for t in tax_txs if t.id.endswith("-lend"))
        assert lend.transaction_type == TransactionType.SELL
        assert lend.event_subtype == EVENT_LEND_DEPOSIT

        report = calculate_uk_cgt(tax_txs, tax_year_label="2023/24")
        assert report.disposal_count >= 1
        assert any(r.disposal_id.endswith("-lend") for r in report.rows)
    finally:
        state.replace_all(prior)
        state.set_data_mode(prior_mode)


def test_ensure_normalized_persists_for_later_active_reads():
    prior_mode = state.data_mode()
    prior = state.transactions()
    try:
        state.set_data_mode("live")
        state.replace_all([_lend_deposit_raw()])

        ensured = _ensure_normalized_active_ledger()
        assert ensured[0].transaction_type == TransactionType.SELL
        assert ensured[0].event_subtype == EVENT_LEND_DEPOSIT

        # Subsequent active_transactions() sees the persisted normalize.
        persisted = spot_transactions(state.transactions())
        assert persisted[0].transaction_type == TransactionType.SELL
    finally:
        state.replace_all(prior)
        state.set_data_mode(prior_mode)
