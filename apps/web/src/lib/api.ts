import type {
  AccountingMethod,
  AssetLabel,
  DisconnectImportResult,
  DisplayCurrency,
  ImportPreviewResult,
  ImportResult,
  CoverageGap,
  DataHealthSummary,
  ImportOverlap,
  ImportSnippet,
  ImportSource,
  LabelImportResult,
  PerpTaxSummary,
  PerpTreatment,
  ManualCostBasisOverride,
  ManualCostBasisOverrideInput,
  PnlBreakdown,
  PortfolioSummary,
  TaxJurisdiction,
  TaxReport,
  TaxSettings,
  DataMode,
  ScamAssetResult,
  ScamAssetsResponse,
  Transaction,
  TransferMatchResult,
  WalletChain,
  WalletImportResult,
  MexcEmailImportResult,
} from "./types";
import { formatApiError } from "./utils";

const BASE = "/api";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(formatApiError(detail || res.statusText, res.status));
  }
  return (await res.json()) as T;
}

export const api = {
  getSettings(): Promise<TaxSettings> {
    return request<TaxSettings>("/settings");
  },

  updateSettings(update: {
    data_mode?: DataMode;
    tax_jurisdiction?: TaxJurisdiction;
    uk_perp_treatment?: PerpTreatment;
    us_perp_treatment?: PerpTreatment;
  }): Promise<TaxSettings> {
    return request<TaxSettings>("/settings", {
      method: "PATCH",
      body: JSON.stringify(update),
    });
  },

  getPortfolio(
    method: AccountingMethod,
    displayCurrency: DisplayCurrency = "GBP",
    applyDustFilter = true
  ): Promise<PortfolioSummary> {
    const params = new URLSearchParams({
      method,
      display_currency: displayCurrency,
      apply_dust_filter: String(applyDustFilter),
    });
    return request<PortfolioSummary>(`/portfolio?${params.toString()}`);
  },

  getPnlBreakdown(
    method: AccountingMethod,
    excludeStaking = false
  ): Promise<PnlBreakdown> {
    const params = new URLSearchParams({ method });
    if (excludeStaking) {
      params.set("exclude_staking", "true");
    }
    return request<PnlBreakdown>(`/pnl-breakdown?${params.toString()}`);
  },

  getTransactions(): Promise<Transaction[]> {
    return request<Transaction[]>("/transactions");
  },

  getAssetLabels(): Promise<Record<string, AssetLabel>> {
    return request<Record<string, AssetLabel>>("/asset-labels");
  },

  cleanupSolanaPhantoms(): Promise<{ removed: number; message: string }> {
    return request<{ removed: number; message: string }>(
      "/transactions/cleanup-solana",
      { method: "POST" }
    );
  },

  refreshSolanaTokens(): Promise<{ tokens: number; message: string }> {
    return request<{ tokens: number; message: string }>(
      "/solana-tokens/refresh",
      { method: "POST" }
    );
  },

  fixMovements(): Promise<{ reclassified: number; message: string }> {
    return request<{ reclassified: number; message: string }>(
      "/transactions/fix-movements",
      { method: "POST" }
    );
  },

  backfillCostBasis(): Promise<{ updated: number; saved?: boolean; message: string }> {
    return request<{ updated: number; saved?: boolean; message: string }>(
      "/transactions/backfill-cost-basis",
      { method: "POST" }
    );
  },

  matchTransfers(): Promise<TransferMatchResult> {
    return request<TransferMatchResult>("/transactions/match-transfers", {
      method: "POST",
    });
  },

  deduplicateLedger(): Promise<{
    removed: number;
    remaining: number;
    message: string;
  }> {
    return request("/transactions/deduplicate", { method: "POST" });
  },

  getDemoStatus(): Promise<{
    count: number;
    active: boolean;
    mode: DataMode;
    persisted_demo_count: number;
  }> {
    return request<{
      count: number;
      active: boolean;
      mode: DataMode;
      persisted_demo_count: number;
    }>("/transactions/demo-status");
  },

  stripDemoData(): Promise<{ removed: number; total: number; message: string }> {
    return request<{ removed: number; total: number; message: string }>(
      "/transactions/strip-demo",
      { method: "POST" }
    );
  },

  async downloadLedgerBackup(): Promise<string> {
    const res = await fetch(`${BASE}/transactions/backup`);
    if (!res.ok) {
      const detail = await res.text();
      throw new Error(formatApiError(detail || res.statusText, res.status));
    }
    const disposition = res.headers.get("Content-Disposition") ?? "";
    const match = /filename="([^"]+)"/i.exec(disposition);
    const filename =
      match?.[1] ??
      `crypto-tax-ledger-backup-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-")}.json`;
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    return filename;
  },

  resetTransactions(): Promise<{ total: number; local_backup: string | null }> {
    return request<{ total: number; local_backup: string | null }>(
      "/transactions/reset",
      { method: "POST" }
    );
  },

  getAvailableYears(): Promise<Array<string | number>> {
    return request<Array<string | number>>("/tax-report/years");
  },

  getTaxReport(
    year: string | number,
    method: AccountingMethod
  ): Promise<TaxReport> {
    const params = new URLSearchParams({ year: String(year), method });
    return request<TaxReport>(`/tax-report?${params.toString()}`);
  },

  getPerpTaxReport(year: string | number): Promise<PerpTaxSummary> {
    const params = new URLSearchParams({ year: String(year) });
    return request<PerpTaxSummary>(`/tax-report/perps?${params.toString()}`);
  },

  downloadTaxReportUrl(
    year: string | number,
    method: AccountingMethod,
    kind: "cgt" | "income" | "perps" = "cgt"
  ): string {
    const params = new URLSearchParams({ year: String(year), method, kind });
    return `${BASE}/tax-report/download?${params.toString()}`;
  },

  async importFiles(
    files: File[],
    replace: boolean
  ): Promise<ImportResult> {
    const form = new FormData();
    for (const file of files) {
      form.append("files", file);
    }
    const res = await fetch(
      `${BASE}/transactions/import?replace=${replace}`,
      { method: "POST", body: form }
    );
    if (!res.ok) {
      const detail = await res.text();
      throw new Error(formatApiError(detail || res.statusText, res.status));
    }
    return (await res.json()) as ImportResult;
  },

  importFile(file: File, replace: boolean): Promise<ImportResult> {
    return this.importFiles([file], replace);
  },

  async previewFiles(files: File[]): Promise<ImportPreviewResult> {
    const form = new FormData();
    for (const file of files) {
      form.append("files", file);
    }
    const res = await fetch(`${BASE}/transactions/preview`, {
      method: "POST",
      body: form,
    });
    if (!res.ok) {
      const detail = await res.text();
      throw new Error(formatApiError(detail || res.statusText, res.status));
    }
    return (await res.json()) as ImportPreviewResult;
  },

  getHealth(): Promise<{
    wallet_import?: {
      solana?: boolean;
      ethereum?: boolean;
      bitcoin?: boolean;
      cardano?: boolean;
      celestia?: boolean;
      evm_chains?: string[];
      providers?: Partial<Record<WalletChain | "evm", string | null>>;
    };
  }> {
    return request("/health");
  },

  importWallet(
    address: string,
    replace: boolean
  ): Promise<WalletImportResult> {
    return request<WalletImportResult>(
      `/transactions/import-wallet?replace=${replace}`,
      {
        method: "POST",
        body: JSON.stringify({ address }),
      }
    );
  },

  importMexcEmails(
    text: string,
    commit: boolean
  ): Promise<MexcEmailImportResult> {
    return request<MexcEmailImportResult>("/transactions/import-mexc-emails", {
      method: "POST",
      body: JSON.stringify({ text, commit }),
    });
  },

  getImportSources(): Promise<ImportSource[]> {
    return request<ImportSource[]>("/import-sources");
  },

  getCoverageGaps(): Promise<CoverageGap[]> {
    return request<CoverageGap[]>("/coverage-gaps");
  },

  getImportOverlaps(): Promise<ImportOverlap[]> {
    return request<ImportOverlap[]>("/import-overlaps");
  },

  getDataHealth(): Promise<DataHealthSummary> {
    return request<DataHealthSummary>("/data-health");
  },

  upsertCostBasisOverride(
    anchorTransactionId: string,
    payload: ManualCostBasisOverrideInput
  ): Promise<ManualCostBasisOverride> {
    return request<ManualCostBasisOverride>(
      `/cost-basis-overrides/${encodeURIComponent(anchorTransactionId)}`,
      {
        method: "PUT",
        body: JSON.stringify(payload),
      }
    );
  },

  deleteCostBasisOverride(anchorTransactionId: string): Promise<{ deleted: boolean }> {
    return request<{ deleted: boolean }>(
      `/cost-basis-overrides/${encodeURIComponent(anchorTransactionId)}`,
      { method: "DELETE" }
    );
  },

  getImportSourceSnippet(importId: string): Promise<ImportSnippet> {
    return request<ImportSnippet>(`/import-sources/${importId}/snippet`);
  },

  labelImportSource(
    importId: string,
    label: string,
    kind?: "csv" | "wallet"
  ): Promise<LabelImportResult> {
    return request<LabelImportResult>(`/import-sources/${importId}`, {
      method: "PATCH",
      body: JSON.stringify({ label, kind }),
    });
  },

  disconnectImportSource(importId: string): Promise<DisconnectImportResult> {
    return request<DisconnectImportResult>(`/import-sources/${importId}`, {
      method: "DELETE",
    });
  },

  disconnectRedundantImports(): Promise<DisconnectImportResult> {
    return request<DisconnectImportResult>("/import-sources/redundant/bulk", {
      method: "DELETE",
    });
  },

  disconnectImportSourcesByKind(
    kind: "csv" | "wallet"
  ): Promise<DisconnectImportResult> {
    return request<DisconnectImportResult>(
      `/import-sources/disconnect-bulk?kind=${kind}`,
      { method: "DELETE" }
    );
  },

  getScamAssets(): Promise<ScamAssetsResponse> {
    return request<ScamAssetsResponse>("/scam-assets");
  },

  markScamAsset(asset: string): Promise<ScamAssetResult> {
    return request<ScamAssetResult>("/scam-assets", {
      method: "POST",
      body: JSON.stringify({ asset }),
    });
  },

  unmarkScamAsset(asset: string): Promise<ScamAssetResult> {
    return request<ScamAssetResult>("/scam-assets", {
      method: "DELETE",
      body: JSON.stringify({ asset }),
    });
  },
};
