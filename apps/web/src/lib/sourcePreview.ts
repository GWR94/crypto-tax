import {
  getSourceDefinition,
  normalizeSourceId,
  type SourceDefinition,
} from "@/lib/sourceCatalog";
import type { ImportSource, Transaction } from "@/lib/types";
import { WALLET_CHAIN_LABELS, WALLET_SOURCE_HINTS } from "@/lib/walletDetect";
import { shortenAddress } from "@/lib/utils";

const EXPORT_KIND_LABELS: Record<string, string> = {
  transfers: "Transfers",
  trades: "Trades",
  orders: "Order history",
  transactions: "Transaction history",
  swaps: "Swaps",
  defi: "DeFi activity",
  funding: "Funding",
  ledger: "Ledger",
  deposits: "Deposits",
  withdrawals: "Withdrawals",
};

export function exportKindLabel(kind?: string | null): string | null {
  if (!kind) return null;
  return EXPORT_KIND_LABELS[kind] ?? kind.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export function csvImportTypeLabel(source: ImportSource): string | null {
  const kind = exportKindLabel(source.export_kind);
  if (!kind || !source.parser_label) return kind;
  return `${source.parser_label} · ${kind}`;
}

export function importsForLedgerSource(
  sourceId: string,
  importSources: ImportSource[]
): ImportSource[] {
  const id = normalizeSourceId(sourceId);
  const def = getSourceDefinition(id);
  return importSources.filter((source) => importMatchesLedgerSource(source, id, def));
}

function importMatchesLedgerSource(
  source: ImportSource,
  sourceId: string,
  def: SourceDefinition
): boolean {
  if (normalizeSourceId(source.chain) === sourceId) return true;
  if (normalizeSourceId(source.source_hint) === sourceId) return true;
  if (source.parser_label?.toLowerCase() === def.label.toLowerCase()) return true;
  if (source.kind === "legacy" && source.label.toLowerCase() === sourceId) return true;
  return false;
}

export function describeImportSource(source: ImportSource): string {
  if (source.kind === "wallet") {
    const chainLabel =
      (source.chain && WALLET_CHAIN_LABELS[source.chain as keyof typeof WALLET_CHAIN_LABELS]) ||
      source.parser_label ||
      source.chain ||
      "wallet";
    if (source.address) {
      return `On-chain activity fetched from ${chainLabel} address ${shortenAddress(source.address)}`;
    }
    return `On-chain ${chainLabel} wallet import`;
  }

  if (source.kind === "csv" && source.parser_label) {
    const kind = exportKindLabel(source.export_kind);
    if (kind) {
      return `${source.parser_label} ${kind} CSV import`;
    }
    return `${source.parser_label} CSV file import`;
  }

  if (source.kind === "legacy") {
    const hint = source.source_hint ?? "unknown";
    if (WALLET_SOURCE_HINTS.has(hint)) {
      const label = getSourceDefinition(hint).label;
      return `Older ${label} import (wallet fetch or CSV — add a name to track it)`;
    }
    return `Older ${source.parser_label ?? hint} CSV import`;
  }

  if (source.kind === "demo") {
    return "Bundled demo transactions for trying the dashboard";
  }

  return "Imported transaction data";
}

export function ledgerSourceSummary(
  sourceId: string,
  importSources: ImportSource[]
): string | null {
  const imports = importsForLedgerSource(sourceId, importSources);
  if (!imports.length) return null;

  const parts = imports.map((source) => {
    if (source.kind === "wallet" && source.address) {
      return `${source.label} (${shortenAddress(source.address)})`;
    }
    return source.label;
  });

  if (parts.length === 1) return `From import: ${parts[0]}`;
  return `From ${parts.length} imports: ${parts.slice(0, 2).join(", ")}${
    parts.length > 2 ? ` +${parts.length - 2} more` : ""
  }`;
}

export function sampleTransactionsForSource(
  transactions: Transaction[],
  sourceId: string,
  limit = 3
): Transaction[] {
  const id = normalizeSourceId(sourceId);
  return [...transactions]
    .filter((tx) => normalizeSourceId(tx.source) === id)
    .sort((a, b) => b.timestamp.localeCompare(a.timestamp))
    .slice(0, limit);
}

export function formatImportDateRange(
  start?: string | null,
  end?: string | null
): string | null {
  if (!start && !end) return null;
  const fmt = (iso: string) =>
    new Date(iso).toLocaleDateString(undefined, { dateStyle: "medium" });
  if (start && end) {
    if (start.slice(0, 10) === end.slice(0, 10)) return fmt(start);
    return `${fmt(start)} – ${fmt(end)}`;
  }
  return fmt(start ?? end!);
}

export function formatImportCoverageLabel(
  source: Pick<
    ImportSource,
    | "coverage_start"
    | "coverage_end"
    | "data_start"
    | "data_end"
    | "coverage_from"
    | "date_start"
    | "date_end"
  >
): string | null {
  const coverageRange = formatImportDateRange(
    source.coverage_start ?? source.date_start,
    source.coverage_end ?? source.date_end
  );
  if (!coverageRange) return null;

  const dataRange = formatImportDateRange(source.data_start, source.data_end);
  const sameAsData =
    !dataRange ||
    ((source.coverage_start ?? source.date_start)?.slice(0, 10) ===
      source.data_start?.slice(0, 10) &&
      (source.coverage_end ?? source.date_end)?.slice(0, 10) ===
        source.data_end?.slice(0, 10));

  if (source.coverage_from === "export_filter") {
    if (sameAsData) {
      return `Export covers ${coverageRange}`;
    }
    return `Export covers ${coverageRange} · rows ${dataRange}`;
  }

  if (sameAsData) {
    return `Covers ${coverageRange}`;
  }
  return `Covers ${coverageRange} · rows ${dataRange}`;
}
