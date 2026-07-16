"""Fetch Solana wallet history via Solscan (or Helius fallback) and parse it."""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Dict, List, Optional

import pandas as pd

from .solana_tokens import get_registry
from .solana_wallet import parse_solana_wallet
from .schemas import Transaction

SOLANA_ADDRESS_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
WSOL_MINT = "So11111111111111111111111111111111111111112"
NATIVE_SOL_MINT = "So11111111111111111111111111111111111111111"
SOLSCAN_BASE = "https://pro-api.solscan.io/v2.0/account/transfer"
HELIUS_BASE = "https://api.helius.xyz/v0/addresses"
DEFAULT_PAGE_SIZE = 100
DEFAULT_MAX_TRANSACTIONS = 5_000
REQUEST_DELAY_SEC = 0.22

SKIP_ACTIVITY_TYPES = frozenset(
    {
        "ACTIVITY_SPL_CREATE_ACCOUNT",
        "ACTIVITY_SPL_CLOSE_ACCOUNT",
    }
)

# Mint/burn activities are kept (not skipped) so an LP position's real receipt
# (mint) and disposal (burn) legs survive. They are tagged with ``token_change``
# and only preserved downstream when part of a same-signature LP add/remove.
BURN_ACTIVITY_TYPES = frozenset({"ACTIVITY_SPL_BURN"})
MINT_ACTIVITY_TYPES = frozenset({"ACTIVITY_SPL_MINT"})


def solscan_api_key() -> Optional[str]:
    return os.environ.get("SOLSCAN_API_KEY") or os.environ.get(
        "CRYPTO_TAX_SOLSCAN_API_KEY"
    )


def helius_api_key() -> Optional[str]:
    return os.environ.get("HELIUS_API_KEY") or os.environ.get("CRYPTO_TAX_HELIUS_API_KEY")


def solana_wallet_import_enabled() -> bool:
    return bool(solscan_api_key() or helius_api_key())


def solana_wallet_provider() -> Optional[str]:
    if helius_api_key():
        return "helius"
    if solscan_api_key():
        # Free Solscan keys are detected at import time; Helius is required for fetch.
        return "solscan"
    return None


def is_valid_solana_address(address: str) -> bool:
    return bool(SOLANA_ADDRESS_RE.match(address.strip()))


def _request_json(url: str, *, headers: Optional[Dict[str, str]] = None) -> object:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "crypto-tax-dashboard/1.0",
            "Accept": "application/json",
            **(headers or {}),
        },
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


class SolscanUpgradeRequired(ValueError):
    """Solscan free tier cannot call the wallet transfer endpoint."""


def _solscan_headers(api_key: str) -> Dict[str, str]:
    return {"token": api_key, "Authorization": f"Bearer {api_key}"}


def _solscan_error_message(payload: object) -> str:
    if isinstance(payload, dict):
        errors = payload.get("errors")
        if isinstance(errors, dict) and errors.get("message"):
            return str(errors["message"])
        if payload.get("message"):
            return str(payload["message"])
    return str(payload)


def _raise_solscan_http_error(exc: urllib.error.HTTPError) -> None:
    body = exc.read().decode("utf-8", errors="replace")
    message = body
    try:
        payload = json.loads(body)
        message = _solscan_error_message(payload)
        if exc.code == 401 and "upgrade" in message.lower():
            raise SolscanUpgradeRequired(
                "Solscan free API keys cannot fetch wallet transfers. "
                "Add HELIUS_API_KEY to .env (free at helius.dev), upgrade Solscan "
                "to a paid Lite plan, or import a Solana CSV export instead."
            ) from exc
    except json.JSONDecodeError:
        pass
    raise ValueError(f"Solscan API error {exc.code}: {message}") from exc


def fetch_solscan_transfers(
    address: str,
    *,
    api_key: str,
    max_transactions: int = DEFAULT_MAX_TRANSACTIONS,
) -> List[dict]:
    """Page through Solscan account transfer history."""
    collected: List[dict] = []
    page = 1
    page_size = min(DEFAULT_PAGE_SIZE, max_transactions)

    while len(collected) < max_transactions:
        query = urllib.parse.urlencode(
            {
                "address": address,
                "page": str(page),
                "page_size": str(page_size),
                "sort_by": "block_time",
                "sort_order": "asc",
                "exclude_amount_zero": "true",
            }
        )
        url = f"{SOLSCAN_BASE}?{query}"

        try:
            payload = _request_json(url, headers=_solscan_headers(api_key))
        except urllib.error.HTTPError as exc:
            _raise_solscan_http_error(exc)
        except urllib.error.URLError as exc:
            raise ValueError(f"Could not reach Solscan API: {exc.reason}") from exc

        if not isinstance(payload, dict):
            raise ValueError("Unexpected Solscan response — expected a JSON object.")

        if not payload.get("success"):
            message = _solscan_error_message(payload)
            if "upgrade" in message.lower():
                raise SolscanUpgradeRequired(
                    "Solscan free API keys cannot fetch wallet transfers. "
                    "Add HELIUS_API_KEY to .env (free at helius.dev), upgrade Solscan "
                    "to a paid Lite plan, or import a Solana CSV export instead."
                )
            raise ValueError(f"Solscan API error: {message}")

        batch = payload.get("data") or []
        if not isinstance(batch, list):
            raise ValueError("Unexpected Solscan response — expected a transfer list.")

        if not batch:
            break

        collected.extend(batch)
        if len(batch) < page_size:
            break
        page += 1
        time.sleep(REQUEST_DELAY_SEC)

    return collected[:max_transactions]


def fetch_helius_transactions(
    address: str,
    *,
    api_key: str,
    max_transactions: int = DEFAULT_MAX_TRANSACTIONS,
) -> List[dict]:
    """Page through Helius enhanced transaction history (legacy fallback)."""
    collected: List[dict] = []
    before: Optional[str] = None

    while len(collected) < max_transactions:
        limit = min(DEFAULT_PAGE_SIZE, max_transactions - len(collected))
        query = urllib.parse.urlencode(
            {
                "api-key": api_key,
                "limit": str(limit),
                "token-accounts": "balanceChanged",
            }
        )
        if before:
            query += "&" + urllib.parse.urlencode({"before": before})
        url = f"{HELIUS_BASE}/{address}/transactions?{query}"

        try:
            payload = _request_json(url)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise ValueError(f"Helius API error {exc.code}: {body}") from exc
        except urllib.error.URLError as exc:
            raise ValueError(f"Could not reach Helius API: {exc.reason}") from exc

        if not isinstance(payload, list):
            raise ValueError("Unexpected Helius response — expected a transaction list.")

        if not payload:
            break

        collected.extend(payload)
        before = str(payload[-1].get("signature") or "")
        if not before or len(payload) < limit:
            break

    return collected


def _human_time_from_unix(raw: object) -> str:
    try:
        stamp = int(raw)
    except (TypeError, ValueError):
        stamp = 0
    if stamp <= 0:
        return datetime.now(timezone.utc).isoformat()
    return datetime.fromtimestamp(stamp, tz=timezone.utc).isoformat()


def _row(
    *,
    signature: str,
    human_time: str,
    flow: str,
    from_addr: str,
    to_addr: str,
    amount: float,
    mint: str,
    decimals: int,
    value: float = 0.0,
    helius_source: str = "",
    helius_type: str = "",
    token_change: str = "",
) -> dict:
    return {
        "Signature": signature,
        "Human Time": human_time,
        "Action": "transfer",
        "From": from_addr,
        "To": to_addr,
        "Amount": amount,
        "Flow": flow,
        "Value": value,
        "Decimals": decimals,
        "Token Address": mint,
        "Multiplier": 1,
        "Helius Source": helius_source,
        "Helius Type": helius_type,
        # "burn" / "mint" for SPL supply changes (LP receipt/disposal legs).
        "Token Change": token_change,
    }


def solscan_transfers_to_rows(wallet: str, transfers: List[dict]) -> List[dict]:
    """Convert Solscan transfer rows into Solana CSV-shaped rows."""
    wallet = wallet.strip()
    rows: List[dict] = []

    for item in transfers:
        if not isinstance(item, dict):
            continue
        activity = str(item.get("activity_type") or "")
        if activity in SKIP_ACTIVITY_TYPES:
            continue

        from_addr = str(item.get("from_address") or "")
        to_addr = str(item.get("to_address") or "")
        flow = str(item.get("flow") or "").lower()

        # SPL supply changes (LP receipt/disposal) rarely carry a counterparty or
        # flow — mint credits the wallet, burn debits it.
        token_change = ""
        if activity in BURN_ACTIVITY_TYPES:
            token_change = "burn"
            flow = "out"
            from_addr = from_addr or wallet
        elif activity in MINT_ACTIVITY_TYPES:
            token_change = "mint"
            flow = "in"
            to_addr = to_addr or wallet

        if flow not in ("in", "out"):
            if from_addr == wallet:
                flow = "out"
            elif to_addr == wallet:
                flow = "in"
            else:
                continue

        mint = str(item.get("token_address") or "")
        if mint == NATIVE_SOL_MINT:
            mint = WSOL_MINT

        decimals = int(item.get("token_decimals") or 9)
        amount = float(item.get("amount") or 0)
        if amount <= 0:
            continue

        value = float(item.get("value") or 0)
        rows.append(
            _row(
                signature=str(item.get("trans_id") or ""),
                human_time=_human_time_from_unix(item.get("block_time")),
                flow=flow,
                from_addr=from_addr,
                to_addr=to_addr,
                amount=amount,
                mint=mint,
                decimals=decimals,
                value=value,
                token_change=token_change,
            )
        )

    return rows


def _helius_supply_change_rows(
    tx: dict,
    *,
    wallet: str,
    signature: str,
    human_time: str,
    helius_source: str,
    helius_type: str,
    skip_mints: set,
) -> List[dict]:
    """Emit LP mint/burn legs from ``accountData[].tokenBalanceChanges``.

    Mints and burns change the wallet's token balance without a matching
    ``tokenTransfer`` (no counterparty). Net the raw deltas per mint for the
    wallet owner and emit an in/out leg tagged with ``token_change`` when the
    mint was not already seen as a regular transfer in this signature.
    """
    net_by_mint: Dict[str, int] = {}
    decimals_by_mint: Dict[str, int] = {}

    for acct in tx.get("accountData") or []:
        if not isinstance(acct, dict):
            continue
        for change in acct.get("tokenBalanceChanges") or []:
            if not isinstance(change, dict):
                continue
            if str(change.get("userAccount") or "") != wallet:
                continue
            mint = str(change.get("mint") or "")
            if not mint or mint in skip_mints:
                continue
            raw = change.get("rawTokenAmount") or {}
            try:
                delta = int(str(raw.get("tokenAmount")))
            except (TypeError, ValueError):
                continue
            if delta == 0:
                continue
            net_by_mint[mint] = net_by_mint.get(mint, 0) + delta
            decimals_by_mint[mint] = int(raw.get("decimals") or 0)

    rows: List[dict] = []
    for mint, delta in net_by_mint.items():
        if delta == 0:
            continue
        is_mint = delta > 0
        rows.append(
            _row(
                signature=signature,
                human_time=human_time,
                flow="in" if is_mint else "out",
                from_addr="" if is_mint else wallet,
                to_addr=wallet if is_mint else "",
                amount=float(abs(delta)),
                mint=mint,
                decimals=decimals_by_mint.get(mint, 0),
                helius_source=helius_source,
                helius_type=helius_type,
                token_change="mint" if is_mint else "burn",
            )
        )
    return rows


def helius_transactions_to_rows(wallet: str, transactions: List[dict]) -> List[dict]:
    """Convert Helius enhanced transactions into Solana CSV-shaped rows."""
    wallet = wallet.strip()
    rows: List[dict] = []

    for tx in transactions:
        human_time = _human_time_from_unix(tx.get("timestamp") or tx.get("blockTime"))
        signature = str(tx.get("signature") or "")
        helius_source = str(tx.get("source") or "")
        helius_type = str(tx.get("type") or "")

        for native in tx.get("nativeTransfers") or []:
            if not isinstance(native, dict):
                continue
            from_addr = str(native.get("fromUserAccount") or "")
            to_addr = str(native.get("toUserAccount") or "")
            lamports = float(native.get("amount") or 0)
            if lamports <= 0:
                continue
            if from_addr == wallet:
                flow = "out"
            elif to_addr == wallet:
                flow = "in"
            else:
                continue
            rows.append(
                _row(
                    signature=signature,
                    human_time=human_time,
                    flow=flow,
                    from_addr=from_addr,
                    to_addr=to_addr,
                    amount=lamports,
                    mint=WSOL_MINT,
                    decimals=9,
                    helius_source=helius_source,
                    helius_type=helius_type,
                )
            )

        # Mints already emitted as tagged burn/mint transfers are skipped in the
        # balance-change pass so we don't double-count the same SPL supply leg.
        transfer_mints: set[str] = set()
        for transfer in tx.get("tokenTransfers") or []:
            if not isinstance(transfer, dict):
                continue
            from_addr = str(transfer.get("fromUserAccount") or "")
            to_addr = str(transfer.get("toUserAccount") or "")
            amount = float(transfer.get("tokenAmount") or 0)
            mint = str(transfer.get("mint") or "")
            if amount <= 0 or not mint:
                continue
            if from_addr == wallet:
                flow = "out"
            elif to_addr == wallet:
                flow = "in"
            else:
                continue
            # Raydium / CPMM LP burns often appear as a transfer with an empty
            # counterparty (and again in tokenBalanceChanges). Tag them here so
            # the LP parser sees a real burn even when skip_mints suppresses the
            # balance-change duplicate.
            token_change = ""
            if flow == "out" and not to_addr:
                token_change = "burn"
            elif flow == "in" and not from_addr:
                token_change = "mint"
            transfer_mints.add(mint)
            rows.append(
                _row(
                    signature=signature,
                    human_time=human_time,
                    flow=flow,
                    from_addr=from_addr,
                    to_addr=to_addr,
                    amount=amount,
                    mint=mint,
                    decimals=0,
                    helius_source=helius_source,
                    helius_type=helius_type,
                    token_change=token_change,
                )
            )

        rows.extend(
            _helius_supply_change_rows(
                tx,
                wallet=wallet,
                signature=signature,
                human_time=human_time,
                helius_source=helius_source,
                helius_type=helius_type,
                skip_mints=transfer_mints,
            )
        )

        if helius_source.upper() == "DRIFT":
            wallet_delta = 0
            fee_lamports = int(tx.get("fee") or 0)
            for acct in tx.get("accountData") or []:
                if not isinstance(acct, dict):
                    continue
                if str(acct.get("account") or "") != wallet:
                    continue
                wallet_delta = int(acct.get("nativeBalanceChange") or 0)
                break
            if wallet_delta != 0 and not (wallet_delta < 0 and abs(wallet_delta) <= fee_lamports):
                covered = sum(
                    float(native.get("amount") or 0)
                    for native in tx.get("nativeTransfers") or []
                    if isinstance(native, dict)
                    and (
                        str(native.get("fromUserAccount") or "") == wallet
                        or str(native.get("toUserAccount") or "") == wallet
                    )
                )
                residual = wallet_delta - (
                    covered if wallet_delta > 0 else -covered
                )
                if abs(residual) > 5000:
                    flow = "in" if residual > 0 else "out"
                    lamports = abs(residual)
                    rows.append(
                        _row(
                            signature=signature,
                            human_time=human_time,
                            flow=flow,
                            from_addr="" if flow == "in" else wallet,
                            to_addr=wallet if flow == "in" else "",
                            amount=float(lamports),
                            mint=WSOL_MINT,
                            decimals=9,
                            helius_source=helius_source,
                            helius_type=helius_type,
                        )
                    )

    return rows


def _fetch_via_helius(address: str, *, max_transactions: int) -> List[dict]:
    helius_key = helius_api_key()
    if not helius_key:
        raise SolscanUpgradeRequired(
            "Solscan free API keys cannot fetch wallet transfers. "
            "Add HELIUS_API_KEY to .env (free at helius.dev), upgrade Solscan "
            "to a paid Lite plan, or import a Solana CSV export instead."
        )
    return helius_transactions_to_rows(
        address,
        fetch_helius_transactions(
            address, api_key=helius_key, max_transactions=max_transactions
        ),
    )


def fetch_wallet_transactions(
    address: str,
    *,
    max_transactions: int = DEFAULT_MAX_TRANSACTIONS,
) -> List[Transaction]:
    """Fetch and parse on-chain activity for a Solana wallet address."""
    address = address.strip()
    if not is_valid_solana_address(address):
        raise ValueError("Invalid Solana wallet address.")

    solscan_key = solscan_api_key()
    helius_key = helius_api_key()
    if not solscan_key and not helius_key:
        raise ValueError(
            "Solana API key required. Set HELIUS_API_KEY (free at helius.dev) "
            "for wallet import, then restart the API."
        )

    get_registry().load()
    rows: List[dict]

    if helius_key:
        rows = helius_transactions_to_rows(
            address,
            fetch_helius_transactions(
                address, api_key=helius_key, max_transactions=max_transactions
            ),
        )
    else:
        try:
            rows = solscan_transfers_to_rows(
                address,
                fetch_solscan_transfers(
                    address,
                    api_key=solscan_key or "",
                    max_transactions=max_transactions,
                ),
            )
        except SolscanUpgradeRequired:
            rows = _fetch_via_helius(address, max_transactions=max_transactions)

    if not rows:
        return []

    frame = pd.DataFrame(rows)
    parsed = parse_solana_wallet(frame, wallet=address)
    try:
        from .drift_fetch import fetch_wallet_transactions as fetch_drift_wallet

        drift = fetch_drift_wallet(address)
    except ValueError:
        drift = []
    return parsed + drift
