"""Real LP receipt (mint) / disposal (burn) leg ingestion and end-to-end booking.

Covers the Solscan / Helius fetch converters that now surface SPL supply changes,
the dedicated LP-group parser that preserves those legs through the spam/dust
filters, and the full normalization chain closing a *partial* LP withdrawal with
the real burn quantity (rather than the inferred full-lot fallback).
"""

import pandas as pd

from app.amm_lp import normalize_lp_for_tax
from app.defi_tax import EVENT_LP_ADD, EVENT_LP_REMOVE
from app.hmrc_cgt_engine import calculate_uk_cgt, compute_uk_open_pools
from app.ledger_filters import strip_dust_transactions
from app.schemas import TransactionType
from app.solana_fetch import (
    helius_transactions_to_rows,
    solscan_transfers_to_rows,
)
from app.solana_wallet import parse_solana_wallet
from app.token_spam import strip_spam_transactions

WALLET = "4K4PdbMGiWt46LQcjDAuMzR8aKgpB2LNqPQaWL6NgEkS"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
WSOL_MINT = "So11111111111111111111111111111111111111112"
# Unlisted LP-share mint (44-char base58, not in the Jupiter registry).
LP_MINT = "RAYLPmintFAKE2222222222222222222222222222222"

ADD_SIG = "addLPsig1111111111111111111111111111111111111111111111111111111111"
REMOVE_SIG = "removeLPsig2222222222222222222222222222222222222222222222222222222"


def _csv_row(sig, flow, mint, token, amount, value, *, decimals=0, token_change=""):
    return {
        "Signature": sig,
        "Human Time": "2024-06-01 12:00:00" if sig == ADD_SIG else "2024-09-01 12:00:00",
        "Action": "transfer",
        "From": WALLET if flow == "out" else "",
        "To": WALLET if flow == "in" else "",
        "Amount": amount,
        "Flow": flow,
        "Value": value,
        "Decimals": decimals,
        "Multiplier": 1,
        "Token Address": mint,
        "Token": token,
        "Token Change": token_change,
    }


# --------------------------------------------------------------------------- #
# Fetch converters
# --------------------------------------------------------------------------- #


def test_solscan_emits_burn_and_mint_legs():
    transfers = [
        {
            "trans_id": ADD_SIG,
            "block_time": 1717243200,
            "activity_type": "ACTIVITY_SPL_MINT",
            "token_address": LP_MINT,
            "token_decimals": 6,
            "amount": 100_000_000,
            "value": 0,
        },
        {
            "trans_id": REMOVE_SIG,
            "block_time": 1725192000,
            "activity_type": "ACTIVITY_SPL_BURN",
            "token_address": LP_MINT,
            "token_decimals": 6,
            "amount": 40_000_000,
            "value": 0,
        },
    ]
    rows = solscan_transfers_to_rows(WALLET, transfers)
    assert len(rows) == 2
    mint_row = next(r for r in rows if r["Token Change"] == "mint")
    burn_row = next(r for r in rows if r["Token Change"] == "burn")
    assert mint_row["Flow"] == "in" and mint_row["To"] == WALLET
    assert burn_row["Flow"] == "out" and burn_row["From"] == WALLET
    assert mint_row["Token Address"] == LP_MINT


def test_helius_emits_supply_change_from_balance_changes():
    tx = {
        "signature": REMOVE_SIG,
        "timestamp": 1725192000,
        "source": "RAYDIUM",
        "type": "WITHDRAW_LIQUIDITY",
        "tokenTransfers": [
            {
                "fromUserAccount": "pool",
                "toUserAccount": WALLET,
                "tokenAmount": 60,
                "mint": USDC_MINT,
            }
        ],
        "accountData": [
            {
                "account": WALLET,
                "tokenBalanceChanges": [
                    {
                        "userAccount": WALLET,
                        "tokenAccount": "lpAta",
                        "mint": LP_MINT,
                        "rawTokenAmount": {"tokenAmount": "-40000000", "decimals": 6},
                    }
                ],
            }
        ],
    }
    rows = helius_transactions_to_rows(WALLET, [tx])
    burn_rows = [r for r in rows if r["Token Change"] == "burn"]
    assert len(burn_rows) == 1
    assert burn_rows[0]["Token Address"] == LP_MINT
    assert burn_rows[0]["Flow"] == "out"
    # The USDC transfer is unaffected and not double-counted as a supply change.
    assert not any(r["Token Address"] == USDC_MINT and r["Token Change"] for r in rows)


def test_helius_tags_empty_counterparty_transfer_as_burn():
    """Raydium burns often appear as tokenTransfers with an empty `to`."""
    tx = {
        "signature": REMOVE_SIG,
        "timestamp": 1725192000,
        "source": "RAYDIUM",
        "type": "WITHDRAW_LIQUIDITY",
        "tokenTransfers": [
            {
                "fromUserAccount": "pool",
                "toUserAccount": WALLET,
                "tokenAmount": 28.33,
                "mint": USDC_MINT,
            },
            {
                "fromUserAccount": WALLET,
                "toUserAccount": "",
                "tokenAmount": 4.504009568,
                "mint": LP_MINT,
            },
        ],
        "accountData": [
            {
                "account": WALLET,
                "tokenBalanceChanges": [
                    {
                        "userAccount": WALLET,
                        "mint": LP_MINT,
                        "rawTokenAmount": {
                            "tokenAmount": "-4504009568",
                            "decimals": 9,
                        },
                    },
                    {
                        "userAccount": WALLET,
                        "mint": USDC_MINT,
                        "rawTokenAmount": {"tokenAmount": "28330000", "decimals": 6},
                    },
                ],
            }
        ],
    }
    rows = helius_transactions_to_rows(WALLET, [tx])
    burn_rows = [r for r in rows if r["Token Change"] == "burn"]
    assert len(burn_rows) == 1
    assert burn_rows[0]["Token Address"] == LP_MINT
    # Tagged from the empty-to transfer; balance-change duplicate suppressed.
    assert abs(burn_rows[0]["Amount"] - 4.504009568) < 1e-9


def test_helius_withdraw_liquidity_survives_spam_and_books_real_burn():
    """End-to-end: empty-to LP burn + returned principals → real disposal."""
    add_tx = {
        "signature": ADD_SIG,
        "timestamp": 1717243200,
        "source": "RAYDIUM",
        "type": "ADD_LIQUIDITY",
        "tokenTransfers": [
            {
                "fromUserAccount": WALLET,
                "toUserAccount": "pool",
                "tokenAmount": 1.0,
                "mint": WSOL_MINT,
            },
            {
                "fromUserAccount": WALLET,
                "toUserAccount": "pool",
                "tokenAmount": 150.0,
                "mint": USDC_MINT,
            },
            {
                "fromUserAccount": "",
                "toUserAccount": WALLET,
                "tokenAmount": 100.0,
                "mint": LP_MINT,
            },
        ],
        "accountData": [],
    }
    remove_tx = {
        "signature": REMOVE_SIG,
        "timestamp": 1725192000,
        "source": "RAYDIUM",
        "type": "WITHDRAW_LIQUIDITY",
        "tokenTransfers": [
            {
                "fromUserAccount": "pool",
                "toUserAccount": WALLET,
                "tokenAmount": 0.4,
                "mint": WSOL_MINT,
            },
            {
                "fromUserAccount": "pool",
                "toUserAccount": WALLET,
                "tokenAmount": 60.0,
                "mint": USDC_MINT,
            },
            {
                "fromUserAccount": WALLET,
                "toUserAccount": "",
                "tokenAmount": 40.0,
                "mint": LP_MINT,
            },
        ],
        "accountData": [],
    }
    rows = helius_transactions_to_rows(WALLET, [add_tx, remove_tx])
    assert any(r["Token Change"] == "burn" for r in rows)
    assert any(r["Token Change"] == "mint" for r in rows)

    txs = parse_solana_wallet(pd.DataFrame(rows), wallet=WALLET)
    kept, _ = strip_spam_transactions(txs)
    kept, _ = strip_dust_transactions(kept)
    assert any(t.venue_order_type == "amm_lp" for t in kept)

    out, _ = normalize_lp_for_tax(kept)
    assert not any(t.id.startswith("lp-dispose-") for t in out)
    burn = next(
        t
        for t in out
        if t.event_subtype == EVENT_LP_REMOVE
        and t.transaction_type == TransactionType.SELL
    )
    assert burn.amount == 40.0
    assert burn.normalization_note is None


def test_pumpswap_deposit_withdraw_tags_fungible_lp_burn():
    """PumpSwap uses DEPOSIT/WITHDRAW naming but still burns a fungible LP mint."""
    remove_tx = {
        "signature": REMOVE_SIG,
        "timestamp": 1725192000,
        "source": "PUMP_AMM",
        "type": "WITHDRAW",
        "tokenTransfers": [
            {
                "fromUserAccount": "pool",
                "toUserAccount": WALLET,
                "tokenAmount": 1.0,
                "mint": WSOL_MINT,
            },
            {
                "fromUserAccount": "pool",
                "toUserAccount": WALLET,
                "tokenAmount": 100.0,
                "mint": USDC_MINT,
            },
            {
                "fromUserAccount": WALLET,
                "toUserAccount": "",
                "tokenAmount": 50.0,
                "mint": LP_MINT,
            },
        ],
        "accountData": [],
    }
    rows = helius_transactions_to_rows(WALLET, [remove_tx])
    assert any(r.get("Token Change") == "burn" for r in rows)
    assert any(r.get("Helius Source") == "PUMP_AMM" for r in rows)

    txs = parse_solana_wallet(pd.DataFrame(rows), wallet=WALLET)
    kept, _ = strip_spam_transactions(txs)
    kept, _ = strip_dust_transactions(kept)
    assert any(t.venue_order_type == "amm_lp" for t in kept)
    assert any(t.venue_order_type == "amm_lp_pool" for t in kept)



# --------------------------------------------------------------------------- #
# Parser preserves LP legs through spam / dust filters
# --------------------------------------------------------------------------- #


def _parse_add_and_partial_remove():
    add_rows = [
        _csv_row(ADD_SIG, "out", WSOL_MINT, "SOL", 1, 145),
        _csv_row(ADD_SIG, "out", USDC_MINT, "USDC", 150, 150),
        _csv_row(ADD_SIG, "in", LP_MINT, LP_MINT[:8], 100, 0, token_change="mint"),
    ]
    remove_rows = [
        _csv_row(REMOVE_SIG, "in", WSOL_MINT, "SOL", 0.4, 64),
        _csv_row(REMOVE_SIG, "in", USDC_MINT, "USDC", 60, 60),
        _csv_row(REMOVE_SIG, "out", LP_MINT, LP_MINT[:8], 40, 0, token_change="burn"),
    ]
    return parse_solana_wallet(pd.DataFrame(add_rows + remove_rows), wallet=WALLET)


def test_lp_legs_survive_parse_and_spam_dust_filters():
    txs = _parse_add_and_partial_remove()
    lp_legs = [t for t in txs if t.venue_order_type == "amm_lp"]
    assert len(lp_legs) == 2  # real mint + real burn

    kept, _ = strip_spam_transactions(txs)
    kept, _ = strip_dust_transactions(kept)
    surviving = [t for t in kept if t.venue_order_type == "amm_lp"]
    assert len(surviving) == 2


def test_partial_withdrawal_closes_with_real_burn_quantity():
    txs = _parse_add_and_partial_remove()
    kept, _ = strip_spam_transactions(txs)
    kept, _ = strip_dust_transactions(kept)

    out, changed = normalize_lp_for_tax(kept)
    assert changed

    # No inference fallback — the real burn leg was used.
    assert not any(t.id.startswith("lp-dispose-") for t in out)
    assert all(t.normalization_note is None for t in out)

    lp_acq = next(
        t for t in out if t.event_subtype == EVENT_LP_ADD and t.transaction_type == TransactionType.BUY
    )
    lp_disp = next(
        t for t in out if t.event_subtype == EVENT_LP_REMOVE and t.transaction_type == TransactionType.SELL
    )
    # Same LP-share asset on both legs, real quantities preserved.
    assert lp_acq.asset == lp_disp.asset
    assert lp_acq.amount == 100
    assert lp_disp.amount == 40

    # UK Section 104: partial disposal — cost apportioned 40/100 of £295.
    # (Proceeds/gain depend on FMV price-store enrichment of the returned assets,
    # so assert only the deterministic cost-side apportionment here.)
    report = calculate_uk_cgt(out, tax_year_label="2024/25")
    disp = next(r for r in report.rows if r.asset == lp_disp.asset)
    assert round(disp.allowable_cost, 2) == 118.0

    pools = compute_uk_open_pools(out)
    qty, basis = pools[lp_disp.asset]
    assert round(qty, 4) == 60.0
    assert round(basis, 2) == 177.0
