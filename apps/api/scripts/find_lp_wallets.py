"""Find wallets that removed fungible LP on a given AMM (for sanity checks).

Does not call Python urllib against Helius (edge often blocks it). Uses curl.

Examples
--------
# PumpSwap (fungible LP, non-Raydium)
python scripts/find_lp_wallets.py --program pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA \\
    --type WITHDRAW --source PUMP_AMM

# Raydium AMM v4 (what we already verified)
python scripts/find_lp_wallets.py --program 675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8 \\
    --type WITHDRAW_LIQUIDITY --source RAYDIUM

Requires HELIUS_API_KEY or KEY in the environment.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def _key() -> str:
    key = (
        os.environ.get("HELIUS_API_KEY")
        or os.environ.get("CRYPTO_TAX_HELIUS_API_KEY")
        or os.environ.get("KEY")
        or ""
    )
    if not key:
        sys.exit("Set HELIUS_API_KEY (or KEY) in the environment.")
    return key


def curl_json(url: str) -> object:
    r = subprocess.run(
        ["curl", "-s", "--max-time", "60", url],
        capture_output=True,
        text=True,
    )
    if not r.stdout.strip():
        return None
    return json.loads(r.stdout)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--program", required=True, help="AMM program id to scan")
    ap.add_argument(
        "--type",
        default="WITHDRAW_LIQUIDITY",
        help="Helius tx type filter (WITHDRAW_LIQUIDITY, WITHDRAW, …)",
    )
    ap.add_argument(
        "--source",
        default="RAYDIUM",
        help="Helius source filter (RAYDIUM, PUMP_AMM, …)",
    )
    ap.add_argument("--out", default="lp_venue.json", help="save matching txs here")
    ap.add_argument("--limit-wallets", type=int, default=8)
    ap.add_argument("--scans", type=int, default=40)
    args = ap.parse_args()

    key = _key()
    base = f"https://api.helius.xyz/v0/addresses/{args.program}/transactions"
    before = ""
    wallets: dict[str, int] = {}
    kept: list[dict] = []

    for i in range(args.scans):
        url = (
            f"{base}?api-key={key}&type={args.type}&source={args.source}&limit=100"
        )
        if before:
            url += f"&before-signature={before}"
        data = curl_json(url)
        if isinstance(data, dict):
            msg = str(data.get("error") or data)
            if "set to " in msg:
                before = msg.split("set to ")[-1].strip().rstrip(".")
                print(f"scan {i}: continue cursor…")
                continue
            print(msg)
            break
        if not isinstance(data, list) or not data:
            print("no more results")
            break
        for t in data:
            fp = t.get("feePayer")
            if not fp:
                continue
            # Prefer txs that look like fungible LP burns (empty-to transfer
            # or negative tokenBalanceChange for the fee payer).
            burnish = False
            for tr in t.get("tokenTransfers") or []:
                if tr.get("fromUserAccount") == fp and not tr.get("toUserAccount"):
                    burnish = True
                    break
            if not burnish:
                for ad in t.get("accountData") or []:
                    for c in ad.get("tokenBalanceChanges") or []:
                        if c.get("userAccount") != fp:
                            continue
                        raw = (c.get("rawTokenAmount") or {}).get("tokenAmount")
                        try:
                            if int(str(raw)) < 0:
                                burnish = True
                                break
                        except (TypeError, ValueError):
                            pass
                    if burnish:
                        break
            if not burnish:
                continue
            wallets[fp] = wallets.get(fp, 0) + 1
            kept.append(t)
        before = data[-1]["signature"]
        print(f"scan {i}: burnish wallets={len(wallets)} kept_txs={len(kept)}")
        if len(wallets) >= args.limit_wallets:
            break
        time.sleep(0.25)

    Path(args.out).write_text(json.dumps(kept, indent=2), encoding="utf-8")
    print(f"\nSaved {len(kept)} txs -> {args.out}")
    print("\n--- candidate wallets (feePayer: #burnish removes) ---")
    for w, n in sorted(wallets.items(), key=lambda kv: kv[1], reverse=True):
        print(w, n)
    if wallets:
        best = max(wallets, key=wallets.get)
        print("\nRUN THIS:")
        print(
            f"python apps/api/scripts/lp_sanity_check.py --helius {args.out} --wallet {best}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
