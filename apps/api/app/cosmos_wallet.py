"""Cosmos / Celestia wallet transaction CSV parser.

Handles exports with columns such as::

    index, type, from, to, txhash, amount, token, denom,
    timestamp, unitPrice, totalPrice
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd

from .kraken import clean_columns
from .schemas import Transaction, TransactionType

# Map chain micro-denoms to tickers when ``token`` is absent.
DENOM_TO_TICKER: Dict[str, str] = {
    "utia": "TIA",
    "uosmo": "OSMO",
    "uatom": "ATOM",
    "ujuno": "JUNO",
    "udvpn": "DVPN",
    "uakt": "AKT",
    "uxprt": "XPRT",
    "unibi": "NIBI",
    "udym": "DYM",
    "uinj": "INJ",
    "uscrt": "SCRT",
    "ustars": "STARS",
    "ukava": "KAVA",
    "uluna": "LUNA",
    "uaxl": "AXL",
}

STAKING_TYPES = frozenset(
    {
        "getreward",
        "withdrawrewards",
        "withdraw_reward",
        "withdrawrewards",
        "claimrewards",
        "claim",
    }
)

DELEGATE_TYPES = frozenset({"delegate", "beginredelegate", "redelegate"})
UNDELEGATE_TYPES = frozenset({"undelegate", "unbond", "cancelundelegate"})

TRANSFER_OUT_TYPES = frozenset(
    {"send", "ibcsend", "ibc_send", "msgsend", "withdraw", "withdrawal"}
)
TRANSFER_IN_TYPES = frozenset(
    {"receive", "ibcrecv", "ibc_receive", "ibcreceive", "deposit", "recv"}
)


def is_cosmos_wallet(df: pd.DataFrame) -> bool:
    """True when the CSV matches a Cosmos-chain wallet export."""
    cols = set(clean_columns(df).columns)
    required = {"type", "from", "to", "txhash", "amount", "timestamp"}
    if not required.issubset(cols):
        return False
    # Distinguish from exchange ledgers and Kraken.
    if "utc_time" in cols or "refid" in cols:
        return False
    return "denom" in cols or "token" in cols


def _parse_time(raw: object) -> datetime:
    ts = pd.to_datetime(raw, utc=True, errors="coerce")
    if pd.isna(ts):
        raise ValueError(f"Unparseable timestamp: {raw!r}")
    return ts.to_pydatetime()


def _float(raw: object) -> float:
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return 0.0
    return float(raw)


def _msg_type(raw: object) -> str:
    return str(raw or "").strip().lower().replace(" ", "").replace("_", "")


def _normalize_asset(token: object, denom: object) -> str:
    ticker = str(token or "").strip().upper()
    if ticker and ticker != "NAN":
        return ticker
    key = str(denom or "").strip().lower()
    if key in DENOM_TO_TICKER:
        return DENOM_TO_TICKER[key]
    if key.startswith("u") and len(key) > 1:
        return key[1:].upper()
    return key.upper()


def _infer_wallet_address(records: List[dict]) -> Optional[str]:
    """Guess the user's wallet from the most frequent self-directed address."""
    counts: Counter[str] = Counter()
    for row in records:
        kind = _msg_type(row.get("type"))
        from_addr = str(row.get("from", "")).strip()
        to_addr = str(row.get("to", "")).strip()
        if kind in DELEGATE_TYPES or kind in TRANSFER_OUT_TYPES:
            if from_addr and "valoper" not in from_addr:
                counts[from_addr] += 2
        if kind in STAKING_TYPES or kind in TRANSFER_IN_TYPES:
            if to_addr and "valoper" not in to_addr:
                counts[to_addr] += 2
        if kind in UNDELEGATE_TYPES and to_addr:
            counts[to_addr] += 2
    if not counts:
        return None
    return counts.most_common(1)[0][0]


def _infer_source(records: List[dict]) -> str:
    for row in records:
        for field in ("from", "to"):
            addr = str(row.get(field, "")).lower()
            if addr.startswith("celestia"):
                return "celestia"
            if addr.startswith("osmo"):
                return "osmosis"
            if addr.startswith("cosmos"):
                return "cosmos"
    return "cosmos"


def _row_id(row: dict) -> str:
    txhash = str(row.get("txhash", "")).strip()
    index = str(row.get("index", "")).strip()
    kind = _msg_type(row.get("type"))
    asset = _normalize_asset(row.get("token"), row.get("denom"))
    if txhash and index:
        return f"{txhash[:16]}-{index}-{kind}-{asset}"
    return f"{txhash}-{kind}-{asset}"


def _parse_row(row: dict, wallet: Optional[str], source: str) -> Optional[Transaction]:
    kind = _msg_type(row.get("type"))
    asset = _normalize_asset(row.get("token"), row.get("denom"))
    amount = _float(row.get("amount"))
    if amount <= 0:
        return None

    timestamp = _parse_time(row.get("timestamp"))
    total_price = _float(row.get("totalprice") or row.get("total_price"))
    unit_price = _float(row.get("unitprice") or row.get("unit_price"))
    from_addr = str(row.get("from", "")).strip()
    to_addr = str(row.get("to", "")).strip()

    tx_type: TransactionType
    fiat_value = 0.0
    fiat_currency: Optional[str] = None
    transfer_direction: Optional[str] = None

    if kind in STAKING_TYPES:
        tx_type = TransactionType.STAKING
        fiat_value = total_price if total_price > 0 else amount * unit_price
        fiat_currency = "USD" if fiat_value > 0 else None
    elif kind in DELEGATE_TYPES:
        tx_type = TransactionType.TRANSFER
        transfer_direction = "OUT"
    elif kind in UNDELEGATE_TYPES:
        tx_type = TransactionType.TRANSFER
        transfer_direction = "IN"
    elif kind in TRANSFER_OUT_TYPES:
        tx_type = TransactionType.TRANSFER
        transfer_direction = "OUT"
    elif kind in TRANSFER_IN_TYPES:
        tx_type = TransactionType.TRANSFER
        transfer_direction = "IN"
        if total_price > 0:
            # FMV at receipt — useful if this becomes a taxable acquisition later.
            fiat_value = total_price
            fiat_currency = "USD"
    elif kind in {"swap", "msgswap", "exchangeswap"}:
        # Swaps need dedicated pairing — skip until swap parser exists.
        return None
    else:
        return None

    # Direction sanity check when we know the user's wallet.
    if wallet and tx_type == TransactionType.TRANSFER:
        if from_addr == wallet and transfer_direction != "IN":
            transfer_direction = "OUT"
        elif to_addr == wallet and transfer_direction != "OUT":
            transfer_direction = "IN"

    return Transaction(
        id=_row_id(row),
        timestamp=timestamp,
        asset=asset,
        transaction_type=tx_type,
        amount=amount,
        fiat_value_at_trigger=round(fiat_value, 2),
        fee_fiat=0.0,
        fiat_currency=fiat_currency,
        source=source,
        transfer_direction=transfer_direction,
    )


def parse_cosmos_wallet(df: pd.DataFrame) -> List[Transaction]:
    """Parse a Cosmos / Celestia wallet CSV into unified transactions."""
    normalised = clean_columns(df)
    # Normalise camelCase headers from some exporters.
    rename = {}
    for col in normalised.columns:
        key = str(col).lower().replace(" ", "_")
        if key == "totalprice":
            rename[col] = "totalprice"
        elif key == "unitprice":
            rename[col] = "unitprice"
    records = normalised.rename(columns=rename).to_dict(orient="records")

    wallet = _infer_wallet_address(records)
    source = _infer_source(records)

    transactions: List[Transaction] = []
    for row in records:
        parsed = _parse_row(row, wallet, source)
        if parsed is not None:
            transactions.append(parsed)

    transactions.sort(key=lambda t: (t.timestamp, t.id))
    return transactions
