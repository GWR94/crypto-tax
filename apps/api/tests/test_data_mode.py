"""Tests for live vs demo data mode."""

from __future__ import annotations

from app.hmrc_cgt_engine import calculate_uk_cgt
from app.ledger_view import active_transactions, is_demo_mode
from app.sample_data import default_transactions, without_sample
from app.schemas import Transaction, TransactionType, spot_transactions
from app.state import state
import app.demo_verification as expected


def test_active_transactions_in_demo_mode():
    prior_mode = state.data_mode()
    try:
        state.set_data_mode("demo")
        assert is_demo_mode()
        assert {tx.id for tx in active_transactions()} == {
            tx.id for tx in default_transactions()
        }
        report = calculate_uk_cgt(
            spot_transactions(active_transactions()),
            tax_year_label=expected.UK_TAX_YEAR,
        )
        assert report.net_gain == expected.UK_CGT_NET_GAIN_2024_25
    finally:
        state.set_data_mode(prior_mode)


def test_active_transactions_in_live_mode_excludes_demo():
    prior_mode = state.data_mode()
    prior = state.transactions()
    sample_live = [
        Transaction(
            id="test-live-buy-data-mode",
            timestamp="2024-06-01T10:00:00Z",
            asset="BTC",
            transaction_type=TransactionType.BUY,
            amount=0.01,
            fiat_value_at_trigger=500.0,
            fee_fiat=0.0,
            fiat_currency="GBP",
            source="coinbase",
        )
    ]
    try:
        state.replace_all(sample_live + default_transactions())
        state.set_data_mode("live")
        active = active_transactions()
        assert len(active) == 1
        assert active[0].id == "test-live-buy-data-mode"
        assert active == without_sample(state.transactions())
    finally:
        state.replace_all(prior)
        state.set_data_mode(prior_mode)
