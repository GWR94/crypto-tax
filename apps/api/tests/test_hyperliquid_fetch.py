"""Hyperliquid Info API wallet import."""

from datetime import datetime, timezone

from app.hyperliquid_fetch import _parse_fill, fetch_wallet_transactions


def test_parse_hyperliquid_fill():
    row = {
        "coin": "BTC",
        "px": "107237.0",
        "sz": "0.06152",
        "side": "A",
        "time": 1761844830526,
        "dir": "Close Long",
        "closedPnl": "-43.80224",
        "hash": "0x90cba2fc108b92d59245042e827504010c00bae1ab8eb1a734944e4ecf8f6cc0",
        "oid": 217281188315,
        "fee": "2.849999",
        "feeToken": "USDC",
        "tid": 559293170053559,
    }
    tx = _parse_fill(row)
    assert tx is not None
    assert tx.source == "hyperliquid"
    assert tx.asset == "BTC"
    assert tx.instrument_kind == "perp"
    assert tx.transaction_type.value == "SELL"
    assert tx.realized_pnl == -43.80224
    assert tx.fiat_value_at_trigger == round(107237.0 * 0.06152, 2)


def test_fetch_wallet_returns_list():
    # Smoke test against live API — address with known activity.
    txs = fetch_wallet_transactions("0xEA0767C2D006914A1B6181E2BFDa60f1290cCf20")
    assert isinstance(txs, list)
    if txs:
        assert txs[0].source == "hyperliquid"
        assert txs[0].timestamp.tzinfo is not None
