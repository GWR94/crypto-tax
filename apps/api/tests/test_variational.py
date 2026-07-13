"""Variational CSV import tests."""

from pathlib import Path

from app.ingestion import detect_upload_parser, parse_csv, preview_upload
from app.schemas import is_perp_transaction

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_variational_transfers_detect_and_preview():
    content = (FIXTURES / "variational_transfers.csv").read_bytes()
    assert detect_upload_parser("vari-export-transfer.csv", content) == "variational"

    preview = preview_upload("vari-export-transfer.csv", content)
    assert preview["parser"] == "variational"
    assert preview["parser_label"] == "Variational"
    assert preview["transaction_count"] == 3
    assert preview["date_start"] is not None
    assert preview["date_end"] is not None


def test_variational_transfers_import():
    content = (FIXTURES / "variational_transfers.csv").read_bytes()
    txs = parse_csv(content, filename="variational_transfers.csv")

    assert len(txs) == 3
    assert all(t.source == "variational" for t in txs)
    assert all(is_perp_transaction(t) for t in txs)

    deposit = next(t for t in txs if t.venue_order_type == "deposit")
    assert deposit.transaction_type.value == "TRANSFER"
    assert deposit.transfer_direction == "IN"
    assert deposit.asset == "USDC"
    assert deposit.amount == 321.555734
    assert deposit.fee_fiat == 0.1
    assert not any(
        t.transaction_type.value == "FEE" and t.venue_order_type == "deposit" for t in txs
    )

    pnl = next(t for t in txs if t.venue_order_type == "realized_pnl")
    assert pnl.asset == "SOL"
    assert pnl.realized_pnl == -114.75506
    assert pnl.fiat_value_at_trigger == 0.0

    funding = next(t for t in txs if t.venue_order_type == "funding")
    assert funding.asset == "SOL"
    assert funding.realized_pnl == -0.600356
    assert funding.fee_fiat == 0.600356
    assert funding.fiat_value_at_trigger == 0.0


def test_variational_trades_import():
    content = (FIXTURES / "variational_trades.csv").read_bytes()
    txs = parse_csv(content, filename="variational_trades.csv")

    assert len(txs) == 1
    trade = txs[0]
    assert trade.source == "variational"
    assert trade.instrument_kind == "perp"
    assert trade.transaction_type.value == "SELL"
    assert trade.asset == "SOL"
    assert trade.amount == 28.9821
    assert trade.venue_order_type == "liquidation"
    assert trade.instrument == "SOL - USDC"
    assert trade.fiat_value_at_trigger == round(28.9821 * 130.845485, 2)
    assert trade.id == "variational-c2d53ea3-0adf-4617-99ef-37d9ccb64f9d"
