import { useMemo, useState } from "react";
import { ChevronDown, ChevronRight, DatabaseZap } from "lucide-react";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { AssetBadge } from "@/components/AssetBadge";
import { SourceBadge } from "@/components/SourceBadge";
import type { AssetLabel, ImportSource, Transaction } from "@/lib/types";
import type {
  CoverageGap,
  ImportOverlap,
  MissingCostBasisFlag,
} from "@/lib/types";
import {
  buildMissingDataItems,
  missingDataKindLabel,
  type MissingDataKind,
} from "@/lib/missingData";
import { formatDateTime } from "@/lib/utils";
import { cn } from "@/lib/utils";

const FILTER_OPTIONS: Array<MissingDataKind | "all"> = [
  "all",
  "missing_cost_basis",
  "coverage_gap",
  "import_overlap",
  "redundant_import",
  "unlabeled_import",
];

interface MissingDataPanelProps {
  missingCostBasis: MissingCostBasisFlag[];
  coverageGaps: CoverageGap[];
  importOverlaps: ImportOverlap[];
  importSources: ImportSource[];
  transactions: Transaction[];
  assetLabels?: Record<string, AssetLabel>;
}

export function MissingDataPanel({
  missingCostBasis,
  coverageGaps,
  importOverlaps,
  importSources,
  transactions,
  assetLabels = {},
}: MissingDataPanelProps) {
  const [collapsed, setCollapsed] = useState(false);
  const [filter, setFilter] = useState<MissingDataKind | "all">("all");

  const items = useMemo(
    () =>
      buildMissingDataItems({
        missingCostBasis,
        coverageGaps,
        importOverlaps,
        importSources,
        transactions,
      }),
    [
      missingCostBasis,
      coverageGaps,
      importOverlaps,
      importSources,
      transactions,
    ]
  );

  const filtered =
    filter === "all" ? items : items.filter((item) => item.kind === filter);

  const counts = useMemo(() => {
    const map = new Map<MissingDataKind, number>();
    for (const item of items) {
      map.set(item.kind, (map.get(item.kind) ?? 0) + 1);
    }
    return map;
  }, [items]);

  if (!items.length) return null;

  return (
    <Card>
      <CardHeader
        className="flex cursor-pointer flex-col gap-3 space-y-0 sm:flex-row sm:items-center sm:justify-between"
        onClick={() => setCollapsed((value) => !value)}
        role="button"
        tabIndex={0}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            setCollapsed((value) => !value);
          }
        }}
        aria-expanded={!collapsed}
      >
        <div className="flex items-center gap-2">
          {collapsed ? (
            <ChevronRight className="h-4 w-4 text-muted-foreground" />
          ) : (
            <ChevronDown className="h-4 w-4 text-muted-foreground" />
          )}
          <DatabaseZap className="h-4 w-4 text-primary" />
          <CardTitle className="text-base">Missing & incomplete data</CardTitle>
          <Badge variant="muted">{items.length}</Badge>
        </div>
        <p className="text-sm text-muted-foreground">
          Coverage gaps, unmatched disposals, and import issues — with the last
          known source or import for each.
        </p>
      </CardHeader>

      {!collapsed ? (
        <CardContent className="space-y-4">
          <div className="flex flex-wrap gap-1.5">
            {FILTER_OPTIONS.map((value) => {
              const count =
                value === "all" ? items.length : counts.get(value) ?? 0;
              if (value !== "all" && count === 0) return null;
              return (
                <button
                  key={value}
                  type="button"
                  onClick={(event) => {
                    event.stopPropagation();
                    setFilter(value);
                  }}
                  className={cn(
                    "rounded-full border px-2.5 py-1 text-xs transition-colors",
                    filter === value
                      ? "border-primary bg-primary/10 text-foreground"
                      : "border-border text-muted-foreground hover:border-primary/50 hover:text-foreground"
                  )}
                >
                  {value === "all"
                    ? `All (${count})`
                    : `${missingDataKindLabel(value)} (${count})`}
                </button>
              );
            })}
          </div>

          <div className="overflow-x-auto rounded-md border border-border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-[8rem]">Type</TableHead>
                  <TableHead>Issue</TableHead>
                  <TableHead>Ledger source</TableHead>
                  <TableHead>Import / last source</TableHead>
                  <TableHead className="whitespace-nowrap">When</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filtered.map((item) => (
                  <TableRow key={item.id}>
                    <TableCell>
                      <Badge
                        variant={
                          item.kind === "missing_cost_basis"
                            ? "destructive"
                            : item.kind === "coverage_gap"
                              ? "outline"
                              : "muted"
                        }
                        className="whitespace-nowrap text-[10px] uppercase"
                      >
                        {missingDataKindLabel(item.kind)}
                      </Badge>
                    </TableCell>
                    <TableCell className="max-w-md">
                      <div className="flex flex-wrap items-center gap-2">
                        {item.asset ? (
                          <AssetBadge asset={item.asset} labels={assetLabels} />
                        ) : null}
                        <span className="font-medium text-foreground">
                          {item.title}
                        </span>
                      </div>
                      <p className="mt-1 text-xs text-muted-foreground">
                        {item.detail}
                      </p>
                      {item.transactionId ? (
                        <p className="mt-1 font-mono text-[11px] text-muted-foreground">
                          tx {item.transactionId}
                        </p>
                      ) : null}
                    </TableCell>
                    <TableCell>
                      {item.ledgerSource ? (
                        <SourceBadge source={item.ledgerSource} />
                      ) : item.ledgerSourceLabel ? (
                        <span className="text-sm">{item.ledgerSourceLabel}</span>
                      ) : (
                        <span className="text-muted-foreground">—</span>
                      )}
                    </TableCell>
                    <TableCell className="max-w-xs">
                      {item.importLabel ? (
                        <p className="text-sm font-medium">{item.importLabel}</p>
                      ) : item.relatedImports?.length ? (
                        <ul className="space-y-0.5 text-sm">
                          {item.relatedImports.map((label) => (
                            <li key={label}>{label}</li>
                          ))}
                        </ul>
                      ) : (
                        <span className="text-muted-foreground">—</span>
                      )}
                      {item.importCoverage ? (
                        <p className="mt-1 text-xs text-muted-foreground">
                          {item.importCoverage}
                        </p>
                      ) : null}
                    </TableCell>
                    <TableCell className="whitespace-nowrap text-sm text-muted-foreground">
                      {item.eventDate
                        ? formatDateTime(item.eventDate)
                        : item.dateRange ?? "—"}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </CardContent>
      ) : null}
    </Card>
  );
}
