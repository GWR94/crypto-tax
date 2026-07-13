import { useMemo } from "react";
import { Filter } from "lucide-react";
import { SourceIcon } from "@/components/icons/SourceIcon";
import {
  countBySource,
  getSourceDefinition,
  SOURCE_DEFINITIONS,
  type SourceKind,
} from "@/lib/sourceCatalog";
import { cn } from "@/lib/utils";

const KIND_ORDER: Record<SourceKind, number> = {
  exchange: 0,
  chain: 1,
};

function sortSourceIds(ids: string[]): string[] {
  return [...ids].sort((a, b) => {
    const da = getSourceDefinition(a);
    const db = getSourceDefinition(b);
    const ka = KIND_ORDER[da.kind];
    const kb = KIND_ORDER[db.kind];
    if (ka !== kb) return ka - kb;
    const ca = SOURCE_DEFINITIONS[a] ? 0 : 1;
    const cb = SOURCE_DEFINITIONS[b] ? 0 : 1;
    if (ca !== cb) return ca - cb;
    return da.label.localeCompare(db.label);
  });
}

export function SourceFilterBar({
  transactions,
  disabledSources,
  onToggleSource,
  onEnableAll,
  className,
}: {
  transactions: { source?: string | null }[];
  disabledSources: ReadonlySet<string>;
  onToggleSource: (sourceId: string) => void;
  onEnableAll: () => void;
  className?: string;
}) {
  const sourceCounts = useMemo(() => countBySource(transactions), [transactions]);
  const sourceIds = useMemo(
    () => sortSourceIds([...sourceCounts.keys()]),
    [sourceCounts]
  );

  if (sourceIds.length === 0) return null;

  const activeCount = sourceIds.filter((id) => !disabledSources.has(id)).length;
  const filtering = disabledSources.size > 0;

  return (
    <div className={cn("space-y-2", className)}>
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <Filter className="h-3.5 w-3.5" />
          <span>Sources</span>
          {filtering ? (
            <button
              type="button"
              onClick={onEnableAll}
              className="rounded px-1.5 py-0.5 text-primary underline-offset-2 hover:underline"
            >
              Show all
            </button>
          ) : null}
        </div>
        <span className="text-xs text-muted-foreground">
          {filtering
            ? `${activeCount} of ${sourceIds.length} visible · click to hide`
            : "Click to hide a source"}
        </span>
      </div>

      <div className="flex flex-wrap gap-2">
        {sourceIds.map((id) => {
          const def = getSourceDefinition(id);
          const count = sourceCounts.get(id) ?? 0;
          const active = !disabledSources.has(id);

          return (
            <button
              key={id}
              type="button"
              title={`${def.label} (${count}) — ${active ? "click to hide" : "click to show"}`}
              aria-pressed={active}
              onClick={() => onToggleSource(id)}
              className={cn(
                "inline-flex items-center gap-2 rounded-lg border px-2.5 py-1.5 text-left text-sm transition-colors",
                active
                  ? "border-border bg-card hover:border-primary/40 hover:bg-muted/30"
                  : "border-border/50 bg-muted/10 opacity-60 hover:opacity-80"
              )}
            >
              <SourceIcon source={id} muted={!active} />
              <span className={cn("font-medium", !active && "line-through")}>
                {def.label}
              </span>
              <span className="tabular-nums text-xs text-muted-foreground">
                {count}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
