"""Transaction ingestion engine.

Parses transaction histories from CSV or JSON into the unified
:class:`~app.schemas.Transaction` schema. The parser is tolerant of common
column-name variants used by popular exchanges/wallets and uses pandas for
robust CSV handling.
"""

from __future__ import annotations

import io
import json
import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import pandas as pd

from .evm_csv import is_evm_wallet_csv, parse_evm_wallet_csv
from .solana_wallet import is_solana_wallet, parse_solana_wallet
from .cryptocom import is_cryptocom_export, parse_cryptocom_export
from .cosmos_wallet import is_cosmos_wallet, parse_cosmos_wallet
from .exchange_ledger import is_exchange_ledger, parse_exchange_ledger
from .kraken import clean_columns, is_kraken_ledger, parse_kraken_ledger
from .woox import is_woox_export, parse_woox_export
from .variational import is_variational_export, parse_variational_export
from .export_coverage import infer_export_coverage
from .schemas import Transaction, TransactionType

# Maps a normalized header to the set of accepted source column names.
COLUMN_ALIASES: Dict[str, List[str]] = {
    "id": ["id", "txid", "tx_id", "transaction_id", "reference"],
    "timestamp": ["timestamp", "time", "date", "datetime", "executed_at"],
    "asset": ["asset", "symbol", "currency", "coin", "token"],
    "transaction_type": [
        "transaction_type",
        "type",
        "side",
        "action",
        "operation",
    ],
    "amount": ["amount", "quantity", "qty", "size", "units"],
    "fiat_value_at_trigger": [
        "fiat_value_at_trigger",
        "fiat_value",
        "usd_value",
        "value_usd",
        "total",
        "subtotal",
    ],
    "fee_fiat": ["fee_fiat", "fee", "fees", "fee_usd", "commission"],
    "source": ["source", "exchange", "wallet", "ledger", "platform"],
}

# Normalizes free-form type strings to canonical TransactionType values.
PARSER_LABELS: Dict[str, str] = {
    "kraken": "Kraken",
    "exchange_ledger": "Binance / Crypto.com",
    "cosmos_wallet": "Celestia",
    "cryptocom": "Crypto.com",
    "woox": "WOO X",
    "variational": "Variational",
    "solana_wallet": "Solana",
    "evm_wallet_csv": "EVM wallet",
    "generic": "Generic CSV",
    "json": "JSON",
}

SOURCE_DISPLAY_LABELS: Dict[str, str] = {
    **PARSER_LABELS,
    "binance": "Binance",
    "hyperliquid": "Hyperliquid",
    "ethereum": "Ethereum",
    "arbitrum": "Arbitrum",
    "base": "Base",
    "polygon": "Polygon",
    "optimism": "Optimism",
    "avalanche": "Avalanche",
    "bsc": "BNB Chain",
    "bitcoin": "Bitcoin",
    "cardano": "Cardano",
    "solana": "Solana",
    "celestia": "Celestia",
}

TYPE_ALIASES: Dict[str, TransactionType] = {
    "buy": TransactionType.BUY,
    "purchase": TransactionType.BUY,
    "deposit": TransactionType.BUY,
    "sell": TransactionType.SELL,
    "sale": TransactionType.SELL,
    "withdrawal": TransactionType.SELL,
    "withdraw": TransactionType.SELL,
    "airdrop": TransactionType.AIRDROP,
    "reward": TransactionType.STAKING,
    "staking": TransactionType.STAKING,
    "stake": TransactionType.STAKING,
    "interest": TransactionType.STAKING,
    "fee": TransactionType.FEE,
    "transfer": TransactionType.TRANSFER,
}


def _build_reverse_alias_map() -> Dict[str, str]:
    reverse: Dict[str, str] = {}
    for canonical, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            reverse[alias.lower().strip()] = canonical
    return reverse


_REVERSE_ALIASES = _build_reverse_alias_map()


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    rename: Dict[str, str] = {}
    for col in df.columns:
        key = str(col).lower().strip().replace(" ", "_")
        if key in _REVERSE_ALIASES:
            rename[col] = _REVERSE_ALIASES[key]
    return df.rename(columns=rename)


def _coerce_type(raw: object) -> TransactionType:
    text = str(raw).strip().lower()
    if text in TYPE_ALIASES:
        return TYPE_ALIASES[text]
    # Fall back to a direct enum match (e.g. already-canonical values).
    try:
        return TransactionType(text.upper())
    except ValueError as exc:  # pragma: no cover - defensive
        raise ValueError(f"Unknown transaction type: {raw!r}") from exc


def _coerce_timestamp(raw: object) -> datetime:
    ts = pd.to_datetime(raw, utc=True, errors="coerce")
    if pd.isna(ts):
        raise ValueError(f"Unparseable timestamp: {raw!r}")
    return ts.to_pydatetime()


def _row_to_transaction(row: Dict[str, object]) -> Transaction:
    tx_id = row.get("id")
    if tx_id is None or str(tx_id).strip() == "" or str(tx_id) == "nan":
        # Deterministic id from row content so re-importing the same CSV does
        # not create new uuids (which would defeat dedup).
        fingerprint = "|".join(
            str(row.get(field, ""))
            for field in ("timestamp", "asset", "transaction_type", "amount", "fiat_value_at_trigger", "source")
        )
        tx_id = uuid.uuid5(uuid.NAMESPACE_OID, fingerprint).hex

    return Transaction(
        id=str(tx_id),
        timestamp=_coerce_timestamp(row.get("timestamp")),
        asset=str(row.get("asset", "")).strip(),
        transaction_type=_coerce_type(row.get("transaction_type")),
        amount=float(row.get("amount", 0) or 0),
        fiat_value_at_trigger=max(0.0, float(row.get("fiat_value_at_trigger", 0) or 0)),
        fee_fiat=float(row.get("fee_fiat", 0) or 0),
        source=(
            str(row.get("source")).strip()
            if row.get("source") not in (None, "", "nan")
            else None
        ),
    )


def _read_csv(content: str | bytes) -> pd.DataFrame:
    """Read CSV bytes/text, stripping UTF-8 BOM when present."""
    if isinstance(content, bytes):
        return pd.read_csv(io.BytesIO(content), encoding="utf-8-sig")
    return pd.read_csv(io.StringIO(content), encoding="utf-8-sig")


def _cell_preview(value: object, max_len: int = 48) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    text = str(value).strip()
    if len(text) > max_len:
        return f"{text[: max_len - 1]}…"
    return text


def csv_text_snippet(
    content: bytes,
    *,
    max_rows: int = 3,
    max_cols: int = 8,
    max_cell_len: int = 48,
) -> Optional[Dict[str, object]]:
    """Column headers and sample rows for a lightweight CSV preview."""
    try:
        df = clean_columns(_read_csv(content))
    except Exception:
        return None

    if df.empty:
        columns = [str(column) for column in df.columns[:max_cols]]
        return {
            "columns": columns,
            "rows": [],
            "total_rows": 0,
            "total_columns": len(df.columns),
            "truncated_columns": len(df.columns) > max_cols,
        }

    visible = df.iloc[:max_rows, :max_cols]
    columns = [str(column) for column in visible.columns]
    rows = [
        [_cell_preview(value, max_cell_len) for value in record.values()]
        for record in visible.to_dict(orient="records")
    ]
    return {
        "columns": columns,
        "rows": rows,
        "total_rows": len(df),
        "total_columns": len(df.columns),
        "truncated_columns": len(df.columns) > max_cols,
    }


LEDGER_SNIPPET_COLUMNS = (
    "timestamp",
    "asset",
    "transaction_type",
    "amount",
    "fiat_value_at_trigger",
    "source",
)


def ledger_snippet_from_transactions(
    transactions: List[Transaction],
    *,
    max_rows: int = 3,
) -> Dict[str, object]:
    """Sample parsed ledger rows for preview UI."""
    ordered = sorted(transactions, key=lambda tx: tx.timestamp)
    sample = ordered[:max_rows]
    columns = list(LEDGER_SNIPPET_COLUMNS)
    rows = [
        [
            _cell_preview(tx.timestamp.isoformat(), 40)
            if column == "timestamp"
            else _cell_preview(getattr(tx, column, ""), 40)
            for column in columns
        ]
        for tx in sample
    ]
    return {
        "columns": columns,
        "rows": rows,
        "total_rows": len(ordered),
        "total_columns": len(columns),
        "truncated_columns": False,
    }


def ledger_snippet_for_import(
    transactions: List[Transaction],
    import_id: str,
    *,
    max_rows: int = 3,
) -> Dict[str, object]:
    """Sample parsed ledger rows when the original CSV was not stored."""
    rows_for_import = [tx for tx in transactions if tx.import_id == import_id]
    return ledger_snippet_from_transactions(rows_for_import, max_rows=max_rows)


def detect_csv_parser(df: pd.DataFrame, filename: str = "") -> str:
    """Return the parser id that :func:`parse_csv` would use for this dataframe."""
    df = clean_columns(df)
    if is_kraken_ledger(df):
        return "kraken"
    if is_exchange_ledger(df):
        return "exchange_ledger"
    if is_cosmos_wallet(df):
        return "cosmos_wallet"
    if is_cryptocom_export(df):
        return "cryptocom"
    if is_woox_export(df):
        return "woox"
    if is_variational_export(df):
        return "variational"
    if is_solana_wallet(df):
        return "solana_wallet"
    if is_evm_wallet_csv(df):
        return "evm_wallet_csv"
    return "generic"


def detect_upload_parser(filename: str, content: bytes) -> str:
    """Return the parser id that :func:`parse_upload` would use."""
    lower = filename.lower()
    if lower.endswith(".json"):
        return "json"
    if lower.endswith(".csv") or lower.endswith(".txt"):
        return detect_csv_parser(_read_csv(content), filename=lower)
    try:
        json.loads(content)
        return "json"
    except (json.JSONDecodeError, ValueError):
        return detect_csv_parser(_read_csv(content), filename=lower)


def parser_label(parser_id: str) -> str:
    return PARSER_LABELS.get(parser_id, parser_id.replace("_", " ").title())


def source_display_label(source: Optional[str]) -> Optional[str]:
    if not source:
        return None
    key = source.strip().lower()
    return SOURCE_DISPLAY_LABELS.get(key, key.replace("_", " ").title())


def primary_source_label(transactions: List[Transaction]) -> Optional[str]:
    """Most common ``source`` slug on parsed transactions, as a display label."""
    counts = Counter(t.source for t in transactions if t.source)
    if not counts:
        return None
    slug = counts.most_common(1)[0][0]
    return source_display_label(slug)


def transaction_date_range(
    transactions: List[Transaction],
) -> Tuple[Optional[datetime], Optional[datetime]]:
    if not transactions:
        return None, None
    timestamps = [t.timestamp for t in transactions]
    return min(timestamps), max(timestamps)


def preview_upload(filename: str, content: bytes) -> Dict[str, object]:
    """Detect format and summarize a file without registering an import."""
    parser_id = detect_upload_parser(filename, content)
    label = parser_label(parser_id)
    try:
        transactions = parse_upload(filename, content)
    except Exception as exc:
        return {
            "parser": parser_id,
            "parser_label": label,
            "transaction_count": 0,
            "error": str(exc),
        }

    source_label = primary_source_label(transactions)
    if source_label:
        label = source_label

    coverage = infer_export_coverage(filename, content, transactions)
    if coverage:
        return {
            "parser": parser_id,
            "parser_label": label,
            "transaction_count": len(transactions),
            "coverage_start": coverage.coverage_start.isoformat(),
            "coverage_end": coverage.coverage_end.isoformat(),
            "data_start": coverage.data_start.isoformat(),
            "data_end": coverage.data_end.isoformat(),
            "coverage_from": coverage.coverage_from,
            "date_start": coverage.coverage_start.isoformat(),
            "date_end": coverage.coverage_end.isoformat(),
        }

    date_start, date_end = transaction_date_range(transactions)
    return {
        "parser": parser_id,
        "parser_label": label,
        "transaction_count": len(transactions),
        "date_start": date_start.isoformat() if date_start else None,
        "date_end": date_end.isoformat() if date_end else None,
    }


def parse_csv(content: str | bytes, filename: str = "") -> List[Transaction]:
    """Parse a CSV transaction history into unified transactions."""
    df = clean_columns(_read_csv(content))
    if is_kraken_ledger(df):
        return parse_kraken_ledger(df)
    if is_exchange_ledger(df):
        return parse_exchange_ledger(df, filename=filename)
    if is_cosmos_wallet(df):
        return parse_cosmos_wallet(df)
    if is_cryptocom_export(df):
        return parse_cryptocom_export(df)
    if is_woox_export(df):
        return parse_woox_export(df)
    if is_variational_export(df):
        return parse_variational_export(df)
    if is_solana_wallet(df):
        return parse_solana_wallet(df)
    if is_evm_wallet_csv(df):
        return parse_evm_wallet_csv(df, filename=filename)

    # Generic schema — columns already cleaned above.
    normalized = _normalize_columns(df)
    transactions: List[Transaction] = []
    for record in normalized.to_dict(orient="records"):
        try:
            transactions.append(_row_to_transaction(record))
        except ValueError as exc:
            # Safety net: Kraken ledgers that slipped past detection.
            if "trade" in str(exc).lower() and is_kraken_ledger(df):
                return parse_kraken_ledger(df)
            raise
    return transactions


def parse_json(content: str | bytes) -> List[Transaction]:
    """Parse a JSON array of transaction objects into unified transactions."""
    data = json.loads(content)
    if isinstance(data, dict):
        # Allow a wrapped payload like {"transactions": [...]}.
        data = data.get("transactions", [])
    if not isinstance(data, list):
        raise ValueError("JSON payload must be a list of transaction objects")

    df = pd.DataFrame(data)
    if df.empty:
        return []
    df = _normalize_columns(df)
    transactions: List[Transaction] = []
    for record in df.to_dict(orient="records"):
        transactions.append(_row_to_transaction(record))
    return transactions


def parse_upload(filename: str, content: bytes) -> List[Transaction]:
    """Dispatch parsing based on file extension."""
    lower = filename.lower()
    if lower.endswith(".json"):
        return parse_json(content)
    if lower.endswith(".csv") or lower.endswith(".txt"):
        return parse_csv(content, filename=lower)
    # Best-effort: try JSON then CSV.
    try:
        return parse_json(content)
    except (json.JSONDecodeError, ValueError):
        return parse_csv(content, filename=lower)
