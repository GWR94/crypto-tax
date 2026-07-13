import { Copy } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import type { ImportOverlap } from "@/lib/types";

function formatOverlapRange(start?: string | null, end?: string | null): string | null {
  if (!start || !end) return null;
  const fmt = (iso: string) =>
    new Date(iso).toLocaleDateString(undefined, { dateStyle: "medium" });
  if (start.slice(0, 10) === end.slice(0, 10)) return fmt(start);
  return `${fmt(start)} – ${fmt(end)}`;
}

function redundantImportTotal(overlaps: ImportOverlap[]): number {
  return overlaps
    .filter((row) => row.kind === "redundant_import")
    .reduce((sum, row) => sum + (row.duplicate_count ?? 1), 0);
}

function issueSummary(overlaps: ImportOverlap[]): string {
  const redundantTotal = redundantImportTotal(overlaps);
  const coverageCount = overlaps.filter((row) => row.kind === "coverage").length;
  const parts: string[] = [];
  if (redundantTotal) {
    parts.push(
      `${redundantTotal} redundant import${redundantTotal === 1 ? "" : "s"}`
    );
  }
  if (coverageCount) {
    parts.push(
      `${coverageCount} overlapping period${coverageCount === 1 ? "" : "s"}`
    );
  }
  return parts.join(", ");
}

interface ImportOverlapsAlertProps {
  overlaps: ImportOverlap[];
  onRemoveRedundant?: () => void;
  removingRedundant?: boolean;
}

export function ImportOverlapsAlert({
  overlaps,
  onRemoveRedundant,
  removingRedundant = false,
}: ImportOverlapsAlertProps) {
  if (!overlaps.length) return null;

  const redundant = overlaps.filter((row) => row.kind === "redundant_import");
  const coverage = overlaps.filter((row) => row.kind === "coverage");
  const redundantTotal = redundantImportTotal(overlaps);
  const summary = issueSummary(overlaps);

  return (
    <Alert variant="warning">
      <Copy className="h-4 w-4" />
      <AlertTitle>Possible duplicate data{summary ? ` (${summary})` : ""}</AlertTitle>
      <AlertDescription>
        <p className="mb-2">
          These imports may cover the same period or repeat transactions already
          in your ledger.
        </p>
        {redundantTotal > 0 && onRemoveRedundant ? (
          <div className="mb-3">
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={removingRedundant}
              onClick={onRemoveRedundant}
            >
              Remove {redundantTotal} redundant import
              {redundantTotal === 1 ? "" : "s"}
            </Button>
          </div>
        ) : null}
        <ul className="max-h-48 space-y-1 overflow-y-auto">
          {redundant.map((overlap) => (
            <li
              key={overlap.import_labels[0]}
              className="rounded-md bg-yellow-500/10 px-3 py-1.5 text-sm text-foreground/90"
            >
              <span className="font-medium">
                {overlap.import_labels[0]}
                {(overlap.duplicate_count ?? 1) > 1 ? (
                  <span className="text-muted-foreground">
                    {" "}
                    · imported {overlap.duplicate_count}×
                  </span>
                ) : null}
              </span>
              <span className="mt-0.5 block text-xs text-muted-foreground">
                {overlap.message}
              </span>
            </li>
          ))}
          {coverage.map((overlap) => (
            <li
              key={overlap.import_ids.join("-")}
              className="rounded-md bg-yellow-500/10 px-3 py-1.5 text-sm text-foreground/90"
            >
              <span className="font-medium">
                {overlap.import_labels.join(" · ")}
              </span>
              {overlap.overlap_days > 0 ? (
                <span className="text-muted-foreground">
                  {" "}
                  · {overlap.overlap_days} day overlap
                  {formatOverlapRange(overlap.overlap_start, overlap.overlap_end)
                    ? ` (${formatOverlapRange(overlap.overlap_start, overlap.overlap_end)})`
                    : ""}
                </span>
              ) : null}
              <span className="mt-0.5 block text-xs text-muted-foreground">
                {overlap.message}
              </span>
            </li>
          ))}
        </ul>
      </AlertDescription>
    </Alert>
  );
}

export function ImportOverlapHint({
  overlap,
  sourceId,
}: {
  overlap: ImportOverlap;
  sourceId?: string;
}) {
  const range = formatOverlapRange(overlap.overlap_start, overlap.overlap_end);
  if (overlap.kind === "redundant_import") {
    return null;
  }
  const otherLabels = sourceId
    ? overlap.import_ids
        .map((id, index) => (id === sourceId ? null : overlap.import_labels[index]))
        .filter((label): label is string => Boolean(label))
    : overlap.import_labels.slice(1);
  if (!otherLabels.length) return null;
  return (
    <span className="block text-amber-200/90">
      Overlaps with {otherLabels.join(", ")}
      {overlap.overlap_days > 0 ? ` for ${overlap.overlap_days} days` : ""}
      {range ? ` (${range})` : ""}
      {overlap.shared_transactions > 0
        ? ` · ${overlap.shared_transactions} shared transaction(s)`
        : ""}
    </span>
  );
}

export function PreviewDuplicateHint({
  duplicateCount,
  duplicateImportLabels,
}: {
  duplicateCount?: number;
  duplicateImportLabels?: string[];
}) {
  if (!duplicateCount) return null;
  const sourceText =
    duplicateImportLabels && duplicateImportLabels.length
      ? ` from ${duplicateImportLabels.join(", ")}`
      : " already in your ledger";
  return (
    <span className="block text-amber-200/90">
      {duplicateCount.toLocaleString()} transaction
      {duplicateCount === 1 ? "" : "s"} match data{sourceText} and will be skipped.
    </span>
  );
}
