"""Solana wallet / explorer transaction CSV parser.

Handles exports with columns such as::

    Signature, Block Time, Human Time, Action, From, To,
    Amount, Flow, Value, Decimals, Token Address, Multiplier

Amounts are raw on-chain integers scaled by ``Decimals`` and ``Multiplier``.
``Value`` is treated as USD fair-market value when present.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple

import pandas as pd

from .config import is_stablecoin
from .kraken import clean_columns
from .schemas import Transaction, TransactionType
from .solana_tokens import get_registry, looks_like_mint_fragment, short_mint
from .income_classification import _is_airdrop_claim_group, _reclassify_solana_airdrop_group
from .kamino_vault import (
    KAMINO_VAULT_SHARE_MINTS,
    KVAULT_PROGRAM_ID,
    is_kamino_farms_authority,
    is_kamino_farms_receipt,
    is_kamino_vault_share,
)
from .drift import (
    DRIFT_COLLATERAL_COUNTERPARTY,
    is_drift_helius_source,
)
from .solana_lending import (
    KAMINO_LEND_AUTHORITIES,
    MARGINFI_HELIUS_SOURCES,
    MARGINFI_PROGRAM_ID,
    MARGINFI_GROUP_MAIN,
    is_kamino_lend_authority,
    is_lending_protocol_authority,
    is_lending_receipt,
    is_marginfi_authority,
)
from .token_spam import is_scam_token_label

WSOL_MINT = "So11111111111111111111111111111111111111112"

# Rent / account housekeeping — not portfolio events.
SKIP_ACTIONS = frozenset(
    {
        "create account",
        "close account",
        "createaccount",
        "closeaccount",
    }
)

# Ignore net swap legs below this quantity (MSOL in/out rounding).
SWAP_NET_EPS = 1e-6
# Jupiter route hops with no USD value are not taxable events.
MIN_SWAP_VALUE_USD = 0.01

_CORE_SOLANA_ASSETS = frozenset(
    {"SOL", "MSOL", "BSOL", "JITOSOL", "JTO", "WSOL"}
)


def is_short_mint_label(asset: str) -> bool:
    """Display label for an unlisted mint, e.g. ``DMKP…KQLK``."""
    text = asset.strip()
    return "\u2026" in text or "..." in text


def _is_core_solana_asset(asset: str) -> bool:
    sym = asset.strip().upper()
    return sym in _CORE_SOLANA_ASSETS or is_stablecoin(sym)


def is_unknown_solana_token(asset: str, token_mint: Optional[str]) -> bool:
    """True for unlisted SPL spam / routing tokens (not in Jupiter registry)."""
    registry = get_registry()
    if _is_core_solana_asset(asset):
        return False
    if token_mint and registry.lookup_mint(token_mint):
        return False
    if registry.lookup_symbol(asset):
        return False
    if looks_like_mint_fragment(asset) and registry.lookup_mint_prefix(asset):
        return False
    if is_short_mint_label(asset):
        return True
    if token_mint and len(token_mint) >= 32:
        return True
    if looks_like_mint_fragment(asset):
        return True
    return False


def _skip_solana_transfer_row(row: dict) -> bool:
    """Never import transfers of unlisted SPL tokens (airdrops & route dust)."""
    asset, mint = _resolve_asset(row)
    if is_scam_token_label(asset):
        return True
    if _is_core_solana_asset(asset) and _flow(row.get("flow")) == "in":
        amount = _human_amount(row)
        if amount > 0 and amount <= 0.0001 and _usd_value(row.get("value")) < MIN_SWAP_VALUE_USD:
            return True
    return is_unknown_solana_token(asset, mint)


def _skip_solana_swap_leg(
    asset: str, fiat: float, token_mint: Optional[str]
) -> bool:
    if is_scam_token_label(asset):
        return True
    if fiat >= MIN_SWAP_VALUE_USD:
        return False
    if _is_core_solana_asset(asset):
        return False
    return is_unknown_solana_token(asset, token_mint)


def _squash_column(name: str) -> str:
    return (
        str(name)
        .lower()
        .replace(" ", "_")
        .replace("(", "")
        .replace(")", "")
        .replace("__", "_")
        .strip("_")
    )


def _prepare_df(df: pd.DataFrame) -> pd.DataFrame:
    out = clean_columns(df)
    out.columns = [_squash_column(c) for c in out.columns]
    return out


def is_solana_wallet(df: pd.DataFrame) -> bool:
    """True when the CSV matches a Solana explorer/wallet export."""
    cols = set(_prepare_df(df).columns)
    return (
        "signature" in cols
        and "human_time" in cols
        and "action" in cols
        and "flow" in cols
        and ("token_address" in cols or "token" in cols)
    )


def _parse_time(raw: object) -> datetime:
    ts = pd.to_datetime(raw, utc=True, errors="coerce")
    if pd.isna(ts):
        raise ValueError(f"Unparseable timestamp: {raw!r}")
    return ts.to_pydatetime()


def _float(raw: object) -> float:
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return 0.0
    return float(raw)


def _usd_value(raw: object) -> float:
    """Normalize USD value columns (Solscan uses -1 for unknown)."""
    value = _float(raw)
    if value < 0:
        return 0.0
    return value


def _str_field(raw: object, default: str = "") -> str:
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return default
    text = str(raw).strip()
    return default if text.lower() == "nan" else text


def _action(raw: object) -> str:
    return _str_field(raw).lower()


def _flow(raw: object) -> str:
    return _str_field(raw).lower()


def _human_amount(row: dict) -> float:
    raw = _float(row.get("amount"))
    decimals = int(_float(row.get("decimals")))
    multiplier = _float(row.get("multiplier")) or 1.0
    value = _float(row.get("value"))
    if decimals < 0:
        return 0.0

    scaled = raw / (10**decimals) * multiplier

    # Many exports leave Decimals at 0 for SPL tokens — infer from Value when possible.
    if decimals == 0 and raw >= 1000 and value > 0:
        for candidate in (9, 6, 8, 5):
            trial = raw / (10**candidate) * multiplier
            if trial <= 0:
                continue
            unit_price = value / trial
            if 1e-9 <= unit_price <= 5_000_000:
                return trial
    if decimals == 0 and raw >= 1_000_000 and value <= 0:
        # Zero-value route hops often use wrong decimals — treat as dust.
        return raw / 1e9 * multiplier

    return scaled


def _mint_from_row(row: dict) -> str:
    token = _str_field(row.get("token"))
    mint = _str_field(row.get("token_address"))
    for candidate in (mint, token):
        if candidate and len(candidate) >= 32:
            return candidate
    return mint or token


def _resolve_asset(row: dict) -> Tuple[str, Optional[str]]:
    """Return ``(ledger_asset, token_mint)`` using the Jupiter token registry."""
    registry = get_registry()
    mint = _mint_from_row(row)
    token = _str_field(row.get("token"))

    if mint and len(mint) >= 32:
        asset, canonical_mint = registry.resolve_asset(mint)
        return asset, canonical_mint

    if token:
        if token.upper() == "SOL":
            return "SOL", None
        info = registry.lookup_symbol(token)
        if info:
            return info.symbol, info.mint
        # Explorer CSVs often truncate mints to 8 chars — not real tickers.
        if len(token) >= 4:
            by_prefix = registry.lookup_mint_prefix(token)
            if by_prefix:
                return by_prefix.symbol, by_prefix.mint
        if len(token) <= 6 and token.isalpha():
            return token.upper(), None

    if mint:
        return short_mint(mint), mint
    return "UNKNOWN", None


def normalize_solana_assets(
    transactions: List[Transaction],
) -> tuple[List[Transaction], int]:
    """Rewrite truncated mint fragments (e.g. GDFNESIA) to real tickers (CROWN)."""
    registry = get_registry()
    registry.ensure_loaded()
    updated = 0
    normalized: List[Transaction] = []
    for tx in transactions:
        updates: dict = {}
        if (
            tx.source == "solana"
            and not tx.token_mint
            and looks_like_mint_fragment(tx.asset)
        ):
            info = registry.lookup_mint_prefix(tx.asset)
            if info and info.symbol.upper() != tx.asset.upper():
                updates["asset"] = info.symbol
                updates["token_mint"] = info.mint
        if tx.counter_asset and looks_like_mint_fragment(tx.counter_asset):
            info = registry.lookup_mint_prefix(tx.counter_asset)
            if info and info.symbol.upper() != tx.counter_asset.upper():
                updates["counter_asset"] = info.symbol
        if updates:
            normalized.append(tx.model_copy(update=updates))
            updated += 1
        else:
            normalized.append(tx)
    return normalized, updated


def collapse_solana_swap_duplicate_legs(
    transactions: List[Transaction],
    *,
    time_window_sec: int = 120,
    amount_rel_tol: float = 0.001,
) -> tuple[List[Transaction], int]:
    """Drop TRANSFER IN rows that duplicate a same-signature Solana swap BUY leg.

    Helius wallet imports often emit both a raw inbound transfer and a netted
  BUY for the same Jupiter swap, which double-counts holdings and cost basis.
    """
    buys: List[Transaction] = []
    for tx in transactions:
        if tx.source != "solana":
            continue
        if tx.transaction_type != TransactionType.BUY:
            continue
        if not tx.trade_group_id:
            continue
        buys.append(tx)

    if not buys:
        return transactions, 0

    drop_ids: Set[str] = set()
    for buy in buys:
        buy_ts = buy.timestamp.timestamp()
        for tx in transactions:
            if tx.id in drop_ids or tx.id == buy.id:
                continue
            if tx.source != "solana":
                continue
            if tx.transaction_type != TransactionType.TRANSFER:
                continue
            if tx.transfer_direction != "IN":
                continue
            if tx.asset != buy.asset:
                continue
            if buy.token_mint and tx.token_mint and tx.token_mint != buy.token_mint:
                continue
            if abs(tx.timestamp.timestamp() - buy_ts) > time_window_sec:
                continue
            if tx.amount <= 0:
                continue
            rel = abs(tx.amount - buy.amount) / max(tx.amount, buy.amount)
            if rel > amount_rel_tol:
                continue
            drop_ids.add(tx.id)

    if not drop_ids:
        return transactions, 0

    return [tx for tx in transactions if tx.id not in drop_ids], len(drop_ids)


_SWAP_ID_SELL_RE = re.compile(r"-sell-", re.IGNORECASE)
_SWAP_ID_BUY_RE = re.compile(r"-buy-", re.IGNORECASE)


def _as_swap_sell(tx: Transaction, *, counter: str) -> Transaction:
    return tx.model_copy(
        update={
            "transaction_type": TransactionType.SELL,
            "transfer_direction": None,
            "counter_asset": counter,
        }
    )


def _as_swap_buy(tx: Transaction, *, counter: str) -> Transaction:
    return tx.model_copy(
        update={
            "transaction_type": TransactionType.BUY,
            "transfer_direction": None,
            "counter_asset": counter,
        }
    )


def reclassify_disguised_solana_swaps(
    transactions: List[Transaction],
) -> tuple[List[Transaction], int]:
    """Restore BUY/SELL legs that were misclassified as wallet transfers.

    Jupiter swaps are parsed as taxable swap legs, but downstream normalizers
    (Kamino Lend, liquid-staking linkers) can downgrade them to TRANSFER when
    unrelated legs share a polluted ``trade_group_id``.
    """
    by_gid: Dict[str, List[Transaction]] = defaultdict(list)
    for tx in transactions:
        if tx.source == "solana" and tx.trade_group_id:
            by_gid[tx.trade_group_id].append(tx)

    patches: Dict[str, Transaction] = {}
    changed = 0

    for group in by_gid.values():
        swap_outs = [
            t
            for t in group
            if t.transaction_type == TransactionType.TRANSFER
            and t.transfer_direction == "OUT"
            and _SWAP_ID_SELL_RE.search(t.id)
        ]
        swap_ins = [
            t
            for t in group
            if t.transaction_type == TransactionType.TRANSFER
            and t.transfer_direction == "IN"
            and _SWAP_ID_BUY_RE.search(t.id)
        ]
        if swap_outs and swap_ins:
            out_leg = swap_outs[0] if len(swap_outs) == 1 else max(swap_outs, key=lambda t: t.amount)
            in_leg = swap_ins[0] if len(swap_ins) == 1 else max(swap_ins, key=lambda t: t.amount)
            if out_leg.id not in patches:
                patches[out_leg.id] = _as_swap_sell(out_leg, counter=in_leg.asset)
                changed += 1
            if in_leg.id not in patches:
                patches[in_leg.id] = _as_swap_buy(in_leg, counter=out_leg.asset)
                changed += 1
            continue

        out_legs = [
            t
            for t in group
            if t.transaction_type == TransactionType.TRANSFER
            and t.transfer_direction == "OUT"
            and t.amount > 0
        ]
        in_legs = [
            t
            for t in group
            if t.transaction_type == TransactionType.TRANSFER
            and t.transfer_direction == "IN"
            and t.amount > 0
        ]
        on_chain_ids = {t.on_chain_tx_id for t in group if t.on_chain_tx_id}
        if len(on_chain_ids) != 1:
            continue
        if len(out_legs) != 1 or len(in_legs) != 1:
            continue
        out_leg, in_leg = out_legs[0], in_legs[0]
        if out_leg.asset == in_leg.asset:
            continue
        if out_leg.id in patches or in_leg.id in patches:
            continue
        patches[out_leg.id] = _as_swap_sell(out_leg, counter=in_leg.asset)
        patches[in_leg.id] = _as_swap_buy(in_leg, counter=out_leg.asset)
        changed += 2

    if not patches:
        return transactions, 0
    return [patches.get(tx.id, tx) for tx in transactions], changed


def repair_mismatched_solana_trade_groups(
    transactions: List[Transaction],
) -> tuple[List[Transaction], int]:
    """Reset ``trade_group_id`` when it disagrees with the signature embedded in ``id``."""
    patches: Dict[str, Transaction] = {}
    changed = 0
    for tx in transactions:
        if tx.source != "solana":
            continue
        if not tx.on_chain_tx_id or not tx.trade_group_id:
            continue
        if tx.on_chain_tx_id == tx.trade_group_id:
            continue
        if tx.on_chain_tx_id not in tx.id:
            continue
        patches[tx.id] = tx.model_copy(update={"trade_group_id": tx.on_chain_tx_id})
        changed += 1
    if not patches:
        return transactions, 0
    return [patches.get(tx.id, tx) for tx in transactions], changed


def is_solana_spam(tx: Transaction) -> bool:
    """True for unlisted SPL airdrops, routing hops, and other wallet noise."""
    if tx.source != "solana":
        return False

    if is_scam_token_label(tx.asset):
        return True

    paired_swap = (
        tx.transaction_type in (TransactionType.BUY, TransactionType.SELL)
        and bool(tx.counter_asset)
    )

    if _is_core_solana_asset(tx.asset):
        if tx.transaction_type not in (TransactionType.BUY, TransactionType.SELL):
            return False
        if tx.fiat_value_at_trigger > MIN_SWAP_VALUE_USD:
            return False
        if paired_swap:
            return False
        if tx.trade_group_id:
            return True
        if tx.amount > 10_000 and len(tx.asset) <= 12:
            return True
        return False

    if is_unknown_solana_token(tx.asset, tx.token_mint):
        # Drop all unlisted-token transfers (spam airdrops).
        if tx.transaction_type == TransactionType.TRANSFER:
            return True
        # Keep swap legs that have real USD or a linked counter leg.
        if (
            tx.transaction_type in (TransactionType.BUY, TransactionType.SELL)
            and (tx.fiat_value_at_trigger >= MIN_SWAP_VALUE_USD or tx.counter_asset)
        ):
            return False
        return True

    if tx.transaction_type not in (TransactionType.BUY, TransactionType.SELL):
        return False
    if tx.fiat_value_at_trigger > MIN_SWAP_VALUE_USD:
        return False
    if paired_swap:
        return False
    if tx.trade_group_id:
        return True
    if tx.amount > 10_000 and len(tx.asset) <= 12:
        return True
    return False


def is_phantom_solana_leg(tx: Transaction) -> bool:
    """Alias used by cleanup — drops SPL spam and Jupiter routing noise."""
    return is_solana_spam(tx)


def strip_phantom_solana_legs(transactions: List[Transaction]) -> tuple[List[Transaction], int]:
    """Remove SPL spam airdrops and bogus swap legs from an existing ledger."""
    kept: List[Transaction] = []
    removed = 0
    for tx in transactions:
        if is_phantom_solana_leg(tx):
            removed += 1
        else:
            kept.append(tx)
    return kept, removed


def _infer_wallet(records: List[dict]) -> Optional[str]:
    counts: Counter[str] = Counter()
    for row in records:
        if _action(row.get("action")) != "transfer":
            continue
        flow = _flow(row.get("flow"))
        if flow == "out":
            counts[_str_field(row.get("from"))] += 2
        elif flow == "in":
            counts[_str_field(row.get("to"))] += 2
    if not counts:
        return None
    return counts.most_common(1)[0][0]


def _row_id(row: dict, timestamp: datetime, asset: str) -> str:
    sig = _str_field(row.get("signature"))
    kind = _action(row.get("action"))
    flow = _flow(row.get("flow"))
    # Full signature (not truncated) avoids collisions between distinct txs that
    # happen to share a 20-char prefix. The token mint disambiguates multiple
    # legs of the same signature touching different assets.
    mint = _str_field(row.get("token_address") or row.get("mint")) or asset
    if sig:
        return f"sol-{sig}-{kind}-{flow}-{mint}"
    return f"sol-{timestamp.isoformat()}-{kind}-{flow}-{mint}"


def _counterparty_from_row(row: dict, direction: str) -> Optional[str]:
    from_addr = _str_field(row.get("from") or row.get("from_address"))
    to_addr = _str_field(row.get("to") or row.get("to_address"))
    if direction == "IN" and from_addr:
        return from_addr
    if direction == "OUT" and to_addr:
        return to_addr
    return None


def _transfer_tx(
    row: dict,
    *,
    timestamp: datetime,
    wallet: Optional[str],
    direction: str,
) -> Optional[Transaction]:
    amount = _human_amount(row)
    if amount < 1e-6:
        return None
    asset, token_mint = _resolve_asset(row)
    value = _usd_value(row.get("value"))
    sig = _str_field(row.get("signature")) or None
    counterparty = _counterparty_from_row(row, direction)
    venue: Optional[str] = None
    if is_drift_helius_source(_helius_source(row)):
        counterparty = DRIFT_COLLATERAL_COUNTERPARTY
        venue = "drift_collateral"
    return Transaction(
        id=_row_id(row, timestamp, asset),
        timestamp=timestamp,
        asset=asset,
        transaction_type=TransactionType.TRANSFER,
        amount=amount,
        fiat_value_at_trigger=round(value, 2) if value > 0 else 0.0,
        fee_fiat=0.0,
        fiat_currency="USD" if value > 0 else None,
        source="solana",
        transfer_direction=direction,
        token_mint=token_mint,
        counterparty_address=counterparty,
        trade_group_id=sig,
        on_chain_tx_id=sig,
        venue_order_type=venue,
    )


def _row_touches_kvault(row: dict) -> bool:
    for key in ("from", "to", "from_address", "to_address"):
        addr = _str_field(row.get(key))
        if addr == KVAULT_PROGRAM_ID:
            return True
    mint = _mint_from_row(row)
    return mint in KAMINO_VAULT_SHARE_MINTS


def _is_kamino_vault_group(rows: List[dict]) -> bool:
    return any(_row_touches_kvault(row) for row in rows)


def _amounts_close(a: float, b: float, rel_tol: float = 0.01) -> bool:
    if a <= 0 or b <= 0:
        return False
    return abs(a - b) / max(a, b) <= rel_tol


def _is_dust_sol_swap_leg(asset: str, qty: float, fiat: float) -> bool:
    """Ephemeral account rent bundled into Jupiter routes — not a fee or disposal."""
    return _sym(asset) in ("SOL", "WSOL") and fiat < MIN_SWAP_VALUE_USD and abs(qty) < 0.05


# Rent-exempt minimum balances (lamports) for token / program accounts.
_RENT_EXEMPT_LAMPORTS = frozenset(
    {
        890_880,
        1_057_920,
        2_039_280,
        1_651_400,
        7_294_080,
    }
)


def _is_solana_rent_row(row: dict) -> bool:
    """Outbound SOL locked as account rent (returned when the account closes)."""
    asset, _ = _resolve_asset(row)
    if _sym(asset) not in ("SOL", "WSOL"):
        return False
    if _flow(row.get("flow")) != "out":
        return False
    raw = _float(row.get("amount"))
    if int(raw) in _RENT_EXEMPT_LAMPORTS:
        return True
    amount = _human_amount(row)
    return amount > 0 and amount < 0.05 and _usd_value(row.get("value")) < MIN_SWAP_VALUE_USD


def _taxable_swap_outs(
    net_outs: Dict[str, float], value_out: Dict[str, float]
) -> Dict[str, float]:
    return {
        asset: qty
        for asset, qty in net_outs.items()
        if not _is_dust_sol_swap_leg(asset, abs(qty), value_out.get(asset, 0.0))
    }


def _row_touches_kamino_farms(row: dict) -> bool:
    for key in ("from", "to", "from_address", "to_address"):
        addr = _str_field(row.get(key))
        if is_kamino_farms_authority(addr):
            return True
    return False


def _is_kamino_farms_receipt_row(row: dict) -> bool:
    asset, token_mint = _resolve_asset(row)
    if is_kamino_vault_share(asset, token_mint):
        return False
    if is_kamino_farms_receipt(asset, token_mint):
        return True
    from_addr = _str_field(row.get("from"))
    to_addr = _str_field(row.get("to"))
    if is_kamino_farms_authority(from_addr) or is_kamino_farms_authority(to_addr):
        if not _is_core_solana_asset(asset) and not is_stablecoin(_sym(asset)):
            return True
    return False


def _is_kamino_farms_group(rows: List[dict]) -> bool:
    if _is_kamino_vault_group(rows):
        return False
    if _is_kamino_lend_group(rows) or _is_marginfi_group(rows):
        return False
    return any(_row_touches_kamino_farms(row) for row in rows)


def _kamino_farms_counterparty(rows: List[dict]) -> Optional[str]:
    for row in rows:
        for key in ("to", "from"):
            addr = _str_field(row.get(key))
            if is_kamino_farms_authority(addr):
                return addr
    return None


def _parse_kamino_farms_group(
    rows: List[dict], wallet: Optional[str]
) -> List[Transaction]:
    """Kamino Farms stake/unstake/harvest, including swap-then-deposit bundles."""
    timestamp = _parse_time(rows[0].get("human_time"))
    sig = _str_field(rows[0].get("signature")) or None
    trade_group_id = sig
    counterparty = _kamino_farms_counterparty(rows)

    principal_rows = [
        row
        for row in rows
        if not _is_kamino_farms_receipt_row(row) and not _is_solana_rent_row(row)
    ]

    ins: Dict[str, float] = defaultdict(float)
    outs: Dict[str, float] = defaultdict(float)
    value_in: Dict[str, float] = defaultdict(float)
    value_out: Dict[str, float] = defaultdict(float)
    mints: Dict[str, Optional[str]] = {}

    for row in principal_rows:
        asset, token_mint = _resolve_asset(row)
        mints[asset] = token_mint
        amount = _human_amount(row)
        value = _usd_value(row.get("value"))
        if amount <= 0:
            continue
        if _flow(row.get("flow")) == "in":
            ins[asset] += amount
            value_in[asset] += value
        else:
            outs[asset] += amount
            value_out[asset] += value

    deposits: Dict[str, float] = {}
    for asset in set(ins) | set(outs):
        gross_in = ins.get(asset, 0.0)
        gross_out = outs.get(asset, 0.0)
        if (
            gross_in > SWAP_NET_EPS
            and gross_out > SWAP_NET_EPS
            and _amounts_close(gross_in, gross_out)
        ):
            deposits[asset] = gross_in

    nets: Dict[str, float] = {}
    for asset in set(ins) | set(outs):
        nets[asset] = ins.get(asset, 0.0) - outs.get(asset, 0.0)

    net_ins = {a: q for a, q in nets.items() if q > SWAP_NET_EPS}
    net_outs = {a: q for a, q in nets.items() if q < -SWAP_NET_EPS}
    taxable_outs = _taxable_swap_outs(net_outs, value_out)

    total_in_usd = sum(max(0.0, v) for v in value_in.values())
    total_out_usd = sum(max(0.0, v) for v in value_out.values())
    if total_out_usd >= MIN_SWAP_VALUE_USD and total_in_usd < MIN_SWAP_VALUE_USD:
        if len(net_ins) == 1:
            value_in[next(iter(net_ins))] = total_out_usd
    elif total_in_usd >= MIN_SWAP_VALUE_USD and total_out_usd < MIN_SWAP_VALUE_USD:
        if len(taxable_outs) == 1:
            value_out[next(iter(taxable_outs))] = total_in_usd

    deposit_asset = next(iter(deposits), None) if len(deposits) == 1 else None
    swap_out_asset = next(iter(taxable_outs), None) if len(taxable_outs) == 1 else None
    counter_for_sell = deposit_asset or (
        next(iter(net_ins), None) if len(net_ins) == 1 else None
    )
    counter_for_buy = swap_out_asset

    transactions: List[Transaction] = []
    for asset, qty in net_outs.items():
        fiat = value_out.get(asset, 0.0)
        if _is_dust_sol_swap_leg(asset, abs(qty), fiat):
            continue
        if fiat > 0 and fiat < MIN_SWAP_VALUE_USD:
            continue
        if _skip_solana_swap_leg(asset, fiat, mints.get(asset)):
            continue
        leg_counter = counter_for_sell
        if leg_counter is None and deposit_asset and asset != deposit_asset:
            leg_counter = deposit_asset
        transactions.append(
            Transaction(
                id=f"sol-{trade_group_id or timestamp.isoformat()}-sell-{asset}",
                timestamp=timestamp,
                asset=asset,
                transaction_type=TransactionType.SELL,
                amount=abs(qty),
                fiat_value_at_trigger=round(max(0.0, fiat), 2),
                fee_fiat=0.0,
                fiat_currency="USD",
                counter_asset=leg_counter,
                trade_group_id=trade_group_id,
                source="solana",
                token_mint=mints.get(asset),
                on_chain_tx_id=sig,
            )
        )

    for asset, qty in net_ins.items():
        if asset in deposits:
            continue
        fiat = value_in.get(asset, 0.0)
        if fiat > 0 and fiat < MIN_SWAP_VALUE_USD:
            continue
        if _skip_solana_swap_leg(asset, fiat, mints.get(asset)):
            continue
        transactions.append(
            Transaction(
                id=f"sol-{trade_group_id or timestamp.isoformat()}-buy-{asset}",
                timestamp=timestamp,
                asset=asset,
                transaction_type=TransactionType.BUY,
                amount=qty,
                fiat_value_at_trigger=round(max(0.0, fiat), 2),
                fee_fiat=0.0,
                fiat_currency="USD",
                counter_asset=counter_for_buy,
                trade_group_id=trade_group_id,
                source="solana",
                token_mint=mints.get(asset),
                on_chain_tx_id=sig,
            )
        )

    deposit_fiat_pool = total_out_usd
    for asset, amount in deposits.items():
        fiat = value_in.get(asset, 0.0)
        if fiat <= 0 and deposit_fiat_pool >= MIN_SWAP_VALUE_USD:
            fiat = deposit_fiat_pool
        if _skip_solana_swap_leg(asset, fiat, mints.get(asset)):
            continue
        transactions.append(
            Transaction(
                id=f"sol-{trade_group_id or timestamp.isoformat()}-buy-{asset}",
                timestamp=timestamp,
                asset=asset,
                transaction_type=TransactionType.BUY,
                amount=amount,
                fiat_value_at_trigger=round(max(0.0, fiat), 2),
                fee_fiat=0.0,
                fiat_currency="USD" if fiat > 0 else None,
                counter_asset=swap_out_asset,
                trade_group_id=trade_group_id,
                source="solana",
                token_mint=mints.get(asset),
                on_chain_tx_id=sig,
            )
        )
        transactions.append(
            Transaction(
                id=f"sol-{trade_group_id or timestamp.isoformat()}-transfer-out-{asset}",
                timestamp=timestamp,
                asset=asset,
                transaction_type=TransactionType.TRANSFER,
                amount=amount,
                fiat_value_at_trigger=round(max(0.0, fiat), 2),
                fee_fiat=0.0,
                fiat_currency="USD" if fiat > 0 else None,
                source="solana",
                transfer_direction="OUT",
                counterparty_address=counterparty,
                trade_group_id=trade_group_id,
                token_mint=mints.get(asset),
                on_chain_tx_id=sig,
            )
        )

    return transactions


def _helius_source(row: dict) -> str:
    return _str_field(
        row.get("helius_source") or row.get("helius source") or row.get("Helius Source")
    ).upper()


def _helius_type(row: dict) -> str:
    return _str_field(
        row.get("helius_type") or row.get("helius type") or row.get("Helius Type")
    ).upper()


def _is_lending_receipt_row(row: dict) -> bool:
    asset, token_mint = _resolve_asset(row)
    if is_kamino_vault_share(asset, token_mint):
        return False
    if is_lending_receipt(asset, token_mint):
        return True
    from_addr = _str_field(row.get("from"))
    to_addr = _str_field(row.get("to"))
    if is_lending_protocol_authority(from_addr) or is_lending_protocol_authority(to_addr):
        if not _is_core_solana_asset(asset) and not is_stablecoin(_sym(asset)):
            return True
    return False


def _row_touches_lending_protocol(row: dict) -> bool:
    for key in ("from", "to", "from_address", "to_address"):
        addr = _str_field(row.get(key))
        if is_lending_protocol_authority(addr):
            return True
    return False


def _is_marginfi_group(rows: List[dict]) -> bool:
    if any(_helius_source(r) in MARGINFI_HELIUS_SOURCES for r in rows):
        return True
    if any("MARGINFI" in _helius_type(r) for r in rows):
        return True
    return any(_row_touches_marginfi(row) for row in rows)


def _row_touches_marginfi(row: dict) -> bool:
    for key in ("from", "to", "from_address", "to_address"):
        addr = _str_field(row.get(key))
        if is_marginfi_authority(addr):
            return True
    if MARGINFI_PROGRAM_ID in _str_field(row.get("token address")):
        return True
    return False


def _is_kamino_lend_group(rows: List[dict]) -> bool:
    if _is_kamino_vault_group(rows):
        return False
    if any(_row_touches_lending_protocol(row) for row in rows):
        return True
    lend_types = (
        "DEPOSIT_RESERVE",
        "WITHDRAW_OBLIGATION",
        "BORROW_OBLIGATION",
        "FLASH_REPAY",
        "REFRESH_OBLIGATION",
        "REPAY_OBLIGATION",
    )
    return any(any(tag in _helius_type(r) for tag in lend_types) for r in rows)


def _lending_protocol_counterparty(rows: List[dict]) -> Optional[str]:
    for row in rows:
        for key in ("to", "from"):
            addr = _str_field(row.get(key))
            if is_lending_protocol_authority(addr):
                return addr
    return None


def _is_lending_principal_asset(asset: str) -> bool:
    sym = _sym(asset)
    return _is_core_solana_asset(asset) or is_stablecoin(sym)


def _parse_lending_protocol_group(
    rows: List[dict], wallet: Optional[str]
) -> List[Transaction]:
    """Kamino Lend / Marginfi deposit, withdraw, and borrow — principal movement only."""
    timestamp = _parse_time(rows[0].get("human_time"))
    sig = _str_field(rows[0].get("signature")) or None
    trade_group_id = sig
    counterparty = _lending_protocol_counterparty(rows)

    principal_rows = [
        row
        for row in rows
        if not _is_lending_receipt_row(row) and not _is_solana_rent_row(row)
    ]

    ins: Dict[str, float] = defaultdict(float)
    outs: Dict[str, float] = defaultdict(float)
    value_in: Dict[str, float] = defaultdict(float)
    value_out: Dict[str, float] = defaultdict(float)
    mints: Dict[str, Optional[str]] = {}

    for row in principal_rows:
        asset, token_mint = _resolve_asset(row)
        if not _is_lending_principal_asset(asset):
            continue
        mints[asset] = token_mint
        amount = _human_amount(row)
        value = _usd_value(row.get("value"))
        if amount <= 0:
            continue
        if _flow(row.get("flow")) == "in":
            ins[asset] += amount
            value_in[asset] += value
        else:
            outs[asset] += amount
            value_out[asset] += value

    transactions: List[Transaction] = []
    for asset in sorted(set(ins) | set(outs)):
        net = ins.get(asset, 0.0) - outs.get(asset, 0.0)
        if abs(net) <= SWAP_NET_EPS:
            continue
        if net > SWAP_NET_EPS:
            direction = "IN"
            qty = net
            fiat = value_in.get(asset, 0.0)
            suffix = "transfer-in"
        else:
            direction = "OUT"
            qty = abs(net)
            fiat = value_out.get(asset, 0.0)
            suffix = "transfer-out"

        if _skip_solana_swap_leg(asset, fiat, mints.get(asset)):
            continue

        transactions.append(
            Transaction(
                id=f"sol-{trade_group_id or timestamp.isoformat()}-{suffix}-{asset}",
                timestamp=timestamp,
                asset=asset,
                transaction_type=TransactionType.TRANSFER,
                amount=qty,
                fiat_value_at_trigger=round(max(0.0, fiat), 2),
                fee_fiat=0.0,
                fiat_currency="USD" if fiat > 0 else None,
                source="solana",
                transfer_direction=direction,
                counterparty_address=counterparty,
                trade_group_id=trade_group_id,
                token_mint=mints.get(asset),
                on_chain_tx_id=sig,
            )
        )

    return transactions


def _is_drift_group(rows: List[dict]) -> bool:
    return any(is_drift_helius_source(_helius_source(r)) for r in rows)


def _parse_drift_collateral_group(
    rows: List[dict], wallet: Optional[str]
) -> List[Transaction]:
    """Drift spot-margin collateral deposit / withdraw — principal movement only."""
    timestamp = _parse_time(rows[0].get("human_time"))
    sig = _str_field(rows[0].get("signature")) or None
    trade_group_id = sig
    counterparty = DRIFT_COLLATERAL_COUNTERPARTY

    principal_rows = [row for row in rows if not _is_solana_rent_row(row)]

    ins: Dict[str, float] = defaultdict(float)
    outs: Dict[str, float] = defaultdict(float)
    value_in: Dict[str, float] = defaultdict(float)
    value_out: Dict[str, float] = defaultdict(float)
    mints: Dict[str, Optional[str]] = {}

    for row in principal_rows:
        asset, token_mint = _resolve_asset(row)
        if not _is_core_solana_asset(asset) and not is_stablecoin(_sym(asset)):
            continue
        mints[asset] = token_mint
        amount = _human_amount(row)
        value = _usd_value(row.get("value"))
        if amount <= 0:
            continue
        if _flow(row.get("flow")) == "in":
            ins[asset] += amount
            value_in[asset] += value
        else:
            outs[asset] += amount
            value_out[asset] += value

    transactions: List[Transaction] = []
    for asset in sorted(set(ins) | set(outs)):
        net = ins.get(asset, 0.0) - outs.get(asset, 0.0)
        if abs(net) <= SWAP_NET_EPS:
            continue
        if net > SWAP_NET_EPS:
            direction = "IN"
            qty = net
            fiat = value_in.get(asset, 0.0)
            suffix = "transfer-in"
        else:
            direction = "OUT"
            qty = abs(net)
            fiat = value_out.get(asset, 0.0)
            suffix = "transfer-out"

        if _skip_solana_swap_leg(asset, fiat, mints.get(asset)):
            continue

        transactions.append(
            Transaction(
                id=f"sol-{trade_group_id or timestamp.isoformat()}-{suffix}-{asset}",
                timestamp=timestamp,
                asset=asset,
                transaction_type=TransactionType.TRANSFER,
                amount=qty,
                fiat_value_at_trigger=round(max(0.0, fiat), 2),
                fee_fiat=0.0,
                fiat_currency="USD" if fiat > 0 else None,
                source="solana",
                transfer_direction=direction,
                counterparty_address=counterparty,
                trade_group_id=trade_group_id,
                token_mint=mints.get(asset),
                on_chain_tx_id=sig,
                venue_order_type="drift_collateral",
            )
        )

    return transactions


def _parse_kamino_vault_group(
    rows: List[dict], wallet: Optional[str]
) -> List[Transaction]:
    """Kvault deposit/withdraw — principal movement, not a taxable swap."""
    timestamp = _parse_time(rows[0].get("human_time"))
    sig = _str_field(rows[0].get("signature")) or None
    trade_group_id = sig

    share_net = 0.0
    wsol_in = wsol_out = 0.0
    native_in = native_out = 0.0
    fiat_in = fiat_out = 0.0

    for row in rows:
        asset, token_mint = _resolve_asset(row)
        amount = _human_amount(row)
        if amount <= 0:
            continue
        value = _usd_value(row.get("value"))
        flow = _flow(row.get("flow"))

        if is_kamino_vault_share(asset, token_mint) or (
            token_mint and token_mint in KAMINO_VAULT_SHARE_MINTS
        ):
            share_net += amount if flow == "in" else -amount
            continue

        if not _is_core_solana_asset(asset) and _sym(asset) != "WSOL":
            continue

        is_wsol = token_mint == WSOL_MINT or _sym(asset) == "WSOL"
        if flow == "in":
            if is_wsol:
                wsol_in += amount
                fiat_in += value
            else:
                native_in += amount
                if value > 0:
                    fiat_in += value
        else:
            if is_wsol:
                wsol_out += amount
                fiat_out += value
            else:
                native_out += amount
                if value > 0:
                    fiat_out += value

    sol_in = wsol_in
    sol_out = wsol_out
    if native_in > SWAP_NET_EPS:
        if sol_in <= SWAP_NET_EPS or _amounts_close(sol_in, native_in):
            sol_in = max(sol_in, native_in)
        elif _amounts_close(sol_in + native_in, 2 * sol_in):
            sol_in = sol_in
        else:
            sol_in += native_in
    if native_out > SWAP_NET_EPS:
        if sol_out <= SWAP_NET_EPS or _amounts_close(sol_out, native_out):
            sol_out = max(sol_out, native_out)
        elif _amounts_close(sol_out + native_out, 2 * sol_out):
            sol_out = sol_out
        else:
            sol_out += native_out

    transactions: List[Transaction] = []
    if share_net < -SWAP_NET_EPS and sol_in > SWAP_NET_EPS:
        transactions.append(
            Transaction(
                id=f"sol-{trade_group_id or timestamp.isoformat()}-transfer-in-SOL",
                timestamp=timestamp,
                asset="SOL",
                transaction_type=TransactionType.TRANSFER,
                amount=sol_in,
                fiat_value_at_trigger=round(max(0.0, fiat_in), 2),
                fee_fiat=0.0,
                fiat_currency="USD" if fiat_in > 0 else None,
                source="solana",
                transfer_direction="IN",
                token_mint=WSOL_MINT,
                trade_group_id=trade_group_id,
                on_chain_tx_id=sig,
            )
        )
    elif share_net > SWAP_NET_EPS and sol_out > SWAP_NET_EPS:
        transactions.append(
            Transaction(
                id=f"sol-{trade_group_id or timestamp.isoformat()}-transfer-out-SOL",
                timestamp=timestamp,
                asset="SOL",
                transaction_type=TransactionType.TRANSFER,
                amount=sol_out,
                fiat_value_at_trigger=round(max(0.0, fiat_out), 2),
                fee_fiat=0.0,
                fiat_currency="USD" if fiat_out > 0 else None,
                source="solana",
                transfer_direction="OUT",
                token_mint=WSOL_MINT,
                trade_group_id=trade_group_id,
                on_chain_tx_id=sig,
            )
        )
    return transactions


def _sym(asset: str) -> str:
    return asset.strip().upper()


def _aggregate_swap_legs(
    rows: List[dict],
) -> tuple[Dict[str, float], Dict[str, float], Dict[str, float], Dict[str, Optional[str]]]:
    """Net token flows per asset inside one on-chain signature (Jupiter routes)."""
    nets: Dict[str, float] = defaultdict(float)
    value_in: Dict[str, float] = defaultdict(float)
    value_out: Dict[str, float] = defaultdict(float)
    mints: Dict[str, Optional[str]] = {}

    for row in rows:
        asset, token_mint = _resolve_asset(row)
        mints[asset] = token_mint
        amount = _human_amount(row)
        value = _usd_value(row.get("value"))
        if _flow(row.get("flow")) == "in":
            nets[asset] += amount
            value_in[asset] += value
        else:
            nets[asset] -= amount
            value_out[asset] += value

    return nets, value_in, value_out, mints


def _parse_swap_group(
    rows: List[dict], wallet: Optional[str]
) -> List[Transaction]:
    """Same-signature transfers netted to taxable BUY/SELL legs."""
    timestamp = _parse_time(rows[0].get("human_time"))
    sig = _str_field(rows[0].get("signature")) or None
    trade_group_id = sig
    nets, value_in, value_out, mints = _aggregate_swap_legs(rows)

    total_in_usd = sum(max(0.0, v) for v in value_in.values())
    total_out_usd = sum(max(0.0, v) for v in value_out.values())
    net_ins = {a: q for a, q in nets.items() if q > SWAP_NET_EPS}
    net_outs = {a: q for a, q in nets.items() if q < -SWAP_NET_EPS}
    if total_out_usd >= MIN_SWAP_VALUE_USD and total_in_usd < MIN_SWAP_VALUE_USD:
        if len(net_ins) == 1:
            value_in[next(iter(net_ins))] = total_out_usd
    elif total_in_usd >= MIN_SWAP_VALUE_USD and total_out_usd < MIN_SWAP_VALUE_USD:
        if len(net_outs) == 1:
            value_out[next(iter(net_outs))] = total_in_usd

    counter_for_sell = next(iter(net_ins), None) if len(net_ins) == 1 else None
    counter_for_buy = next(iter(net_outs), None) if len(net_outs) == 1 else None

    transactions: List[Transaction] = []
    for asset, qty in net_outs.items():
        fiat = value_out.get(asset, 0.0)
        if fiat > 0 and fiat < MIN_SWAP_VALUE_USD:
            continue
        if _skip_solana_swap_leg(asset, fiat, mints.get(asset)):
            continue
        transactions.append(
            Transaction(
                id=f"sol-{trade_group_id or timestamp.isoformat()}-sell-{asset}",
                timestamp=timestamp,
                asset=asset,
                transaction_type=TransactionType.SELL,
                amount=abs(qty),
                fiat_value_at_trigger=round(max(0.0, fiat), 2),
                fee_fiat=0.0,
                fiat_currency="USD",
                counter_asset=counter_for_sell,
                trade_group_id=trade_group_id,
                source="solana",
                token_mint=mints.get(asset),
                on_chain_tx_id=sig,
            )
        )
    for asset, qty in net_ins.items():
        fiat = value_in.get(asset, 0.0)
        if fiat > 0 and fiat < MIN_SWAP_VALUE_USD:
            continue
        if _skip_solana_swap_leg(asset, fiat, mints.get(asset)):
            continue
        transactions.append(
            Transaction(
                id=f"sol-{trade_group_id or timestamp.isoformat()}-buy-{asset}",
                timestamp=timestamp,
                asset=asset,
                transaction_type=TransactionType.BUY,
                amount=qty,
                fiat_value_at_trigger=round(max(0.0, fiat), 2),
                fee_fiat=0.0,
                fiat_currency="USD",
                counter_asset=counter_for_buy,
                trade_group_id=trade_group_id,
                source="solana",
                token_mint=mints.get(asset),
                on_chain_tx_id=sig,
            )
        )
    if _is_airdrop_claim_group(transactions):
        return _reclassify_solana_airdrop_group(transactions)
    return transactions


def _parse_single_transfer(
    row: dict, wallet: Optional[str]
) -> Optional[Transaction]:
    timestamp = _parse_time(row.get("human_time"))
    flow = _flow(row.get("flow"))
    from_addr = _str_field(row.get("from"))
    to_addr = _str_field(row.get("to"))

    if flow == "in":
        direction = "IN"
    elif flow == "out":
        direction = "OUT"
    elif wallet and to_addr == wallet:
        direction = "IN"
    elif wallet and from_addr == wallet:
        direction = "OUT"
    else:
        direction = "IN" if flow == "in" else "OUT"

    if _skip_solana_transfer_row(row):
        return None
    return _transfer_tx(row, timestamp=timestamp, wallet=wallet, direction=direction)


def parse_solana_wallet(df: pd.DataFrame, wallet: Optional[str] = None) -> List[Transaction]:
    """Parse a Solana wallet/explorer CSV into unified transactions."""
    get_registry().load()
    prepared = _prepare_df(df)
    records = prepared.to_dict(orient="records")
    resolved_wallet = wallet or _infer_wallet(records)

    by_signature: Dict[str, List[dict]] = defaultdict(list)
    singles: List[dict] = []

    for row in records:
        act = _action(row.get("action"))
        if act in SKIP_ACTIONS:
            continue
        if act != "transfer":
            continue
        sig = _str_field(row.get("signature"))
        if sig:
            by_signature[sig].append(row)
        else:
            singles.append(row)

    transactions: List[Transaction] = []
    processed_sigs: Set[str] = set()

    for sig, group in by_signature.items():
        assets = {_resolve_asset(r)[0] for r in group}
        flows = {_flow(r.get("flow")) for r in group}
        if len(group) >= 2 and "in" in flows and "out" in flows and len(assets) >= 1:
            if _is_kamino_vault_group(group):
                km_txs = _parse_kamino_vault_group(group, resolved_wallet)
                if km_txs:
                    transactions.extend(km_txs)
                    processed_sigs.add(sig)
                    continue
            if _is_drift_group(group):
                drift_txs = _parse_drift_collateral_group(group, resolved_wallet)
                if drift_txs:
                    transactions.extend(drift_txs)
                    processed_sigs.add(sig)
                    continue
            if _is_kamino_lend_group(group) or _is_marginfi_group(group):
                lend_txs = _parse_lending_protocol_group(group, resolved_wallet)
                if lend_txs:
                    transactions.extend(lend_txs)
                    processed_sigs.add(sig)
                    continue
            if _is_kamino_farms_group(group):
                farms_txs = _parse_kamino_farms_group(group, resolved_wallet)
                if farms_txs:
                    transactions.extend(farms_txs)
                    processed_sigs.add(sig)
                    continue
            swap_txs = _parse_swap_group(group, resolved_wallet)
            if swap_txs:
                transactions.extend(swap_txs)
                processed_sigs.add(sig)
            else:
                for row in group:
                    tx = _parse_single_transfer(row, resolved_wallet)
                    if tx:
                        transactions.append(tx)
                processed_sigs.add(sig)
        elif _is_drift_group(group):
            drift_txs = _parse_drift_collateral_group(group, resolved_wallet)
            if drift_txs:
                transactions.extend(drift_txs)
                processed_sigs.add(sig)
            else:
                for row in group:
                    tx = _parse_single_transfer(row, resolved_wallet)
                    if tx:
                        transactions.append(tx)
                processed_sigs.add(sig)
        elif _is_kamino_lend_group(group) or _is_marginfi_group(group):
            lend_txs = _parse_lending_protocol_group(group, resolved_wallet)
            if lend_txs:
                transactions.extend(lend_txs)
                processed_sigs.add(sig)
            else:
                for row in group:
                    tx = _parse_single_transfer(row, resolved_wallet)
                    if tx:
                        transactions.append(tx)
                processed_sigs.add(sig)
        else:
            for row in group:
                tx = _parse_single_transfer(row, resolved_wallet)
                if tx:
                    transactions.append(tx)
            processed_sigs.add(sig)

    for row in singles:
        tx = _parse_single_transfer(row, resolved_wallet)
        if tx:
            transactions.append(tx)

    seen: Set[str] = set()
    unique: List[Transaction] = []
    for tx in sorted(transactions, key=lambda t: (t.timestamp, t.id)):
        if tx.id in seen:
            continue
        seen.add(tx.id)
        unique.append(tx)

    return unique
