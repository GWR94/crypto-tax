"""Seed transaction ledger used on first launch.

Every amount is in **GBP** (``fiat_currency="GBP"``) so UK CGT / income figures are
exact with no FX rounding. See ``tests/test_demo_ledger.py`` for the full
verification matrix and ``demo_verification.py`` for human-readable expected values.

Scenarios covered
-----------------
* HMRC same-day (XRP), 30-day B&B (ADA), and Section 104 (AVAX) matching
* US FIFO vs HIFO difference (BNB)
* Staking + ARB airdrop income
* Missing cost basis (DOGE)
* Paired internal transfer (basis-neutral)
* Open underwater lot (SOL) for tax-loss harvest
* Orphaned inflow (zero-fiat deposit) for Data Health Ledger
* Perps: Hyperliquid (+£1,200), Variational (+£100), WOOX (−£500) closed PnL
"""

from __future__ import annotations

from typing import List, Optional

from .schemas import Transaction, TransactionType


def _spot(
    id: str,
    timestamp: str,
    asset: str,
    ttype: TransactionType,
    amount: float,
    fiat: float,
    *,
    fee: float = 0.0,
    source: str = "coinbase",
) -> Transaction:
    return Transaction(
        id=id,
        timestamp=timestamp,
        asset=asset,
        transaction_type=ttype,
        amount=amount,
        fiat_value_at_trigger=fiat,
        fee_fiat=fee,
        fiat_currency="GBP",
        source=source,
    )


def _transfer(
    id: str,
    timestamp: str,
    asset: str,
    amount: float,
    *,
    direction: str,
    source: str,
    fiat: float = 0.0,
) -> Transaction:
    return Transaction(
        id=id,
        timestamp=timestamp,
        asset=asset,
        transaction_type=TransactionType.TRANSFER,
        amount=amount,
        fiat_value_at_trigger=fiat,
        fee_fiat=0.0,
        fiat_currency="GBP",
        source=source,
        transfer_direction=direction,
    )


def _perp_close(
    id: str,
    timestamp: str,
    *,
    source: str,
    instrument: str,
    asset: str,
    amount: float,
    notional: float,
    realized_pnl: float,
    fee: float = 0.0,
    ttype: TransactionType = TransactionType.SELL,
) -> Transaction:
    return Transaction(
        id=id,
        timestamp=timestamp,
        asset=asset,
        transaction_type=ttype,
        amount=amount,
        fiat_value_at_trigger=notional,
        fee_fiat=fee,
        fiat_currency="GBP",
        source=source,
        instrument_kind="perp",
        instrument=instrument,
        realized_pnl=realized_pnl,
    )


def default_transactions() -> List[Transaction]:
    return [
        # --- CGT pedagogy (2024/25 tax year) — large-cap tickers, round GBP amounts ---
        # SAME-DAY (XRP): buy & sell 2024-06-01 → gain £500
        _spot(
            "demo-xrp-sameday-buy",
            "2024-06-01T09:00:00Z",
            "XRP",
            TransactionType.BUY,
            2000.0,
            1000.0,
            source="coinbase",
        ),
        _spot(
            "demo-xrp-sameday-sell",
            "2024-06-01T15:00:00Z",
            "XRP",
            TransactionType.SELL,
            2000.0,
            1500.0,
            source="coinbase",
        ),
        # 30-DAY B&B (ADA): sell 2024-06-01 matched to repurchase 2024-06-15 → gain £100
        _spot(
            "demo-ada-bb-buy-1",
            "2024-01-01T00:00:00Z",
            "ADA",
            TransactionType.BUY,
            2000.0,
            1000.0,
            source="kraken",
        ),
        _spot(
            "demo-ada-bb-sell",
            "2024-06-01T12:00:00Z",
            "ADA",
            TransactionType.SELL,
            2000.0,
            1200.0,
            source="kraken",
        ),
        _spot(
            "demo-ada-bb-buy-2",
            "2024-06-15T10:00:00Z",
            "ADA",
            TransactionType.BUY,
            2000.0,
            1100.0,
            source="kraken",
        ),
        # SECTION 104 (AVAX): pooled disposal → gain £250
        _spot(
            "demo-avax-pool-buy",
            "2023-01-01T00:00:00Z",
            "AVAX",
            TransactionType.BUY,
            20.0,
            1000.0,
            source="binance",
        ),
        _spot(
            "demo-avax-pool-sell",
            "2024-08-01T12:00:00Z",
            "AVAX",
            TransactionType.SELL,
            20.0,
            1250.0,
            source="binance",
        ),
        # FIFO vs HIFO (BNB): two lots, one sell → FIFO +£500, HIFO −£500
        _spot(
            "demo-bnb-fifo-buy-low",
            "2024-01-01T00:00:00Z",
            "BNB",
            TransactionType.BUY,
            2.0,
            1000.0,
            source="coinbase",
        ),
        _spot(
            "demo-bnb-fifo-buy-high",
            "2024-02-01T00:00:00Z",
            "BNB",
            TransactionType.BUY,
            2.0,
            2000.0,
            source="coinbase",
        ),
        _spot(
            "demo-bnb-fifo-sell",
            "2024-09-01T12:00:00Z",
            "BNB",
            TransactionType.SELL,
            2.0,
            1500.0,
            source="coinbase",
        ),
        # MISSING COST BASIS: full proceeds taxed as gain
        _spot(
            "demo-doge-sell",
            "2024-06-15T15:00:00Z",
            "DOGE",
            TransactionType.SELL,
            100.0,
            160.0,
            fee=2.0,
            source="robinhood",
        ),
        # INTERNAL TRANSFER pair (coinbase → cold wallet) + separate BTC lot
        _spot(
            "demo-btc-buy",
            "2024-01-15T10:00:00Z",
            "BTC",
            TransactionType.BUY,
            0.2,
            8000.0,
            fee=10.0,
            source="coinbase",
        ),
        _transfer(
            "demo-btc-xfer-out",
            "2024-07-01T12:00:00Z",
            "BTC",
            0.1,
            direction="OUT",
            source="coinbase",
        ),
        _transfer(
            "demo-btc-xfer-in",
            "2024-07-01T12:08:00Z",
            "BTC",
            0.1,
            direction="IN",
            source="ledger-wallet",
        ),
        # ETH on Coinbase: buy → native staking rewards (income at FMV when received)
        _spot(
            "demo-eth-cb-buy",
            "2024-03-01T10:00:00Z",
            "ETH",
            TransactionType.BUY,
            2.0,
            5000.0,
            fee=15.0,
            source="coinbase",
        ),
        _spot(
            "demo-eth-stake-1",
            "2024-05-01T00:00:00Z",
            "ETH",
            TransactionType.STAKING,
            0.008,
            20.0,
            source="coinbase",
        ),
        _spot(
            "demo-eth-stake-2",
            "2024-08-01T00:00:00Z",
            "ETH",
            TransactionType.STAKING,
            0.012,
            30.0,
            source="coinbase",
        ),
        # ARB airdrop: 625 ARB @ £0.80 (typical claim size, May 2024 ballpark price)
        _spot(
            "demo-arb-airdrop",
            "2024-05-15T00:00:00Z",
            "ARB",
            TransactionType.AIRDROP,
            625.0,
            500.0,
            source="wallet",
        ),
        # OPEN LOSER for harvest matrix
        _spot(
            "demo-sol-buy",
            "2024-04-01T10:00:00Z",
            "SOL",
            TransactionType.BUY,
            50.0,
            9500.0,
            fee=15.0,
            source="kraken",
        ),
        # OPEN HOLDING
        _spot(
            "demo-link-buy",
            "2023-12-01T10:00:00Z",
            "LINK",
            TransactionType.BUY,
            100.0,
            1450.0,
            fee=10.0,
            source="coinbase",
        ),
        # ORPHANED INFLOW (zero-fiat deposit — Data Health; separate asset from Coinbase ETH)
        _transfer(
            "demo-mexc-deposit",
            "2024-04-15T10:00:00Z",
            "ATOM",
            50.0,
            direction="IN",
            source="mexc",
            fiat=0.0,
        ),
        # DUST stablecoin (filtered from PnL views)
        _spot(
            "demo-usdc-dust",
            "2024-01-01T10:00:00Z",
            "USDC",
            TransactionType.BUY,
            0.2,
            0.2,
            source="coinbase",
        ),
        # --- Perps: Hyperliquid (best), Variational (ok), WOOX (worst) ----------
        # Hyperliquid closed PnL +£1,200 total
        _perp_close(
            "demo-hl-close-1",
            "2024-05-10T14:00:00Z",
            source="hyperliquid",
            instrument="BTC-PERP",
            asset="BTC",
            amount=0.05,
            notional=5000.0,
            realized_pnl=800.0,
            fee=20.0,
        ),
        _perp_close(
            "demo-hl-close-2",
            "2024-07-20T11:00:00Z",
            source="hyperliquid",
            instrument="ETH-PERP",
            asset="ETH",
            amount=2.0,
            notional=6000.0,
            realized_pnl=400.0,
            fee=10.0,
        ),
        # Variational closed PnL +£100 net (+150 / −50)
        _perp_close(
            "demo-var-close-win",
            "2024-06-05T09:00:00Z",
            source="variational",
            instrument="BTC-PERP",
            asset="BTC",
            amount=0.02,
            notional=2000.0,
            realized_pnl=150.0,
            fee=5.0,
        ),
        _perp_close(
            "demo-var-close-loss",
            "2024-08-12T16:00:00Z",
            source="variational",
            instrument="ETH-PERP",
            asset="ETH",
            amount=1.0,
            notional=3000.0,
            realized_pnl=-50.0,
            fee=5.0,
        ),
        # WOOX closed PnL −£500 total
        _perp_close(
            "demo-woox-close-1",
            "2024-05-22T10:00:00Z",
            source="woox",
            instrument="BTC-PERP",
            asset="BTC",
            amount=0.03,
            notional=3000.0,
            realized_pnl=-300.0,
            fee=15.0,
        ),
        _perp_close(
            "demo-woox-close-2",
            "2024-09-03T13:00:00Z",
            source="woox",
            instrument="SOL-PERP",
            asset="SOL",
            amount=10.0,
            notional=1500.0,
            realized_pnl=-200.0,
            fee=10.0,
        ),
    ]


SAMPLE_TRANSACTION_IDS = frozenset(tx.id for tx in default_transactions())


def without_sample(transactions: List[Transaction]) -> List[Transaction]:
    """Drop bundled demo rows so real imports do not mix with seed data."""
    return [t for t in transactions if t.id not in SAMPLE_TRANSACTION_IDS]


def demo_transaction_count(transactions: List[Transaction]) -> int:
    return sum(1 for t in transactions if t.id in SAMPLE_TRANSACTION_IDS)
