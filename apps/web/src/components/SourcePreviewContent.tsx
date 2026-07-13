import type { ImportSource, Transaction } from "@/lib/types";
import { getSourceDefinition, type SourceKind } from "@/lib/sourceCatalog";
import {
  describeImportSource,
  formatImportDateRange,
  importsForLedgerSource,
  ledgerSourceSummary,
  sampleTransactionsForSource,
} from "@/lib/sourcePreview";
import { shortenAddress } from "@/lib/utils";

const KIND_LABEL: Record<SourceKind, string> = {
  exchange: "Exchange",
  chain: "On-chain wallet",
};

function formatSampleTx(tx: Transaction): string {
  const date = new Date(tx.timestamp).toLocaleDateString(undefined, {
    dateStyle: "medium",
  });
  const type = tx.transaction_type.replace(/_/g, " ").toLowerCase();
  return `${date} · ${type} · ${tx.asset}`;
}

export function SourcePreviewContent({
  source,
  transactionCount,
  importSources = [],
  importId,
  transactions = [],
  filterHint,
}: {
  source: string | null | undefined;
  transactionCount?: number;
  importSources?: ImportSource[];
  importId?: string | null;
  transactions?: Transaction[];
  filterHint?: string;
}) {
  const def = getSourceDefinition(source);
  const importMatch = importId
    ? importSources.find((item) => item.id === importId)
    : undefined;
  const relatedImports = importsForLedgerSource(def.id, importSources);
  const samples = sampleTransactionsForSource(transactions, def.id, 3);
  const importSummary = ledgerSourceSummary(def.id, importSources);

  return (
    <div className="space-y-1.5">
      <p className="font-semibold text-card-foreground">{def.label}</p>
      <p className="text-muted-foreground">{KIND_LABEL[def.kind]}</p>
      {def.description ? (
        <p className="text-card-foreground/90">{def.description}</p>
      ) : null}
      {transactionCount !== undefined ? (
        <p className="tabular-nums text-card-foreground">
          {transactionCount.toLocaleString()} transaction
          {transactionCount === 1 ? "" : "s"} in ledger
        </p>
      ) : null}
      {importSummary ? (
        <p className="text-muted-foreground">{importSummary}</p>
      ) : null}
      {importMatch ? (
        <div className="border-t border-border/60 pt-1.5">
          <p className="text-muted-foreground">This transaction</p>
          <p className="font-medium text-card-foreground">{importMatch.label}</p>
          <p className="text-muted-foreground">{describeImportSource(importMatch)}</p>
          {importMatch.address ? (
            <p className="font-mono text-[10px] text-muted-foreground">
              {shortenAddress(importMatch.address)}
            </p>
          ) : null}
        </div>
      ) : null}
      {!importMatch && relatedImports.length > 0 ? (
        <div className="border-t border-border/60 pt-1.5">
          <p className="text-muted-foreground">Connected imports</p>
          <ul className="mt-0.5 space-y-1">
            {relatedImports.slice(0, 4).map((imp) => (
              <li key={imp.id}>
                <p className="font-medium text-card-foreground">{imp.label}</p>
                <p className="text-muted-foreground">{describeImportSource(imp)}</p>
                <p className="text-[10px] text-muted-foreground">
                  {imp.transaction_count} tx
                  {formatImportDateRange(imp.date_start, imp.date_end)
                    ? ` · ${formatImportDateRange(imp.date_start, imp.date_end)}`
                    : ""}
                </p>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {samples.length > 0 ? (
        <div className="border-t border-border/60 pt-1.5">
          <p className="text-muted-foreground">Recent activity</p>
          <ul className="mt-0.5 space-y-0.5">
            {samples.map((tx) => (
              <li key={tx.id} className="text-card-foreground">
                {formatSampleTx(tx)}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {filterHint ? (
        <p className="border-t border-border/60 pt-1.5 text-muted-foreground">
          {filterHint}
        </p>
      ) : null}
    </div>
  );
}
