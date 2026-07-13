import {
  formatImportCoverageLabel,
  formatImportDateRange,
  ledgerSourceSummary,
} from "@/lib/sourcePreview";
import { getSourceDefinition, normalizeSourceId } from "@/lib/sourceCatalog";
import type {
  CoverageGap,
  ImportOverlap,
  ImportSource,
  MissingCostBasisFlag,
  Transaction,
} from "@/lib/types";

export type MissingDataKind =
  | "coverage_gap"
  | "missing_cost_basis"
  | "import_overlap"
  | "redundant_import"
  | "unlabeled_import";

export interface MissingDataItem {
  id: string;
  kind: MissingDataKind;
  title: string;
  detail: string;
  asset?: string;
  eventDate?: string;
  dateRange?: string;
  ledgerSource?: string | null;
  ledgerSourceLabel?: string | null;
  importLabel?: string | null;
  importId?: string | null;
  importCoverage?: string | null;
  relatedImports?: string[];
  transactionId?: string;
}

const KIND_LABEL: Record<MissingDataKind, string> = {
  coverage_gap: "Coverage gap",
  missing_cost_basis: "Missing cost basis",
  import_overlap: "Overlapping imports",
  redundant_import: "Redundant import",
  unlabeled_import: "Unlabeled import",
};

export function missingDataKindLabel(kind: MissingDataKind): string {
  return KIND_LABEL[kind];
}

function importById(
  importSources: ImportSource[]
): Map<string, ImportSource> {
  return new Map(importSources.map((source) => [source.id, source]));
}

function importMeta(source: ImportSource | undefined): {
  label: string | null;
  coverage: string | null;
} {
  if (!source) return { label: null, coverage: null };
  return {
    label: source.label,
    coverage: formatImportCoverageLabel(source),
  };
}

function ledgerSourceLabel(sourceId: string | null | undefined): string | null {
  if (!sourceId) return null;
  return getSourceDefinition(normalizeSourceId(sourceId)).label;
}

function nearestImportForAsset(
  asset: string,
  beforeIso: string,
  importSources: ImportSource[],
  transactions: Transaction[]
): ImportSource | null {
  const importsWithAsset = new Set<string>();
  for (const tx of transactions) {
    if (tx.asset !== asset || !tx.import_id) continue;
    if (tx.timestamp <= beforeIso) {
      importsWithAsset.add(tx.import_id);
    }
  }
  if (!importsWithAsset.size) return null;

  let best: ImportSource | null = null;
  for (const source of importSources) {
    if (!importsWithAsset.has(source.id)) continue;
    if (!best || (source.date_end ?? "") > (best.date_end ?? "")) {
      best = source;
    }
  }
  return best;
}

export function buildMissingDataItems(input: {
  missingCostBasis: MissingCostBasisFlag[];
  coverageGaps: CoverageGap[];
  importOverlaps: ImportOverlap[];
  importSources: ImportSource[];
  transactions: Transaction[];
}): MissingDataItem[] {
  const {
    missingCostBasis,
    coverageGaps,
    importOverlaps,
    importSources,
    transactions,
  } = input;

  const imports = importById(importSources);
  const txById = new Map(transactions.map((tx) => [tx.id, tx]));
  const items: MissingDataItem[] = [];

  for (const gap of coverageGaps) {
    if (gap.kind !== "ledger") continue;
    const bordering =
      gap.import_labels?.length
        ? gap.import_labels
        : gap.import_ids
            ?.map((id) => imports.get(id)?.label)
            .filter(Boolean) as string[] | undefined;

    items.push({
      id: `gap-${gap.source_label}-${gap.gap_start}`,
      kind: "coverage_gap",
      title: gap.source_label,
      detail: gap.message,
      dateRange: formatImportDateRange(gap.gap_start, gap.gap_end) ?? undefined,
      ledgerSource: gap.source_slug ?? null,
      ledgerSourceLabel: gap.source_label,
      relatedImports: bordering,
      importCoverage: bordering?.join(" · ") ?? undefined,
    });
  }

  for (const flag of missingCostBasis) {
    const tx = txById.get(flag.disposal_id);
    const importSource = tx?.import_id ? imports.get(tx.import_id) : undefined;
    const meta = importMeta(importSource);
    const sourceId = tx?.source ?? null;
    const nearest = nearestImportForAsset(
      flag.asset,
      flag.timestamp,
      importSources,
      transactions
    );
    const nearestMeta = importMeta(nearest ?? undefined);

    items.push({
      id: `mcb-${flag.disposal_id}`,
      kind: "missing_cost_basis",
      title: `${flag.asset} disposal`,
      detail: flag.message,
      asset: flag.asset,
      eventDate: flag.timestamp,
      transactionId: flag.disposal_id,
      ledgerSource: sourceId,
      ledgerSourceLabel: ledgerSourceLabel(sourceId),
      importLabel: meta.label,
      importId: tx?.import_id ?? null,
      importCoverage: meta.coverage,
      relatedImports: nearest
        ? [nearest.label]
        : sourceId
          ? [ledgerSourceSummary(sourceId, importSources)].filter(Boolean) as string[]
          : undefined,
    });

    if (nearest && nearest.id !== tx?.import_id) {
      items[items.length - 1].detail = `${flag.message} Nearest earlier import for this asset: ${nearest.label}${
        nearestMeta.coverage ? ` (${nearestMeta.coverage})` : ""
      }.`;
    }
  }

  for (const overlap of importOverlaps) {
    const kind =
      overlap.kind === "redundant_import"
        ? "redundant_import"
        : "import_overlap";
    items.push({
      id: `overlap-${overlap.import_ids.join("-")}-${overlap.overlap_start ?? "none"}`,
      kind,
      title:
        overlap.parser_label ??
        overlap.import_labels.join(" ↔ ") ??
        "Import overlap",
      detail: overlap.message,
      dateRange: formatImportDateRange(
        overlap.overlap_start,
        overlap.overlap_end
      ) ?? undefined,
      ledgerSourceLabel: overlap.parser_label ?? null,
      relatedImports: overlap.import_labels,
      importCoverage:
        overlap.import_labels.length > 1
          ? overlap.import_labels.join(" · ")
          : overlap.import_labels[0],
    });
  }

  for (const source of importSources) {
    if (!source.is_unlabeled || source.transaction_count <= 0) continue;
    items.push({
      id: `unlabeled-${source.id}`,
      kind: "unlabeled_import",
      title: source.label,
      detail:
        "Imported before per-file tracking. Add a name in Connected sources so you can tell it apart.",
      ledgerSource: source.source_hint ?? null,
      ledgerSourceLabel:
        source.parser_label ??
        ledgerSourceLabel(source.source_hint) ??
        source.label,
      importLabel: source.label,
      importId: source.id,
      importCoverage: formatImportCoverageLabel(source) ?? undefined,
      dateRange: formatImportDateRange(
        source.coverage_start ?? source.date_start,
        source.coverage_end ?? source.date_end
      ) ?? undefined,
    });
  }

  const kindOrder: Record<MissingDataKind, number> = {
    missing_cost_basis: 0,
    coverage_gap: 1,
    import_overlap: 2,
    redundant_import: 3,
    unlabeled_import: 4,
  };

  return items.sort((a, b) => {
    const byKind = kindOrder[a.kind] - kindOrder[b.kind];
    if (byKind !== 0) return byKind;
    return (b.eventDate ?? b.dateRange ?? "").localeCompare(
      a.eventDate ?? a.dateRange ?? ""
    );
  });
}
