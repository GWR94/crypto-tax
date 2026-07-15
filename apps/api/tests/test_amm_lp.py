"""AMM LP add/remove tax normalization."""

from datetime import datetime, timezone

from app.amm_lp import normalize_lp_for_tax
from app.defi_tax import EVENT_LP_ADD, EVENT_LP_REMOVE
from app.hmrc_cgt_engine import calculate_uk_cgt, compute_uk_open_pools
from app.ledger_normalize import normalize_tax_ledger
from app.schemas import Transaction, TransactionType


def _tx(
    tx_id: str,
    when: str,
    asset: str,
    ttype: TransactionType,
    amount: float,
    value: float,
    *,
    direction: str | None = None,
    gid: str | None = None,
    mint: str | None = None,
) -> Transaction:
    return Transaction(
        id=tx_id,
        timestamp=datetime.fromisoformat(when).replace(tzinfo=timezone.utc),
        asset=asset,
        transaction_type=ttype,
        amount=amount,
        fiat_value_at_trigger=value,
        fee_fiat=0.0,
        fiat_currency="GBP" if value else None,
        source="solana",
        transfer_direction=direction,
        trade_group_id=gid,
        on_chain_tx_id=gid,
        token_mint=mint,
    )


def test_lp_add_books_disposals_and_synthetic_share():
    txs, n = normalize_lp_for_tax(
        [
            _tx(
                "sol",
                "2024-06-01T12:00:00",
                "SOL",
                TransactionType.TRANSFER,
                1,
                145,
                direction="OUT",
                gid="g1",
            ),
            _tx(
                "usdc",
                "2024-06-01T12:00:00",
                "USDC",
                TransactionType.TRANSFER,
                150,
                150,
                direction="OUT",
                gid="g1",
            ),
        ]
    )
    assert n >= 2
    sells = [t for t in txs if t.transaction_type == TransactionType.SELL]
    assert all(t.event_subtype == EVENT_LP_ADD for t in sells)
    lp = next(t for t in txs if t.asset == "LP:G1")
    assert lp.transaction_type == TransactionType.BUY
    assert lp.fiat_value_at_trigger == 295.0
    assert lp.event_subtype == EVENT_LP_ADD


def test_lp_remove_with_share_burn_closes_basis():
    add, _ = normalize_lp_for_tax(
        [
            _tx(
                "sol-a",
                "2024-06-01T12:00:00",
                "SOL",
                TransactionType.TRANSFER,
                1,
                145,
                direction="OUT",
                gid="add1",
            ),
            _tx(
                "usdc-a",
                "2024-06-01T12:00:00",
                "USDC",
                TransactionType.TRANSFER,
                150,
                150,
                direction="OUT",
                gid="add1",
            ),
        ]
    )
    remove_legs = [
        _tx(
            "lp-burn",
            "2024-09-01T12:00:00",
            "LP:ADD1",
            TransactionType.TRANSFER,
            1,
            0,
            direction="OUT",
            gid="rm1",
        ),
        _tx(
            "sol-b",
            "2024-09-01T12:00:00",
            "SOL",
            TransactionType.TRANSFER,
            1,
            160,
            direction="IN",
            gid="rm1",
        ),
        _tx(
            "usdc-b",
            "2024-09-01T12:00:00",
            "USDC",
            TransactionType.TRANSFER,
            150,
            150,
            direction="IN",
            gid="rm1",
        ),
    ]
    out, n = normalize_lp_for_tax(add + remove_legs)
    assert n >= 2
    burn = next(t for t in out if t.id == "lp-burn")
    assert burn.transaction_type == TransactionType.SELL
    assert burn.event_subtype == EVENT_LP_REMOVE
    assert burn.fiat_value_at_trigger == 310.0
    sol_in = next(t for t in out if t.id == "sol-b")
    assert sol_in.transaction_type == TransactionType.BUY
    assert sol_in.event_subtype == EVENT_LP_REMOVE

    report = calculate_uk_cgt(out, tax_year_label="2024/25")
    lp_disp = next(r for r in report.rows if r.disposal_id == "lp-burn")
    assert lp_disp.allowable_cost == 295.0
    assert lp_disp.gain == 15.0


def test_lp_add_with_explicit_mint_reuses_receipt_leg():
    mint = "So11111111111111111111111111111111111111112LP"
    txs, _ = normalize_lp_for_tax(
        [
            _tx(
                "sol",
                "2024-06-01T12:00:00",
                "SOL",
                TransactionType.TRANSFER,
                1,
                100,
                direction="OUT",
                gid="g2",
            ),
            _tx(
                "usdc",
                "2024-06-01T12:00:00",
                "USDC",
                TransactionType.TRANSFER,
                100,
                100,
                direction="OUT",
                gid="g2",
            ),
            _tx(
                "lp-in",
                "2024-06-01T12:00:00",
                "RAYLP",
                TransactionType.TRANSFER,
                5.0,
                0.0,
                direction="IN",
                gid="g2",
                mint=mint,
            ),
        ]
    )
    lp = next(t for t in txs if t.id == "lp-in")
    assert lp.transaction_type == TransactionType.BUY
    assert lp.event_subtype == EVENT_LP_ADD
    assert lp.fiat_value_at_trigger == 200.0
    assert not any(t.asset.startswith("LP:") for t in txs)


def test_basis_neutral_skips_lp_tax():
    raw = [
        _tx(
            "sol",
            "2024-06-01T12:00:00",
            "SOL",
            TransactionType.TRANSFER,
            1,
            145,
            direction="OUT",
            gid="g3",
        ),
        _tx(
            "usdc",
            "2024-06-01T12:00:00",
            "USDC",
            TransactionType.TRANSFER,
            150,
            150,
            direction="OUT",
            gid="g3",
        ),
    ]
    txs, n = normalize_lp_for_tax(raw, policy="basis_neutral")
    assert n == 0
    assert all(t.transaction_type == TransactionType.TRANSFER for t in txs)


def test_ledger_normalize_wires_lp_tax():
    normalized, changed = normalize_tax_ledger(
        [
            _tx(
                "sol",
                "2024-06-01T12:00:00",
                "SOL",
                TransactionType.TRANSFER,
                1,
                145,
                direction="OUT",
                gid="wire1",
            ),
            _tx(
                "usdc",
                "2024-06-01T12:00:00",
                "USDC",
                TransactionType.TRANSFER,
                150,
                150,
                direction="OUT",
                gid="wire1",
            ),
        ]
    )
    assert changed
    assert any(t.event_subtype == EVENT_LP_ADD and t.asset == "SOL" for t in normalized)
    pools = compute_uk_open_pools(normalized)
    assert pools["LP:WIRE1"] == (1.0, 295.0)
