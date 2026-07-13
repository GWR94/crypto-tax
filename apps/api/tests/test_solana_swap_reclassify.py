"""Tests for Jupiter swaps misclassified as wallet transfers."""

from __future__ import annotations

from datetime import datetime, timezone

from app.liquid_staking import normalize_liquid_staking
from app.schemas import Transaction, TransactionType
from app.solana_lending import normalize_lending_protocols
from app.solana_wallet import reclassify_disguised_solana_swaps

KAMINO_LEND = "9DrvZvyWh1HuAoZxvYWMvkf2XCzryCpGgHqrMjyDWpmo"
KAMINO_WITHDRAW_SIG = "ScGyXB81DtPLC8zTFoeB28ebDpjUrvTN6JqvjGm8LKcXRLyzRVwxfRvRwoR9ys8k9Ffomxv41Ldoxj8KLWM5ngP"
JUPITER_SWAP_SIG = (
    "5XbMAkuWHFXiNb1srxxPgQKBcwk52HvdYFMbUf3zVw6a6HS8E4VkBbpCXNB5jNEzSKCJrh9b9qEcwnQ5SAVHCinL"
)
MSOL_AMOUNT = 1.399453074
SOL_AMOUNT = 3.672093348


def _tx(
    tx_id: str,
    when: str,
    asset: str,
    ttype: TransactionType,
    amount: float,
    *,
    direction: str | None = None,
    gid: str | None = None,
    on_chain: str | None = None,
    counterparty: str | None = None,
) -> Transaction:
    return Transaction(
        id=tx_id,
        timestamp=datetime.fromisoformat(when).replace(tzinfo=timezone.utc),
        asset=asset,
        transaction_type=ttype,
        amount=amount,
        fiat_value_at_trigger=0.0,
        fee_fiat=0.0,
        fiat_currency="USD",
        source="solana",
        transfer_direction=direction,
        trade_group_id=gid,
        on_chain_tx_id=on_chain or gid,
        counterparty_address=counterparty,
    )


def test_liquid_staking_does_not_link_lst_transfer_across_signatures():
    """Earlier Kamino mSOL receipt must not join a later Jupiter swap group."""
    txs = [
        _tx(
            f"sol-{KAMINO_WITHDRAW_SIG}-transfer-in-MSOL",
            "2025-07-31T08:36:46",
            "MSOL",
            TransactionType.TRANSFER,
            MSOL_AMOUNT,
            direction="IN",
            gid=KAMINO_WITHDRAW_SIG,
            on_chain=KAMINO_WITHDRAW_SIG,
            counterparty=KAMINO_LEND,
        ),
        _tx(
            f"sol-{JUPITER_SWAP_SIG}-sell-MSOL",
            "2025-07-31T08:39:20",
            "MSOL",
            TransactionType.SELL,
            MSOL_AMOUNT,
            gid=JUPITER_SWAP_SIG,
            on_chain=JUPITER_SWAP_SIG,
        ),
        _tx(
            f"sol-{JUPITER_SWAP_SIG}-buy-SOL",
            "2025-07-31T08:39:20",
            "SOL",
            TransactionType.BUY,
            SOL_AMOUNT,
            gid=JUPITER_SWAP_SIG,
            on_chain=JUPITER_SWAP_SIG,
        ),
    ]
    out, _ = normalize_liquid_staking(txs)
    kamino = next(t for t in out if t.on_chain_tx_id == KAMINO_WITHDRAW_SIG)
    assert kamino.trade_group_id == KAMINO_WITHDRAW_SIG
    assert kamino.transaction_type == TransactionType.TRANSFER


def test_lending_normalizer_skips_mixed_signature_groups():
    """Kamino counterparty on another tx must not poison a Jupiter swap group."""
    polluted = [
        _tx(
            f"sol-{KAMINO_WITHDRAW_SIG}-transfer-in-MSOL",
            "2025-07-31T08:36:46",
            "MSOL",
            TransactionType.TRANSFER,
            MSOL_AMOUNT,
            direction="IN",
            gid=JUPITER_SWAP_SIG,
            on_chain=KAMINO_WITHDRAW_SIG,
            counterparty=KAMINO_LEND,
        ),
        _tx(
            f"sol-{JUPITER_SWAP_SIG}-sell-MSOL",
            "2025-07-31T08:39:20",
            "MSOL",
            TransactionType.SELL,
            MSOL_AMOUNT,
            gid=JUPITER_SWAP_SIG,
            on_chain=JUPITER_SWAP_SIG,
        ),
        _tx(
            f"sol-{JUPITER_SWAP_SIG}-buy-SOL",
            "2025-07-31T08:39:20",
            "SOL",
            TransactionType.BUY,
            SOL_AMOUNT,
            gid=JUPITER_SWAP_SIG,
            on_chain=JUPITER_SWAP_SIG,
        ),
    ]
    out, n = normalize_lending_protocols(polluted)
    assert n == 0
    sell = next(t for t in out if t.id.endswith("-sell-MSOL"))
    buy = next(t for t in out if t.id.endswith("-buy-SOL"))
    assert sell.transaction_type == TransactionType.SELL
    assert buy.transaction_type == TransactionType.BUY


def test_reclassify_disguised_swap_from_transfer_legs():
    """Restore SELL/BUY when swap legs were downgraded to TRANSFER."""
    txs = [
        _tx(
            f"sol-{JUPITER_SWAP_SIG}-sell-MSOL",
            "2025-07-31T08:39:20",
            "MSOL",
            TransactionType.TRANSFER,
            MSOL_AMOUNT,
            direction="OUT",
            gid=JUPITER_SWAP_SIG,
            on_chain=JUPITER_SWAP_SIG,
        ),
        _tx(
            f"sol-{JUPITER_SWAP_SIG}-buy-SOL",
            "2025-07-31T08:39:20",
            "SOL",
            TransactionType.TRANSFER,
            SOL_AMOUNT,
            direction="IN",
            gid=JUPITER_SWAP_SIG,
            on_chain=JUPITER_SWAP_SIG,
        ),
    ]
    out, n = reclassify_disguised_solana_swaps(txs)
    assert n == 2
    sell = next(t for t in out if "sell" in t.id)
    buy = next(t for t in out if "buy" in t.id)
    assert sell.transaction_type == TransactionType.SELL
    assert buy.transaction_type == TransactionType.BUY
    assert sell.counter_asset == "SOL"
    assert buy.counter_asset == "MSOL"


def test_repair_mismatched_trade_group_id():
    txs = [
        _tx(
            f"sol-{KAMINO_WITHDRAW_SIG}-transfer-in-MSOL",
            "2025-07-31T08:36:46",
            "MSOL",
            TransactionType.TRANSFER,
            MSOL_AMOUNT,
            direction="IN",
            gid=JUPITER_SWAP_SIG,
            on_chain=KAMINO_WITHDRAW_SIG,
            counterparty=KAMINO_LEND,
        ),
    ]
    from app.solana_wallet import repair_mismatched_solana_trade_groups

    out, n = repair_mismatched_solana_trade_groups(txs)
    assert n == 1
    assert out[0].trade_group_id == KAMINO_WITHDRAW_SIG
