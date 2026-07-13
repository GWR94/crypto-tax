"""CSV export kind inference tests."""

from app.csv_export_kind import infer_csv_export_kind
from app.schemas import Transaction, TransactionType


def test_solana_transfer_filename():
    assert (
        infer_csv_export_kind(
            "export_transfer_7jEvut3Ck87PAxK5mF1bbG1NJ73tcnhz1VZSKqfBT8Eh_1755171161152.csv"
        )
        == "transfers"
    )


def test_variational_kind_from_transactions():
    transfers = [
        Transaction(
            id="1",
            timestamp="2024-01-01T00:00:00+00:00",
            asset="USDC",
            transaction_type=TransactionType.TRANSFER,
            amount=1.0,
            fiat_value_at_trigger=1.0,
            fee_fiat=0.0,
            source="variational",
            venue_order_type="deposit",
        )
    ]
    trades = [
        Transaction(
            id="2",
            timestamp="2024-01-01T00:00:00+00:00",
            asset="BTC",
            transaction_type=TransactionType.BUY,
            amount=1.0,
            fiat_value_at_trigger=100.0,
            fee_fiat=0.0,
            source="variational",
            trade_group_id="abc",
        )
    ]
    assert infer_csv_export_kind("variational.csv", transfers) == "transfers"
    assert infer_csv_export_kind("variational.csv", trades) == "trades"
