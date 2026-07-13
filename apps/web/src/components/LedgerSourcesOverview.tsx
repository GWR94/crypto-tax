import { useMemo } from "react";
import { SourceIcon } from "@/components/icons/SourceIcon";
import { countBySource, getSourceDefinition } from "@/lib/sourceCatalog";
import { ledgerSourceSummary, sampleTransactionsForSource } from "@/lib/sourcePreview";
import type { ImportSource, Transaction } from "@/lib/types";

function formatSampleTx(tx: Transaction): string {
  const date = new Date(tx.timestamp).toLocaleDateString(undefined, {
    dateStyle: "medium",
  });
  return `${date} · ${tx.transaction_type.replace(/_/g, " ").toLowerCase()} · ${tx.asset}`;
}

export function LedgerSourcesOverview({
  transactions,
  importSources = [],
}: {
  transactions: Transaction[];
  importSources?: ImportSource[];
}) {
  const sourceIds = useMemo(() => {
    const counts = countBySource(transactions);
    return [...counts.keys()].sort((a, b) => {
      const ca = counts.get(a) ?? 0;
      const cb = counts.get(b) ?? 0;
      return cb - ca;
    });
  }, [transactions]);

  if (sourceIds.length === 0) return null;

  return (
    <details className="rounded-lg border border-border bg-muted/10 px-3 py-2 text-sm">
      <summary className="cursor-pointer font-medium text-foreground">
        What are these sources? ({sourceIds.length})
      </summary>
      <ul className="mt-3 space-y-3">
        {sourceIds.map((id) => {
          const def = getSourceDefinition(id);
          const importSummary = ledgerSourceSummary(id, importSources);
          const samples = sampleTransactionsForSource(transactions, id, 2);
          return (
            <li
              key={id}
              className="rounded-md border border-border bg-card px-3 py-2.5"
            >
              <div className="flex items-start gap-2.5">
                <SourceIcon source={id} className="mt-0.5 h-4 w-4 shrink-0" />
                <div className="min-w-0 flex-1">
                  <p className="font-medium">{def.label}</p>
                  {def.description ? (
                    <p className="mt-0.5 text-xs text-muted-foreground">
                      {def.description}
                    </p>
                  ) : null}
                  {importSummary ? (
                    <p className="mt-1 text-xs text-foreground/80">{importSummary}</p>
                  ) : null}
                  {samples.length > 0 ? (
                    <ul className="mt-1.5 space-y-0.5 text-xs text-muted-foreground">
                      {samples.map((tx) => (
                        <li key={tx.id}>{formatSampleTx(tx)}</li>
                      ))}
                    </ul>
                  ) : null}
                </div>
              </div>
            </li>
          );
        })}
      </ul>
    </details>
  );
}
