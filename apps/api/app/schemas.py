"""Pydantic v2 data models for the Crypto Tax Dashboard.

These models define the unified transaction schema used across the ingestion
engine, the tax engine, and the REST API responses.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class TransactionType(str, Enum):
    """The canonical set of transaction types supported by the engine."""

    BUY = "BUY"
    SELL = "SELL"
    AIRDROP = "AIRDROP"
    STAKING = "STAKING"
    FEE = "FEE"
    TRANSFER = "TRANSFER"


class AccountingMethod(str, Enum):
    """Supported cost-basis accounting methods."""

    FIFO = "FIFO"
    LIFO = "LIFO"
    HIFO = "HIFO"
    SECTION_104 = "SECTION_104"


# Transaction types that represent acquiring an asset (create cost-basis lots).
ACQUISITION_TYPES = {
    TransactionType.BUY,
    TransactionType.AIRDROP,
    TransactionType.STAKING,
}

# Transaction types that represent disposing of an asset (taxable events).
DISPOSAL_TYPES = {TransactionType.SELL, TransactionType.FEE}

# Transaction types that count as ordinary crypto income at fair-market value.
INCOME_TYPES = {TransactionType.AIRDROP, TransactionType.STAKING}


class Transaction(BaseModel):
    """Unified transaction schema.

    The base schema strictly contains the required fields. ``source`` is an
    optional ledger/exchange identifier used by the internal transfer matcher.
    """

    model_config = ConfigDict(use_enum_values=False, extra="ignore")

    id: str = Field(..., description="Stable unique identifier for the row.")
    timestamp: datetime = Field(..., description="ISO 8601 event timestamp.")
    asset: str = Field(..., description="Asset ticker, e.g. BTC, ETH.")
    transaction_type: TransactionType = Field(..., description="Event class.")
    amount: float = Field(..., ge=0, description="Quantity of the asset.")
    fiat_value_at_trigger: float = Field(
        ..., ge=0, description="Total fiat (USD) value of the event at its time."
    )
    fee_fiat: float = Field(
        default=0.0, ge=0, description="Fee in fiat associated with the event."
    )
    fiat_currency: Optional[str] = Field(
        default=None,
        description="ISO currency code for fiat_value_at_trigger and fee_fiat (e.g. GBP, USD).",
    )
    source: Optional[str] = Field(
        default=None, description="Originating ledger/exchange/wallet identifier."
    )
    import_id: Optional[str] = Field(
        default=None,
        description="Batch id linking rows to a specific CSV or wallet import.",
    )
    transfer_direction: Optional[Literal["IN", "OUT"]] = Field(
        default=None,
        description="For TRANSFER rows: IN = received from external wallet, OUT = sent out.",
    )
    transfer_pair_id: Optional[str] = Field(
        default=None,
        description="Links the OUT and IN legs of a matched internal wallet/exchange transfer.",
    )
    counterparty_address: Optional[str] = Field(
        default=None,
        description="Counterparty wallet or contract (from/to) for transfers and swaps.",
    )
    counter_asset: Optional[str] = Field(
        default=None,
        description="Quote asset in a trade (e.g. USDT when selling SOL for stables).",
    )
    counter_amount: Optional[float] = Field(
        default=None,
        ge=0,
        description="Quantity of counter_asset received or paid in a trade.",
    )
    trade_group_id: Optional[str] = Field(
        default=None,
        description="Exchange reference id grouping legs of the same trade.",
    )
    on_chain_tx_id: Optional[str] = Field(
        default=None,
        description="Full on-chain transaction id (EVM hash, Solana signature, etc.).",
    )
    token_mint: Optional[str] = Field(
        default=None,
        description="On-chain mint address when asset is resolved from an SPL token.",
    )
    instrument_kind: Optional[Literal["spot", "perp"]] = Field(
        default=None,
        description="spot (default) or perp for derivatives; perps are excluded from spot FIFO.",
    )
    instrument: Optional[str] = Field(
        default=None,
        description="Exchange contract id, e.g. SOL - USDC.",
    )
    venue_order_type: Optional[str] = Field(
        default=None,
        description="Exchange order type, e.g. LIMIT, MARKET, LIQUIDATE.",
    )
    realized_pnl: Optional[float] = Field(
        default=None,
        description="Exchange-reported realized PnL for perp closes (quote currency).",
    )
    event_subtype: Optional[str] = Field(
        default=None,
        description=(
            "Tax-aware subtype, e.g. lend_deposit, lend_withdraw, hard_fork, "
            "lp_add, lp_remove."
        ),
    )
    parent_asset: Optional[str] = Field(
        default=None,
        description="Parent ticker for hard-fork acquisitions (e.g. ETH for ETHW).",
    )
    normalization_note: Optional[str] = Field(
        default=None,
        description=(
            "Human-readable note when a row was synthesized or inferred during "
            "normalization (e.g. an LP burn reconstructed from a missing mint leg)."
        ),
    )

    @field_validator("asset")
    @classmethod
    def _normalize_asset(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("asset must be a non-empty string")
        # Solana mint addresses are case-sensitive base58 strings.
        if len(normalized) >= 32 and normalized.isalnum():
            return normalized
        return normalized.upper()

    @property
    def unit_price(self) -> float:
        """Fiat value per single unit of the asset for this event."""
        if self.amount <= 0:
            return 0.0
        return self.fiat_value_at_trigger / self.amount


# Sources whose rows are perps when no explicit instrument_kind was recorded
# (legacy imports before the field existed).
LEGACY_PERP_SOURCES = {"woox", "hyperliquid", "variational", "drift"}


def is_perp_transaction(tx: Transaction) -> bool:
    """True for perpetual-futures rows (kept out of spot FIFO / holdings)."""
    if tx.instrument_kind == "perp":
        return True
    # An explicit spot tag always wins (e.g. Hyperliquid spot markets).
    if tx.instrument_kind == "spot":
        return False
    # Legacy imports before instrument_kind was stored: infer from source.
    if tx.source in LEGACY_PERP_SOURCES:
        return True
    return False


def spot_transactions(transactions: List[Transaction]) -> List[Transaction]:
    return [t for t in transactions if not is_perp_transaction(t)]


def perp_transactions(transactions: List[Transaction]) -> List[Transaction]:
    return [t for t in transactions if is_perp_transaction(t)]


class TransactionCreate(BaseModel):
    """Payload for appending a new transaction. ``id`` is server-generated."""

    timestamp: datetime
    asset: str
    transaction_type: TransactionType
    amount: float = Field(..., ge=0)
    fiat_value_at_trigger: float = Field(..., ge=0)
    fee_fiat: float = Field(default=0.0, ge=0)
    source: Optional[str] = None


class Form8949Row(BaseModel):
    """A single disposal line in the IRS Form 8949 structure."""

    asset: str
    quantity: float
    date_acquired: datetime
    date_sold: datetime
    proceeds: float
    cost_basis: float
    gain_loss: float
    term: str = Field(..., description="'SHORT' or 'LONG'.")
    holding_period_days: int
    disposal_id: str
    lot_source_id: str
    missing_cost_basis: bool = False


class RealizedGainsSummary(BaseModel):
    """Aggregated realized capital-gains figures for a tax year."""

    tax_year: int
    method: AccountingMethod
    reporting_currency: str = "GBP"
    tax_jurisdiction: str = "UK"
    short_term_proceeds: float
    short_term_cost_basis: float
    short_term_gain: float
    long_term_proceeds: float
    long_term_cost_basis: float
    long_term_gain: float
    total_gain: float
    rows: List[Form8949Row]


class CgtMatchType(str, Enum):
    """HMRC share-matching rule applied to a disposal leg."""

    SAME_DAY = "same_day"
    THIRTY_DAY = "thirty_day"
    SECTION_104 = "section_104"
    UNMATCHED = "unmatched"
    # Synthetic row from exchange-reported perp PnL (not share-matched).
    PERP = "perp"


class CgtDisposalRow(BaseModel):
    """A single matched disposal leg under HMRC share-matching rules."""

    asset: str
    quantity: float
    disposal_date: datetime
    acquisition_date: Optional[datetime] = None
    proceeds: float
    allowable_cost: float
    gain: float
    match_type: CgtMatchType
    disposal_id: str
    acquisition_ids: List[str] = []
    missing_cost_basis: bool = False


class UkCgtSummary(BaseModel):
    """HMRC Capital Gains Tax summary for a UK tax year (or lifetime)."""

    tax_year_label: Optional[str] = None
    reporting_currency: str = "GBP"
    tax_jurisdiction: str = "UK"
    total_proceeds: float = 0.0
    total_allowable_costs: float = 0.0
    total_gains: float = 0.0
    total_losses: float = 0.0
    net_gain: float = 0.0
    disposal_count: int = 0
    annual_exempt_amount: float = 0.0
    taxable_gain_after_allowance: float = 0.0
    rows: List[CgtDisposalRow] = []


class UkIncomeRow(BaseModel):
    """A single crypto-income event (airdrop or staking) valued in GBP."""

    date: datetime
    asset: str
    kind: str  # AIRDROP | STAKING
    quantity: float
    value_gbp: float
    tx_id: str


class UkIncomeSummary(BaseModel):
    """Crypto income (miscellaneous income, not CGT) for a UK tax year."""

    tax_year_label: Optional[str] = None
    reporting_currency: str = "GBP"
    total_income: float = 0.0
    airdrop_income: float = 0.0
    staking_income: float = 0.0
    rows: List[UkIncomeRow] = []


class PerpTaxRow(BaseModel):
    """A single perp realized-PnL event valued in the reporting currency."""

    date: datetime
    contract: str
    asset: str
    source: Optional[str] = None
    realized_pnl: float  # reporting currency (GBP), net of fees
    fee: float = 0.0
    tx_id: str


class PerpTaxSummary(BaseModel):
    """Perp PnL for a tax year under the chosen treatment (income or CGT)."""

    period_label: Optional[str] = None
    treatment: str = "income"  # exclude | income | capital_gains
    tax_jurisdiction: str = "UK"
    reporting_currency: str = "GBP"
    total_realized_pnl: float = 0.0
    total_fees: float = 0.0
    net_pnl: float = 0.0
    gains: float = 0.0
    losses: float = 0.0
    event_count: int = 0
    rows: List[PerpTaxRow] = []


class AssetLabel(BaseModel):
    """Display metadata for a ledger asset key."""

    symbol: str
    name: str
    mint: Optional[str] = None


class MissingCostBasisFlag(BaseModel):
    """Raised when a disposal cannot be fully matched to acquisitions."""

    disposal_id: str
    asset: str
    timestamp: datetime
    disposed_amount: float
    uncovered_amount: float
    message: str


class OrphanedInflowFlag(BaseModel):
    """Inbound transfer/deposit without explainable historical acquisition data."""

    transaction_id: str
    asset: str
    timestamp: datetime
    quantity: float
    source: Optional[str] = None
    import_id: Optional[str] = None
    fiat_value_at_trigger: float = 0.0
    message: str
    has_override: bool = False


class ManualCostBasisOverride(BaseModel):
    """User-supplied acquisition data for an orphaned inflow batch."""

    anchor_transaction_id: str
    asset: str
    quantity: float
    acquisition_date: datetime
    unit_cost: float
    total_fiat_spent: float
    reporting_currency: str = "GBP"
    notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class ManualCostBasisOverrideCreate(BaseModel):
    """Payload to create or update a manual cost-basis override."""

    acquisition_date: datetime
    unit_cost: Optional[float] = Field(default=None, ge=0)
    total_fiat_spent: Optional[float] = Field(default=None, ge=0)
    notes: Optional[str] = None


class LpInferenceFlag(BaseModel):
    """An LP disposal that was reconstructed because the on-chain burn was missing."""

    transaction_id: str
    asset: str
    timestamp: datetime
    quantity: float
    proceeds: float
    ambiguous: bool = False
    message: str


class DataHealthSummary(BaseModel):
    """Data Health Ledger scan results and saved manual overrides."""

    orphaned_inflows: List[OrphanedInflowFlag] = Field(default_factory=list)
    cost_basis_overrides: List[ManualCostBasisOverride] = Field(default_factory=list)
    lp_inference_notes: List[LpInferenceFlag] = Field(default_factory=list)


class Position(BaseModel):
    """Current holdings and PnL for a single asset."""

    asset: str
    quantity: float
    average_cost_basis: float
    current_price: float
    total_invested: float
    current_value: float
    unrealized_pnl: float
    unrealized_pnl_pct: float
    realized_income: float


class RealizedPnlRow(BaseModel):
    """Lifetime realized capital gains aggregated per asset."""

    asset: str
    disposal_count: int
    quantity_disposed: float
    proceeds: float
    cost_basis: float
    realized_pnl: float
    realized_pnl_pct: float


class PnlOpenLotLine(BaseModel):
    """An open acquisition lot contributing to unrealized P&L."""

    transaction_id: str
    quantity: float
    cost_basis: float
    current_value: float
    unrealized_pnl: float
    acquired_at: datetime
    is_pooled: bool = Field(
        default=False,
        description="True for UK Section 104 pool aggregate (not a single trade).",
    )


class PnlRealizedDisposalLine(BaseModel):
    """A disposal (sell/swap) contributing to realized P&L."""

    transaction_id: str
    quantity: float
    proceeds: float
    cost_basis: float
    gain_loss: float
    disposed_at: datetime


class AssetPnlDetail(BaseModel):
    """Drill-down lines for one asset's P&L."""

    asset: str
    open_lots: List[PnlOpenLotLine] = Field(default_factory=list)
    disposals: List[PnlRealizedDisposalLine] = Field(default_factory=list)


class PnlBreakdown(BaseModel):
    """Per-asset open lots and realized disposals for dashboard drill-down."""

    by_asset: Dict[str, AssetPnlDetail] = Field(default_factory=dict)


class TaxHarvestRow(BaseModel):
    """A single row in the tax-loss-harvesting matrix (losers only)."""

    asset: str
    current_bags: float
    current_value: float
    unrealized_loss: float
    potential_tax_savings: float
    # UK: loss sliced across unused basic-rate band then higher rate.
    basic_rate_loss: float = 0.0
    higher_rate_loss: float = 0.0
    # US: unrealised PnL on short- vs long-term lots if sold today (losses ≥ 0).
    short_term_loss: float = 0.0
    long_term_loss: float = 0.0


class IncomeSummary(BaseModel):
    """Ordinary crypto income from airdrops and staking."""

    total_income: float
    airdrop_income: float
    staking_income: float


class HoldingRow(BaseModel):
    """An open (unsold) position for the holdings breakdown."""

    asset: str
    quantity: float
    average_cost_basis: float
    current_value: float
    total_invested: float
    portfolio_pct: float
    is_stablecoin: bool = False
    price_source: Optional[str] = None  # market | live | dex | illiquid | cost_basis
    is_estimated: bool = False
    unrealized_pnl: float = 0.0
    unrealized_pnl_pct: float = 0.0


class PerpsSummary(BaseModel):
    """Aggregated perpetual-futures activity (separate from spot portfolio)."""

    trade_count: int = 0
    closed_pnl: float = 0.0
    total_fees: float = 0.0
    total_notional: float = 0.0
    winning_closes: int = 0
    losing_closes: int = 0


class PortfolioSummary(BaseModel):
    """The full dashboard payload."""

    total_portfolio_value: float
    total_invested: float
    total_unrealized_gain: float
    total_realized_gain: float
    income_summary: IncomeSummary
    positions: List[Position]
    holdings: List[HoldingRow] = []
    tax_harvest: List[TaxHarvestRow]
    realized_pnl: List[RealizedPnlRow] = []
    missing_cost_basis: List[MissingCostBasisFlag]
    method: AccountingMethod
    reporting_currency: str = "GBP"
    display_currency: str = "GBP"
    tax_jurisdiction: str = "UK"
    # Effective blended savings rate for this harvest set (savings / loss).
    tax_harvest_rate: float = 0.24
    # Rate schedule used for the estimate (fractions, e.g. 0.18).
    tax_harvest_basic_rate: float = 0.18
    tax_harvest_higher_rate: float = 0.24
    tax_harvest_ordinary_rate: float = 0.24
    tax_harvest_ltcg_rate: float = 0.15
    tax_harvest_unused_basic_band: float = 0.0
    perps: PerpsSummary = Field(default_factory=PerpsSummary)


class TransferMatchResult(BaseModel):
    """Reports how many internal transfers were reclassified."""

    matched_pairs: int
    reclassified_transaction_ids: List[str]
    message: str


class PriceUpdate(BaseModel):
    """Payload to set the current market price of an asset."""

    asset: str
    price: float = Field(..., ge=0)


class ImportSourceView(BaseModel):
    """One disconnectable import source shown in the dashboard."""

    id: str
    kind: Literal["csv", "wallet", "legacy", "demo"]
    label: str
    chain: Optional[str] = None
    address: Optional[str] = None
    imported_at: Optional[datetime] = None
    transaction_count: int = 0
    is_unlabeled: bool = False
    source_hint: Optional[str] = None
    parser_label: Optional[str] = None
    date_start: Optional[datetime] = None
    date_end: Optional[datetime] = None
    coverage_start: Optional[datetime] = None
    coverage_end: Optional[datetime] = None
    data_start: Optional[datetime] = None
    data_end: Optional[datetime] = None
    coverage_from: Literal["export_filter", "transactions"] = "transactions"
    export_kind: Optional[str] = None


class ImportSnippetView(BaseModel):
    """Sample CSV rows or ledger fallback for a connected import."""

    columns: List[str] = Field(default_factory=list)
    rows: List[List[str]] = Field(default_factory=list)
    total_rows: int = 0
    total_columns: int = 0
    truncated_columns: bool = False
    preview_from: Literal["csv_file", "ledger"] = "csv_file"
    note: Optional[str] = None


class ImportFilePreview(BaseModel):
    """Detected parser and date span for a file before import."""

    filename: str
    parser: Optional[str] = None
    parser_label: Optional[str] = None
    transaction_count: int = 0
    date_start: Optional[datetime] = None
    date_end: Optional[datetime] = None
    coverage_start: Optional[datetime] = None
    coverage_end: Optional[datetime] = None
    data_start: Optional[datetime] = None
    data_end: Optional[datetime] = None
    coverage_from: Literal["export_filter", "transactions"] = "transactions"
    error: Optional[str] = None
    coverage_gaps: List["CoverageGapView"] = Field(default_factory=list)
    duplicate_count: int = 0
    duplicate_import_labels: List[str] = Field(default_factory=list)
    export_kind: Optional[str] = None
    csv_columns: List[str] = Field(default_factory=list)
    csv_sample_rows: List[List[str]] = Field(default_factory=list)
    csv_total_rows: int = 0
    csv_total_columns: int = 0
    csv_truncated_columns: bool = False


class CoverageGapView(BaseModel):
    """A span with no transactions that may indicate a missing export."""

    kind: Literal["ledger", "preview"]
    source_label: str
    source_slug: Optional[str] = None
    gap_start: datetime
    gap_end: datetime
    gap_days: int
    import_ids: List[str] = Field(default_factory=list)
    import_labels: List[str] = Field(default_factory=list)
    message: str


class ImportOverlapView(BaseModel):
    """Overlapping import coverage or redundant re-imports."""

    kind: Literal["coverage", "redundant_import"]
    import_ids: List[str] = Field(default_factory=list)
    import_labels: List[str] = Field(default_factory=list)
    parser_label: Optional[str] = None
    overlap_start: Optional[datetime] = None
    overlap_end: Optional[datetime] = None
    overlap_days: int = 0
    shared_transactions: int = 0
    same_platform: bool = False
    duplicate_count: int = 1
    message: str


class MexcEmailImportRequest(BaseModel):
    """Paste one or more MEXC notification emails."""

    text: str = Field(..., min_length=1)
    commit: bool = Field(
        default=False,
        description="When true, append parsed rows to the ledger.",
    )


class MexcEmailImportResponse(BaseModel):
    transactions: List[Transaction] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    skipped_blocks: List[str] = Field(default_factory=list)
    csv: str = ""
    imported: int = 0
    skipped_duplicates: int = 0
    message: Optional[str] = None


class ImportPreviewResponse(BaseModel):
    files: List[ImportFilePreview]
    coverage_gaps: List[CoverageGapView] = Field(default_factory=list)
    import_overlaps: List[ImportOverlapView] = Field(default_factory=list)


class LabelImportRequest(BaseModel):
    """Name an unlabeled import or rename a tracked one."""

    label: str = Field(..., min_length=1, max_length=200)
    kind: Optional[Literal["csv", "wallet"]] = Field(
        default=None,
        description="For legacy imports: whether this was a CSV or wallet fetch.",
    )


class ScamAssetRequest(BaseModel):
    """Mark or unmark a ledger asset key as a scam token."""

    asset: str = Field(..., min_length=1, max_length=128)


class TaxSettingsUpdate(BaseModel):
    """Dashboard tax settings (jurisdiction + perp treatment)."""

    data_mode: Optional[str] = Field(
        default=None, description="live = imported ledger; demo = bundled sample data"
    )
    tax_jurisdiction: Optional[str] = Field(default=None, description="UK or US")
    uk_perp_treatment: Optional[str] = Field(
        default=None, description="exclude | income | capital_gains"
    )
    us_perp_treatment: Optional[str] = Field(
        default=None, description="exclude | income | capital_gains"
    )
    uk_unused_basic_band: Optional[float] = Field(
        default=None,
        ge=0,
        description=(
            "Unused UK basic-rate Income Tax band (GBP) for CGT rate banding "
            "on harvest estimates."
        ),
    )
    us_ordinary_income_rate: Optional[float] = Field(
        default=None,
        ge=0,
        le=1,
        description="Illustrative US ordinary / short-term rate (0–1).",
    )
    us_long_term_cg_rate: Optional[float] = Field(
        default=None,
        ge=0,
        le=1,
        description="Illustrative US long-term capital gains rate (0–1).",
    )


class WalletImportRequest(BaseModel):
    """Import on-chain history for a wallet address (chain auto-detected)."""

    address: str = Field(..., min_length=14, max_length=120)
    chain: Optional[str] = Field(
        default=None,
        description="Optional override; inferred from address when omitted.",
    )

    @model_validator(mode="after")
    def _resolve_chain(self) -> "WalletImportRequest":
        from .wallet_detect import resolve_wallet_import

        normalized, chosen = resolve_wallet_import(self.address, self.chain)
        self.address = normalized
        self.chain = chosen
        return self
