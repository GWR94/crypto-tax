"""FastAPI application exposing the crypto tax dashboard REST API."""

from __future__ import annotations

from pathlib import Path


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    root = Path(__file__).resolve().parents[3]
    load_dotenv(root / ".env")


_load_dotenv()

import csv
import io
import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from threading import Lock
from typing import Dict, List, Literal, Optional

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse

from . import ingestion
from .asset_labels import build_asset_labels
from .config import (
    REPORTING_CURRENCY,
    SUPPORTED_DISPLAY_CURRENCIES,
    SUPPORTED_TAX_JURISDICTIONS,
    TAX_JURISDICTION,
    is_stablecoin,
    reporting_currency_for,
)
from .cryptocom import normalize_cryptocom_exchange_legs
from .display import build_portfolio_summary
from .fx import fx, us_calendar_year
from .hmrc_cgt_engine import calculate_uk_cgt, calculate_uk_income, compute_uk_open_pools
from .uk_tax_year import available_tax_year_labels, uk_tax_year_range
from .ledger_normalize import normalize_tax_ledger
from .income_classification import reclassify_income_events
from .on_chain_links import backfill_on_chain_tx_ids
from .exchange_ledger import collapse_exchange_timezone_duplicates
from .kraken import (
    collapse_stablecoin_quote_legs,
    normalize_exchange_asset_aliases,
    normalize_kraken_ledger,
    normalize_movements,
)
from .ledger_filters import (
    collapse_staking_echo_transfers,
    filter_exclude_staking,
    strip_dust_transactions,
)
from .kamino_vault import normalize_kamino_vault
from .drift import normalize_drift_collateral
from .solana_lending import normalize_lending_protocols
from .mexc_email import parse_mexc_emails, transactions_to_csv
from .liquid_staking import normalize_liquid_staking
from .perps import build_perps_summary
from .perp_tax import (
    build_perp_tax_summary,
    merge_perp_into_realized_pnl_by_asset,
    merge_perp_into_uk_cgt,
    merge_perp_into_us_realized,
)
from .wallet_detect import CHAIN_LABELS
from .price_resolver import merge_price_maps, resolve_prices
from .schemas import (
    AccountingMethod,
    HoldingRow,
    ImportFilePreview,
    ImportPreviewResponse,
    ImportSourceView,
    CoverageGapView,
    DataHealthSummary,
    ImportOverlapView,
    ImportSnippetView,
    LabelImportRequest,
    ManualCostBasisOverride,
    ManualCostBasisOverrideCreate,
    PerpTaxSummary,
    PnlBreakdown,
    PortfolioSummary,
    PriceUpdate,
    RealizedGainsSummary,
    Transaction,
    TransactionCreate,
    ScamAssetRequest,
    TaxSettingsUpdate,
    spot_transactions,
    TransferMatchResult,
    UkCgtSummary,
    UkIncomeSummary,
    WalletImportRequest,
    MexcEmailImportRequest,
    MexcEmailImportResponse,
)
from .pnl_breakdown import build_pnl_breakdown
from .btc_fetch import btc_wallet_import_enabled, fetch_wallet_transactions as fetch_btc_wallet
from .celestia_fetch import (
    celestia_wallet_import_enabled,
    fetch_wallet_transactions as fetch_celestia_wallet,
)
from .evm_chains import EVM_AUTO_IMPORT_CHAINS, EVM_AUTO_IMPORT_LABEL, EVM_CHAIN_META
from .cardano_fetch import (
    blockfrost_api_key,
    cardano_wallet_import_enabled,
    fetch_wallet_transactions as fetch_cardano_wallet,
)
from .hyperliquid_fetch import (
    fetch_wallet_transactions as fetch_hyperliquid_wallet,
    hyperliquid_import_enabled,
)
from .evm_fetch import (
    CHAIN_CONFIG as EVM_CHAIN_CONFIG,
    etherscan_api_key,
    fetch_wallet_transactions as fetch_evm_wallet,
    fetch_wallet_transactions_multi as fetch_evm_wallet_multi,
)
from .solana_fetch import (
    fetch_wallet_transactions as fetch_solana_wallet,
    helius_api_key,
    solana_wallet_import_enabled,
    solana_wallet_provider,
    solscan_api_key,
)
from .solana_tokens import get_registry
from .solana_wallet import (
    collapse_solana_swap_duplicate_legs,
    normalize_solana_assets,
    reclassify_disguised_solana_swaps,
    repair_mismatched_solana_trade_groups,
)
from .token_spam import strip_spam_transactions
from .transaction_dedup import dedupe_transactions, dedup_keys_for_transaction, transaction_fingerprint
from .transfer_matching import annotate_transfer_pairs
from .wallet_enrichment import backfill_wallet_cost_basis, enrich_imported_fiat_values
from .csv_export_kind import infer_csv_export_kind
from .coverage_gaps import find_coverage_gaps, find_preview_ledger_gaps
from .cost_basis_overrides import build_override_from_request, prepare_tax_ledger
from .data_health import build_data_health_summary
from .import_overlaps import (
    count_ledger_duplicates,
    find_import_overlaps,
    format_fully_duplicate_rejection,
    ledger_dedup_keys,
    partition_novel_transactions,
)
from .export_coverage import infer_export_coverage, transaction_date_range
from .import_registry import registry
from .import_file_storage import read_import_file, save_import_file
from .scam_assets import registry as scam_registry
from .ledger_view import (
    active_cost_basis_overrides,
    active_transactions,
    is_demo_mode,
)
from .sample_data import (
    SAMPLE_TRANSACTION_IDS,
    default_transactions,
    demo_transaction_count,
    without_sample,
)
from .state import state
from .tax_engine import (
    _run_engine,
    build_tax_harvest_matrix,
    calculate_positions,
    calculate_realized_gains,
    calculate_realized_pnl_by_asset,
    match_internal_transfers,
)

app = FastAPI(
    title="Crypto Tax Dashboard API",
    version="1.0.0",
    description=(
        "Local, self-hosted crypto portfolio PnL and capital-gains tax engine "
        "with deterministic FIFO/LIFO/HIFO accounting."
    ),
)

# Allow the local Vite dev server (and any local origin) to call the API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

_DEMO_MODE_WRITE_ALLOWLIST = frozenset({"/api/settings"})


@app.middleware("http")
async def guard_demo_mode_writes(request: Request, call_next):
    """Block ledger mutations while viewing bundled demo data."""
    if (
        request.method not in ("GET", "HEAD", "OPTIONS")
        and is_demo_mode()
        and request.url.path.startswith("/api/")
        and request.url.path not in _DEMO_MODE_WRITE_ALLOWLIST
    ):
        return JSONResponse(
            status_code=403,
            content={
                "detail": "Switch to Live data mode to modify your ledger.",
            },
        )
    return await call_next(request)


# --- Health ----------------------------------------------------------------


WALLET_IMPORT_CHAINS = frozenset(
    {"solana", "bitcoin", "cardano", "celestia", "hyperliquid", *EVM_CHAIN_META.keys()}
)


@app.get("/api/health")
def health() -> Dict[str, object]:
    has_etherscan = bool(etherscan_api_key())
    return {
        "status": "ok",
        "service": "crypto-tax-dashboard",
        "parsers": ["generic", "kraken", "exchange_ledger", "cosmos_wallet", "cryptocom", "solana_wallet"],
        "reporting_currency": reporting_currency_for(state.tax_jurisdiction()),
        "display_currencies": sorted(SUPPORTED_DISPLAY_CURRENCIES),
        "tax_jurisdiction": state.tax_jurisdiction(),
        "supported_tax_jurisdictions": sorted(SUPPORTED_TAX_JURISDICTIONS),
        "wallet_import": {
            "solana": bool(helius_api_key()),
            "ethereum": has_etherscan,
            "bitcoin": btc_wallet_import_enabled(),
            "cardano": cardano_wallet_import_enabled(),
            "celestia": celestia_wallet_import_enabled(),
            "hyperliquid": hyperliquid_import_enabled(),
            "evm_chains": list(EVM_AUTO_IMPORT_CHAINS),
            "providers": {
                "helius": bool(helius_api_key()),
                "solscan": bool(solscan_api_key()),
                "evm": "etherscan" if has_etherscan else None,
                "bitcoin": "blockstream",
                "cardano": "blockfrost" if blockfrost_api_key() else "koios",
                "celestia": "publicnode",
                "hyperliquid": "info-api",
            },
        },
    }


# --- Transactions ----------------------------------------------------------

# Serialise normalize+persist so parallel dashboard fetches (portfolio +
# transactions) cannot race on the first post-import read.
_ledger_normalize_lock = Lock()


def _normalize_ledger(txs: List[Transaction]) -> tuple[List[Transaction], bool]:
    """Apply read-time ledger fixes; return (transactions, changed)."""
    return normalize_tax_ledger(txs)


def _ensure_normalized_active_ledger() -> List[Transaction]:
    """Return the active ledger after tax normalization (persist when live).

    Import commits only run a subset of normalizers. Full ``normalize_tax_ledger``
    (LP / lending tax / hard forks / fee FMV / …) historically ran only on
    ``GET /transactions``. Portfolio and tax endpoints must use the same view.
    """
    with _ledger_normalize_lock:
        if is_demo_mode():
            txs, _ = _normalize_ledger(active_transactions())
            return txs

        prior = state.transactions()
        txs, changed = _normalize_ledger(prior)
        if changed:
            state.replace_all(txs)
        return without_sample(txs)


@app.get("/api/transactions", response_model=List[Transaction])
def list_transactions() -> List[Transaction]:
    return _ensure_normalized_active_ledger()


@app.get("/api/asset-labels")
def asset_labels() -> Dict[str, object]:
    """Human-readable names for ledger assets (Solana SPL symbols, etc.)."""
    return build_asset_labels(active_transactions())


@app.get("/api/scam-assets")
def list_scam_assets() -> Dict[str, List[str]]:
    """Ledger asset keys the user has marked as scam/spam."""
    return {"assets": scam_registry.all()}


@app.post("/api/scam-assets")
def mark_scam_asset(payload: ScamAssetRequest) -> Dict[str, object]:
    """Hide an asset from portfolio views (transactions remain in the ledger)."""
    asset = payload.asset.strip()
    if not asset:
        raise HTTPException(status_code=400, detail="Asset cannot be empty.")
    scam_registry.add(asset)
    return {
        "asset": asset,
        "hidden": True,
        "message": f'"{asset}" marked as scam and hidden from portfolio views.',
    }


@app.delete("/api/scam-assets")
def unmark_scam_asset(payload: ScamAssetRequest) -> Dict[str, object]:
    """Restore a previously hidden scam asset to portfolio views."""
    asset = payload.asset.strip()
    if not scam_registry.remove(asset):
        raise HTTPException(status_code=404, detail="Asset is not marked as scam.")
    return {
        "asset": asset,
        "hidden": False,
        "message": f'"{asset}" restored to portfolio views.',
    }


def _visible_asset(asset: str) -> bool:
    return not scam_registry.is_hidden(asset)


@app.post("/api/transactions/cleanup-solana")
def cleanup_solana_phantoms() -> Dict[str, object]:
    """Drop phishing tokens (all chains) and Solana routing noise."""
    txs, removed = strip_spam_transactions(state.transactions())
    txs, dust_removed = strip_dust_transactions(txs)
    txs, renamed = normalize_solana_assets(txs)
    txs, swap_dupes = collapse_solana_swap_duplicate_legs(txs)
    txs, lst_fix = normalize_liquid_staking(txs)
    txs, kamino_fix = normalize_kamino_vault(txs)
    txs, lend_fix = normalize_lending_protocols(txs)
    txs, swap_fix = reclassify_disguised_solana_swaps(txs)
    txs, drift_fix = normalize_drift_collateral(txs)
    txs, staking_echo = collapse_staking_echo_transfers(txs)
    if removed or dust_removed or renamed or swap_dupes or lst_fix or kamino_fix or lend_fix or swap_fix or drift_fix or staking_echo:
        state.replace_all(txs)
    return {
        "removed": removed,
        "dust_removed": dust_removed,
        "renamed": renamed,
        "swap_duplicates_removed": swap_dupes,
        "staking_echo_removed": staking_echo,
        "total": len(state.transactions()),
        "message": (
            f"Removed {removed} spam row(s), {dust_removed} dust row(s), renamed {renamed} mint(s), "
            f"dropped {swap_dupes} duplicate swap transfer(s), "
            f"normalized {lst_fix} liquid-staking leg(s), "
            f"normalized {kamino_fix} Kamino vault leg(s), "
            f"and {staking_echo} exchange staking echo transfer(s)."
            if removed or dust_removed or renamed or swap_dupes or lst_fix or kamino_fix or staking_echo
            else "No spam cleanup needed."
        ),
    }


@app.post("/api/solana-tokens/refresh")
def refresh_solana_tokens() -> Dict[str, object]:
    """Re-download Jupiter token list into the local cache."""
    count = get_registry().load(force_refresh=True)
    return {"tokens": count, "message": f"Loaded {count} Solana token metadata entries."}


@app.post("/api/transactions", response_model=Transaction, status_code=201)
def create_transaction(payload: TransactionCreate) -> Transaction:
    tx = Transaction(id=uuid.uuid4().hex, **payload.model_dump())
    state.add_one(tx)
    return tx


def _tag_import_id(transactions: List[Transaction], import_id: str) -> List[Transaction]:
    return [t.model_copy(update={"import_id": import_id}) for t in transactions]


def _import_source_metadata(
    transactions: List[Transaction], import_id: str
) -> tuple[
    Optional[str],
    Optional[datetime],
    Optional[datetime],
    Optional[datetime],
    Optional[datetime],
    str,
]:
    txs = [t for t in transactions if t.import_id == import_id]
    parser_label = ingestion.primary_source_label(txs)
    data_start, data_end = ingestion.transaction_date_range(txs)
    return parser_label, data_start, data_end, data_start, data_end, "transactions"


def _build_import_sources(transactions: List[Transaction]) -> List[ImportSourceView]:
    registry.reconcile_orphans(transactions)
    views: List[ImportSourceView] = []

    demo_count = demo_transaction_count(transactions)
    if demo_count:
        views.append(
            ImportSourceView(
                id="demo",
                kind="demo",
                label="Demo data",
                transaction_count=demo_count,
            )
        )

    for source in registry.all():
        count = sum(1 for t in transactions if t.import_id == source.id)
        parser_label, data_start, data_end, _, _, _ = _import_source_metadata(
            transactions, source.id
        )
        if source.kind == "wallet" and source.chain:
            parser_label = CHAIN_LABELS.get(source.chain, source.chain)
        coverage_start = source.coverage_start or data_start
        coverage_end = source.coverage_end or data_end
        import_txs = [t for t in transactions if t.import_id == source.id]
        export_kind = (
            infer_csv_export_kind(source.label, import_txs)
            if source.kind == "csv"
            else None
        )
        views.append(
            ImportSourceView(
                id=source.id,
                kind=source.kind,
                label=source.label,
                chain=source.chain,
                address=source.address,
                imported_at=source.imported_at,
                transaction_count=count,
                parser_label=parser_label,
                date_start=coverage_start,
                date_end=coverage_end,
                coverage_start=coverage_start,
                coverage_end=coverage_end,
                data_start=source.data_start or data_start,
                data_end=source.data_end or data_end,
                coverage_from=source.coverage_from,
                export_kind=export_kind,
            )
        )

    legacy_counts: Dict[str, int] = {}
    for tx in transactions:
        if tx.import_id or tx.id in SAMPLE_TRANSACTION_IDS:
            continue
        key = tx.source or "unknown"
        legacy_counts[key] = legacy_counts.get(key, 0) + 1

    for source_key, count in sorted(legacy_counts.items()):
        display = source_key if source_key != "unknown" else "Unknown"
        legacy_txs = [
            t
            for t in transactions
            if not t.import_id
            and t.id not in SAMPLE_TRANSACTION_IDS
            and (t.source or "unknown") == source_key
        ]
        parser_label = ingestion.primary_source_label(legacy_txs)
        data_start, data_end = ingestion.transaction_date_range(legacy_txs)
        views.append(
            ImportSourceView(
                id=f"legacy:{source_key}",
                kind="legacy",
                label=display,
                source_hint=source_key,
                is_unlabeled=True,
                transaction_count=count,
                parser_label=parser_label or ingestion.source_display_label(source_key),
                date_start=data_start,
                date_end=data_end,
                coverage_start=data_start,
                coverage_end=data_end,
                data_start=data_start,
                data_end=data_end,
                coverage_from="transactions",
            )
        )

    return views


def _transaction_matches_import(tx: Transaction, import_id: str) -> bool:
    if import_id == "demo":
        return tx.id in SAMPLE_TRANSACTION_IDS
    if import_id.startswith("legacy:"):
        if tx.import_id or tx.id in SAMPLE_TRANSACTION_IDS:
            return False
        source_key = import_id[7:]
        return (tx.source or "unknown") == source_key
    return tx.import_id == import_id


@app.get("/api/import-sources", response_model=List[ImportSourceView])
def list_import_sources() -> List[ImportSourceView]:
    """List CSV files and wallets currently connected to the ledger."""
    _cleanup_empty_imports(active_transactions())
    return _build_import_sources(active_transactions())


def _gap_view(raw: Dict[str, object]) -> CoverageGapView:
    return CoverageGapView.model_validate(raw)


def _overlap_view(raw: Dict[str, object]) -> ImportOverlapView:
    return ImportOverlapView.model_validate(raw)


def _cleanup_empty_imports(transactions: List[Transaction]) -> List[str]:
    """Remove import registry entries that ended up with zero ledger rows."""
    removed_labels: List[str] = []
    for source in list(registry.all()):
        count = sum(1 for tx in transactions if tx.import_id == source.id)
        if count > 0:
            continue
        if registry.remove(source.id):
            removed_labels.append(source.label)
    return removed_labels


def _duplicate_import_labels(
    matching: Dict[str, int], sources: List[ImportSourceView]
) -> List[str]:
    labels_by_id = {source.id: source.label for source in sources}
    ordered: List[str] = []
    for import_id in sorted(
        matching,
        key=lambda key: (-matching[key], labels_by_id.get(key, key)),
    ):
        label = labels_by_id.get(import_id, import_id)
        if label not in ordered:
            ordered.append(label)
    return ordered


def _coerce_datetime(value: object) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


@app.get("/api/coverage-gaps", response_model=List[CoverageGapView])
def list_coverage_gaps() -> List[CoverageGapView]:
    """Gaps between import coverage windows across the whole ledger."""
    sources = _build_import_sources(active_transactions())
    return [_gap_view(row) for row in find_coverage_gaps(sources)]


@app.get("/api/import-overlaps", response_model=List[ImportOverlapView])
def list_import_overlaps() -> List[ImportOverlapView]:
    """Overlapping import windows and redundant re-imports."""
    transactions = active_transactions()
    sources = _build_import_sources(transactions)
    return [
        _overlap_view(row)
        for row in find_import_overlaps(sources, transactions)
    ]


@app.get("/api/data-health", response_model=DataHealthSummary)
def data_health() -> DataHealthSummary:
    """Scan for orphaned inflows and return saved manual cost-basis overrides."""
    spot_txs = spot_transactions(_ensure_normalized_active_ledger())
    overrides = active_cost_basis_overrides()
    return build_data_health_summary(spot_txs, overrides)


@app.put(
    "/api/cost-basis-overrides/{anchor_transaction_id}",
    response_model=ManualCostBasisOverride,
)
def upsert_cost_basis_override(
    anchor_transaction_id: str,
    payload: ManualCostBasisOverrideCreate,
) -> ManualCostBasisOverride:
    """Save manual acquisition data for an orphaned inflow."""
    spot_txs = spot_transactions(state.transactions())
    anchor = next((t for t in spot_txs if t.id == anchor_transaction_id), None)
    if anchor is None:
        raise HTTPException(status_code=404, detail="Anchor transaction not found.")
    if anchor.transaction_type.value != "TRANSFER" or anchor.transfer_direction != "IN":
        raise HTTPException(
            status_code=400,
            detail="Overrides can only be attached to inbound transfer rows.",
        )

    existing = next(
        (
            o
            for o in state.cost_basis_overrides()
            if o.anchor_transaction_id == anchor_transaction_id
        ),
        None,
    )
    try:
        override = build_override_from_request(
            anchor=anchor,
            acquisition_date=payload.acquisition_date,
            unit_cost=payload.unit_cost,
            total_fiat_spent=payload.total_fiat_spent,
            notes=payload.notes,
            existing=existing,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return state.upsert_cost_basis_override(override)


@app.delete("/api/cost-basis-overrides/{anchor_transaction_id}")
def delete_cost_basis_override(anchor_transaction_id: str) -> Dict[str, object]:
    """Remove a manual cost-basis override."""
    if not state.delete_cost_basis_override(anchor_transaction_id):
        raise HTTPException(status_code=404, detail="Override not found.")
    return {"deleted": True, "anchor_transaction_id": anchor_transaction_id}


def _default_legacy_kind(source_key: str) -> str:
    if source_key in WALLET_IMPORT_CHAINS:
        return "wallet"
    return "csv"


@app.patch("/api/import-sources/{import_id}")
def label_import_source(
    import_id: str, payload: LabelImportRequest
) -> Dict[str, object]:
    """Name an unlabeled legacy import or rename a tracked source."""
    label = payload.label.strip()
    if not label:
        raise HTTPException(status_code=400, detail="Label cannot be empty.")
    if import_id == "demo":
        raise HTTPException(status_code=400, detail="Demo data cannot be renamed.")

    if import_id.startswith("legacy:"):
        prior = state.transactions()
        matching = [t for t in prior if _transaction_matches_import(t, import_id)]
        if not matching:
            raise HTTPException(
                status_code=404, detail="Import source not found or already empty."
            )

        kind_raw = payload.kind or _default_legacy_kind(source_key)
        kind: Literal["csv", "wallet"] = (
            "wallet" if kind_raw == "wallet" else "csv"
        )
        source_key = import_id[7:]
        chain = source_key if kind == "wallet" and source_key in WALLET_IMPORT_CHAINS else None
        new_id = registry.register(kind, label, chain=chain)
        updated = [
            t.model_copy(update={"import_id": new_id})
            if _transaction_matches_import(t, import_id)
            else t
            for t in prior
        ]
        state.replace_all(updated)
        return {
            "import_id": new_id,
            "label": label,
            "kind": kind,
            "transaction_count": len(matching),
            "message": f'Labeled import as "{label}".',
        }

    if not registry.update_label(import_id, label):
        raise HTTPException(status_code=404, detail="Import source not found.")

    return {
        "import_id": import_id,
        "label": label,
        "message": f'Renamed import to "{label}".',
    }


def _snippet_view(
    raw: Dict[str, object],
    *,
    preview_from: Literal["csv_file", "ledger"],
    note: Optional[str] = None,
) -> ImportSnippetView:
    return ImportSnippetView(
        columns=list(raw.get("columns") or []),  # type: ignore[arg-type]
        rows=list(raw.get("rows") or []),  # type: ignore[arg-type]
        total_rows=int(raw.get("total_rows") or 0),
        total_columns=int(raw.get("total_columns") or 0),
        truncated_columns=bool(raw.get("truncated_columns")),
        preview_from=preview_from,
        note=note,
    )


@app.get("/api/import-sources/{import_id}/snippet", response_model=ImportSnippetView)
def get_import_source_snippet(import_id: str) -> ImportSnippetView:
    """Sample CSV rows or ledger fallback for a connected import."""
    if import_id == "demo":
        raise HTTPException(status_code=404, detail="Demo data has no file preview.")

    transactions = state.transactions()

    if import_id.startswith("legacy:"):
        matching = [
            tx for tx in transactions if _transaction_matches_import(tx, import_id)
        ]
        if not matching:
            raise HTTPException(status_code=404, detail="Import source not found.")
        return _snippet_view(
            ingestion.ledger_snippet_from_transactions(matching),
            preview_from="ledger",
            note="Sample from ledger rows — original import was not tracked.",
        )

    tracked = next((source for source in registry.all() if source.id == import_id), None)
    matching = [tx for tx in transactions if tx.import_id == import_id]

    if tracked and tracked.kind == "csv":
        content = read_import_file(import_id)
        if content:
            raw = ingestion.csv_text_snippet(content)
            if raw:
                return _snippet_view(raw, preview_from="csv_file")

    if not matching and not tracked:
        raise HTTPException(status_code=404, detail="Import source not found.")

    note = (
        "Sample from on-chain wallet fetch."
        if tracked and tracked.kind == "wallet"
        else "Original CSV not stored for this import — showing parsed ledger rows."
    )
    return _snippet_view(
        ingestion.ledger_snippet_from_transactions(matching),
        preview_from="ledger",
        note=note,
    )


@app.delete("/api/import-sources/disconnect-bulk")
def disconnect_import_sources_bulk(
    kind: Literal["csv", "wallet"] = Query(..., description="Import kind to disconnect."),
) -> Dict[str, object]:
    """Disconnect all CSV or wallet imports from the ledger."""
    sources = _build_import_sources(state.transactions())
    if kind == "csv":
        targets = [source for source in sources if source.kind in ("csv", "legacy")]
    else:
        targets = [source for source in sources if source.kind == "wallet"]

    if not targets:
        label = "CSV imports" if kind == "csv" else "wallet imports"
        return {
            "removed": 0,
            "total": len(state.transactions()),
            "disconnected": 0,
            "message": f"No {label} to disconnect.",
        }

    kept = state.transactions()
    total_removed = 0
    for source in targets:
        before = len(kept)
        kept = [tx for tx in kept if not _transaction_matches_import(tx, source.id)]
        total_removed += before - len(kept)
        if not source.id.startswith("legacy:") and source.id != "demo":
            registry.remove(source.id)

    state.replace_all(kept)
    label = "CSV import" if kind == "csv" else "wallet"
    return {
        "removed": total_removed,
        "total": len(kept),
        "disconnected": len(targets),
        "message": (
            f"Disconnected {len(targets)} {label}(s) and removed "
            f"{total_removed} transaction(s)."
        ),
    }


@app.delete("/api/import-sources/redundant/bulk")
def disconnect_redundant_imports() -> Dict[str, object]:
    """Remove tracked imports that kept zero ledger transactions."""
    sources = _build_import_sources(state.transactions())
    redundant_ids = [
        source.id
        for source in sources
        if source.transaction_count == 0 and source.kind not in ("demo", "legacy")
    ]
    for import_id in redundant_ids:
        registry.remove(import_id)

    return {
        "removed": len(redundant_ids),
        "total": len(state.transactions()),
        "message": (
            f"Removed {len(redundant_ids)} redundant import(s) that added no "
            "transactions."
            if redundant_ids
            else "No redundant imports to remove."
        ),
    }


@app.delete("/api/import-sources/{import_id}")
def disconnect_import_source(import_id: str) -> Dict[str, object]:
    """Remove all transactions from one import batch."""
    prior = state.transactions()
    kept = [t for t in prior if not _transaction_matches_import(t, import_id)]
    removed = len(prior) - len(kept)
    is_tracked = not import_id.startswith("legacy:") and import_id != "demo"
    in_registry = is_tracked and any(
        source.id == import_id for source in registry.all()
    )
    if not removed and not in_registry:
        raise HTTPException(
            status_code=404, detail="Import source not found or already empty."
        )

    state.replace_all(kept)
    if is_tracked:
        registry.remove(import_id)

    if removed:
        message = f"Disconnected import and removed {removed} transaction(s)."
    else:
        message = "Removed redundant import with no transactions in the ledger."

    return {
        "import_id": import_id,
        "removed": removed,
        "total": len(kept),
        "message": message,
    }


def _commit_imported_transactions(
    parsed: List[Transaction], *, replace: bool
) -> Dict[str, object]:
    """Merge parsed rows into the ledger and run post-import normalizers."""
    prior = state.transactions()
    demo_in_prior = len(prior) - len(without_sample(prior))

    # Collapse duplicates inside this batch before touching the ledger so a CSV
    # that repeats the same event (or assigns colliding ids) only lands once.
    parsed, _batch_stats = dedupe_transactions(parsed)

    if replace:
        merged = parsed
        skipped_duplicates = 0
    else:
        existing = without_sample(prior)
        existing_ids = {t.id for t in existing}
        existing_fingerprints: set = set()
        for t in existing:
            existing_fingerprints.update(dedup_keys_for_transaction(t))
        novel: List[Transaction] = []
        skipped_duplicates = 0
        for tx in parsed:
            if tx.id in existing_ids or any(
                fp in existing_fingerprints for fp in dedup_keys_for_transaction(tx)
            ):
                skipped_duplicates += 1
                continue
            novel.append(tx)
        merged = existing + novel

    merged, _priced = backfill_wallet_cost_basis(merged, store=state.prices)
    merged, _on_chain = backfill_on_chain_tx_ids(merged)
    merged, _income = reclassify_income_events(merged)
    state.replace_all(merged)

    txs, fixed = normalize_kraken_ledger(state.transactions())
    txs, linked = collapse_stablecoin_quote_legs(txs)
    txs, swap_dupes = collapse_solana_swap_duplicate_legs(txs)
    txs, lst_fix = normalize_liquid_staking(txs)
    txs, kamino_fix = normalize_kamino_vault(txs)
    txs, lend_fix = normalize_lending_protocols(txs)
    txs, swap_fix = reclassify_disguised_solana_swaps(txs)
    txs, drift_fix = normalize_drift_collateral(txs)
    txs, cdc_fix = normalize_cryptocom_exchange_legs(txs)
    txs, staking_echo = collapse_staking_echo_transfers(txs)
    txs, tz_dupes = collapse_exchange_timezone_duplicates(txs)
    txs = annotate_transfer_pairs(txs)
    if fixed or linked or swap_dupes or lst_fix or kamino_fix or lend_fix or swap_fix or drift_fix or cdc_fix or staking_echo or tz_dupes:
        state.replace_all(txs)

    txs, removed = strip_spam_transactions(state.transactions())
    txs, dust_removed = strip_dust_transactions(txs)
    txs, renamed = normalize_solana_assets(txs)
    txs, swap_dupes2 = collapse_solana_swap_duplicate_legs(txs)
    txs, lst_fix2 = normalize_liquid_staking(txs)
    txs, kamino_fix2 = normalize_kamino_vault(txs)
    txs, lend_fix2 = normalize_lending_protocols(txs)
    txs, swap_fix2 = reclassify_disguised_solana_swaps(txs)
    txs, drift_fix2 = normalize_drift_collateral(txs)
    txs, cdc_fix2 = normalize_cryptocom_exchange_legs(txs)
    txs, staking_echo2 = collapse_staking_echo_transfers(txs)
    txs, tz_dupes2 = collapse_exchange_timezone_duplicates(txs)
    txs = annotate_transfer_pairs(txs)
    if (
        removed
        or dust_removed
        or renamed
        or swap_dupes2
        or lst_fix2
        or kamino_fix2
        or lend_fix2
        or swap_fix2
        or drift_fix2
        or cdc_fix2
        or staking_echo2
        or tz_dupes2
    ):
        state.replace_all(txs)

    # Final safety net: collapse any cross-batch duplicates the normalizers may
    # have surfaced (e.g. rows whose ids drifted between imports).
    txs, _final_stats = dedupe_transactions(state.transactions())
    if (
        _final_stats["skipped_id"]
        or _final_stats["skipped_fingerprint"]
        or _final_stats["skipped_on_chain"]
    ):
        state.replace_all(txs)

    return {
        "imported": len(parsed),
        "skipped_duplicates": skipped_duplicates,
        "demo_removed": demo_in_prior if demo_in_prior else 0,
        "total": len(state.transactions()),
    }


@app.post("/api/transactions/preview", response_model=ImportPreviewResponse)
async def preview_transactions(
    files: List[UploadFile] = File(...),
) -> ImportPreviewResponse:
    """Detect parser and date range for uploaded files without importing."""
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded.")

    existing_sources = _build_import_sources(state.transactions())
    existing_transactions = state.transactions()
    previews: List[ImportFilePreview] = []
    ledger_gaps: List[CoverageGapView] = []
    preview_sources: List[ImportSourceView] = list(existing_sources)
    preview_ids, preview_fingerprints = ledger_dedup_keys(
        without_sample(existing_transactions)
    )

    for upload in files:
        name = upload.filename or "upload.csv"
        content = await upload.read()
        summary = ingestion.preview_upload(name, content)
        coverage_start = _coerce_datetime(
            summary.get("coverage_start") or summary.get("date_start")
        )
        coverage_end = _coerce_datetime(
            summary.get("coverage_end") or summary.get("date_end")
        )
        data_start = _coerce_datetime(summary.get("data_start"))
        data_end = _coerce_datetime(summary.get("data_end"))
        parser_label = summary.get("parser_label")
        duplicate_count = 0
        duplicate_import_labels: List[str] = []
        export_kind: Optional[str] = None
        csv_columns: List[str] = []
        csv_sample_rows: List[List[str]] = []
        csv_total_rows = 0
        csv_total_columns = 0
        csv_truncated_columns = False
        lower_name = name.lower()
        if lower_name.endswith((".csv", ".txt")):
            snippet = ingestion.csv_text_snippet(content)
            if snippet:
                csv_columns = list(snippet.get("columns") or [])  # type: ignore[arg-type]
                csv_sample_rows = list(snippet.get("rows") or [])  # type: ignore[arg-type]
                csv_total_rows = int(snippet.get("total_rows") or 0)
                csv_total_columns = int(snippet.get("total_columns") or 0)
                csv_truncated_columns = bool(snippet.get("truncated_columns"))
        if not summary.get("error"):
            try:
                batch = ingestion.parse_upload(name, content)
                duplicate_count, matching = count_ledger_duplicates(
                    batch, existing_transactions
                )
                duplicate_import_labels = _duplicate_import_labels(
                    matching, existing_sources
                )
                novel, _within_upload_dupes = partition_novel_transactions(
                    batch,
                    known_ids=preview_ids,
                    known_fingerprints=preview_fingerprints,
                )
                export_kind = infer_csv_export_kind(name, batch)
                if batch and not novel:
                    same_upload = duplicate_count < len(batch)
                    summary = {
                        **summary,
                        "error": format_fully_duplicate_rejection(
                            name,
                            len(batch),
                            ledger_labels=duplicate_import_labels,
                            same_upload=same_upload,
                        ),
                    }
            except Exception:  # noqa: BLE001 - preview still useful without dupes
                pass
        file_gaps = [
            _gap_view(row)
            for row in find_preview_ledger_gaps(
                coverage_start=coverage_start,
                coverage_end=coverage_end,
                label=name,
                parser_label=str(parser_label) if parser_label else None,
                sources=existing_sources,
            )
        ]
        previews.append(
            ImportFilePreview(
                filename=name,
                parser=summary.get("parser"),  # type: ignore[arg-type]
                parser_label=parser_label,  # type: ignore[arg-type]
                transaction_count=int(summary.get("transaction_count") or 0),
                date_start=coverage_start,
                date_end=coverage_end,
                coverage_start=coverage_start,
                coverage_end=coverage_end,
                data_start=data_start,
                data_end=data_end,
                coverage_from=summary.get("coverage_from") or "transactions",  # type: ignore[arg-type]
                error=summary.get("error"),  # type: ignore[arg-type]
                coverage_gaps=file_gaps,
                duplicate_count=duplicate_count,
                duplicate_import_labels=duplicate_import_labels,
                export_kind=export_kind,
                csv_columns=csv_columns,
                csv_sample_rows=csv_sample_rows,
                csv_total_rows=csv_total_rows,
                csv_total_columns=csv_total_columns,
                csv_truncated_columns=csv_truncated_columns,
            )
        )
        ledger_gaps.extend(file_gaps)
        if coverage_start and coverage_end and not summary.get("error"):
            preview_sources.append(
                ImportSourceView(
                    id=f"preview:{name}",
                    kind="csv",
                    label=name,
                    parser_label=str(parser_label) if parser_label else name,
                    coverage_start=coverage_start,
                    coverage_end=coverage_end,
                    date_start=coverage_start,
                    date_end=coverage_end,
                    transaction_count=int(summary.get("transaction_count") or 0),
                )
            )

    import_overlaps = [
        _overlap_view(row)
        for row in find_import_overlaps(preview_sources, existing_transactions)
        if any(
            import_id.startswith("preview:")
            for import_id in row["import_ids"]  # type: ignore[index]
        )
    ]

    return ImportPreviewResponse(
        files=previews,
        coverage_gaps=ledger_gaps,
        import_overlaps=import_overlaps,
    )


@app.post("/api/transactions/import-mexc-emails", response_model=MexcEmailImportResponse)
def import_mexc_emails(payload: MexcEmailImportRequest) -> MexcEmailImportResponse:
    """Parse pasted MEXC deposit/withdrawal/futures emails; optionally import."""
    parsed = parse_mexc_emails(payload.text)
    csv_text = transactions_to_csv(parsed.transactions)

    if not payload.commit:
        return MexcEmailImportResponse(
            transactions=parsed.transactions,
            warnings=parsed.warnings,
            skipped_blocks=parsed.skipped_blocks,
            csv=csv_text,
            message=(
                f"Parsed {len(parsed.transactions)} row(s)."
                if parsed.transactions
                else "No recognizable MEXC emails found."
            ),
        )

    if not parsed.transactions:
        return MexcEmailImportResponse(
            warnings=parsed.warnings,
            skipped_blocks=parsed.skipped_blocks,
            csv=csv_text,
            message="No recognizable MEXC emails found — nothing imported.",
        )

    prior = state.transactions()
    known_ids, known_fingerprints = ledger_dedup_keys(without_sample(prior))
    novel, _within = partition_novel_transactions(
        parsed.transactions,
        known_ids=known_ids,
        known_fingerprints=known_fingerprints,
    )
    skipped = len(parsed.transactions) - len(novel)
    if not novel:
        return MexcEmailImportResponse(
            transactions=parsed.transactions,
            warnings=parsed.warnings,
            skipped_blocks=parsed.skipped_blocks,
            csv=csv_text,
            skipped_duplicates=skipped,
            message="All parsed rows already exist in the ledger.",
        )

    data_start, data_end = transaction_date_range(parsed.transactions)
    import_id = registry.register(
        "csv",
        label="MEXC emails",
        coverage_start=data_start,
        coverage_end=data_end,
        data_start=data_start,
        data_end=data_end,
        coverage_from="transactions",
    )
    tagged = _tag_import_id(novel, import_id)
    result = _commit_imported_transactions(tagged, replace=False)
    _cleanup_empty_imports(state.transactions())

    return MexcEmailImportResponse(
        transactions=parsed.transactions,
        warnings=parsed.warnings,
        skipped_blocks=parsed.skipped_blocks,
        csv=csv_text,
        imported=int(result.get("imported", len(novel))),
        skipped_duplicates=skipped,
        message=(
            f"Imported {result.get('imported', len(novel))} MEXC email row(s)"
            + (f" ({skipped} duplicate(s) skipped)." if skipped else ".")
        ),
    )


@app.post("/api/transactions/import")
async def import_transactions(
    files: List[UploadFile] = File(...),
    replace: bool = Query(False, description="Replace the ledger instead of appending."),
) -> Dict[str, object]:
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded.")

    if replace:
        registry.clear()

    parsed: List[Transaction] = []
    file_results: List[Dict[str, object]] = []
    errors: List[str] = []
    prior = state.transactions()
    existing_sources = _build_import_sources(prior)
    known_ids, known_fingerprints = ledger_dedup_keys(without_sample(prior))

    for upload in files:
        name = upload.filename or "upload.csv"
        content = await upload.read()
        try:
            batch = ingestion.parse_upload(name, content)
            duplicate_count, matching = count_ledger_duplicates(batch, prior)
            duplicate_import_labels = _duplicate_import_labels(
                matching, existing_sources
            )
            novel, _within_upload_dupes = partition_novel_transactions(
                batch,
                known_ids=known_ids,
                known_fingerprints=known_fingerprints,
            )
            if batch and not novel:
                same_upload = duplicate_count < len(batch)
                errors.append(
                    format_fully_duplicate_rejection(
                        name,
                        len(batch),
                        ledger_labels=duplicate_import_labels,
                        same_upload=same_upload,
                    )
                )
                continue

            coverage = infer_export_coverage(name, content, batch)
            if coverage:
                import_id = registry.register(
                    "csv",
                    label=name,
                    coverage_start=coverage.coverage_start,
                    coverage_end=coverage.coverage_end,
                    data_start=coverage.data_start,
                    data_end=coverage.data_end,
                    coverage_from=coverage.coverage_from,
                )
            else:
                import_id = registry.register("csv", label=name)
            save_import_file(import_id, content)
            parsed.extend(_tag_import_id(novel, import_id))
            file_results.append(
                {
                    "filename": name,
                    "imported": len(batch),
                    "added": len(novel),
                    "skipped_duplicates": len(batch) - len(novel),
                    "duplicate_import_labels": duplicate_import_labels,
                }
            )
        except Exception as exc:  # noqa: BLE001 - surface parse errors to client
            errors.append(f"{name}: {exc}")

    if errors and not parsed:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "No files were imported.",
                "errors": errors,
            },
        )

    demo_in_prior = len(prior) - len(without_sample(prior))

    result = _commit_imported_transactions(parsed, replace=replace)
    _cleanup_empty_imports(state.transactions())
    result["files"] = file_results
    result["demo_removed"] = demo_in_prior if demo_in_prior else 0
    if errors:
        result["errors"] = errors
    return result


@app.post("/api/transactions/import-wallet")
def import_wallet(
    payload: WalletImportRequest,
    replace: bool = Query(False, description="Replace the ledger instead of appending."),
) -> Dict[str, object]:
    """Fetch wallet history by address (Solana / EVM / Bitcoin / Cardano)."""
    address = payload.address
    chain = payload.chain  # resolved in schema validator

    try:
        hl_count = 0
        on_chain_count = 0
        if chain == "solana":
            parsed = fetch_solana_wallet(address)
            chain_label = CHAIN_LABELS["solana"]
        elif chain == "bitcoin":
            parsed = fetch_btc_wallet(address)
            chain_label = CHAIN_LABELS["bitcoin"]
        elif chain == "cardano":
            parsed = fetch_cardano_wallet(address)
            chain_label = CHAIN_LABELS["cardano"]
        elif chain == "celestia":
            parsed = fetch_celestia_wallet(address)
            chain_label = CHAIN_LABELS["celestia"]
        elif chain == "ethereum":
            on_chain = fetch_evm_wallet_multi(address)
            try:
                hl = fetch_hyperliquid_wallet(address)
            except ValueError:
                hl = []
            parsed = on_chain + hl
            chain_label = (
                f"On-chain ({EVM_AUTO_IMPORT_LABEL})"
                + (" + Hyperliquid" if hl else "")
            )
            hl_count = len(hl)
            on_chain_count = len(on_chain)
        elif chain in EVM_CHAIN_META:
            parsed = fetch_evm_wallet(address, chain=chain)  # type: ignore[arg-type]
            chain_label = CHAIN_LABELS[chain]
        else:
            raise ValueError(f"Unsupported wallet chain: {chain}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not parsed:
        return {
            "imported": 0,
            "demo_removed": 0,
            "total": len(state.transactions()),
            "address": address,
            "chain": chain,
            "message": f"No wallet activity found for that {chain_label} address.",
        }

    prior = state.transactions()
    demo_in_prior = len(prior) - len(without_sample(prior))
    if chain in (*EVM_CHAIN_META.keys(), "bitcoin"):
        short = address[:10] + "…"
    else:
        short = address[:8] + "…"

    if replace:
        registry.clear()

    wallet_label = f"{chain_label} {short}"
    known_ids, known_fingerprints = ledger_dedup_keys(without_sample(prior))
    novel, _duplicate_count = partition_novel_transactions(
        parsed,
        known_ids=known_ids,
        known_fingerprints=known_fingerprints,
    )
    if parsed and not novel:
        duplicate_count, matching = count_ledger_duplicates(parsed, prior)
        duplicate_labels = _duplicate_import_labels(
            matching, _build_import_sources(prior)
        )
        raise HTTPException(
            status_code=400,
            detail=format_fully_duplicate_rejection(
                wallet_label,
                len(parsed),
                ledger_labels=duplicate_labels,
            ),
        )

    data_start, data_end = transaction_date_range(parsed)
    import_id = registry.register(
        "wallet",
        label=wallet_label,
        chain=chain,
        address=address,
        coverage_start=data_start,
        coverage_end=data_end,
        data_start=data_start,
        data_end=data_end,
        coverage_from="transactions",
    )
    parsed = _tag_import_id(novel, import_id)

    result = _commit_imported_transactions(parsed, replace=replace)
    _cleanup_empty_imports(state.transactions())
    result["address"] = address
    result["chain"] = chain
    result["demo_removed"] = demo_in_prior if demo_in_prior else 0
    if chain == "ethereum" and (on_chain_count or hl_count):
        parts = []
        if on_chain_count:
            parts.append(f"{on_chain_count} on-chain")
        if hl_count:
            parts.append(f"{hl_count} Hyperliquid perp")
        breakdown = " + ".join(parts)
        result["hyperliquid_imported"] = hl_count
        result["on_chain_imported"] = on_chain_count
        result["message"] = (
            f"Imported {breakdown} transaction(s) for {short} "
            f"({result['imported']} new rows appended)."
            if not replace
            else f"Imported {breakdown} transaction(s) for {short}."
        )
    else:
        result["message"] = (
            f"Imported {result['imported']} transaction(s) from {chain_label} wallet {short}"
        )
    return result


@app.get("/api/transactions/demo-status")
def demo_status() -> Dict[str, object]:
    """Current data mode and whether demo rows remain on the persisted ledger."""
    persisted_demo = demo_transaction_count(state.transactions())
    mode = state.data_mode()
    return {
        "count": len(default_transactions()) if mode == "demo" else persisted_demo,
        "active": mode == "demo",
        "mode": mode,
        "persisted_demo_count": persisted_demo,
    }


@app.post("/api/transactions/strip-demo")
def strip_demo_transactions() -> Dict[str, object]:
    """Remove bundled demo/seed transactions from the current ledger."""
    prior = state.transactions()
    cleaned = without_sample(prior)
    removed = len(prior) - len(cleaned)
    if removed:
        state.replace_all(cleaned)
    return {
        "removed": removed,
        "total": len(cleaned),
        "message": (
            f"Removed {removed} demo transaction(s)."
            if removed
            else "No demo data in the ledger."
        ),
    }


@app.get("/api/transactions/backup")
def download_ledger_backup() -> Response:
    """Download a JSON backup of the live ledger (and cost-basis overrides)."""
    payload = state.build_backup_payload()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    filename = f"crypto-tax-ledger-backup-{stamp}.json"
    body = json.dumps(payload, indent=2, default=str)
    return Response(
        content=body,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/transactions/reset")
def reset_transactions(
    backup: bool = Query(
        True,
        description="Write data/ledger.json.bak before replacing with the sample ledger.",
    ),
) -> Dict[str, object]:
    registry.clear()
    bak = state.reset_to_sample(backup=backup)
    return {
        "total": len(state.transactions()),
        "local_backup": str(bak) if bak else None,
    }


# --- Internal transfer matching -------------------------------------------


@app.post("/api/transactions/backfill-cost-basis")
def backfill_cost_basis() -> Dict[str, object]:
    """Infer missing acquisition values from swap legs, historical prices, and transfer pairs."""
    prior = state.transactions()
    txs = annotate_transfer_pairs(prior)
    txs, updated = backfill_wallet_cost_basis(txs, store=state.prices)
    prior_by_id = {t.id: t for t in prior}
    changed = len(prior) != len(txs) or any(prior_by_id.get(t.id) != t for t in txs)
    if changed:
        state.replace_all(txs)
    return {
        "updated": updated,
        "saved": changed,
        "message": (
            f"Inferred cost basis on {updated} transaction(s) and saved the ledger."
            if changed and updated
            else (
                "Ledger saved (transfer pairing updated)."
                if changed and not updated
                else (
                    f"Inferred cost basis on {updated} transaction(s)."
                    if updated
                    else "No rows needed cost basis backfill."
                )
            )
        ),
    }


@app.post("/api/transactions/fix-movements")
def fix_movements() -> Dict[str, object]:
    """Reclassify Kraken fee debits, receive/sell pairs, and wallet withdrawals."""
    txs, count = normalize_kraken_ledger(state.transactions())
    txs, linked = collapse_stablecoin_quote_legs(txs)
    if count or linked:
        state.replace_all(txs)
    return {
        "reclassified": count + linked,
        "message": (
            f"Normalised {count} Kraken row(s) and linked {linked} stablecoin quote leg(s)."
            if count or linked
            else "No Kraken movements needed reclassification."
        ),
    }


@app.post("/api/transactions/match-transfers", response_model=TransferMatchResult)
def match_transfers(persist: bool = Query(True)) -> TransferMatchResult:
    """Reclassify mis-typed cross-ledger SELL+BUY pairs as paired TRANSFERs.

    Only matches when both legs have sources, lack market-trade markers
    (counter asset / order type), and fall within a short time window.
    """
    txs = state.transactions()
    updated, reclassified = match_internal_transfers(txs)
    if persist and reclassified:
        state.replace_all(updated)
    pairs = len(reclassified) // 2
    return TransferMatchResult(
        matched_pairs=pairs,
        reclassified_transaction_ids=reclassified,
        message=(
            f"Matched {pairs} internal transfer pair(s); "
            f"reclassified {len(reclassified)} transaction(s) as TRANSFER."
        ),
    )


@app.post("/api/transactions/deduplicate")
def deduplicate_ledger() -> Dict[str, object]:
    """Collapse duplicate rows already in the ledger (one-time cleanup)."""
    prior = state.transactions()
    deduped, stats = dedupe_transactions(prior)
    removed = len(prior) - len(deduped)
    if removed:
        state.replace_all(deduped)
    return {
        "removed": removed,
        "remaining": len(deduped),
        "skipped_id": stats["skipped_id"],
        "skipped_fingerprint": stats["skipped_fingerprint"],
        "skipped_on_chain": stats["skipped_on_chain"],
        "message": (
            f"Removed {removed} duplicate transaction(s); {len(deduped)} remaining."
            if removed
            else "No duplicate transactions found."
        ),
    }


@app.delete("/api/transactions/{transaction_id}")
def delete_transaction(transaction_id: str) -> Dict[str, bool]:
    deleted = state.delete(transaction_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return {"deleted": True}


# --- Settings --------------------------------------------------------------


@app.get("/api/settings")
def get_settings() -> Dict[str, object]:
    jurisdiction = state.tax_jurisdiction()
    return {
        "tax_jurisdiction": jurisdiction,
        "reporting_currency": reporting_currency_for(jurisdiction),
        "uk_perp_treatment": state.perp_treatment("UK"),
        "us_perp_treatment": state.perp_treatment("US"),
        "data_mode": state.data_mode(),
        "uk_unused_basic_band": state.uk_unused_basic_band(),
        "us_ordinary_income_rate": state.us_ordinary_income_rate(),
        "us_long_term_cg_rate": state.us_long_term_cg_rate(),
    }


@app.patch("/api/settings")
def update_settings(payload: TaxSettingsUpdate) -> Dict[str, object]:
    if payload.data_mode is not None:
        try:
            state.set_data_mode(payload.data_mode)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    if payload.tax_jurisdiction is not None:
        code = payload.tax_jurisdiction.strip().upper()
        if code not in SUPPORTED_TAX_JURISDICTIONS:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported tax jurisdiction: {payload.tax_jurisdiction}",
            )
        state.set_tax_jurisdiction(code)

    for code, value in (
        ("UK", payload.uk_perp_treatment),
        ("US", payload.us_perp_treatment),
    ):
        if value is None:
            continue
        try:
            state.set_perp_treatment(code, value)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    if payload.uk_unused_basic_band is not None:
        state.set_uk_unused_basic_band(payload.uk_unused_basic_band)
    if payload.us_ordinary_income_rate is not None:
        try:
            state.set_us_ordinary_income_rate(payload.us_ordinary_income_rate)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    if payload.us_long_term_cg_rate is not None:
        try:
            state.set_us_long_term_cg_rate(payload.us_long_term_cg_rate)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return get_settings()


# --- Prices ----------------------------------------------------------------


@app.get("/api/prices")
def get_prices() -> Dict[str, float]:
    return state.prices.all()


@app.put("/api/prices")
def update_prices(updates: List[PriceUpdate]) -> Dict[str, float]:
    state.prices.update_many({u.asset: u.price for u in updates})
    return state.prices.all()


# --- Portfolio dashboard ---------------------------------------------------


def _resolve_method(method: str) -> AccountingMethod:
    try:
        return AccountingMethod(method.upper())
    except ValueError:
        raise HTTPException(
            status_code=400, detail=f"Unsupported accounting method: {method}"
        )


def _portfolio_price_maps(
    transactions: List[Transaction],
    method: AccountingMethod,
    *,
    tax_jurisdiction: str,
) -> tuple[Dict[str, float], Dict[str, object]]:
    """Resolve live + fallback USD prices for all open holdings."""
    jurisdiction = tax_jurisdiction.upper()
    reporting_currency = reporting_currency_for(jurisdiction)
    cost_basis_usd: Dict[str, float] = {}

    if jurisdiction == "UK":
        pools = compute_uk_open_pools(transactions)
        assets = list(pools.keys())
        for asset, (quantity, invested) in pools.items():
            if quantity > 0 and invested > 0:
                avg = invested / quantity
                cost_basis_usd[asset] = fx.convert(
                    avg, reporting_currency, "USD", datetime.now(timezone.utc)
                )
    else:
        result = _run_engine(
            transactions, method, reporting_currency=reporting_currency
        )
        assets = list(result.open_lots.keys())
        for asset, lots in result.open_lots.items():
            quantity = float(sum((lot.quantity for lot in lots), Decimal("0")))
            invested = sum(lot.remaining_cost_basis for lot in lots)
            if quantity > 0 and invested > 0:
                avg = invested / quantity
                # US lots are already in USD.
                cost_basis_usd[asset] = (
                    avg
                    if reporting_currency == "USD"
                    else fx.convert(
                        avg, reporting_currency, "USD", datetime.now(timezone.utc)
                    )
                )

    resolved = resolve_prices(
        assets=assets,
        transactions=transactions,
        store=state.prices,
        cost_basis_usd=cost_basis_usd,
    )
    return merge_price_maps(state.prices, resolved), resolved


def _spot_tax_ledger(*, exclude_staking: bool = False) -> List[Transaction]:
    """Spot ledger with manual cost-basis overrides applied for tax math."""
    txs = spot_transactions(_ensure_normalized_active_ledger())
    if exclude_staking:
        txs = filter_exclude_staking(txs)
    return prepare_tax_ledger(txs, active_cost_basis_overrides())


def _uk_cgt_report(tax_year_label: Optional[str] = None) -> UkCgtSummary:
    """UK CGT summary, folding perp PnL when treatment is capital_gains."""
    ledger = _ensure_normalized_active_ledger()
    report = calculate_uk_cgt(
        prepare_tax_ledger(
            spot_transactions(ledger), active_cost_basis_overrides()
        ),
        tax_year_label=tax_year_label,
    )
    return merge_perp_into_uk_cgt(
        report,
        ledger,
        state.perp_treatment("UK"),
    )


def _us_realized_report(
    method: AccountingMethod,
    tax_year: Optional[int] = None,
) -> RealizedGainsSummary:
    """US Form 8949 summary, folding perp PnL when treatment is capital_gains."""
    ledger = _ensure_normalized_active_ledger()
    report = calculate_realized_gains(
        prepare_tax_ledger(
            spot_transactions(ledger), active_cost_basis_overrides()
        ),
        method,
        tax_year=tax_year,
        tax_jurisdiction="US",
    )
    return merge_perp_into_us_realized(
        report,
        ledger,
        state.perp_treatment("US"),
    )


def _portfolio_realized_gain(
    tax_txs: List[Transaction],
    accounting: AccountingMethod,
    jurisdiction: str,
    ledger: List[Transaction],
) -> float:
    """Lifetime realized gain including capital-gains-treated perps."""
    if jurisdiction.upper() == "UK":
        return merge_perp_into_uk_cgt(
            calculate_uk_cgt(tax_txs, tax_year_label=None),
            ledger,
            state.perp_treatment("UK"),
        ).net_gain

    report = calculate_realized_gains(
        tax_txs, accounting, tax_year=None, tax_jurisdiction=jurisdiction
    )
    return merge_perp_into_us_realized(
        report,
        ledger,
        state.perp_treatment(jurisdiction),
    ).total_gain


@app.get("/api/portfolio", response_model=PortfolioSummary)
def portfolio(
    method: str = Query("FIFO"),
    apply_dust_filter: bool = Query(True),
    exclude_staking: bool = Query(
        False,
        description="Omit staking income and exchange earn echo transfers from tax math.",
    ),
    display_currency: str = Query(
        "GBP", description="Dashboard display currency (GBP or USD)."
    ),
) -> PortfolioSummary:
    jurisdiction = state.tax_jurisdiction()
    if jurisdiction == "UK":
        accounting = AccountingMethod.SECTION_104
    else:
        accounting = _resolve_method(method)
    if display_currency.upper() not in SUPPORTED_DISPLAY_CURRENCIES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported display currency: {display_currency}",
        )

    txs = _ensure_normalized_active_ledger()
    spot_txs = spot_transactions(txs)
    if exclude_staking:
        spot_txs = filter_exclude_staking(spot_txs)
    tax_txs = prepare_tax_ledger(spot_txs, active_cost_basis_overrides())
    perps_summary = build_perps_summary(txs)
    prices, price_meta = _portfolio_price_maps(
        spot_txs, accounting, tax_jurisdiction=jurisdiction
    )

    positions, missing, income = calculate_positions(
        tax_txs,
        accounting,
        prices,
        apply_dust_filter=apply_dust_filter,
        tax_jurisdiction=jurisdiction,
    )
    all_holdings, _, _ = calculate_positions(
        tax_txs,
        accounting,
        prices,
        apply_dust_filter=False,
        tax_jurisdiction=jurisdiction,
    )
    positions = [p for p in positions if _visible_asset(p.asset)]
    all_holdings = [p for p in all_holdings if _visible_asset(p.asset)]
    missing = [m for m in missing if _visible_asset(m.asset)]
    total_realized = _portfolio_realized_gain(
        tax_txs, accounting, jurisdiction, txs
    )

    # Stablecoins are cash — keep them in total value but hide from PnL views.
    tradable = [p for p in positions if not is_stablecoin(p.asset)]
    # Harvest losers even when market value is below the dust threshold (e.g. rugged
    # memecoins with real cost basis still have harvestable unrealized losses).
    tradable_for_harvest = [
        p for p in all_holdings if not is_stablecoin(p.asset)
    ]
    harvest = build_tax_harvest_matrix(
        tradable_for_harvest,
        tax_jurisdiction=jurisdiction,
        transactions=tax_txs,
        method=accounting,
        prices_usd=prices,
        uk_unused_basic_band=state.uk_unused_basic_band(),
        us_ordinary_rate=state.us_ordinary_income_rate(),
        us_ltcg_rate=state.us_long_term_cg_rate(),
    )
    realized_by_asset = calculate_realized_pnl_by_asset(
        tax_txs, accounting, tax_jurisdiction=jurisdiction
    )
    realized_by_asset = merge_perp_into_realized_pnl_by_asset(
        realized_by_asset,
        txs,
        jurisdiction=jurisdiction,
        treatment=state.perp_treatment(jurisdiction),
    )
    realized_by_asset = [r for r in realized_by_asset if _visible_asset(r.asset)]

    total_value = round(sum(p.current_value for p in all_holdings), 2)
    total_invested = round(sum(p.total_invested for p in tradable), 2)
    total_unrealized = round(sum(p.unrealized_pnl for p in tradable), 2)

    holdings_reporting = sorted(
        [
            {
                "asset": p.asset,
                "quantity": p.quantity,
                "average_cost_basis": p.average_cost_basis,
                "current_value": p.current_value,
                "total_invested": p.total_invested,
                "portfolio_pct": round(
                    (p.current_value / total_value * 100) if total_value > 0 else 0.0,
                    2,
                ),
                "is_stablecoin": is_stablecoin(p.asset),
                "price_source": (
                    price_meta[p.asset].source
                    if p.asset in price_meta
                    else None
                ),
                "is_estimated": (
                    price_meta[p.asset].source in {"cost_basis"}
                    if p.asset in price_meta
                    else False
                ),
                "unrealized_pnl": p.unrealized_pnl,
                "unrealized_pnl_pct": p.unrealized_pnl_pct,
            }
            for p in all_holdings
        ],
        key=lambda h: h["current_value"],
        reverse=True,
    )

    holdings_rows = [HoldingRow(**h) for h in holdings_reporting]

    return build_portfolio_summary(
        positions_reporting=tradable,
        holdings_reporting=holdings_rows,
        income_reporting=income,
        harvest_reporting=harvest,
        realized_pnl_reporting=realized_by_asset,
        missing=missing,
        method=accounting,
        total_value=total_value,
        total_invested=total_invested,
        total_unrealized=total_unrealized,
        total_realized=total_realized,
        display_currency=display_currency,
        tax_jurisdiction=jurisdiction,
        reporting_currency=reporting_currency_for(jurisdiction),
        perps_reporting=perps_summary,
        uk_unused_basic_band=state.uk_unused_basic_band(),
        us_ordinary_rate=state.us_ordinary_income_rate(),
        us_ltcg_rate=state.us_long_term_cg_rate(),
    )


@app.get("/api/pnl-breakdown", response_model=PnlBreakdown)
def pnl_breakdown(
    method: str = Query("FIFO"),
    exclude_staking: bool = Query(
        False,
        description="Omit staking income from open-lot and disposal drill-down.",
    ),
) -> PnlBreakdown:
    """Per-asset open lots and realized disposals for dashboard drill-down."""
    jurisdiction = state.tax_jurisdiction()
    accounting = (
        AccountingMethod.SECTION_104
        if jurisdiction == "UK"
        else _resolve_method(method)
    )
    spot_txs = spot_transactions(_ensure_normalized_active_ledger())
    if exclude_staking:
        spot_txs = filter_exclude_staking(spot_txs)
    tax_txs = prepare_tax_ledger(spot_txs, active_cost_basis_overrides())
    prices, _ = _portfolio_price_maps(
        spot_txs, accounting, tax_jurisdiction=jurisdiction
    )
    return build_pnl_breakdown(
        tax_txs,
        accounting,
        prices,
        tax_jurisdiction=jurisdiction,
    )


# --- Tax report ------------------------------------------------------------


def _uk_jurisdiction() -> bool:
    return state.tax_jurisdiction().upper() == "UK"


@app.get("/api/tax-report", response_model=None)
def tax_report(
    year: str = Query(
        ...,
        description="UK tax-year label (e.g. 2024/25) or US calendar year (e.g. 2024).",
    ),
    method: str = Query("FIFO"),
) -> UkCgtSummary | RealizedGainsSummary:
    if _uk_jurisdiction():
        return _uk_cgt_report(tax_year_label=year)
    accounting = _resolve_method(method)
    return _us_realized_report(accounting, tax_year=int(year))


@app.get("/api/tax-report/income", response_model=UkIncomeSummary)
def tax_report_income(
    year: str = Query(..., description="UK tax-year label, e.g. 2024/25."),
) -> UkIncomeSummary:
    """Crypto income (airdrops, staking) for a UK tax year, valued in GBP."""
    return calculate_uk_income(
        spot_transactions(_ensure_normalized_active_ledger()), tax_year_label=year
    )


@app.get("/api/tax-report/years")
def available_years() -> List[str] | List[int]:
    timestamps = [t.timestamp for t in active_transactions()]
    if _uk_jurisdiction():
        return available_tax_year_labels(timestamps)
    return sorted({us_calendar_year(ts) for ts in timestamps}, reverse=True)


@app.get("/api/tax-report/perps", response_model=PerpTaxSummary)
def tax_report_perps(
    year: str = Query(
        ...,
        description="UK tax-year label (e.g. 2024/25) or US calendar year (e.g. 2024).",
    ),
) -> PerpTaxSummary:
    """Perp realized PnL for a period, under the jurisdiction's perp treatment."""
    jurisdiction = state.tax_jurisdiction()
    treatment = state.perp_treatment(jurisdiction)
    return build_perp_tax_summary(
        _ensure_normalized_active_ledger(),
        jurisdiction=jurisdiction,
        treatment=treatment,
        period_label=year,
    )


def _csv_response(buffer: io.StringIO, filename: str) -> StreamingResponse:
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


def _safe_year_slug(year: str) -> str:
    return year.replace("/", "-")


def _download_uk_cgt(year: str) -> StreamingResponse:
    """Disposal detail (with HMRC match type) plus an SA108-ready summary."""
    report = _uk_cgt_report(tax_year_label=year)

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "Asset",
            "Quantity",
            "Disposal Date",
            "Acquisition Date",
            "Match Rule",
            "Proceeds (GBP)",
            "Allowable Cost (GBP)",
            "Gain/Loss (GBP)",
            "Missing Cost Basis",
        ]
    )
    for row in report.rows:
        writer.writerow(
            [
                row.asset,
                row.quantity,
                row.disposal_date.date().isoformat(),
                row.acquisition_date.date().isoformat() if row.acquisition_date else "",
                row.match_type.value,
                f"{row.proceeds:.2f}",
                f"{row.allowable_cost:.2f}",
                f"{row.gain:.2f}",
                "YES" if row.missing_cost_basis else "",
            ]
        )

    writer.writerow([])
    writer.writerow(["SA108 SUMMARY", year])
    writer.writerow(["Number of disposals", report.disposal_count])
    writer.writerow(["Total proceeds (GBP)", f"{report.total_proceeds:.2f}"])
    writer.writerow(["Total allowable costs (GBP)", f"{report.total_allowable_costs:.2f}"])
    writer.writerow(["Total gains (GBP)", f"{report.total_gains:.2f}"])
    writer.writerow(["Total losses (GBP)", f"{report.total_losses:.2f}"])
    writer.writerow(["Net gain (GBP)", f"{report.net_gain:.2f}"])
    writer.writerow(["Annual exempt amount (GBP)", f"{report.annual_exempt_amount:.2f}"])
    writer.writerow(
        ["Taxable gain after allowance (GBP)", f"{report.taxable_gain_after_allowance:.2f}"]
    )

    return _csv_response(buffer, f"uk_cgt_{_safe_year_slug(year)}.csv")


def _download_uk_income(year: str) -> StreamingResponse:
    """Crypto income (airdrops, staking) schedule — miscellaneous income, not CGT."""
    income = calculate_uk_income(
        spot_transactions(_ensure_normalized_active_ledger()), tax_year_label=year
    )

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Date", "Asset", "Type", "Quantity", "Value (GBP)"])
    for row in income.rows:
        writer.writerow(
            [
                row.date.date().isoformat(),
                row.asset,
                row.kind,
                row.quantity,
                f"{row.value_gbp:.2f}",
            ]
        )

    writer.writerow([])
    writer.writerow(["INCOME SUMMARY", year])
    writer.writerow(["Airdrop income (GBP)", f"{income.airdrop_income:.2f}"])
    writer.writerow(["Staking income (GBP)", f"{income.staking_income:.2f}"])
    writer.writerow(["Total income (GBP)", f"{income.total_income:.2f}"])

    return _csv_response(buffer, f"uk_crypto_income_{_safe_year_slug(year)}.csv")


def _download_us_form_8949(year: str, method: str) -> StreamingResponse:
    accounting = _resolve_method(method)
    report = _us_realized_report(accounting, tax_year=int(year))

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "Description (Form 8949, Col a)",
            "Date Acquired (Col b)",
            "Date Sold (Col c)",
            "Proceeds USD (Col d)",
            "Cost Basis USD (Col e)",
            "Gain/Loss USD (Col h)",
            "Term",
            "Holding Days",
            "Missing Cost Basis",
        ]
    )
    for row in report.rows:
        writer.writerow(
            [
                f"{row.quantity} {row.asset}",
                row.date_acquired.date().isoformat(),
                row.date_sold.date().isoformat(),
                f"{row.proceeds:.2f}",
                f"{row.cost_basis:.2f}",
                f"{row.gain_loss:.2f}",
                row.term,
                row.holding_period_days,
                "YES" if row.missing_cost_basis else "",
            ]
        )

    writer.writerow([])
    writer.writerow(["SUMMARY (USD)", "", "", "", "", "", "", "", ""])
    writer.writerow(
        [
            "Short-Term",
            "",
            "",
            f"{report.short_term_proceeds:.2f}",
            f"{report.short_term_cost_basis:.2f}",
            f"{report.short_term_gain:.2f}",
            "SHORT",
            "",
            "",
        ]
    )
    writer.writerow(
        [
            "Long-Term",
            "",
            "",
            f"{report.long_term_proceeds:.2f}",
            f"{report.long_term_cost_basis:.2f}",
            f"{report.long_term_gain:.2f}",
            "LONG",
            "",
            "",
        ]
    )
    writer.writerow(
        ["Total Gain/Loss", "", "", "", "", f"{report.total_gain:.2f}", "", "", ""]
    )
    writer.writerow(["Reporting currency", report.reporting_currency])

    return _csv_response(buffer, f"form_8949_{year}_{accounting.value}.csv")


def _download_perp_tax(year: str) -> StreamingResponse:
    """Perp realized-PnL schedule under the configured treatment."""
    jurisdiction = state.tax_jurisdiction()
    treatment = state.perp_treatment(jurisdiction)
    report = build_perp_tax_summary(
        _ensure_normalized_active_ledger(),
        jurisdiction=jurisdiction,
        treatment=treatment,
        period_label=year,
    )

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Date", "Contract", "Source", f"Realized PnL ({report.reporting_currency})", f"Fee ({report.reporting_currency})"])
    for row in report.rows:
        writer.writerow(
            [
                row.date.date().isoformat(),
                row.contract,
                row.source or "",
                f"{row.realized_pnl:.2f}",
                f"{row.fee:.2f}",
            ]
        )

    schedule = "Capital Gains" if treatment == "capital_gains" else "Trading Income"
    writer.writerow([])
    writer.writerow([f"PERP {schedule.upper()} SUMMARY", year])
    writer.writerow(["Treatment", treatment])
    writer.writerow(["Total realized PnL (GBP)", f"{report.total_realized_pnl:.2f}"])
    writer.writerow(["Total fees (GBP)", f"{report.total_fees:.2f}"])
    writer.writerow(["Gains (GBP)", f"{report.gains:.2f}"])
    writer.writerow(["Losses (GBP)", f"{report.losses:.2f}"])
    writer.writerow(["Net PnL (GBP)", f"{report.net_pnl:.2f}"])

    return _csv_response(buffer, f"perp_pnl_{_safe_year_slug(year)}.csv")


@app.get("/api/tax-report/download")
def download_tax_report(
    year: str = Query(...),
    method: str = Query("FIFO"),
    kind: str = Query("cgt", description="UK: 'cgt' (default), 'income'; or 'perps'."),
) -> StreamingResponse:
    """Download a tax schedule CSV for the year (UK CGT/income, US Form 8949, or perps)."""
    if kind == "perps":
        return _download_perp_tax(year)
    if _uk_jurisdiction():
        if kind == "income":
            return _download_uk_income(year)
        return _download_uk_cgt(year)
    return _download_us_form_8949(year, method)
