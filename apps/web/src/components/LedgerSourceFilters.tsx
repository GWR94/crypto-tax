import { useMemo } from "react";
import { SourceIcon } from "@/components/icons/SourceIcon";
import { CheckboxFilterDropdown } from "@/components/CheckboxFilterDropdown";
import {
  countBySource,
  getSourceDefinition,
  SOURCE_DEFINITIONS,
  type SourceKind,
} from "@/lib/sourceCatalog";
import { ledgerSourceSummary } from "@/lib/sourcePreview";
import type { ImportSource } from "@/lib/types";

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

export function LedgerSourceFilters({
  transactions,
  importSources = [],
  disabledSources,
  onToggleSource,
  onToggleAllSources,
  className,
}: {
  transactions: { source?: string | null }[];
  importSources?: ImportSource[];
  disabledSources: ReadonlySet<string>;
  onToggleSource: (sourceId: string) => void;
  onToggleAllSources: () => void;
  className?: string;
}) {
  const sourceCounts = useMemo(() => countBySource(transactions), [transactions]);
  const sourceIds = useMemo(
    () => sortSourceIds([...sourceCounts.keys()]),
    [sourceCounts]
  );

  const items = useMemo(
    () =>
      sourceIds.map((id) => {
        const def = getSourceDefinition(id);
        const active = !disabledSources.has(id);
        const importSummary = ledgerSourceSummary(id, importSources);
        return {
          id,
          label: def.label,
          count: sourceCounts.get(id),
          icon: <SourceIcon source={id} muted={!active} className="h-4 w-4" />,
          description: importSummary ?? def.description,
        };
      }),
    [sourceIds, sourceCounts, disabledSources, importSources]
  );

  if (sourceIds.length === 0) return null;

  return (
    <CheckboxFilterDropdown
      allLabel="All sources"
      items={items}
      hiddenIds={disabledSources}
      onToggleAll={onToggleAllSources}
      onToggleItem={onToggleSource}
      className={className}
      menuClassName="min-w-[300px]"
    />
  );
}
