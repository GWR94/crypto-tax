import { useCallback, useEffect, useMemo, useRef, useState, type DragEvent } from "react";
import {
  ChevronDown,
  ChevronRight,
  FileUp,
  Loader2,
  Upload,
  FileText,
  Wallet,
  X,
} from "lucide-react";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { cn, shortenAddress } from "@/lib/utils";
import {
  detectWalletChain,
  EVM_AUTO_IMPORT_LABEL,
  WALLET_CHAIN_LABELS,
  walletDetectError,
  type WalletChain,
} from "@/lib/walletDetect";
import { ImportSourcesPanel } from "@/components/ImportSourcesPanel";
import { MexcEmailImport } from "@/components/MexcEmailImport";
import { CoverageGapHint } from "@/components/CoverageGapsAlert";
import {
  ImportOverlapsAlert,
  PreviewDuplicateHint,
} from "@/components/ImportOverlapsAlert";
import { ImportPreviewSnippet } from "@/components/CsvPreviewSnippet";
import { formatImportCoverageLabel, exportKindLabel } from "@/lib/sourcePreview";
import {
  loadSavedWallets,
  removeSavedWallet,
  syncSavedWalletsFromSources,
  upsertSavedWallet,
  type SavedWallet,
} from "@/lib/savedWallets";
import type { ImportFilePreview, ImportOverlap, ImportSource } from "@/lib/types";

const COLLAPSED_STORAGE_KEY = "crypto-tax-import-panel-collapsed";

interface ImportPanelProps {
  onImported: (message: string) => void;
  onError: (message: string | null) => void;
  disabled?: boolean;
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function fileKey(file: File): string {
  return `${file.name}:${file.size}`;
}

export function ImportPanel({
  onImported,
  onError,
  disabled = false,
}: ImportPanelProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [replace, setReplace] = useState(false);
  const [importing, setImporting] = useState(false);
  const [walletImporting, setWalletImporting] = useState(false);
  const [walletAddress, setWalletAddress] = useState("");
  const [walletProviders, setWalletProviders] = useState<
    Partial<Record<WalletChain, boolean>>
  >({});
  const [dragging, setDragging] = useState(false);
  const [sourcesRefreshKey, setSourcesRefreshKey] = useState(0);
  const [collapsed, setCollapsed] = useState(() => {
    try {
      return localStorage.getItem(COLLAPSED_STORAGE_KEY) === "true";
    } catch {
      return false;
    }
  });
  const [sourceSummary, setSourceSummary] = useState<ImportSource[]>([]);
  const [filePreviews, setFilePreviews] = useState<
    Record<string, ImportFilePreview & { loading?: boolean }>
  >({});
  const [previewOverlaps, setPreviewOverlaps] = useState<ImportOverlap[]>([]);
  const [savedWallets, setSavedWallets] = useState<SavedWallet[]>(() =>
    loadSavedWallets()
  );

  const loadSourceSummary = useCallback(async () => {
    try {
      const data = await api.getImportSources();
      setSourceSummary(data.filter((s) => s.transaction_count > 0));
    } catch {
      setSourceSummary([]);
    }
  }, []);

  useEffect(() => {
    void loadSourceSummary();
  }, [loadSourceSummary, sourcesRefreshKey]);

  useEffect(() => {
    setSavedWallets(syncSavedWalletsFromSources(sourceSummary));
  }, [sourceSummary]);

  useEffect(() => {
    if (!files.length) {
      setFilePreviews({});
      setPreviewOverlaps([]);
      return;
    }

    const keys = files.map(fileKey);
    setFilePreviews(
      Object.fromEntries(keys.map((key) => [key, { filename: "", transaction_count: 0, loading: true }]))
    );

    const timer = window.setTimeout(() => {
      void api
        .previewFiles(files)
        .then((result) => {
          setPreviewOverlaps(result.import_overlaps ?? []);
          setFilePreviews(
            Object.fromEntries(
              files.map((file, index) => {
                const key = fileKey(file);
                const preview = result.files[index] ?? {
                  filename: file.name,
                  transaction_count: 0,
                  error: "Could not preview file",
                };
                return [key, preview];
              })
            )
          );
        })
        .catch(() => {
          setPreviewOverlaps([]);
          setFilePreviews(
            Object.fromEntries(
              files.map((file) => [
                fileKey(file),
                {
                  filename: file.name,
                  transaction_count: 0,
                  error: "Preview failed",
                },
              ])
            )
          );
        });
    }, 300);

    return () => window.clearTimeout(timer);
  }, [files]);

  function setCollapsedPersist(next: boolean) {
    setCollapsed(next);
    try {
      localStorage.setItem(COLLAPSED_STORAGE_KEY, String(next));
    } catch {
      /* ignore */
    }
  }

  useEffect(() => {
    api
      .getHealth()
      .then((health) => {
        const wi = health.wallet_import;
        setWalletProviders({
          solana: Boolean(wi?.solana),
          ethereum: Boolean(wi?.ethereum),
          bitcoin: wi?.bitcoin !== false,
          cardano: wi?.cardano !== false,
          celestia: wi?.celestia !== false,
        });
      })
      .catch(() => setWalletProviders({}));
  }, []);

  const detectedChain = useMemo(
    () => detectWalletChain(walletAddress),
    [walletAddress]
  );
  const addressError = useMemo(
    () => walletDetectError(walletAddress),
    [walletAddress]
  );
  const walletImportEnabled =
    detectedChain != null && Boolean(walletProviders[detectedChain]);

  const busy = importing || walletImporting;

  function addFiles(incoming: FileList | File[] | null) {
    if (!incoming?.length) return;
    const next = [...incoming];
    setFiles((prev) => {
      const seen = new Set(prev.map((f) => `${f.name}:${f.size}`));
      const merged = [...prev];
      for (const file of next) {
        const key = `${file.name}:${file.size}`;
        if (!seen.has(key)) {
          seen.add(key);
          merged.push(file);
        }
      }
      return merged;
    });
    onError(null);
  }

  function removeFile(index: number) {
    setFiles((prev) => prev.filter((_, i) => i !== index));
    onError(null);
  }

  function clearFiles() {
    setFiles([]);
    if (inputRef.current) inputRef.current.value = "";
    onError(null);
  }

  function handleDragOver(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    if (!disabled && !busy) setDragging(true);
  }

  function handleDragLeave() {
    setDragging(false);
  }

  function handleDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragging(false);
    if (disabled || busy) return;
    addFiles(e.dataTransfer.files);
  }

  function openFilePicker() {
    if (!disabled && !busy) inputRef.current?.click();
  }

  async function handleImport() {
    if (!files.length) return;
    setImporting(true);
    onError(null);
    try {
      const result = await api.importFiles(files, replace);
      if (result.errors?.length) {
        onError(result.errors.join("\n\n"));
      }
      if (result.imported <= 0) {
        return;
      }
      const breakdown =
        result.files?.length && result.files.length > 1
          ? ` (${result.files
              .map((f) => {
                const skipped =
                  f.skipped_duplicates && f.skipped_duplicates > 0
                    ? `, ${f.skipped_duplicates} skipped as duplicates`
                    : "";
                return `${f.filename}: ${f.added ?? f.imported}${skipped}`;
              })
              .join(", ")})`
          : "";
      const skippedTotal =
        result.skipped_duplicates ??
        result.files?.reduce(
          (sum, file) => sum + (file.skipped_duplicates ?? 0),
          0
        ) ??
        0;
      onImported(
        `Imported ${result.imported} transaction(s) from ${files.length} file(s)${breakdown}. Ledger now has ${result.total} total.${
          skippedTotal
            ? ` Skipped ${skippedTotal} duplicate row(s) already in the ledger.`
            : ""
        }${
          result.demo_removed
            ? ` Removed ${result.demo_removed} demo row(s).`
            : ""
        }`
      );
      setSourcesRefreshKey((k) => k + 1);
      clearFiles();
      setCollapsedPersist(true);
    } catch (e) {
      onError(String(e));
    } finally {
      setImporting(false);
    }
  }

  async function handleWalletImport(addressOverride?: string) {
    const address = (addressOverride ?? walletAddress).trim();
    if (!address) return;
    setWalletImporting(true);
    onError(null);
    try {
      const result = await api.importWallet(address, replace);
      setSavedWallets(
        upsertSavedWallet(address, result.chain ?? detectWalletChain(address))
      );
      onImported(
        `${result.message ?? `Imported ${result.imported} transaction(s).`} Ledger now has ${result.total} total.${
          result.demo_removed
            ? ` Removed ${result.demo_removed} demo row(s).`
            : ""
        }`
      );
      setSourcesRefreshKey((k) => k + 1);
      setWalletAddress("");
      setCollapsedPersist(true);
    } catch (e) {
      onError(String(e));
    } finally {
      setWalletImporting(false);
    }
  }

  const connectedCount = sourceSummary.length;
  const txFromSources = sourceSummary.reduce(
    (sum, s) => sum + s.transaction_count,
    0
  );
  const allFilesRejected =
    files.length > 0 &&
    files.every((file) => {
      const preview = filePreviews[fileKey(file)];
      return preview && !preview.loading && Boolean(preview.error);
    });

  return (
    <Card>
      <CardHeader
        className="flex cursor-pointer flex-row items-center justify-between gap-2 space-y-0"
        onClick={() => setCollapsedPersist(!collapsed)}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            setCollapsedPersist(!collapsed);
          }
        }}
        aria-expanded={!collapsed}
      >
        <div className="flex min-w-0 flex-1 items-center gap-2">
          {collapsed ? (
            <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" />
          ) : (
            <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" />
          )}
          <Upload className="h-4 w-4 shrink-0 text-primary" />
          <CardTitle className="text-base">Import Transactions</CardTitle>
          {collapsed && connectedCount > 0 ? (
            <span className="truncate text-sm text-muted-foreground">
              · {connectedCount} source{connectedCount !== 1 ? "s" : ""} ·{" "}
              {txFromSources.toLocaleString()} tx
            </span>
          ) : null}
        </div>
        {collapsed ? (
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="shrink-0"
            onClick={(e) => {
              e.stopPropagation();
              setCollapsedPersist(false);
            }}
          >
            Expand
          </Button>
        ) : null}
      </CardHeader>
      {!collapsed ? (
      <CardContent className="space-y-4">
        <p className="text-sm text-muted-foreground">
          Upload one or more <strong className="text-foreground">CSV</strong> or{" "}
          <strong className="text-foreground">JSON</strong> exports from Kraken,
          Crypto.com, Solana, or any exchange. Drop multiple files at once when
          building a combined ledger.
        </p>

        <div className="flex flex-col gap-3 sm:flex-row sm:items-stretch">
          <div
            role="button"
            tabIndex={0}
            onClick={openFilePicker}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") openFilePicker();
            }}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
            className={cn(
              "flex flex-1 cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed px-4 py-6 text-center transition-colors",
              dragging
                ? "border-primary bg-primary/10"
                : "border-border hover:border-primary/60 hover:bg-primary/5",
              (disabled || busy) && "pointer-events-none opacity-50"
            )}
          >
            <input
              ref={inputRef}
              id="import-file"
              type="file"
              accept=".csv,.json,.txt"
              multiple
              disabled={disabled || busy}
              className="sr-only"
              onChange={(e) => {
                addFiles(e.target.files);
                e.target.value = "";
              }}
            />

            {files.length > 0 ? (
              <div className="w-full space-y-2 text-left">
                <div className="flex items-center justify-between gap-2">
                  <p className="text-sm font-medium text-foreground">
                    {files.length} file{files.length > 1 ? "s" : ""} selected
                  </p>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="h-7 px-2 text-xs"
                    onClick={(e) => {
                      e.stopPropagation();
                      clearFiles();
                    }}
                    disabled={disabled || busy}
                  >
                    Clear all
                  </Button>
                </div>
                {previewOverlaps.length ? (
                  <ImportOverlapsAlert overlaps={previewOverlaps} />
                ) : null}
                <ul className="max-h-64 space-y-1 overflow-y-auto">
                  {files.map((file, index) => {
                    const preview = filePreviews[fileKey(file)];
                    const coverageLabel = preview
                      ? formatImportCoverageLabel(preview)
                      : null;
                    return (
                    <li
                      key={`${file.name}-${file.size}-${index}`}
                      className="flex items-center gap-2 rounded-md bg-muted/40 px-2 py-1.5 text-sm"
                    >
                      <FileText className="h-4 w-4 shrink-0 text-primary" />
                      <div className="min-w-0 flex-1">
                        <span className="block truncate font-medium">
                          {file.name}
                        </span>
                        {preview?.loading ? (
                          <span className="text-xs text-muted-foreground">
                            Detecting format…
                          </span>
                        ) : preview?.error ? (
                          <span className="text-xs text-destructive">
                            {preview.error}
                          </span>
                        ) : preview?.parser_label ? (
                          <span className="text-xs text-muted-foreground">
                            {exportKindLabel(preview.export_kind)
                              ? `${preview.parser_label} · ${exportKindLabel(preview.export_kind)}`
                              : preview.parser_label}
                            {coverageLabel ? ` · ${coverageLabel}` : ""}
                            {preview.transaction_count > 0
                              ? ` · ${preview.transaction_count.toLocaleString()} tx`
                              : ""}
                          </span>
                        ) : null}
                        {preview?.coverage_gaps?.map((gap) => (
                          <CoverageGapHint
                            key={`${gap.gap_start}-${gap.gap_days}`}
                            gap={gap}
                          />
                        ))}
                        <PreviewDuplicateHint
                          duplicateCount={preview?.duplicate_count}
                          duplicateImportLabels={preview?.duplicate_import_labels}
                        />
                        {preview && !preview.loading && !preview.error ? (
                          <ImportPreviewSnippet inlinePreview={preview} />
                        ) : null}
                      </div>
                      <span className="shrink-0 text-xs text-muted-foreground">
                        {formatFileSize(file.size)}
                      </span>
                      <button
                        type="button"
                        className="shrink-0 rounded p-0.5 text-muted-foreground hover:text-foreground"
                        aria-label={`Remove ${file.name}`}
                        onClick={(e) => {
                          e.stopPropagation();
                          removeFile(index);
                        }}
                        disabled={disabled || busy}
                      >
                        <X className="h-3.5 w-3.5" />
                      </button>
                    </li>
                    );
                  })}
                </ul>
                <p className="text-center text-xs text-muted-foreground">
                  Drop more files or click to add
                </p>
              </div>
            ) : (
              <>
                <Upload className="h-8 w-8 text-primary" />
                <div>
                  <p className="font-medium text-foreground">
                    Drop files here, or click to browse
                  </p>
                  <p className="text-xs text-muted-foreground">
                    Select multiple CSV / JSON exports at once
                  </p>
                </div>
                <Button
                  type="button"
                  variant="default"
                  size="sm"
                  className="mt-1"
                  onClick={(e) => {
                    e.stopPropagation();
                    openFilePicker();
                  }}
                  disabled={disabled || busy}
                >
                  Choose files
                </Button>
              </>
            )}
          </div>

          <Button
            onClick={handleImport}
            disabled={!files.length || disabled || busy || allFilesRejected}
            className="shrink-0 sm:self-end"
            size="lg"
          >
            {importing ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <FileUp className="h-4 w-4" />
            )}
            Import{files.length > 1 ? ` ${files.length}` : ""}
          </Button>
        </div>

        <div className="relative">
          <div className="absolute inset-0 flex items-center">
            <span className="w-full border-t border-border" />
          </div>
          <div className="relative flex justify-center text-xs uppercase">
            <span className="bg-card px-2 text-muted-foreground">or</span>
          </div>
        </div>

        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <Wallet className="h-4 w-4 text-primary" />
            <p className="text-sm font-medium">Import from wallet address</p>
          </div>
          <p className="text-sm text-muted-foreground">
            Paste a wallet address — chain is detected automatically. A{" "}
            <code className="text-xs">0x…</code> address pulls{" "}
            <strong className="text-foreground">{EVM_AUTO_IMPORT_LABEL}</strong>{" "}
            on-chain activity plus{" "}
            <strong className="text-foreground">Hyperliquid</strong> perp trades
            (no extra key).
          </p>
          {detectedChain ? (
            <p className="text-sm text-muted-foreground">
              Detected:{" "}
              <span className="font-medium text-foreground">
                {WALLET_CHAIN_LABELS[detectedChain]}
              </span>
            </p>
          ) : null}
          {addressError ? (
            <p className="text-sm text-destructive">{addressError}</p>
          ) : null}
          {detectedChain && !walletImportEnabled ? (
            <p className="rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-200">
              {detectedChain === "solana" ? (
                <>
                  Solana import needs <code className="text-xs">HELIUS_API_KEY</code> in{" "}
                  <code className="text-xs">.env</code> (
                  <a
                    href="https://helius.dev"
                    target="_blank"
                    rel="noreferrer"
                    className="underline hover:text-amber-100"
                  >
                    helius.dev
                  </a>
                  ).
                </>
              ) : detectedChain === "ethereum" ? (
                <>
                  EVM import needs <code className="text-xs">ETHERSCAN_API_KEY</code> in{" "}
                  <code className="text-xs">.env</code> (
                  <a
                    href="https://etherscan.io/apis"
                    target="_blank"
                    rel="noreferrer"
                    className="underline hover:text-amber-100"
                  >
                    etherscan.io
                  </a>
                  ).
                </>
              ) : null}
            </p>
          ) : null}
          <div className="flex flex-col gap-3 sm:flex-row sm:items-stretch">
            <input
              type="text"
              placeholder="Solana, 0x…, bc1…, or addr1… wallet address"
              value={walletAddress}
              disabled={disabled || busy}
              className="flex-1 rounded-md border border-input bg-background px-3 py-2 text-sm font-mono placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
              onChange={(e) => setWalletAddress(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && walletAddress.trim()) {
                  void handleWalletImport();
                }
              }}
            />
            <Button
              onClick={() => void handleWalletImport()}
              disabled={
                !walletAddress.trim() ||
                disabled ||
                busy ||
                !detectedChain ||
                !walletImportEnabled
              }
              className="shrink-0 sm:self-stretch"
              size="lg"
            >
              {walletImporting ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Wallet className="h-4 w-4" />
              )}
              Fetch wallet
            </Button>
          </div>
          {savedWallets.length ? (
            <div className="space-y-2">
              <p className="text-xs font-medium text-muted-foreground">
                Saved wallets (stored in this browser)
              </p>
              <ul className="flex flex-wrap gap-2">
                {savedWallets.map((wallet) => (
                  <li
                    key={`${wallet.chain}:${wallet.address}`}
                    className="flex items-center gap-1 rounded-md border border-border bg-muted/30 pl-2"
                  >
                    <button
                      type="button"
                      disabled={disabled || busy}
                      className="py-1.5 text-left text-xs hover:text-primary"
                      onClick={() => setWalletAddress(wallet.address)}
                      title={wallet.address}
                    >
                      <span className="font-medium text-foreground">
                        {wallet.label ?? shortenAddress(wallet.address)}
                      </span>
                      <span className="ml-1.5 text-muted-foreground">
                        {WALLET_CHAIN_LABELS[wallet.chain].split(",")[0]}
                      </span>
                    </button>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      className="h-7 px-2 text-xs"
                      disabled={disabled || busy}
                      onClick={() => void handleWalletImport(wallet.address)}
                    >
                      Fetch
                    </Button>
                    <button
                      type="button"
                      className="rounded p-1 text-muted-foreground hover:text-foreground"
                      aria-label={`Remove saved wallet ${wallet.address}`}
                      disabled={disabled || busy}
                      onClick={() =>
                        setSavedWallets(
                          removeSavedWallet(wallet.address, wallet.chain)
                        )
                      }
                    >
                      <X className="h-3.5 w-3.5" />
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>

        <label className="flex cursor-pointer items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={replace}
            disabled={disabled || busy}
            className="h-4 w-4 rounded border-input"
            onChange={(e) => setReplace(e.target.checked)}
          />
          <span>
            Replace existing ledger{" "}
            <span className="text-muted-foreground">
              (destructive — wipes your current data before importing; leave
              unchecked to add to what you already have)
            </span>
          </span>
        </label>

        <MexcEmailImport
          disabled={disabled}
          onImported={(message) => {
            onImported(message);
            setSourcesRefreshKey((k) => k + 1);
          }}
          onError={onError}
        />

        <ImportSourcesPanel
          disabled={disabled}
          refreshKey={sourcesRefreshKey}
          onDisconnected={onImported}
          onError={onError}
        />
      </CardContent>
      ) : null}
    </Card>
  );
}
