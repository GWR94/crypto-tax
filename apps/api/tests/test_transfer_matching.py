"""Internal transfer matching and cost-basis continuity tests."""

from __future__ import annotations

from datetime import datetime, timezone

from app.hmrc_cgt_engine import calculate_uk_cgt, compute_uk_open_pools
from app.schemas import AccountingMethod, Transaction, TransactionType
from app.tax_engine import calculate_realized_gains
from app.transfer_matching import match_transfer_pairs


def _tx(
    tx_id: str,
    when: str,
    ttype: TransactionType,
    amount: float,
    value: float = 0.0,
    *,
    direction: str | None = None,
    source: str | None = None,
) -> Transaction:
    return Transaction(
        id=tx_id,
        timestamp=datetime.fromisoformat(when).replace(tzinfo=timezone.utc),
        asset="BTC",
        transaction_type=ttype,
        amount=amount,
        fiat_value_at_trigger=value,
        fiat_currency="GBP",
        source=source,
        transfer_direction=direction,
    )


def test_pairs_wallet_to_exchange_transfer():
    txs = [
        _tx("out", "2024-05-01T07:00:00", TransactionType.TRANSFER, 0.5, direction="OUT", source="bitcoin"),
        _tx("in", "2024-05-01T07:30:00", TransactionType.TRANSFER, 0.4995, direction="IN", source="kraken"),
    ]
    pairs = match_transfer_pairs(txs)
    assert pairs.get("out") == pairs.get("in")
    assert pairs["out"] is not None


def test_pairs_two_solana_wallets_same_signature():
    sig = "5PwsehdRxpR7tJToi266esgpLjzYuhz3n4EzWMf2o8a9p1kp2YkMFhthsN4c9aCMMeeyMvj88fhadyepULyJRps2"
    txs = [
        Transaction(
            id="out",
            timestamp=datetime.fromisoformat("2025-09-17T17:27:24").replace(tzinfo=timezone.utc),
            asset="SOL",
            transaction_type=TransactionType.TRANSFER,
            amount=3.99909882,
            fiat_value_at_trigger=579.87,
            fiat_currency="USD",
            source="solana",
            transfer_direction="OUT",
            trade_group_id=sig,
            on_chain_tx_id=sig,
        ),
        Transaction(
            id="in",
            timestamp=datetime.fromisoformat("2025-09-17T17:27:24").replace(tzinfo=timezone.utc),
            asset="SOL",
            transaction_type=TransactionType.TRANSFER,
            amount=3.99909882,
            fiat_value_at_trigger=579.87,
            fiat_currency="USD",
            source="solana",
            transfer_direction="IN",
            trade_group_id=sig,
            on_chain_tx_id=sig,
        ),
    ]
    pairs = match_transfer_pairs(txs)
    assert pairs.get("out") == pairs.get("in")
    assert pairs["out"] is not None


def test_does_not_pair_distant_or_mismatched():
    txs = [
        _tx("out", "2024-05-01T07:00:00", TransactionType.TRANSFER, 0.5, direction="OUT", source="bitcoin"),
        # Far too small to be the same transfer (well below fee tolerance).
        _tx("in", "2024-05-01T07:30:00", TransactionType.TRANSFER, 0.1, direction="IN", source="kraken"),
    ]
    pairs = match_transfer_pairs(txs)
    assert pairs == {}


def test_unpaired_external_receipt_establishes_uk_basis():
    # Receive BTC on-chain with a known value, then sell it later. The receipt
    # should establish cost basis so the sale is not flagged uncovered.
    txs = [
        _tx("recv", "2024-04-10T00:00:00", TransactionType.TRANSFER, 1.0, 20000.0, direction="IN", source="bitcoin"),
        _tx("sell", "2024-06-01T00:00:00", TransactionType.SELL, 1.0, 30000.0, source="kraken"),
    ]
    report = calculate_uk_cgt(txs, tax_year_label="2024/25")
    assert report.disposal_count == 1
    row = report.rows[0]
    assert not row.missing_cost_basis
    assert row.allowable_cost == 20000.0
    assert row.gain == 10000.0


def test_internal_transfer_preserves_us_basis():
    # Buy on one venue, move to another, then sell: basis must carry over and
    # not reset to zero across the internal transfer.
    txs = [
        _tx("buy", "2024-01-01T00:00:00", TransactionType.BUY, 1.0, 20000.0, source="bitcoin"),
        _tx("out", "2024-02-01T00:00:00", TransactionType.TRANSFER, 1.0, direction="OUT", source="bitcoin"),
        _tx("in", "2024-02-01T00:30:00", TransactionType.TRANSFER, 1.0, direction="IN", source="kraken"),
        _tx("sell", "2024-03-01T00:00:00", TransactionType.SELL, 1.0, 30000.0, source="kraken"),
    ]
    report = calculate_realized_gains(txs, AccountingMethod.FIFO, tax_year=2024)
    assert report.total_gain == 10000.0
    assert all(not r.missing_cost_basis for r in report.rows)


def _cdc_earn_tx(
    tx_id: str,
    when: str,
    asset: str,
    amount: float,
    *,
    direction: str,
    value: float,
) -> Transaction:
    return Transaction(
        id=tx_id,
        timestamp=datetime.fromisoformat(when).replace(tzinfo=timezone.utc),
        asset=asset,
        transaction_type=TransactionType.TRANSFER,
        amount=amount,
        fiat_value_at_trigger=value,
        fiat_currency="GBP",
        source="cryptocom",
        transfer_direction=direction,
    )


def test_pairs_cryptocom_earn_deposit_and_withdrawal():
    txs = [
        _cdc_earn_tx(
            "cdc-2021-12-02T22:29:36+00:00-crypto_earn_program_created-ETH",
            "2021-12-02T22:29:36",
            "ETH",
            0.2,
            direction="OUT",
            value=671.94,
        ),
        _cdc_earn_tx(
            "cdc-2022-01-01T22:30:03+00:00-crypto_earn_program_withdrawn-ETH",
            "2022-01-01T22:30:03",
            "ETH",
            0.2,
            direction="IN",
            value=547.46,
        ),
    ]
    pairs = match_transfer_pairs(txs)
    assert pairs.get(txs[0].id) == pairs.get(txs[1].id)
    assert pairs[txs[0].id] is not None


def test_cryptocom_earn_round_trip_does_not_inflate_uk_pool():
    txs = [
        Transaction(
            id="buy-eth",
            timestamp=datetime.fromisoformat("2021-08-07T10:08:55").replace(
                tzinfo=timezone.utc
            ),
            asset="ETH",
            transaction_type=TransactionType.BUY,
            amount=0.2,
            fiat_value_at_trigger=671.94,
            fiat_currency="GBP",
            source="cryptocom",
        ),
        _cdc_earn_tx(
            "cdc-2021-12-02T22:29:36+00:00-crypto_earn_program_created-ETH",
            "2021-12-02T22:29:36",
            "ETH",
            0.2,
            direction="OUT",
            value=671.94,
        ),
        _cdc_earn_tx(
            "cdc-2022-01-01T22:30:03+00:00-crypto_earn_program_withdrawn-ETH",
            "2022-01-01T22:30:03",
            "ETH",
            0.2,
            direction="IN",
            value=547.46,
        ),
    ]
    pools = compute_uk_open_pools(txs)
    assert pools["ETH"][0] == 0.2
