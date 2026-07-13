"""Price resolver and CoinGecko registry behaviour."""

from datetime import datetime, timezone
from unittest.mock import patch

from app.coingecko_registry import resolve_coingecko_id
from app.price_resolver import COINGECKO_IDS, resolve_prices
from app.pricing import PriceStore
from app.schemas import Transaction, TransactionType
from app.tax_engine import is_dust

BLZE_MINT = "BLZEEuZUBVqFhj8adcCFPJvPVCiCyVmh3hkJMrU8KuJA"
B_PUMP_MINT = "jKUo4bdgLggxhimCYuVZK1kx8faVsQBjG9sQ4oBpump"


def test_ambiguous_symbols_have_overrides():
    for symbol in ("LTC", "BNB", "VET", "MSOL", "SOL"):
        assert symbol in COINGECKO_IDS


def test_is_dust_keeps_positions_with_cost_basis():
    assert is_dust(1.0, 0.0, total_invested=50.0) is False
    assert is_dust(1.0, 0.0, total_invested=0.0) is True


def test_b_meme_ticker_does_not_resolve_to_bitcoin():
    coin_id = resolve_coingecko_id("B", token_mint=B_PUMP_MINT)
    assert coin_id != "bitcoin"


def test_minted_wallet_token_skips_symbol_coingecko_search():
    """Memecoins with a mint must not use CoinGecko symbol search."""
    txs = [
        Transaction(
            id="sol-b-buy",
            timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
            asset="B",
            transaction_type=TransactionType.BUY,
            amount=1000.0,
            fiat_value_at_trigger=10.0,
            fiat_currency="USD",
            source="solana",
            token_mint=B_PUMP_MINT,
        )
    ]
    with patch("app.price_resolver.resolve_coingecko_id") as mock_resolve:
        with patch(
            "app.price_resolver._fetch_dexscreener_prices", return_value={B_PUMP_MINT: 0.0001}
        ):
            with patch("app.price_resolver._fetch_coingecko_contracts", return_value={}):
                resolved = resolve_prices(
                    assets=["B"],
                    transactions=txs,
                    store=PriceStore(),
                )
        mock_resolve.assert_not_called()
    assert resolved["B"].source == "dex"
    assert resolved["B"].usd == 0.0001


def test_exchange_asset_uses_symbol_coingecko():
    with patch(
        "app.price_resolver._apply_coingecko_symbol_prices",
        return_value={},
    ) as mock_symbol:
        with patch("app.price_resolver._fetch_coingecko_contracts", return_value={}):
            resolve_prices(
                assets=["LTC"],
                transactions=[],
                store=PriceStore(),
            )
    mock_symbol.assert_called_once()
    assert mock_symbol.call_args.kwargs["assets"] == {"LTC"}


def test_blze_resolves_via_solana_mint():
    coin_id = resolve_coingecko_id("BLZE", token_mint=BLZE_MINT)
    assert coin_id in {"solblaze", "blaze", "blaze-2"}


def test_anyone_symbol_resolves():
    coin_id = resolve_coingecko_id("ANYONE")
    assert coin_id in {"anyone-protocol", "airtor-protocol"}
