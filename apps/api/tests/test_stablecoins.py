"""Stablecoin recognition."""

from app.config import is_stablecoin, normalize_asset_ticker


def test_usdt0_stylised_symbol_is_stablecoin():
    assert normalize_asset_ticker("USD₮0") == "USDT0"
    assert is_stablecoin("USD₮0")
    assert is_stablecoin("usdt0")


def test_usdt0_excluded_from_cgt_pool():
    from app.hmrc_cgt_engine import _collect
    from app.schemas import Transaction, TransactionType
    from datetime import datetime, timezone

    txs = [
        Transaction(
            id="usdt0-in",
            timestamp=datetime(2025, 9, 18, 9, 34, 4, tzinfo=timezone.utc),
            asset="USD₮0",
            transaction_type=TransactionType.TRANSFER,
            amount=21.414079,
            fiat_value_at_trigger=0.0,
            fee_fiat=0.0,
            fiat_currency="GBP",
            source="arbitrum",
            transfer_direction="IN",
        ),
    ]
    buckets = _collect(txs)
    assert "USD₮0" not in buckets
    assert "USDT0" not in buckets
