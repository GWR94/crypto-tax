export type TransactionType =
  | "BUY"
  | "SELL"
  | "AIRDROP"
  | "STAKING"
  | "FEE"
  | "TRANSFER";

export type TaxJurisdiction = "UK" | "US";

export type PerpTreatment = "exclude" | "income" | "capital_gains";

export type DataMode = "live" | "demo";

export interface TaxSettings {
  tax_jurisdiction: TaxJurisdiction;
  reporting_currency: string;
  data_mode?: DataMode;
  uk_perp_treatment?: PerpTreatment;
  us_perp_treatment?: PerpTreatment;
  /** Unused UK basic-rate band (GBP) for harvest CGT banding. */
  uk_unused_basic_band?: number;
  /** Illustrative US ordinary / short-term rate (0–1). */
  us_ordinary_income_rate?: number;
  /** Illustrative US long-term CG rate (0–1). */
  us_long_term_cg_rate?: number;
}

export type DisplayCurrency = "GBP" | "USD";

export type AccountingMethod = "FIFO" | "LIFO" | "HIFO" | "SECTION_104";

export interface AssetLabel {
  symbol: string;
  name: string;
  mint?: string | null;
}

export interface Transaction {
  id: string;
  timestamp: string;
  asset: string;
  transaction_type: TransactionType;
  amount: number;
  fiat_value_at_trigger: number;
  fee_fiat: number;
  fiat_currency: string | null;
  source: string | null;
  transfer_direction?: "IN" | "OUT" | null;
  counter_asset?: string | null;
  counter_amount?: number | null;
  counterparty_address?: string | null;
  trade_group_id?: string | null;
  on_chain_tx_id?: string | null;
  import_id?: string | null;
  token_mint?: string | null;
  instrument_kind?: "spot" | "perp" | null;
  instrument?: string | null;
  venue_order_type?: string | null;
  realized_pnl?: number | null;
  event_subtype?: string | null;
  parent_asset?: string | null;
}

export interface Position {
  asset: string;
  quantity: number;
  average_cost_basis: number;
  current_price: number;
  total_invested: number;
  current_value: number;
  unrealized_pnl: number;
  unrealized_pnl_pct: number;
  realized_income: number;
}

export interface HoldingRow {
  asset: string;
  quantity: number;
  average_cost_basis: number;
  current_value: number;
  total_invested: number;
  portfolio_pct: number;
  is_stablecoin: boolean;
  price_source?: string | null;
  is_estimated?: boolean;
  unrealized_pnl: number;
  unrealized_pnl_pct: number;
}

export interface RealizedPnlRow {
  asset: string;
  disposal_count: number;
  quantity_disposed: number;
  proceeds: number;
  cost_basis: number;
  realized_pnl: number;
  realized_pnl_pct: number;
}

export interface PnlOpenLotLine {
  transaction_id: string;
  quantity: number;
  cost_basis: number;
  current_value: number;
  unrealized_pnl: number;
  acquired_at: string;
  is_pooled?: boolean;
}

export interface PnlRealizedDisposalLine {
  transaction_id: string;
  quantity: number;
  proceeds: number;
  cost_basis: number;
  gain_loss: number;
  disposed_at: string;
}

export interface AssetPnlDetail {
  asset: string;
  open_lots: PnlOpenLotLine[];
  disposals: PnlRealizedDisposalLine[];
}

export interface PnlBreakdown {
  by_asset: Record<string, AssetPnlDetail>;
}

export interface TaxHarvestRow {
  asset: string;
  current_bags: number;
  current_value: number;
  unrealized_loss: number;
  potential_tax_savings: number;
  basic_rate_loss?: number;
  higher_rate_loss?: number;
  short_term_loss?: number;
  long_term_loss?: number;
}

export interface IncomeSummary {
  total_income: number;
  airdrop_income: number;
  staking_income: number;
}

export interface MissingCostBasisFlag {
  disposal_id: string;
  asset: string;
  timestamp: string;
  disposed_amount: number;
  uncovered_amount: number;
  message: string;
}

export interface OrphanedInflowFlag {
  transaction_id: string;
  asset: string;
  timestamp: string;
  quantity: number;
  source: string | null;
  import_id: string | null;
  fiat_value_at_trigger: number;
  message: string;
  has_override: boolean;
}

export interface ManualCostBasisOverride {
  anchor_transaction_id: string;
  asset: string;
  quantity: number;
  acquisition_date: string;
  unit_cost: number;
  total_fiat_spent: number;
  reporting_currency: string;
  notes?: string | null;
  created_at: string;
  updated_at: string;
}

export interface LpInferenceFlag {
  transaction_id: string;
  asset: string;
  timestamp: string;
  quantity: number;
  proceeds: number;
  ambiguous: boolean;
  message: string;
}

export interface DataHealthSummary {
  orphaned_inflows: OrphanedInflowFlag[];
  cost_basis_overrides: ManualCostBasisOverride[];
  lp_inference_notes?: LpInferenceFlag[];
}

export interface ManualCostBasisOverrideInput {
  acquisition_date: string;
  unit_cost?: number;
  total_fiat_spent?: number;
  notes?: string;
}

export interface PerpsSummary {
  trade_count: number;
  closed_pnl: number;
  total_fees: number;
  total_notional: number;
  winning_closes: number;
  losing_closes: number;
}

export type StakingEventKind =
  | "reward"
  | "unstake"
  | "liquid_stake"
  | "liquid_unstake";

export interface StakingEvent {
  id: string;
  kind: StakingEventKind;
  timestamp: string;
  asset: string;
  source: string | null;
  principal_amount?: number;
  reward_amount?: number;
  reward_asset?: string;
  staked_amount?: number;
  lst_asset?: string;
  lst_amount?: number;
  income: number;
  fiat_currency: string | null;
  counterparty?: string | null;
  trade_group_id?: string | null;
  transaction_ids: string[];
}

export interface StakingPosition {
  asset: string;
  net_amount: number;
  kind: "liquid_staking";
  total_income: number;
}

export interface StakingSummary {
  total_income: number;
  reward_count: number;
  unstake_count: number;
  liquid_stake_count: number;
  liquid_unstake_count: number;
  event_count: number;
  hidden_dust_count: number;
  total_staked_lst: number;
  positions: StakingPosition[];
  income_by_asset: Record<string, number>;
  events: StakingEvent[];
}

export interface PortfolioSummary {
  total_portfolio_value: number;
  total_invested: number;
  total_unrealized_gain: number;
  total_realized_gain: number;
  income_summary: IncomeSummary;
  positions: Position[];
  holdings: HoldingRow[];
  tax_harvest: TaxHarvestRow[];
  realized_pnl?: RealizedPnlRow[];
  missing_cost_basis: MissingCostBasisFlag[];
  method: AccountingMethod;
  reporting_currency: string;
  display_currency: DisplayCurrency;
  tax_jurisdiction?: TaxJurisdiction;
  /** Blended effective harvest savings rate (savings / loss). */
  tax_harvest_rate?: number;
  tax_harvest_basic_rate?: number;
  tax_harvest_higher_rate?: number;
  tax_harvest_ordinary_rate?: number;
  tax_harvest_ltcg_rate?: number;
  tax_harvest_unused_basic_band?: number;
  perps?: PerpsSummary;
}

export interface Form8949Row {
  asset: string;
  quantity: number;
  date_acquired: string;
  date_sold: string;
  proceeds: number;
  cost_basis: number;
  gain_loss: number;
  term: "SHORT" | "LONG";
  holding_period_days: number;
  disposal_id: string;
  lot_source_id: string;
  missing_cost_basis: boolean;
}

export interface RealizedGainsSummary {
  tax_year: number;
  method: AccountingMethod;
  reporting_currency: string;
  tax_jurisdiction?: TaxJurisdiction;
  short_term_proceeds: number;
  short_term_cost_basis: number;
  short_term_gain: number;
  long_term_proceeds: number;
  long_term_cost_basis: number;
  long_term_gain: number;
  total_gain: number;
  rows: Form8949Row[];
}

export type CgtMatchType =
  | "same_day"
  | "thirty_day"
  | "section_104"
  | "unmatched"
  | "perp";

export interface CgtDisposalRow {
  asset: string;
  quantity: number;
  disposal_date: string;
  acquisition_date: string | null;
  proceeds: number;
  allowable_cost: number;
  gain: number;
  match_type: CgtMatchType;
  disposal_id: string;
  acquisition_ids: string[];
  missing_cost_basis: boolean;
}

export interface UkCgtSummary {
  tax_year_label: string | null;
  reporting_currency: string;
  tax_jurisdiction: "UK";
  total_proceeds: number;
  total_allowable_costs: number;
  total_gains: number;
  total_losses: number;
  net_gain: number;
  disposal_count: number;
  annual_exempt_amount: number;
  taxable_gain_after_allowance: number;
  rows: CgtDisposalRow[];
}

export interface PerpTaxRow {
  date: string;
  contract: string;
  asset: string;
  source?: string | null;
  realized_pnl: number;
  fee: number;
  tx_id: string;
}

export interface PerpTaxSummary {
  period_label: string | null;
  treatment: PerpTreatment;
  tax_jurisdiction: TaxJurisdiction;
  reporting_currency: string;
  total_realized_pnl: number;
  total_fees: number;
  net_pnl: number;
  gains: number;
  losses: number;
  event_count: number;
  rows: PerpTaxRow[];
}

export type TaxReport = RealizedGainsSummary | UkCgtSummary;

export function isUkCgtReport(report: TaxReport): report is UkCgtSummary {
  return (report as UkCgtSummary).net_gain !== undefined;
}

export interface ImportResult {
  imported: number;
  total: number;
  demo_removed?: number;
  skipped_duplicates?: number;
  errors?: string[];
  files?: {
    filename: string;
    imported: number;
    added?: number;
    skipped_duplicates?: number;
    duplicate_import_labels?: string[];
  }[];
}

export type WalletChain =
  | "solana"
  | "ethereum"
  | "bitcoin"
  | "cardano"
  | "celestia";

export interface WalletImportResult {
  imported: number;
  total: number;
  demo_removed?: number;
  address?: string;
  chain?: WalletChain;
  message?: string;
}

export interface TransferMatchResult {
  matched_pairs: number;
  reclassified_transaction_ids: string[];
  message: string;
}

export type ImportSourceKind = "csv" | "wallet" | "legacy" | "demo";

export interface ImportSnippet {
  columns: string[];
  rows: string[][];
  total_rows: number;
  total_columns: number;
  truncated_columns: boolean;
  preview_from: "csv_file" | "ledger";
  note?: string | null;
}

export interface ImportSource {
  id: string;
  kind: ImportSourceKind;
  label: string;
  chain?: string | null;
  address?: string | null;
  imported_at?: string | null;
  transaction_count: number;
  is_unlabeled?: boolean;
  source_hint?: string | null;
  parser_label?: string | null;
  date_start?: string | null;
  date_end?: string | null;
  coverage_start?: string | null;
  coverage_end?: string | null;
  data_start?: string | null;
  data_end?: string | null;
  coverage_from?: "export_filter" | "transactions" | null;
  export_kind?: string | null;
}

export interface ImportFilePreview {
  filename: string;
  parser?: string | null;
  parser_label?: string | null;
  transaction_count: number;
  date_start?: string | null;
  date_end?: string | null;
  coverage_start?: string | null;
  coverage_end?: string | null;
  data_start?: string | null;
  data_end?: string | null;
  coverage_from?: "export_filter" | "transactions" | null;
  error?: string | null;
  coverage_gaps?: CoverageGap[];
  duplicate_count?: number;
  duplicate_import_labels?: string[];
  export_kind?: string | null;
  csv_columns?: string[];
  csv_sample_rows?: string[][];
  csv_total_rows?: number;
  csv_total_columns?: number;
  csv_truncated_columns?: boolean;
}

export interface MexcEmailImportResult {
  transactions: Transaction[];
  warnings: string[];
  skipped_blocks: string[];
  csv: string;
  imported: number;
  skipped_duplicates: number;
  message: string | null;
}

export interface ImportOverlap {
  kind: "coverage" | "redundant_import";
  import_ids: string[];
  import_labels: string[];
  parser_label?: string | null;
  overlap_start?: string | null;
  overlap_end?: string | null;
  overlap_days: number;
  shared_transactions: number;
  same_platform: boolean;
  duplicate_count?: number;
  message: string;
}

export interface CoverageGap {
  kind: "ledger" | "preview";
  source_label: string;
  source_slug?: string | null;
  gap_start: string;
  gap_end: string;
  gap_days: number;
  import_ids?: string[];
  import_labels?: string[];
  message: string;
}

export interface ImportPreviewResult {
  files: ImportFilePreview[];
  coverage_gaps?: CoverageGap[];
  import_overlaps?: ImportOverlap[];
}

export interface LabelImportResult {
  import_id: string;
  label: string;
  kind?: string;
  transaction_count?: number;
  message: string;
}

export interface DisconnectImportResult {
  import_id?: string;
  removed: number;
  total: number;
  disconnected?: number;
  message: string;
}

export interface ScamAssetsResponse {
  assets: string[];
}

export interface ScamAssetResult {
  asset: string;
  hidden: boolean;
  message: string;
}
