"""Sanity-check the AMM LP ingestion path against real wallet data.

Runs the exact production pipeline (fetch -> parse -> spam/dust strip ->
normalize_lp_for_tax) and prints what happened to LP positions, so you can
confirm real SPL mint/burn legs are captured and that removes close against the
real burned quantity instead of the inferred full-lot fallback.

Usage
-----
Live fetch (needs HELIUS_API_KEY or SOLSCAN_API_KEY in the environment/.env):

    python scripts/lp_sanity_check.py <SOLANA_WALLET_ADDRESS>

Offline, from a captured provider JSON dump (no API key needed):

    python scripts/lp_sanity_check.py --helius helius_txs.json --wallet <ADDR>
    python scripts/lp_sanity_check.py --solscan solscan_transfers.json --wallet <ADDR>

The provider JSON is the raw array returned by:
    https://api.helius.xyz/v0/addresses/<ADDR>/transactions   (Helius)
    https://pro-api.solscan.io/v2.0/account/transfer           (Solscan, "data")
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow running as `python scripts/lp_sanity_check.py` from apps/api.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from app.amm_lp import normalize_lp_for_tax  # noqa: E402
from app.defi_tax import EVENT_LP_ADD, EVENT_LP_REMOVE  # noqa: E402
from app.ledger_filters import strip_dust_transactions  # noqa: E402
from app.schemas import TransactionType  # noqa: E402
from app.solana_fetch import (  # noqa: E402
    fetch_wallet_transactions,
    helius_transactions_to_rows,
    solscan_transfers_to_rows,
)
from app.solana_tokens import get_registry  # noqa: E402
from app.solana_wallet import parse_solana_wallet  # noqa: E402
from app.token_spam import strip_spam_transactions  # noqa: E402


def _load_from_rows(rows: list[dict], wallet: str):
    get_registry().load()
    parsed = parse_solana_wallet(pd.DataFrame(rows), wallet=wallet)
    kept, spam = strip_spam_transactions(parsed)
    kept, dust = strip_dust_transactions(kept)
    return kept, spam, dust


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("wallet", nargs="?", help="Solana wallet address (live fetch)")
    ap.add_argument("--wallet", dest="wallet_flag", help="wallet address (offline mode)")
    ap.add_argument("--helius", help="path to captured Helius transactions JSON")
    ap.add_argument("--solscan", help="path to captured Solscan transfers JSON")
    args = ap.parse_args()

    wallet = args.wallet or args.wallet_flag
    if not wallet:
        ap.error("a wallet address is required (positional, or --wallet in offline mode)")

    if args.helius or args.solscan:
        path = Path(args.helius or args.solscan)
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            payload = payload.get("data") or payload.get("transactions") or []
        rows = (
            helius_transactions_to_rows(wallet, payload)
            if args.helius
            else solscan_transfers_to_rows(wallet, payload)
        )
        print(f"Converted {len(rows)} provider records -> CSV rows")
        supply_rows = [r for r in rows if r.get("Token Change")]
        print(f"  of which SPL supply-change (mint/burn) legs: {len(supply_rows)}")
        txs, spam, dust = _load_from_rows(rows, wallet)
    else:
        print(f"Fetching live wallet history for {wallet} ...")
        txs = fetch_wallet_transactions(wallet)
        spam = dust = None

    lp_legs = [t for t in txs if t.venue_order_type == "amm_lp"]
    print("\n=== Parsed ledger ===")
    print(f"total transactions: {len(txs)}")
    if spam is not None:
        print(f"spam stripped: {spam}   dust stripped: {dust}")
    print(f"preserved LP-share legs (venue=amm_lp): {len(lp_legs)}")
    for t in lp_legs:
        print(
            f"  [{t.transfer_direction:>3}] {t.asset:<14} qty={t.amount:<18} "
            f"mint={t.token_mint} sig={t.on_chain_tx_id}"
        )

    out, changed = normalize_lp_for_tax(txs)
    lp_events = [
        t for t in out if t.event_subtype in (EVENT_LP_ADD, EVENT_LP_REMOVE)
    ]
    inferred = [t for t in out if t.normalization_note]
    real_disposals = [
        t
        for t in out
        if t.event_subtype == EVENT_LP_REMOVE
        and t.transaction_type == TransactionType.SELL
        and not t.normalization_note
    ]

    print("\n=== After normalize_lp_for_tax ===")
    print(f"changed rows: {changed}")
    print(f"lp_add / lp_remove events: {len(lp_events)}")
    print(f"real (burn-backed) LP disposals: {len(real_disposals)}")
    print(f"INFERRED LP disposals (no burn leg found): {len(inferred)}")
    for t in inferred:
        print(f"  ! {t.asset:<14} qty={t.amount:<18} note={t.normalization_note}")

    print("\nInterpretation:")
    print("  - LP-share legs > 0 and real disposals present => real mint/burn captured.")
    print("  - INFERRED count is the fallback; ideally low. Each is flagged for review.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
