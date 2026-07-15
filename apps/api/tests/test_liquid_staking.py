"""Tests for Solana liquid-staking normalization."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from app.liquid_staking import (
    collapse_lst_transfer_sell_duplicates,
    inherit_import_id_for_derived_lst_yield,
    normalize_liquid_staking,
    reclassify_lst_unstake_swaps,
    split_lst_staking_income,
)
from app.schemas import AccountingMethod, Transaction, TransactionType
from app.tax_engine import _run_engine


def _tx(
    tx_id: str,
    when: str,
    asset: str,
    ttype: TransactionType,
    amount: float,
    value: float = 0.0,
    *,
    direction: str | None = None,
    gid: str | None = None,
    import_id: str | None = None,
) -> Transaction:
    return Transaction(
        id=tx_id,
        timestamp=datetime.fromisoformat(when).replace(tzinfo=timezone.utc),
        asset=asset,
        transaction_type=ttype,
        amount=amount,
        fiat_value_at_trigger=value,
        fee_fiat=0.0,
        fiat_currency="USD" if value > 0 else None,
        source="solana",
        import_id=import_id,
        transfer_direction=direction,
        trade_group_id=gid,
        on_chain_tx_id=gid,
    )


def test_collapse_transfer_out_when_lst_sell_exists():
    txs = [
        _tx("buy", "2024-02-12T10:13:52", "MSOL", TransactionType.BUY, 4.0, 500, gid="sig-stake"),
        _tx("sell-sol", "2024-02-12T10:13:52", "SOL", TransactionType.SELL, 4.5, 500, gid="sig-stake"),
        _tx("xfer-out", "2024-02-12T11:46:10", "MSOL", TransactionType.TRANSFER, 2.0, 250, direction="OUT"),
        _tx("sell-lst", "2024-02-12T11:46:10", "MSOL", TransactionType.SELL, 2.0, 195, gid="sig-unstake"),
    ]
    out, n = collapse_lst_transfer_sell_duplicates(txs)
    assert n == 1
    assert len(out) == 3
    assert all(t.id != "xfer-out" for t in out)


def test_reclassify_dust_sol_sell_to_fee_on_unstake():
    txs = [
        _tx("sell-lst", "2024-02-12T11:46:10", "MSOL", TransactionType.SELL, 2.0, 195, gid="sig-unstake"),
        _tx("sell-sol", "2024-02-12T11:46:10", "SOL", TransactionType.SELL, 0.03, 5, gid="sig-unstake"),
    ]
    out, n = reclassify_lst_unstake_swaps(txs)
    assert n == 1
    sol = next(t for t in out if t.asset == "SOL")
    assert sol.transaction_type == TransactionType.FEE


def test_link_transfer_in_sol_after_unstake():
    txs = [
        _tx("buy", "2024-02-12T10:13:52", "MSOL", TransactionType.BUY, 4.0, 500, gid="sig-stake"),
        _tx("sell-sol", "2024-02-12T10:13:52", "SOL", TransactionType.SELL, 4.5, 500, gid="sig-stake"),
        _tx("sell-lst", "2024-02-12T11:46:10", "MSOL", TransactionType.SELL, 2.0, 195, gid="sig-unstake"),
        _tx("sell-dust", "2024-02-12T11:46:10", "SOL", TransactionType.SELL, 0.03, 5, gid="sig-unstake"),
        _tx(
            "xfer-in",
            "2024-02-12T11:51:59",
            "SOL",
            TransactionType.TRANSFER,
            0.8,
            116,
            direction="IN",
        ),
    ]
    out, _ = normalize_liquid_staking(txs)
    buys = [t for t in out if t.transaction_type == TransactionType.BUY and t.asset == "SOL"]
    assert len(buys) == 1
    assert buys[0].amount == 0.8
    assert buys[0].fiat_value_at_trigger == 195


def test_staking_income_on_sol_yield():
    txs = [
        _tx("buy", "2024-01-01T00:00:00", "MSOL", TransactionType.BUY, 1.0, 100, gid="stake-1"),
        _tx("sell-sol", "2024-01-01T00:00:00", "SOL", TransactionType.SELL, 1.0, 100, gid="stake-1"),
        _tx("sell-lst", "2024-06-01T00:00:00", "MSOL", TransactionType.SELL, 1.0, 150, gid="unstake-1"),
        _tx("buy-sol", "2024-06-01T00:00:00", "SOL", TransactionType.BUY, 1.1, 150, gid="unstake-1"),
    ]
    out, n = split_lst_staking_income(txs)
    assert n >= 1
    staking = [t for t in out if t.transaction_type == TransactionType.STAKING]
    assert len(staking) == 1
    assert staking[0].asset == "SOL"
    assert staking[0].amount == 0.1
    assert staking[0].fiat_value_at_trigger == 13.64  # 0.1 / 1.1 * 150

    buy = next(t for t in out if t.id == "buy-sol")
    assert buy.amount == 1.0
    assert buy.fiat_value_at_trigger == 136.36  # 1.0 / 1.1 * 150

    # Principal BUY + yield STAKING == gross SOL received (no double-count).
    sol_acquired = sum(
        t.amount
        for t in out
        if t.asset == "SOL"
        and t.transaction_type in (TransactionType.BUY, TransactionType.STAKING)
        and t.id in {"buy-sol", staking[0].id}
    )
    assert abs(sol_acquired - 1.1) < 1e-9


def test_split_lst_staking_income_is_idempotent():
    txs = [
        _tx("buy", "2024-01-01T00:00:00", "MSOL", TransactionType.BUY, 1.0, 100, gid="stake-1"),
        _tx("sell-sol", "2024-01-01T00:00:00", "SOL", TransactionType.SELL, 1.0, 100, gid="stake-1"),
        _tx("sell-lst", "2024-06-01T00:00:00", "MSOL", TransactionType.SELL, 1.0, 150, gid="unstake-1"),
        _tx("buy-sol", "2024-06-01T00:00:00", "SOL", TransactionType.BUY, 1.1, 150, gid="unstake-1"),
    ]
    first, n1 = split_lst_staking_income(txs)
    assert n1 >= 1
    second, n2 = split_lst_staking_income(first)
    assert n2 == 0
    staking = [t for t in second if t.transaction_type == TransactionType.STAKING]
    assert len(staking) == 1
    buy = next(t for t in second if t.id == "buy-sol")
    assert buy.amount == 1.0


def test_heals_legacy_double_counted_yield_buy():
    """Prior yield row + full SOL BUY must shrink BUY without inflating SOL."""
    txs = [
        _tx("buy", "2024-01-01T00:00:00", "MSOL", TransactionType.BUY, 1.0, 100, gid="stake-1"),
        _tx("sell-sol", "2024-01-01T00:00:00", "SOL", TransactionType.SELL, 1.0, 100, gid="stake-1"),
        _tx("sell-lst", "2024-06-01T00:00:00", "MSOL", TransactionType.SELL, 1.0, 150, gid="unstake-1"),
        _tx("buy-sol", "2024-06-01T00:00:00", "SOL", TransactionType.BUY, 1.1, 150, gid="unstake-1"),
        Transaction(
            id="sell-lst-lst-yield",
            timestamp=datetime(2024, 6, 1, tzinfo=timezone.utc),
            asset="SOL",
            transaction_type=TransactionType.STAKING,
            amount=0.1,
            fiat_value_at_trigger=13.64,
            fee_fiat=0.0,
            fiat_currency="USD",
            source="solana",
            trade_group_id="unstake-1",
        ),
    ]
    out, n = split_lst_staking_income(txs)
    assert n >= 1
    buy = next(t for t in out if t.id == "buy-sol")
    assert buy.amount == 1.0
    staking = [t for t in out if t.transaction_type == TransactionType.STAKING]
    assert len(staking) == 1
    assert abs(buy.amount + staking[0].amount - 1.1) < 1e-9


def test_yield_split_open_lots_match_received_sol():
    """US lot engine must not hold more SOL than was received on unstake."""
    txs = [
        _tx("buy", "2024-01-01T00:00:00", "MSOL", TransactionType.BUY, 1.0, 100, gid="stake-1"),
        _tx("sell-sol", "2024-01-01T00:00:00", "SOL", TransactionType.SELL, 1.0, 100, gid="stake-1"),
        _tx("sell-lst", "2024-06-01T00:00:00", "MSOL", TransactionType.SELL, 1.0, 150, gid="unstake-1"),
        _tx("buy-sol", "2024-06-01T00:00:00", "SOL", TransactionType.BUY, 1.1, 150, gid="unstake-1"),
    ]
    fixed, _ = split_lst_staking_income(txs)
    result = _run_engine(fixed, AccountingMethod.FIFO)
    sol_qty = float(sum((lot.quantity for lot in result.open_lots.get("SOL", [])), Decimal("0")))
    assert abs(sol_qty - 1.1) < 1e-9


def test_fifo_yield_on_partial_unstake():
    """Regression: do not match a tiny recent stake to a large unstake."""
    txs = [
        _tx("stake-big", "2024-02-12T10:00:00", "MSOL", TransactionType.BUY, 4.0, 500, gid="stake-big"),
        _tx("stake-big-sol", "2024-02-12T10:00:00", "SOL", TransactionType.SELL, 4.5, 500, gid="stake-big"),
        _tx("stake-tiny", "2024-03-15T10:00:00", "MSOL", TransactionType.BUY, 0.14, 20, gid="stake-tiny"),
        _tx("stake-tiny-sol", "2024-03-15T10:00:00", "SOL", TransactionType.SELL, 0.16, 20, gid="stake-tiny"),
        _tx("sell-lst", "2024-06-01T00:00:00", "MSOL", TransactionType.SELL, 0.59, 150, gid="unstake-1"),
        _tx("buy-sol", "2024-06-01T00:00:00", "SOL", TransactionType.BUY, 0.69, 150, gid="unstake-1"),
    ]
    out, n = split_lst_staking_income(txs)
    assert n >= 1
    staking = next(t for t in out if t.transaction_type == TransactionType.STAKING)
    assert staking.amount < 0.15


def test_staking_yield_inherits_import_id():
    txs = [
        _tx(
            "buy",
            "2024-01-01T00:00:00",
            "MSOL",
            TransactionType.BUY,
            1.0,
            100,
            gid="stake-1",
            import_id="import-abc",
        ),
        _tx(
            "sell-sol",
            "2024-01-01T00:00:00",
            "SOL",
            TransactionType.SELL,
            1.0,
            100,
            gid="stake-1",
            import_id="import-abc",
        ),
        _tx(
            "sell-lst",
            "2024-06-01T00:00:00",
            "MSOL",
            TransactionType.SELL,
            1.0,
            150,
            gid="unstake-1",
            import_id="import-abc",
        ),
        _tx(
            "buy-sol",
            "2024-06-01T00:00:00",
            "SOL",
            TransactionType.BUY,
            1.1,
            150,
            gid="unstake-1",
            import_id="import-abc",
        ),
    ]
    out, n = split_lst_staking_income(txs)
    assert n >= 1
    staking = next(t for t in out if t.transaction_type == TransactionType.STAKING)
    assert staking.import_id == "import-abc"


def test_backfill_import_id_on_existing_lst_yield_rows():
    orphan = Transaction(
        id="sell-lst-lst-yield",
        timestamp=datetime(2024, 6, 1, tzinfo=timezone.utc),
        asset="SOL",
        transaction_type=TransactionType.STAKING,
        amount=0.1,
        fiat_value_at_trigger=10.0,
        fee_fiat=0.0,
        fiat_currency="USD",
        source="solana",
        trade_group_id="unstake-1",
    )
    txs = [
        _tx(
            "sell-lst",
            "2024-06-01T00:00:00",
            "MSOL",
            TransactionType.SELL,
            1.0,
            150,
            gid="unstake-1",
            import_id="import-abc",
        ),
        orphan,
    ]
    out, n = inherit_import_id_for_derived_lst_yield(txs)
    assert n == 1
    fixed = next(t for t in out if t.id == orphan.id)
    assert fixed.import_id == "import-abc"


def test_missing_cost_basis_cleared_after_normalize():
    """Regression: duplicate TRANSFER OUT must not drain the mSOL pool before SELL."""
    txs = [
        _tx("buy", "2024-02-12T10:13:52", "MSOL", TransactionType.BUY, 4.242918, 532.34, gid="stake"),
        _tx("sell-sol", "2024-02-12T10:13:52", "SOL", TransactionType.SELL, 4.9538, 532.06, gid="stake"),
        _tx("out1", "2024-02-12T10:51:45", "MSOL", TransactionType.TRANSFER, 2.021459, 253.62, direction="OUT"),
        _tx("out2", "2024-02-12T11:46:10", "MSOL", TransactionType.TRANSFER, 2.021459, 253.62, direction="OUT"),
        _tx("sell-lst", "2024-02-12T11:46:10", "MSOL", TransactionType.SELL, 2.021459, 195.15, gid="unstake"),
        _tx("sell-dust", "2024-02-12T11:46:10", "SOL", TransactionType.SELL, 0.035559, 5.16, gid="unstake"),
    ]
    before = _run_engine(txs, AccountingMethod.FIFO)
    assert any(m.asset == "MSOL" for m in before.missing_cost_basis)

    fixed, _ = normalize_liquid_staking(txs)
    after = _run_engine(fixed, AccountingMethod.FIFO)
    assert not any(m.asset == "MSOL" for m in after.missing_cost_basis)
