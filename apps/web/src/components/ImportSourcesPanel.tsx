import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  FileText,
  Loader2,
  Pencil,
  Plug,
  Tag,
  Unplug,
  Wallet,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { api } from "@/lib/api";
import type { CoverageGap, ImportOverlap, ImportSource, ImportSourceKind } from "@/lib/types";
import { WALLET_SOURCE_HINTS } from "@/lib/walletDetect";
import { cn } from "@/lib/utils";
import {
  CoverageGapsAlert,
  CoverageGapHint,
} from "@/components/CoverageGapsAlert";
import {
  ImportOverlapHint,
  ImportOverlapsAlert,
} from "@/components/ImportOverlapsAlert";
import { ImportPreviewSnippet } from "@/components/CsvPreviewSnippet";
import { describeImportSource, exportKindLabel, formatImportCoverageLabel } from "@/lib/sourcePreview";

interface ImportSourcesPanelProps {
  disabled?: boolean;
  refreshKey?: number;
  onDisconnected: (message: string) => void;
  onError: (message: string | null) => void;
}

const SOURCES_COLLAPSED_STORAGE_KEY = "crypto-tax-sources-collapsed";

type SourceFilter = "all" | "csv" | "wallet";

const KIND_ICON: Record<ImportSourceKind, typeof FileText> = {
  csv: FileText,
  wallet: Wallet,
  legacy: FileText,
  demo: Plug,
};

const KIND_LABEL: Record<ImportSourceKind, string> = {
  csv: "CSV",
  wallet: "Wallet",
  legacy: "Unlabeled",
  demo: "Demo",
};

function formatImportedAt(value?: string | null): string | null {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

function defaultLabelForSource(source: ImportSource): string {
  if (source.source_hint && source.source_hint !== "unknown") {
    return `${source.source_hint}-export`;
  }
  return "my-import";
}

function defaultKindForSource(source: ImportSource): "csv" | "wallet" {
  const hint = source.source_hint ?? "";
  if (WALLET_SOURCE_HINTS.has(hint)) {
    return "wallet";
  }
  return "csv";
}

function overlapsForSource(
  source: ImportSource,
  overlaps: ImportOverlap[]
): ImportOverlap[] {
  return overlaps.filter((overlap) => overlap.import_ids.includes(source.id));
}

function gapsForSource(source: ImportSource, gaps: CoverageGap[]): CoverageGap[] {
  return gaps.filter((gap) => {
    if (gap.import_ids?.includes(source.id)) return true;
    if (
      source.kind === "legacy" &&
      gap.source_label === (source.parser_label ?? source.label)
    ) {
      return true;
    }
    return false;
  });
}

interface ImportSourceCardProps {
  source: ImportSource;
  coverageGaps: CoverageGap[];
  importOverlaps: ImportOverlap[];
  disabled: boolean;
  rowBusy: boolean;
  isEditing: boolean;
  editLabel: string;
  editKind: "csv" | "wallet";
  onEditLabelChange: (value: string) => void;
  onEditKindChange: (kind: "csv" | "wallet") => void;
  onStartEditing: () => void;
  onCancelEditing: () => void;
  onSaveLabel: () => void;
  onDisconnect: () => void;
}

function ImportSourceCard({
  source,
  coverageGaps,
  importOverlaps,
  disabled,
  rowBusy,
  isEditing,
  editLabel,
  editKind,
  onEditLabelChange,
  onEditKindChange,
  onStartEditing,
  onCancelEditing,
  onSaveLabel,
  onDisconnect,
}: ImportSourceCardProps) {
  const Icon = KIND_ICON[source.kind];
  const importedAt = formatImportedAt(source.imported_at);
  const coverageLabel = formatImportCoverageLabel(source);
  const sourceGaps = gapsForSource(source, coverageGaps);
  const sourceOverlaps = overlapsForSource(source, importOverlaps);

  return (
    <div className="rounded-md border border-border bg-card px-3 py-2.5">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start">
        <div className="flex min-w-0 flex-1 items-start gap-2.5">
          <Icon className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <p className="truncate text-sm font-medium">{source.label}</p>
              <Badge
                variant={source.is_unlabeled ? "muted" : "outline"}
                className="text-[10px] uppercase"
              >
                {KIND_LABEL[source.kind]}
              </Badge>
            </div>
            <p className="text-xs text-muted-foreground">
              <span className="block text-foreground/80">
                {describeImportSource(source)}
              </span>
              {source.parser_label ? (
                <span className="text-foreground/90">
                  {exportKindLabel(source.export_kind)
                    ? `${source.parser_label} · ${exportKindLabel(source.export_kind)}`
                    : source.parser_label}
                  {coverageLabel ? ` · ${coverageLabel}` : ""}
                  {" · "}
                </span>
              ) : coverageLabel ? (
                <span className="text-foreground/90">
                  {coverageLabel}
                  {" · "}
                </span>
              ) : null}
              {source.transaction_count} transaction
              {source.transaction_count === 1 ? "" : "s"}
              {importedAt ? ` · imported ${importedAt}` : ""}
              {source.is_unlabeled ? (
                <span className="block text-amber-200/90">
                  Imported before per-file tracking — add a name below.
                </span>
              ) : null}
              {source.address ? (
                <span className="block truncate font-mono text-[11px]">
                  {source.address}
                </span>
              ) : null}

              {sourceGaps.map((gap) => (
                <CoverageGapHint key={`${gap.gap_start}-${gap.gap_days}`} gap={gap} />
              ))}
              {sourceOverlaps.map((overlap) => (
                <ImportOverlapHint
                  key={overlap.import_ids.join("-")}
                  overlap={overlap}
                  sourceId={source.id}
                />
              ))}
              {source.kind !== "demo" ? (
                <ImportPreviewSnippet
                  importId={source.id}
                  buttonLabel={
                    source.kind === "wallet"
                      ? "Show data preview"
                      : "Show CSV preview"
                  }
                />
              ) : null}
            </p>
          </div>
        </div>

        {!isEditing ? (
          <div className="flex shrink-0 flex-wrap gap-2 sm:justify-end">
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={disabled || rowBusy}
              onClick={onStartEditing}
            >
              {source.is_unlabeled ? (
                <Tag className="h-3.5 w-3.5" />
              ) : (
                <Pencil className="h-3.5 w-3.5" />
              )}
              {source.is_unlabeled ? "Name" : "Rename"}
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={disabled || rowBusy}
              className={cn(
                "border-destructive/40 text-destructive hover:bg-destructive/10 hover:text-destructive",
                rowBusy && "opacity-70"
              )}
              onClick={onDisconnect}
            >
              {rowBusy ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Unplug className="h-3.5 w-3.5" />
              )}
              Disconnect
            </Button>
          </div>
        ) : null}
      </div>

      {isEditing ? (
        <div className="mt-3 space-y-3 border-t border-border pt-3">
          <div className="space-y-1.5">
            <label
              htmlFor={`import-label-${source.id}`}
              className="text-xs font-medium text-foreground"
            >
              {source.is_unlabeled ? "Import name" : "New name"}
            </label>
            <input
              id={`import-label-${source.id}`}
              type="text"
              value={editLabel}
              disabled={rowBusy}
              placeholder="e.g. kraken-ledger-2024.csv"
              className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              onChange={(e) => onEditLabelChange(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") onSaveLabel();
                if (e.key === "Escape") onCancelEditing();
              }}
              autoFocus
            />
          </div>

          {source.is_unlabeled ? (
            <div className="space-y-1.5">
              <p className="text-xs font-medium text-foreground">Import type</p>
              <div className="flex flex-wrap gap-2">
                {(["csv", "wallet"] as const).map((kind) => (
                  <button
                    key={kind}
                    type="button"
                    disabled={rowBusy}
                    onClick={() => onEditKindChange(kind)}
                    className={cn(
                      "rounded-md border px-3 py-1.5 text-sm transition-colors",
                      editKind === kind
                        ? "border-primary bg-primary/10 text-foreground"
                        : "border-border text-muted-foreground hover:border-primary/50"
                    )}
                  >
                    {kind === "csv" ? "CSV file" : "Wallet address"}
                  </button>
                ))}
              </div>
            </div>
          ) : null}

          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              size="sm"
              disabled={rowBusy || !editLabel.trim()}
              onClick={onSaveLabel}
            >
              {rowBusy ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Tag className="h-3.5 w-3.5" />
              )}
              Save
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              disabled={rowBusy}
              onClick={onCancelEditing}
            >
              Cancel
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

export function ImportSourcesPanel({
  disabled = false,
  refreshKey = 0,
  onDisconnected,
  onError,
}: ImportSourcesPanelProps) {
  const [sources, setSources] = useState<ImportSource[]>([]);
  const [coverageGaps, setCoverageGaps] = useState<CoverageGap[]>([]);
  const [importOverlaps, setImportOverlaps] = useState<ImportOverlap[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [removingRedundant, setRemovingRedundant] = useState(false);
  const [bulkBusy, setBulkBusy] = useState<"csv" | "wallet" | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editLabel, setEditLabel] = useState("");
  const [editKind, setEditKind] = useState<"csv" | "wallet">("csv");
  const [sourceFilter, setSourceFilter] = useState<SourceFilter>("all");
  const [collapsed, setCollapsed] = useState(() => {
    try {
      return localStorage.getItem(SOURCES_COLLAPSED_STORAGE_KEY) !== "false";
    } catch {
      return true;
    }
  });

  const setCollapsedPersist = useCallback((next: boolean) => {
    setCollapsed(next);
    try {
      localStorage.setItem(SOURCES_COLLAPSED_STORAGE_KEY, String(next));
    } catch {
      /* ignore */
    }
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [data, gaps, overlaps] = await Promise.all([
        api.getImportSources(),
        api.getCoverageGaps().catch(() => []),
        api.getImportOverlaps().catch(() => []),
      ]);
      setSources(data.filter((s) => s.transaction_count > 0));
      setCoverageGaps(gaps);
      setImportOverlaps(overlaps);
      onError(null);
    } catch (e) {
      onError(String(e));
    } finally {
      setLoading(false);
    }
  }, [onError]);

  useEffect(() => {
    void load();
  }, [load, refreshKey]);

  function startEditing(source: ImportSource) {
    setEditingId(source.id);
    setEditLabel(
      source.is_unlabeled ? defaultLabelForSource(source) : source.label
    );
    setEditKind(
      source.is_unlabeled ? defaultKindForSource(source) : source.kind === "wallet" ? "wallet" : "csv"
    );
    onError(null);
  }

  function cancelEditing() {
    setEditingId(null);
    setEditLabel("");
  }

  async function handleSaveLabel(source: ImportSource) {
    const label = editLabel.trim();
    if (!label) {
      onError("Enter a name for this import.");
      return;
    }

    setBusyId(source.id);
    onError(null);
    try {
      const result = await api.labelImportSource(
        source.id,
        label,
        source.is_unlabeled ? editKind : undefined
      );
      onDisconnected(result.message);
      setEditingId(null);
      await load();
    } catch (e) {
      onError(String(e));
    } finally {
      setBusyId(null);
    }
  }

  async function handleRemoveRedundant() {
    const redundantTotal = importOverlaps
      .filter((row) => row.kind === "redundant_import")
      .reduce((sum, row) => sum + (row.duplicate_count ?? 1), 0);
    if (
      !window.confirm(
        `Remove ${redundantTotal} redundant import(s)? These added no transactions to your ledger.`
      )
    ) {
      return;
    }

    setRemovingRedundant(true);
    onError(null);
    try {
      const result = await api.disconnectRedundantImports();
      onDisconnected(result.message);
      await load();
    } catch (e) {
      onError(String(e));
    } finally {
      setRemovingRedundant(false);
    }
  }

  async function handleDisconnectAll(kind: "csv" | "wallet") {
    const targets = sources.filter((source) =>
      kind === "csv"
        ? source.kind === "csv" || source.kind === "legacy"
        : source.kind === "wallet"
    );
    if (!targets.length) return;

    const txTotal = targets.reduce((sum, source) => sum + source.transaction_count, 0);
    const noun = kind === "csv" ? "CSV import" : "wallet";
    if (
      !window.confirm(
        `Disconnect all ${targets.length} ${noun}(s)? This removes ${txTotal.toLocaleString()} transaction(s) from your ledger.${
          kind === "wallet"
            ? " Saved wallet addresses in your browser will be kept."
            : ""
        }`
      )
    ) {
      return;
    }

    setBulkBusy(kind);
    onError(null);
    try {
      const result = await api.disconnectImportSourcesByKind(kind);
      onDisconnected(
        `${result.message} Ledger now has ${result.total} transaction(s).`
      );
      if (editingId && targets.some((source) => source.id === editingId)) {
        cancelEditing();
      }
      await load();
    } catch (e) {
      onError(String(e));
    } finally {
      setBulkBusy(null);
    }
  }

  async function handleDisconnect(source: ImportSource) {
    if (
      !window.confirm(
        `Disconnect "${source.label}"? This removes ${source.transaction_count} transaction(s) from your ledger.`
      )
    ) {
      return;
    }

    setBusyId(source.id);
    onError(null);
    try {
      const result = await api.disconnectImportSource(source.id);
      onDisconnected(
        `${result.message} Ledger now has ${result.total} transaction(s).`
      );
      if (editingId === source.id) cancelEditing();
      await load();
    } catch (e) {
      onError(String(e));
    } finally {
      setBusyId(null);
    }
  }

  const csvSources = useMemo(
    () =>
      sources.filter(
        (source) => source.kind === "csv" || source.kind === "legacy"
      ),
    [sources]
  );
  const walletSources = useMemo(
    () => sources.filter((source) => source.kind === "wallet"),
    [sources]
  );
  const filteredSources = useMemo(() => {
    if (sourceFilter === "csv") return csvSources;
    if (sourceFilter === "wallet") return walletSources;
    return sources;
  }, [sourceFilter, sources, csvSources, walletSources]);
  const txTotal = sources.reduce(
    (sum, source) => sum + source.transaction_count,
    0
  );

  if (loading && sources.length === 0) {
    return (
      <div className="flex items-center gap-2 rounded-lg border border-border bg-muted/20 px-4 py-3 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />
        Loading connected sources…
      </div>
    );
  }

  if (sources.length === 0) {
    return null;
  }

  return (
    <div className="space-y-3 rounded-lg border border-border bg-muted/10 p-4">
      <div
        className="flex cursor-pointer flex-col gap-2 sm:flex-row sm:items-center sm:justify-between"
        role="button"
        tabIndex={0}
        aria-expanded={!collapsed}
        onClick={() => setCollapsedPersist(!collapsed)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            setCollapsedPersist(!collapsed);
          }
        }}
      >
        <div className="flex min-w-0 items-center gap-2">
          {collapsed ? (
            <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" />
          ) : (
            <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" />
          )}
          <Plug className="h-4 w-4 shrink-0 text-primary" />
          <p className="text-sm font-medium">Connected sources</p>
          <Badge variant="muted">
            {sources.length} source{sources.length !== 1 ? "s" : ""}
          </Badge>
          {loading ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />
          ) : null}
        </div>
        <p className="text-xs text-muted-foreground sm:text-right">
          {txTotal.toLocaleString()} transaction{txTotal !== 1 ? "s" : ""}
          {csvSources.length && walletSources.length
            ? ` · ${csvSources.length} CSV · ${walletSources.length} wallet${walletSources.length !== 1 ? "s" : ""}`
            : null}
        </p>
      </div>

      {!collapsed ? (
        <div className="space-y-3" onClick={(e) => e.stopPropagation()}>
          <p className="text-xs text-muted-foreground">
            Name each import so you can tell files and wallets apart. Hover
            source names in the ledger for quick previews.
          </p>

          {csvSources.length + walletSources.length > 1 ? (
            <div className="flex flex-wrap gap-1.5">
              {(
                [
                  ["all", "All", sources.length],
                  ["csv", "CSV", csvSources.length],
                  ["wallet", "Wallets", walletSources.length],
                ] as const
              ).map(([value, label, count]) =>
                count > 0 ? (
                  <button
                    key={value}
                    type="button"
                    onClick={() => {
                      if (editingId) cancelEditing();
                      setSourceFilter(value);
                    }}
                    className={cn(
                      "rounded-full border px-2.5 py-1 text-xs transition-colors",
                      sourceFilter === value
                        ? "border-primary bg-primary/10 text-foreground"
                        : "border-border text-muted-foreground hover:border-primary/50 hover:text-foreground"
                    )}
                  >
                    {label} ({count})
                  </button>
                ) : null
              )}
            </div>
          ) : null}

          {csvSources.length || walletSources.length ? (
            <div className="flex flex-wrap gap-2">
              {csvSources.length ? (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={disabled || bulkBusy !== null}
                  className="border-destructive/40 text-destructive hover:bg-destructive/10 hover:text-destructive"
                  onClick={() => void handleDisconnectAll("csv")}
                >
                  {bulkBusy === "csv" ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Unplug className="h-3.5 w-3.5" />
                  )}
                  Remove all CSVs ({csvSources.length})
                </Button>
              ) : null}
              {walletSources.length ? (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={disabled || bulkBusy !== null}
                  className="border-destructive/40 text-destructive hover:bg-destructive/10 hover:text-destructive"
                  onClick={() => void handleDisconnectAll("wallet")}
                >
                  {bulkBusy === "wallet" ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Unplug className="h-3.5 w-3.5" />
                  )}
                  Disconnect all wallets ({walletSources.length})
                </Button>
              ) : null}
            </div>
          ) : null}

          <CoverageGapsAlert gaps={coverageGaps} />
          <ImportOverlapsAlert
            overlaps={importOverlaps}
            onRemoveRedundant={
              importOverlaps.some((row) => row.kind === "redundant_import")
                ? () => void handleRemoveRedundant()
                : undefined
            }
            removingRedundant={removingRedundant}
          />

          <ul className="max-h-[32rem] space-y-2 overflow-y-auto pr-1">
            {filteredSources.map((source) => {
              const isEditing = editingId === source.id;
              const rowBusy = busyId === source.id;

              return (
                <li key={source.id}>
                  <ImportSourceCard
                    source={source}
                    coverageGaps={coverageGaps}
                    importOverlaps={importOverlaps}
                    disabled={disabled}
                    rowBusy={rowBusy}
                    isEditing={isEditing}
                    editLabel={editLabel}
                    editKind={editKind}
                    onEditLabelChange={setEditLabel}
                    onEditKindChange={setEditKind}
                    onStartEditing={() => startEditing(source)}
                    onCancelEditing={cancelEditing}
                    onSaveLabel={() => void handleSaveLabel(source)}
                    onDisconnect={() => void handleDisconnect(source)}
                  />
                </li>
              );
            })}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
