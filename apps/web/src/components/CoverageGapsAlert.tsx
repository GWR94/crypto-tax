import { AlertTriangle } from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import type { CoverageGap } from "@/lib/types";

function formatGapRange(start: string, end: string): string {
  const fmt = (iso: string) =>
    new Date(iso).toLocaleDateString(undefined, { dateStyle: "medium" });
  if (start.slice(0, 10) === end.slice(0, 10)) return fmt(start);
  return `${fmt(start)} – ${fmt(end)}`;
}

export function CoverageGapsAlert({ gaps }: { gaps: CoverageGap[] }) {
  if (!gaps.length) return null;

  return (
    <Alert variant="warning">
      <AlertTriangle className="h-4 w-4" />
      <AlertTitle>
        Possible missing data ({gaps.length} gap{gaps.length === 1 ? "" : "s"})
      </AlertTitle>
      <AlertDescription>
        <p className="mb-2">
          These periods are not covered by any import&apos;s date range. You may
          be missing an export for that time.
        </p>
        <ul className="space-y-1">
          {gaps.map((gap) => (
            <li
              key={`${gap.kind}-${gap.source_label}-${gap.gap_start}-${gap.gap_days}`}
              className="rounded-md bg-yellow-500/10 px-3 py-1.5 text-sm text-foreground/90"
            >
              <span className="font-medium">{gap.source_label}</span>
              <span className="text-muted-foreground">
                {" "}
                · {gap.gap_days} days ·{" "}
                {formatGapRange(gap.gap_start, gap.gap_end)}
              </span>
              <span className="mt-0.5 block text-xs text-muted-foreground">
                {gap.message}
              </span>
            </li>
          ))}
        </ul>
      </AlertDescription>
    </Alert>
  );
}

export function CoverageGapHint({ gap }: { gap: CoverageGap }) {
  return (
    <span className="block text-amber-200/90">
      Gap: {gap.gap_days} days not covered by any import (
      {formatGapRange(gap.gap_start, gap.gap_end)})
    </span>
  );
}
