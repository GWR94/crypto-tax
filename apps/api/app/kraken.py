"""Kraken Ledgers CSV parser.

Kraken exports every balance change as a ledger row. Spot trades appear as two
(or more) rows sharing the same ``refid`` — one debit and one credit. This
module pairs those rows and maps them into the unified :class:`Transaction`
schema.
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Set, Tuple

import pandas as pd

from .config import STABLECOIN_ASSETS, EXCHANGE_ASSET_ALIASES
from .schemas import Transaction, TransactionType

# Kraken uses internal asset codes; map the common ones to tickers.
KRAKEN_ASSET_MAP: Dict[str, str] = {
    "XXBT": "BTC",
    "XBT": "BTC",
    "XETH": "ETH",
    "XLTC": "LTC",
    "XXRP": "XRP",
    "XDOGE": "DOGE",
    "XXDG": "DOGE",
    "ZUSD": "USD",
    "ZEUR": "EUR",
    "ZGBP": "GBP",
    "ZCAD": "CAD",
    "ZAUD": "AUD",
    "ZJPY": "JPY",
    "ZCHF": "CHF",
}

# Fiat currencies used as the quote leg in trades (not crypto/stablecoin).
FIAT_CURRENCIES: Set[str] = {
    "USD",
    "EUR",
    "GBP",
    "CAD",
    "AUD",
    "JPY",
    "CHF",
    "NZD",
    "SGD",
    "HKD",
}

def clean_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Normalise CSV headers (BOM, quotes, casing, spaces) for reliable detection."""
    out = df.copy()
    out.columns = [
        str(col).strip().lstrip("\ufeff").strip('"').lower().replace(" ", "_")
        for col in out.columns
    ]
    return out


def is_kraken_ledger(df: pd.DataFrame) -> bool:
    """True when the CSV looks like a Kraken Ledgers export."""
    cols = set(clean_columns(df).columns)
    required = {"txid", "refid", "time", "type", "asset", "amount"}
    if required.issubset(cols):
        return True
    # Slightly relaxed — Kraken ledgers always carry refid + txid together.
    return {"txid", "refid", "type", "asset", "amount"}.issubset(cols)


def normalize_asset(raw: str) -> str:
    asset = str(raw).strip().upper()
    asset = KRAKEN_ASSET_MAP.get(asset, asset)
    return EXCHANGE_ASSET_ALIASES.get(asset, asset)


def normalize_exchange_asset_aliases(
    transactions: List[Transaction],
) -> Tuple[List[Transaction], int]:
    """Rewrite exchange-specific tickers (e.g. MEXC CROWN2) to wallet symbols."""
    patches: List[Transaction] = []
    changed = 0
    for tx in transactions:
        asset = normalize_asset(tx.asset)
        counter = normalize_asset(tx.counter_asset) if tx.counter_asset else None
        if asset == tx.asset and (not counter or counter == tx.counter_asset):
            patches.append(tx)
            continue
        updates: Dict[str, object] = {}
        if asset != tx.asset:
            updates["asset"] = asset
        if counter and tx.counter_asset and counter != tx.counter_asset:
            updates["counter_asset"] = counter
        patches.append(tx.model_copy(update=updates))
        changed += 1
    return patches, changed


def is_fiat(asset: str) -> bool:
    return normalize_asset(asset) in FIAT_CURRENCIES


def is_stablecoin(asset: str) -> bool:
    return normalize_asset(asset) in STABLECOIN_ASSETS


def is_quote_leg(asset: str) -> bool:
    """Fiat or stablecoin used as the quote side of a spot trade."""
    normalized = normalize_asset(asset)
    return is_fiat(normalized) or is_stablecoin(normalized)


def _parse_time(raw: object) -> datetime:
    ts = pd.to_datetime(raw, utc=True, errors="coerce")
    if pd.isna(ts):
        raise ValueError(f"Unparseable Kraken timestamp: {raw!r}")
    return ts.to_pydatetime()


def _float(raw: object) -> float:
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return 0.0
    return float(raw)


def _fee_fiat(fee: float, qty: float, fiat_value: float) -> float:
    """Estimate fee in fiat when Kraken reports the fee in the asset itself."""
    if fee <= 0 or qty <= 0 or fiat_value <= 0:
        return 0.0
    return round(fee * (fiat_value / qty), 4)


def _spend_fiat_notional(asset: str, qty: float, fee: float) -> tuple[float, float]:
    """Split Kraken ``spend`` row amount vs a fiat notional stuffed in ``fee``.

    Buy-crypto / app ``spend`` rows debit crypto (a disposal). Some exports park
    the GBP/USD notional in the ``fee`` column instead of a separate fiat leg.
    """
    if fee <= 0 or qty <= 0:
        return 0.0, fee
    if is_fiat(asset) or is_stablecoin(asset):
        return qty, 0.0
    # Fee qty in the same asset would be tiny vs the spend amount.
    if fee <= qty * 0.05:
        return 0.0, fee
    return fee, 0.0


def _fiat_currency_from_rows(fiat_rows: List[dict]) -> str | None:
    for row in fiat_rows:
        asset = normalize_asset(str(row["asset"]))
        if is_fiat(asset):
            return asset
    return None


def _fiat_spent(fiat_rows: List[dict]) -> float:
    return sum(abs(_float(r["amount"])) for r in fiat_rows if _float(r["amount"]) < 0)


def _fiat_received(fiat_rows: List[dict]) -> float:
    return sum(_float(r["amount"]) for r in fiat_rows if _float(r["amount"]) > 0)


def _crypto_trade_value(
    crypto_rows: List[dict], current_amount: float
) -> float:
    """Implied quote for crypto↔crypto trades (no fiat leg). Uses other leg qty."""
    others = [r for r in crypto_rows if _float(r["amount"]) * current_amount < 0]
    if not others:
        return 0.0
    # Use the counter-asset quantity as a notional placeholder when no fiat leg.
    return abs(_float(others[0]["amount"]))


def _quote_spent(quote_rows: List[dict]) -> float:
    return sum(abs(_float(r["amount"])) for r in quote_rows if _float(r["amount"]) < 0)


def _quote_received(quote_rows: List[dict]) -> float:
    return sum(_float(r["amount"]) for r in quote_rows if _float(r["amount"]) > 0)


def _parse_trade_group(rows: List[dict]) -> List[Transaction]:
    """Convert a group of Kraken ``trade`` rows (same refid) to transactions."""
    if not rows:
        return []

    timestamp = _parse_time(rows[0]["time"])
    refid = str(rows[0].get("refid", ""))
    quote_rows = [r for r in rows if is_quote_leg(str(r["asset"]))]
    crypto_rows = [r for r in rows if not is_quote_leg(str(r["asset"]))]
    fiat_currency = _fiat_currency_from_rows(quote_rows)
    stable_quote = next(
        (
            normalize_asset(str(r["asset"]))
            for r in quote_rows
            if is_stablecoin(normalize_asset(str(r["asset"])))
        ),
        None,
    )

    total_quote_spent = _quote_spent(quote_rows)
    total_quote_received = _quote_received(quote_rows)

    # Aggregate partial crypto legs (same refid) into one BUY/SELL per asset.
    buckets: dict[tuple[str, bool], dict] = {}
    for row in crypto_rows:
        asset = normalize_asset(str(row["asset"]))
        amount = _float(row["amount"])
        if amount == 0:
            continue
        is_buy = amount > 0
        key = (asset, is_buy)
        bucket = buckets.setdefault(
            key,
            {"qty": 0.0, "fee_asset": 0.0, "txids": []},
        )
        bucket["qty"] += abs(amount)
        bucket["fee_asset"] += _float(row.get("fee"))
        txid = str(row.get("txid") or "").strip()
        if txid:
            bucket["txids"].append(txid)

    transactions: List[Transaction] = []

    for (asset, is_buy), bucket in buckets.items():
        qty = bucket["qty"]
        if qty <= 0:
            continue

        tx_type = TransactionType.BUY if is_buy else TransactionType.SELL
        fiat_value = total_quote_spent if is_buy else total_quote_received
        if fiat_value <= 0:
            sign = 1.0 if is_buy else -1.0
            fiat_value = _crypto_trade_value(crypto_rows, sign * qty)

        counter_asset: str | None = None
        if not fiat_currency and stable_quote:
            counter_asset = stable_quote
            fiat_currency = stable_quote
        elif not fiat_currency:
            others = [
                r
                for r in crypto_rows
                if _float(r["amount"]) * (1 if is_buy else -1) < 0
            ]
            if others:
                counter_asset = normalize_asset(str(others[0]["asset"]))

        txids = bucket["txids"]
        tx_id = txids[0] if txids else f"kraken-{refid}-{asset}-{'buy' if is_buy else 'sell'}"

        transactions.append(
            Transaction(
                id=tx_id,
                timestamp=timestamp,
                asset=asset,
                transaction_type=tx_type,
                amount=qty,
                fiat_value_at_trigger=round(fiat_value, 2),
                fee_fiat=_fee_fiat(bucket["fee_asset"], qty, fiat_value),
                fiat_currency=fiat_currency or counter_asset,
                counter_asset=counter_asset,
                trade_group_id=refid or None,
                source="kraken",
            )
        )

    return transactions


def _parse_single_row(row: dict) -> Transaction | None:
    """Parse a non-trade Kraken ledger row."""
    tx_type_raw = str(row.get("type", "")).strip().lower()
    subtype = str(row.get("subtype", "")).strip().lower()
    asset = normalize_asset(str(row["asset"]))
    amount = _float(row["amount"])
    qty = abs(amount)
    fee = _float(row.get("fee"))

    if qty <= 0 and tx_type_raw not in {"fee"}:
        return None

    # Fiat deposits/withdrawals fund the account — not crypto tax events.
    if is_fiat(asset) and tx_type_raw in {"deposit", "withdrawal"}:
        return None

    timestamp = _parse_time(row["time"])
    txid = str(row.get("txid") or "").strip()
    refid = str(row.get("refid") or "").strip()
    # Deterministic fallback so re-imports of the same ledger row reuse one id
    # instead of a random uuid that would defeat dedup.
    tx_id = txid or (
        f"kraken-{refid}-{tx_type_raw}-{asset}-{qty}-{timestamp.isoformat()}"
    )
    fiat_currency: str | None = None
    transfer_direction: str | None = None

    if tx_type_raw == "deposit" or tx_type_raw == "receive":
        if is_fiat(asset):
            return None
        if is_stablecoin(asset):
            tx_type = TransactionType.BUY
            fiat_value = qty
            fiat_currency = asset
        else:
            # Crypto deposited from an external wallet — non-taxable transfer in.
            tx_type = TransactionType.TRANSFER
            fiat_value = 0.0
            transfer_direction = "IN"
    elif tx_type_raw == "withdrawal":
        if is_fiat(asset):
            return None
        # Crypto sent to an external wallet — non-taxable transfer out.
        tx_type = TransactionType.TRANSFER
        fiat_value = 0.0
        transfer_direction = "OUT"
    elif tx_type_raw in {"spend", "sale"}:
        if is_stablecoin(asset):
            tx_type = TransactionType.SELL
            fiat_value = qty
            fiat_currency = asset
        else:
            # Buy-crypto / app spend: crypto disposed to acquire another asset.
            tx_type = TransactionType.SELL
            fiat_value, fee = _spend_fiat_notional(asset, qty, fee)
            if fiat_value > 0 and not fiat_currency:
                fiat_currency = "GBP"
    elif tx_type_raw in {"transfer"}:
        tx_type = TransactionType.TRANSFER
        fiat_value = 0.0
    elif tx_type_raw in {"staking", "dividend", "earn"}:
        if subtype in {"allocation", "deallocation"}:
            tx_type = TransactionType.TRANSFER
            fiat_value = 0.0
        else:
            tx_type = TransactionType.STAKING
            fiat_value = qty if is_stablecoin(asset) else 0.0
            fiat_currency = asset if is_stablecoin(asset) else None
    elif tx_type_raw == "fee":
        tx_type = TransactionType.FEE
        fiat_value = 0.0
    else:
        # Skip margin, rollover, adjustment, etc. until explicitly supported.
        return None

    return Transaction(
        id=tx_id,
        timestamp=timestamp,
        asset=asset,
        transaction_type=tx_type,
        amount=qty,
        fiat_value_at_trigger=round(fiat_value, 2),
        fee_fiat=_fee_fiat(fee, qty, fiat_value) if fiat_value else round(fee, 4),
        fiat_currency=fiat_currency,
        source="kraken",
        transfer_direction=transfer_direction,
        trade_group_id=refid or None,
    )


def parse_kraken_ledger(df: pd.DataFrame) -> List[Transaction]:
    """Parse a Kraken Ledgers CSV dataframe into unified transactions."""
    # Normalise column names to lowercase for consistent access.
    normalised = df.copy()
    normalised.columns = [str(c).lower().strip() for c in normalised.columns]

    transactions: List[Transaction] = []
    trade_rows: Dict[str, List[dict]] = {}
    processed_trade_refids: Set[str] = set()

    for record in normalised.to_dict(orient="records"):
        tx_type = str(record.get("type", "")).strip().lower()
        refid = str(record.get("refid", "")).strip()

        if tx_type == "trade" and refid:
            trade_rows.setdefault(refid, []).append(record)
            continue

        parsed = _parse_single_row(record)
        if parsed is not None:
            transactions.append(parsed)

    for refid, group in trade_rows.items():
        processed_trade_refids.add(refid)
        transactions.extend(_parse_trade_group(group))

    transactions.sort(key=lambda t: (t.timestamp, t.id))
    return normalize_kraken_ledger(transactions)[0]


def normalize_movements(
    transactions: List[Transaction],
) -> tuple[List[Transaction], int]:
    """Reclassify Kraken wallet movements mistakenly stored as zero-value sells.

    Withdrawals to an external wallet have no fiat proceeds. Those must be
    ``TRANSFER`` events so cost basis carries over to the destination wallet.
    """
    changed = 0
    result: List[Transaction] = []

    for tx in transactions:
        updated = tx.model_copy(deep=True)
        if (
            updated.source == "kraken"
            and updated.transaction_type == TransactionType.SELL
            and updated.fiat_value_at_trigger <= 0
            and not is_stablecoin(updated.asset)
        ):
            updated.transaction_type = TransactionType.TRANSFER
            updated.transfer_direction = "OUT"
            changed += 1
        result.append(updated)

    return result, changed


# Kraken often debits a sliver of BTC to pay trading fees. When there is no BTC
# purchase history these appear as micro BTC/GBP "trades" with missing basis.
_KRAKEN_FEE_BTC_MAX = 0.0025
_KRAKEN_RECEIVE_SELL_WINDOW_SEC = 7 * 24 * 3600


def _reclassify_transfer_in_as_buy(
    tx: Transaction, *, fiat: float, fiat_currency: str | None
) -> Transaction:
    return tx.model_copy(
        update={
            "transaction_type": TransactionType.BUY,
            "transfer_direction": None,
            "fiat_value_at_trigger": round(max(0.0, fiat), 2),
            "fiat_currency": fiat_currency or tx.fiat_currency or "GBP",
        }
    )


def _pair_kraken_receive_before_sell(
    transactions: List[Transaction],
) -> tuple[List[Transaction], int]:
    """Kraken ``receive`` rows are TRANSFER IN; pair with the follow-up sell for basis."""
    from datetime import timedelta

    changed = 0
    result = [tx.model_copy(deep=True) for tx in transactions]
    by_id = {tx.id: tx for tx in result}

    sells = [
        tx
        for tx in result
        if tx.source == "kraken"
        and tx.transaction_type == TransactionType.SELL
        and not is_stablecoin(tx.asset)
    ]
    transfers = [
        tx
        for tx in result
        if tx.source == "kraken"
        and tx.transaction_type == TransactionType.TRANSFER
        and tx.transfer_direction == "IN"
        and not is_stablecoin(tx.asset)
    ]

    used_sell_ids: Set[str] = set()
    for xfer in transfers:
        if xfer.id not in by_id:
            continue
        candidates = [
            sell
            for sell in sells
            if sell.id not in used_sell_ids
            and sell.asset == xfer.asset
            and sell.timestamp >= xfer.timestamp
            and (sell.timestamp - xfer.timestamp)
            <= timedelta(seconds=_KRAKEN_RECEIVE_SELL_WINDOW_SEC)
            and _amounts_close(sell.amount, xfer.amount, rel_tol=0.005)
        ]
        if not candidates:
            continue
        sell = min(
            candidates,
            key=lambda t: (t.timestamp - xfer.timestamp).total_seconds(),
        )
        ratio = xfer.amount / sell.amount if sell.amount > 0 else 1.0
        fiat = sell.fiat_value_at_trigger * ratio
        by_id[xfer.id] = _reclassify_transfer_in_as_buy(
            xfer,
            fiat=fiat,
            fiat_currency=sell.fiat_currency,
        )
        used_sell_ids.add(sell.id)
        changed += 1

    return list(by_id.values()), changed


def _pair_kraken_spend_receive(
    transactions: List[Transaction],
) -> tuple[List[Transaction], int]:
    """Pair Buy-crypto ``spend`` disposals with same-refid ``receive`` acquisitions."""
    from collections import defaultdict

    by_ref: Dict[str, List[Transaction]] = defaultdict(list)
    for tx in transactions:
        if tx.source == "kraken" and tx.trade_group_id:
            by_ref[tx.trade_group_id].append(tx)

    changed = 0
    result = [tx.model_copy(deep=True) for tx in transactions]
    by_id = {tx.id: tx for tx in result}

    for group in by_ref.values():
        spends = [
            t
            for t in group
            if t.transaction_type == TransactionType.SELL
            and not is_stablecoin(t.asset)
            and not is_fiat(t.asset)
        ]
        receives = [
            t
            for t in group
            if t.transaction_type == TransactionType.TRANSFER
            and t.transfer_direction == "IN"
            and not is_stablecoin(t.asset)
            and not is_fiat(t.asset)
        ]
        if not spends or not receives:
            continue

        total_fiat = sum(s.fiat_value_at_trigger for s in spends)
        if total_fiat <= 0:
            continue
        total_received = sum(r.amount for r in receives)
        if total_received <= 0:
            continue

        quote_ccy = next(
            (s.fiat_currency for s in spends if s.fiat_currency),
            "GBP",
        )
        for recv in receives:
            if recv.id not in by_id:
                continue
            ratio = recv.amount / total_received
            fiat = total_fiat * ratio
            by_id[recv.id] = _reclassify_transfer_in_as_buy(
                recv,
                fiat=fiat,
                fiat_currency=quote_ccy,
            )
            changed += 1

        for spend in spends:
            if spend.id not in by_id:
                continue
            counter = receives[0]
            by_id[spend.id] = spend.model_copy(
                update={
                    "counter_asset": counter.asset,
                    "counter_amount": counter.amount,
                    "fiat_currency": quote_ccy,
                }
            )

    return list(by_id.values()), changed


# App ``spend`` rows park GBP/USD proceeds in the ``fee`` column. When that
# notional is mistaken for a trading fee it dwarfs the crypto amount (ratio ≈
# spot price). Real Kraken fee debits keep sub-unit ``fee_fiat``.
_KRAKEN_SPEND_NOTIONAL_MIN_RATIO = 1000.0


def _looks_like_kraken_spend_notional(tx: Transaction) -> bool:
    """True when ``fee_fiat`` is proceeds parked in the fee column, not a fee."""
    if tx.amount <= 0 or tx.fee_fiat < 1.0:
        return False
    return (tx.fee_fiat / tx.amount) >= _KRAKEN_SPEND_NOTIONAL_MIN_RATIO


def _reclassify_kraken_misparsed_spend_fees(
    transactions: List[Transaction],
) -> tuple[List[Transaction], int]:
    """Upgrade legacy Kraken app sells that were stored as FEE rows.

    Simple BTC→GBP sells export as ``spend`` with the GBP proceeds in the
    ``fee`` column. Older parser versions stored those as ``FEE`` with
    ``fee_fiat`` ≈ proceeds. Scoped to ``source == kraken`` only — exchange
    deposit fees on other venues (e.g. MEXC ``BUY`` rows) are never touched.
    """
    changed = 0
    result: List[Transaction] = []
    for tx in transactions:
        updated = tx.model_copy(deep=True)
        if (
            updated.source == "kraken"
            and updated.transaction_type == TransactionType.FEE
            and not is_quote_leg(updated.asset)
            and updated.trade_group_id
            and updated.fiat_value_at_trigger <= 0
            and _looks_like_kraken_spend_notional(updated)
        ):
            quote = updated.fiat_currency or "GBP"
            proceeds = round(updated.fee_fiat, 2)
            updated.transaction_type = TransactionType.SELL
            updated.fiat_value_at_trigger = proceeds
            updated.fee_fiat = 0.0
            updated.fiat_currency = quote
            updated.counter_asset = quote
            updated.counter_amount = proceeds
            changed += 1
        result.append(updated)
    return result, changed


def _reclassify_kraken_orphan_fee_sells(
    transactions: List[Transaction],
) -> tuple[List[Transaction], int]:
    """Micro BTC sells with no Kraken purchase history are trading-fee debits."""
    changed = 0
    result: List[Transaction] = []
    btc_acquired = 0.0

    for tx in sorted(transactions, key=lambda t: (t.timestamp, t.id)):
        updated = tx.model_copy(deep=True)
        if (
            updated.source == "kraken"
            and updated.asset == "BTC"
            and updated.transaction_type == TransactionType.BUY
        ):
            btc_acquired += updated.amount
        elif (
            updated.source == "kraken"
            and updated.asset == "BTC"
            and updated.transaction_type == TransactionType.SELL
            and updated.fiat_value_at_trigger <= 0
            and updated.amount <= _KRAKEN_FEE_BTC_MAX
            and updated.amount > btc_acquired + 1e-12
        ):
            updated.transaction_type = TransactionType.FEE
            updated.fee_fiat = round(updated.fiat_value_at_trigger, 2)
            updated.fiat_value_at_trigger = 0.0
            changed += 1
        elif (
            updated.source == "kraken"
            and updated.asset == "BTC"
            and updated.transaction_type == TransactionType.SELL
        ):
            btc_acquired = max(0.0, btc_acquired - updated.amount)

        result.append(updated)

    result.sort(key=lambda t: (t.timestamp, t.id))
    return result, changed


def normalize_kraken_ledger(
    transactions: List[Transaction],
) -> tuple[List[Transaction], int]:
    """Apply all Kraken-specific ledger normalisations."""
    txs, n_legacy = _reclassify_kraken_misparsed_spend_fees(transactions)
    txs, n_fee = _reclassify_kraken_orphan_fee_sells(txs)
    txs, n_move = normalize_movements(txs)
    txs, n_spend = _pair_kraken_spend_receive(txs)
    txs, n_pair = _pair_kraken_receive_before_sell(txs)
    return txs, n_legacy + n_fee + n_move + n_spend + n_pair


def collapse_stablecoin_quote_legs(
    transactions: List[Transaction],
) -> tuple[List[Transaction], int]:
    """Link crypto trades to stablecoin quote legs and drop redundant stable rows."""
    from collections import defaultdict

    def _key(tx: Transaction) -> str:
        return f"{tx.id}|{tx.asset}|{tx.transaction_type}|{tx.amount}"

    groups: Dict[tuple, List[Transaction]] = defaultdict(list)
    for tx in transactions:
        ts = tx.timestamp.replace(microsecond=0)
        groups[(ts, tx.source or "")].append(tx)

    drop_keys: Set[str] = set()
    updated = {_key(tx): tx.model_copy(deep=True) for tx in transactions}

    for group in groups.values():
        crypto_sells = [
            t
            for t in group
            if t.transaction_type == TransactionType.SELL and not is_stablecoin(t.asset)
        ]
        crypto_buys = [
            t
            for t in group
            if t.transaction_type == TransactionType.BUY and not is_stablecoin(t.asset)
        ]
        stable_buys = [
            t
            for t in group
            if t.transaction_type == TransactionType.BUY and is_stablecoin(t.asset)
        ]
        stable_sells = [
            t
            for t in group
            if t.transaction_type == TransactionType.SELL and is_stablecoin(t.asset)
        ]

        for sell in crypto_sells:
            for stable in stable_buys:
                if _amounts_close(stable.amount, sell.fiat_value_at_trigger):
                    tx = updated[_key(sell)]
                    tx.counter_asset = stable.asset
                    tx.fiat_currency = stable.asset
                    drop_keys.add(_key(stable))
                    break

        for buy in crypto_buys:
            for stable in stable_sells:
                if _amounts_close(stable.amount, buy.fiat_value_at_trigger):
                    tx = updated[_key(buy)]
                    tx.counter_asset = stable.asset
                    tx.fiat_currency = stable.asset
                    drop_keys.add(_key(stable))
                    break

    result = [tx for key, tx in updated.items() if key not in drop_keys]
    result.sort(key=lambda t: (t.timestamp, t.id))
    return result, len(drop_keys)


def _amounts_close(a: float, b: float, rel_tol: float = 0.02) -> bool:
    if a == b:
        return True
    scale = max(abs(a), abs(b), 1e-9)
    return abs(a - b) / scale <= rel_tol
