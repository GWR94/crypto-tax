"""Parse pasted MEXC notification emails into ledger transactions."""

from __future__ import annotations

import csv
import io
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import List, Optional, Tuple

from .kraken import normalize_asset
from .schemas import Transaction, TransactionType

SOURCE = "mexc"

_BLOCK_START = re.compile(
    r"(?=(?:Payment ID:|Withdrawal Amount:|Your\s+[A-Z0-9]+_USDT\s+futures))",
    re.IGNORECASE,
)

_DEPOSIT_PAYMENT_ID = re.compile(r"Payment ID:\s*(\S+)", re.IGNORECASE)
_DEPOSIT_FIAT = re.compile(
    r"Deposit Fiat Amount:\s*([\d,.]+)\s*([A-Za-z]{3,10})",
    re.IGNORECASE,
)
_DEPOSIT_CRYPTO = re.compile(
    r"Received Crypto:\s*([\d,.]+)\s*([A-Za-z0-9]+)",
    re.IGNORECASE,
)
_DEPOSIT_DATE = re.compile(r"Deposit Date:\s*(.+?)(?:\n|$)", re.IGNORECASE)
_DEPOSIT_FEE = re.compile(
    r"Processing Fee:\s*([\d,.]+)\s*([A-Za-z]{3,10})?",
    re.IGNORECASE,
)

_WITHDRAWAL_AMOUNT = re.compile(
    r"Withdrawal Amount:\s*\n?\s*([\d,.]+)\s+([A-Za-z0-9]+)",
    re.IGNORECASE,
)
_WITHDRAWAL_TIME = re.compile(r"Withdrawal Time:\s*\n?\s*([^\n]+)", re.IGNORECASE)
_WITHDRAWAL_ADDRESS = re.compile(
    r"Withdrawal Address:\s*\n?\s*(\S+)",
    re.IGNORECASE,
)
_TX_ID = re.compile(r"TxID:\s*\n?\s*(\S+)", re.IGNORECASE)

# Optional words between "futures" and order type, e.g. "futures position SL order".
_FUTURES_FILL = re.compile(
    r"Your\s+([A-Z0-9]+)_USDT\s+futures(?:\s+\w+)*\s+(\w+)\s+order.*?filled completely at\s+"
    r"([^\n.]+)\.\s*Number of cont\.\s*filled:\s*([\d,.]+)\.\s*"
    r"Average filled price:\s*([\d,.]+)",
    re.IGNORECASE | re.DOTALL,
)

_INVISIBLE_RE = re.compile(r"[\u200e\u200f\u200b\u200c\u200d\ufeff\u2060]")


@dataclass
class MexcEmailParseResult:
    transactions: List[Transaction] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    skipped_blocks: List[str] = field(default_factory=list)


def _float(raw: str) -> float:
    text = str(raw).replace(",", "").strip().rstrip(".")
    return float(text)


def _strip_invisible(text: str) -> str:
    return _INVISIBLE_RE.sub("", text).strip()


def _parse_futures_timestamp(raw: str) -> datetime:
    cleaned = _strip_invisible(raw)
    return datetime.strptime(cleaned, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


def _parse_deposit_date(raw: str) -> datetime:
    text = _strip_invisible(raw)
    try:
        return parsedate_to_datetime(text).astimezone(timezone.utc)
    except (TypeError, ValueError, IndexError):
        pass
    ts = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def _parse_withdrawal_time(raw: str) -> datetime:
    text = raw.strip()
    match = re.match(
        r"(\d{4}/\d{2}/\d{2})\s+(\d{2}:\d{2}:\d{2})\(UTC\+8\)",
        text,
    )
    if match:
        local = datetime.strptime(
            f"{match.group(1)} {match.group(2)}",
            "%Y/%m/%d %H:%M:%S",
        )
        return (local - timedelta(hours=8)).replace(tzinfo=timezone.utc)
    return _parse_deposit_date(text)


def _fee_in_fiat(
    fee_amount: float,
    fee_currency: str,
    *,
    fiat_amount: float,
    fiat_currency: str,
    crypto_amount: float,
    crypto_asset: str,
) -> Tuple[float, List[str]]:
    if fee_amount <= 0:
        return 0.0, []
    fee_ccy = (fee_currency or fiat_currency).strip().upper()
    fiat_ccy = fiat_currency.strip().upper()
    asset = crypto_asset.strip().upper()
    if fee_ccy == fiat_ccy or not fee_ccy:
        return round(fee_amount, 8), []
    if fee_ccy in {asset, "USDT", "USDC", "USD"} and crypto_amount > 0 and fiat_amount > 0:
        converted = fee_amount * (fiat_amount / crypto_amount)
        return (
            round(converted, 8),
            [
                f"Processing fee {fee_amount} {fee_ccy} imputed as "
                f"{converted:.2f} {fiat_ccy} using the deposit exchange rate."
            ],
        )
    return (
        round(fee_amount, 8),
        [f"Processing fee listed as {fee_amount} {fee_ccy} — verify fee_fiat."],
    )


def split_email_blocks(text: str) -> List[str]:
    normalized = text.replace("\r\n", "\n").strip()
    if not normalized:
        return []
    parts = _BLOCK_START.split(normalized)
    return [part.strip() for part in parts if part.strip()]


def _parse_deposit_block(block: str, warnings: List[str]) -> Optional[Transaction]:
    payment = _DEPOSIT_PAYMENT_ID.search(block)
    fiat = _DEPOSIT_FIAT.search(block)
    crypto = _DEPOSIT_CRYPTO.search(block)
    date = _DEPOSIT_DATE.search(block)
    if not (payment and fiat and crypto and date):
        return None

    payment_id = payment.group(1).strip()
    fiat_amount = _float(fiat.group(1))
    fiat_currency = fiat.group(2).strip().upper()
    crypto_amount = _float(crypto.group(1))
    crypto_asset = normalize_asset(crypto.group(2))
    timestamp = _parse_deposit_date(date.group(1))

    fee_amount = 0.0
    fee_currency = fiat_currency
    fee_match = _DEPOSIT_FEE.search(block)
    if fee_match:
        fee_amount = _float(fee_match.group(1))
        fee_currency = (fee_match.group(2) or fiat_currency).strip().upper()

    fee_fiat, fee_notes = _fee_in_fiat(
        fee_amount,
        fee_currency,
        fiat_amount=fiat_amount,
        fiat_currency=fiat_currency,
        crypto_amount=crypto_amount,
        crypto_asset=crypto_asset,
    )
    warnings.extend(fee_notes)

    return Transaction(
        id=f"mexc-deposit-{payment_id}",
        timestamp=timestamp,
        asset=crypto_asset,
        transaction_type=TransactionType.BUY,
        amount=crypto_amount,
        fiat_value_at_trigger=round(fiat_amount, 2),
        fee_fiat=fee_fiat,
        fiat_currency=fiat_currency,
        counter_asset=fiat_currency,
        counter_amount=fiat_amount,
        source=SOURCE,
        trade_group_id=payment_id,
        venue_order_type="fiat_deposit",
    )


def _parse_withdrawal_block(block: str, warnings: List[str]) -> Optional[Transaction]:
    amount_match = _WITHDRAWAL_AMOUNT.search(block)
    time_match = _WITHDRAWAL_TIME.search(block)
    if not (amount_match and time_match):
        return None

    qty = _float(amount_match.group(1))
    asset = normalize_asset(amount_match.group(2))
    timestamp = _parse_withdrawal_time(time_match.group(1))
    address = (
        _WITHDRAWAL_ADDRESS.search(block).group(1).strip()
        if _WITHDRAWAL_ADDRESS.search(block)
        else None
    )
    txid = _TX_ID.search(block).group(1).strip() if _TX_ID.search(block) else None
    tx_id = f"mexc-withdraw-{txid}" if txid else f"mexc-withdraw-{uuid.uuid5(uuid.NAMESPACE_OID, block).hex}"

    if not txid:
        warnings.append(
            f"Withdrawal of {qty} {asset} missing TxID — id generated from email text."
        )

    return Transaction(
        id=tx_id,
        timestamp=timestamp,
        asset=asset,
        transaction_type=TransactionType.TRANSFER,
        amount=qty,
        fiat_value_at_trigger=0.0,
        fee_fiat=0.0,
        source=SOURCE,
        transfer_direction="OUT",
        counterparty_address=address,
        on_chain_tx_id=txid,
        trade_group_id=txid,
        venue_order_type="withdrawal",
    )


def _futures_skip_warning(block: str) -> Optional[str]:
    """SL/TP emails are exit-only — no entry, PnL, or funding; not importable."""
    match = _FUTURES_FILL.search(block)
    if not match:
        return None
    base = normalize_asset(match.group(1))
    order_type = match.group(2).strip().upper()
    timestamp = _parse_futures_timestamp(match.group(3))
    contracts = _float(match.group(4))
    avg_price = _float(match.group(5))
    return (
        f"Skipped {base} futures {order_type} fill ({timestamp.date()}, "
        f"{contracts:g} contracts @ {avg_price:g}) — email is exit-only: no entry "
        f"price, realized PnL, or funding. Cannot reconstruct perp tax from SL "
        f"notifications alone."
    )


def parse_mexc_email_block(block: str) -> Tuple[List[Transaction], List[str]]:
    warnings: List[str] = []
    futures_note = _futures_skip_warning(block)
    if futures_note:
        return [], [futures_note]
    for parser in (_parse_deposit_block, _parse_withdrawal_block):
        tx = parser(block, warnings)
        if tx is not None:
            return [tx], warnings
    return [], warnings


def parse_mexc_emails(text: str) -> MexcEmailParseResult:
    """Parse one or more pasted MEXC emails into transactions."""
    result = MexcEmailParseResult()
    blocks = split_email_blocks(text)
    if not blocks:
        result.skipped_blocks.append("(empty paste)")
        return result

    for block in blocks:
        txs, block_warnings = parse_mexc_email_block(block)
        result.warnings.extend(block_warnings)
        if txs:
            result.transactions.extend(txs)
        elif not block_warnings:
            preview = block[:120].replace("\n", " ")
            result.skipped_blocks.append(preview + ("…" if len(block) > 120 else ""))

    return result


def transactions_to_csv(transactions: List[Transaction]) -> str:
    """Serialize parsed rows for download or manual review."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "id",
            "timestamp",
            "asset",
            "transaction_type",
            "amount",
            "fiat_value_at_trigger",
            "fee_fiat",
            "fiat_currency",
            "source",
            "transfer_direction",
            "counterparty_address",
            "on_chain_tx_id",
            "instrument_kind",
            "instrument",
            "venue_order_type",
            "trade_group_id",
        ]
    )
    for tx in sorted(transactions, key=lambda row: (row.timestamp, row.id)):
        writer.writerow(
            [
                tx.id,
                tx.timestamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                tx.asset,
                tx.transaction_type.value,
                tx.amount,
                tx.fiat_value_at_trigger,
                tx.fee_fiat,
                tx.fiat_currency or "",
                tx.source or "",
                tx.transfer_direction or "",
                tx.counterparty_address or "",
                tx.on_chain_tx_id or "",
                tx.instrument_kind or "",
                tx.instrument or "",
                tx.venue_order_type or "",
                tx.trade_group_id or "",
            ]
        )
    return buffer.getvalue()
