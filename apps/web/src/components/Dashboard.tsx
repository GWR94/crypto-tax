import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Loader2, RefreshCw, GitMerge, RotateCcw, Ban, ListMinus, Coins } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { KpiRibbon } from "@/components/KpiRibbon";
import { MissingDataPanel } from "@/components/MissingDataPanel";
import { DataHealthPanel } from "@/components/DataHealthPanel";
import { TaxHarvestTable } from "@/components/TaxHarvestTable";
import { PerCoinTable } from "@/components/PerCoinTable";
import { PerCoinRealizedTable } from "@/components/PerCoinRealizedTable";
import { PnlAllocationChart } from "@/components/PnlAllocationChart";
import { AllocationChart } from "@/components/AllocationChart";
import { TaxReporter } from "@/components/TaxReporter";
import { ImportPanel } from "@/components/ImportPanel";
import { PerpsSection } from "@/components/PerpsSection";
import { StakingSection } from "@/components/StakingSection";
import { TransactionList } from "@/components/TransactionList";
import { api } from "@/lib/api";
import { splitLedger } from "@/lib/ledger";
import { isBackendConnectionError } from "@/lib/utils";
import type {
  AccountingMethod,
  AssetLabel,
  DisplayCurrency,
  CoverageGap,
  DataHealthSummary,
  ImportOverlap,
  ImportSource,
  PerpTreatment,
  PnlBreakdown,
  PortfolioSummary,
  TaxJurisdiction,
  TaxSettings,
  Transaction,
  DataMode,
} from "@/lib/types";

function jurisdictionSubtitle(jurisdiction: TaxJurisdiction): string {
  if (jurisdiction === "UK") {
    return "UK capital gains · HMRC share-matching & Section 104 pools";
  }
  return "US capital gains · deterministic FIFO / LIFO / HIFO accounting";
}

function perpTreatmentFor(
  settings: TaxSettings,
  jurisdiction: TaxJurisdiction
): PerpTreatment {
  const value =
    jurisdiction === "UK" ? settings.uk_perp_treatment : settings.us_perp_treatment;
  return value ?? "income";
}

export function Dashboard() {
  const [method, setMethod] = useState<AccountingMethod>("FIFO");
  const [taxJurisdiction, setTaxJurisdiction] = useState<TaxJurisdiction>("UK");
  const [perpTreatment, setPerpTreatment] = useState<PerpTreatment>("income");
  const [displayCurrency, setDisplayCurrency] = useState<DisplayCurrency>("GBP");
  const [summary, setSummary] = useState<PortfolioSummary | null>(null);
  const [pnlBreakdown, setPnlBreakdown] = useState<PnlBreakdown | null>(null);
  const [transactions, setTransactions] = useState<Transaction[]>([]);
  const [assetLabels, setAssetLabels] = useState<Record<string, AssetLabel>>({});
  const [scamAssets, setScamAssets] = useState<string[]>([]);
  const [importSources, setImportSources] = useState<ImportSource[]>([]);
  const [coverageGaps, setCoverageGaps] = useState<CoverageGap[]>([]);
  const [importOverlaps, setImportOverlaps] = useState<ImportOverlap[]>([]);
  const [dataHealth, setDataHealth] = useState<DataHealthSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [dataMode, setDataMode] = useState<DataMode>("live");
  const [persistedDemoCount, setPersistedDemoCount] = useState(0);
  const [hideStakingInPnl, setHideStakingInPnl] = useState(false);
  const [ledgerFocusAsset, setLedgerFocusAsset] = useState<string | null>(null);
  const [repriceRunning, setRepriceRunning] = useState(false);
  const maintenanceCompletedRef = useRef(false);
  const repriceInFlightRef = useRef(false);

  function appendNotice(message: string) {
    setNotice((prev) => {
      if (!prev) return message;
      if (prev.includes(message)) return prev;
      return `${prev} ${message}`;
    });
  }

  const fetchDashboard = useCallback(async () => {
    // Normalize the ledger via /transactions before portfolio/tax reads so a
    // fresh import cannot race KPI totals against an un-normalized ledger.
    // (API endpoints also normalize server-side; this keeps the UI sequence clear.)
    const settings = await api
      .getSettings()
      .catch(() => ({
        tax_jurisdiction: "UK" as const,
        reporting_currency: "GBP",
      }));

    const txs = await api.getTransactions();

    const [data, labels, scams, demo, imports, breakdown, gaps, overlaps, health] =
      await Promise.all([
        api.getPortfolio(method, displayCurrency),
        api.getAssetLabels(),
        api.getScamAssets().catch(() => ({ assets: [] })),
        api.getDemoStatus().catch(() => ({ count: 0, active: false })),
        api.getImportSources().catch(() => []),
        api
          .getPnlBreakdown(method, hideStakingInPnl)
          .catch(() => ({ by_asset: {} })),
        api.getCoverageGaps().catch(() => []),
        api.getImportOverlaps().catch(() => []),
        api.getDataHealth().catch(() => ({
          orphaned_inflows: [],
          cost_basis_overrides: [],
        })),
      ]);
    setTaxJurisdiction(settings.tax_jurisdiction);
    setPerpTreatment(perpTreatmentFor(settings, settings.tax_jurisdiction));
    setDataMode(settings.data_mode ?? demo.mode ?? "live");
    setPersistedDemoCount(demo.persisted_demo_count ?? 0);
    setSummary(data);
    setPnlBreakdown(breakdown);
    setTransactions(txs);
    setAssetLabels(labels);
    setScamAssets(scams.assets);
    setImportSources(imports);
    setCoverageGaps(gaps);
    setImportOverlaps(overlaps);
    setDataHealth(health);
  }, [method, displayCurrency, hideStakingInPnl]);

  const isDemo = dataMode === "demo";

  const runStartupMaintenance = useCallback(async () => {
    if (maintenanceCompletedRef.current) {
      return;
    }
    maintenanceCompletedRef.current = true;

    try {
      const settings = await api.getSettings();
      if (settings.data_mode === "demo") {
        return;
      }
    } catch {
      // Continue with maintenance if settings are unavailable.
    }

    let needsRefresh = false;

    try {
      const fix = await api.fixMovements();
      if (fix.reclassified > 0) {
        appendNotice(fix.message);
        needsRefresh = true;
      }
    } catch {
      // Non-fatal if the endpoint is unavailable on an older API process.
    }

    if (!repriceInFlightRef.current) {
      try {
        const backfill = await api.backfillCostBasis();
        if (backfill.updated > 0 || backfill.saved) {
          appendNotice(backfill.message);
          needsRefresh = true;
        }
      } catch {
        // Non-fatal on older API builds.
      }
    }

    try {
      const cleanup = await api.cleanupSolanaPhantoms();
      if (
        cleanup.removed > 0 ||
        (cleanup as { staking_echo_removed?: number }).staking_echo_removed
      ) {
        appendNotice(cleanup.message);
        needsRefresh = true;
      }
    } catch {
      // Non-fatal on older API builds.
    }

    if (needsRefresh) {
      await fetchDashboard();
    }
  }, [fetchDashboard]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      await fetchDashboard();
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, [fetchDashboard]);

  function handleViewInLedger(asset: string) {
    setLedgerFocusAsset(asset);
    requestAnimationFrame(() => {
      document
        .getElementById("transaction-ledger")
        ?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }

  async function handleJurisdictionChange(next: TaxJurisdiction) {
    if (next === taxJurisdiction) return;
    setBusy("jurisdiction");
    setError(null);
    try {
      const settings = await api.updateSettings({ tax_jurisdiction: next });
      setTaxJurisdiction(settings.tax_jurisdiction);
      setPerpTreatment(perpTreatmentFor(settings, settings.tax_jurisdiction));
      await fetchDashboard();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  }

  async function handleDataModeChange(next: DataMode) {
    if (next === dataMode) return;
    setBusy("data-mode");
    setError(null);
    try {
      const settings = await api.updateSettings({ data_mode: next });
      setDataMode(settings.data_mode ?? next);
      await fetchDashboard();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  }

  async function handlePerpTreatmentChange(next: PerpTreatment) {
    if (next === perpTreatment) return;
    setBusy("perp-treatment");
    setError(null);
    try {
      const update =
        taxJurisdiction === "UK"
          ? { uk_perp_treatment: next }
          : { us_perp_treatment: next };
      const settings = await api.updateSettings(update);
      setPerpTreatment(perpTreatmentFor(settings, taxJurisdiction));
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  }

  const isUk = taxJurisdiction === "UK";

  const { spot: spotTransactions, perps: perpTransactions } = useMemo(
    () => splitLedger(transactions),
    [transactions]
  );

  useEffect(() => {
    void (async () => {
      await load();
      await runStartupMaintenance();
    })();
  }, [load, runStartupMaintenance]);

  const refreshAfterMutation = useCallback((message?: string) => {
    if (message) {
      setNotice(message);
    }
    void fetchDashboard().catch((e) => setError(String(e)));
  }, [fetchDashboard]);

  async function handleHideStakingInPnl(checked: boolean) {
    setHideStakingInPnl(checked);
    try {
      const breakdown = await api.getPnlBreakdown(method, checked);
      setPnlBreakdown(breakdown);
    } catch {
      // Keep prior breakdown if refetch fails.
    }
  }

  async function handlePurgeSplSpam() {
    setBusy("spam");
    setNotice(null);
    try {
      const result = await api.cleanupSolanaPhantoms();
      refreshAfterMutation(result.message);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  }

  async function handleMatchTransfers() {
    setBusy("match");
    setNotice(null);
    try {
      const result = await api.matchTransfers();
      refreshAfterMutation(result.message);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  }

  function handleRepriceWallets() {
    if (repriceRunning || repriceInFlightRef.current) return;
    repriceInFlightRef.current = true;
    setRepriceRunning(true);
    setNotice(
      "Repricing wallet transactions… this may take a minute for large ledgers."
    );
    setError(null);
    void (async () => {
      try {
        const result = await api.backfillCostBasis();
        setNotice(result.message);
      } catch (e) {
        setError(String(e));
      } finally {
        repriceInFlightRef.current = false;
        setRepriceRunning(false);
        void fetchDashboard().catch((e) => setError(String(e)));
      }
    })();
  }

  async function handleDeduplicate() {
    setBusy("dedupe");
    setNotice(null);
    try {
      const result = await api.deduplicateLedger();
      refreshAfterMutation(result.message);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  }

  async function handleStripDemo() {
    setBusy("strip");
    setNotice(null);
    try {
      const result = await api.stripDemoData();
      setPersistedDemoCount(0);
      refreshAfterMutation(result.message);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  }

  async function handleReset() {
    const count = transactions.length;
    const confirmed = window.confirm(
      `Reset replaces your live ledger (${count.toLocaleString()} transaction${
        count === 1 ? "" : "s"
      }) with the bundled sample dataset.\n\n` +
        "A JSON backup will download first. You can re-import that file later " +
        '(Import → enable "Replace existing ledger").\n\n' +
        "A copy is also saved on disk as data/ledger.json.bak.\n\nContinue?"
    );
    if (!confirmed) return;

    setBusy("reset");
    setNotice(null);
    try {
      const filename = await api.downloadLedgerBackup();
      const result = await api.resetTransactions();
      refreshAfterMutation(
        `Backup saved as ${filename}` +
          (result.local_backup ? " (and data/ledger.json.bak)" : "") +
          ". Ledger reset to the bundled sample dataset."
      );
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  }

  async function handleMarkScam(asset: string) {
    const label = assetLabels[asset]?.symbol ?? asset;
    if (
      !window.confirm(
        `Mark "${label}" as a scam token? It will be hidden from portfolio totals and holdings. Transactions stay in your ledger.`
      )
    ) {
      return;
    }
    setBusy("scam");
    setNotice(null);
    try {
      const result = await api.markScamAsset(asset);
      refreshAfterMutation(result.message);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  }

  async function handleUnmarkScam(asset: string) {
    setBusy("scam");
    setNotice(null);
    try {
      const result = await api.unmarkScamAsset(asset);
      refreshAfterMutation(result.message);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="min-h-screen bg-background">
      <header className="sticky top-0 z-10 border-b border-border/60 bg-background/80 backdrop-blur">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-3 px-4 py-4 sm:px-6">
          <div>
            <h1 className="text-lg font-bold tracking-tight">
              Crypto Tax &amp; PnL Dashboard
            </h1>
            <p className="text-xs text-muted-foreground">
              {jurisdictionSubtitle(taxJurisdiction)}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Select
              value={dataMode}
              onChange={(e) =>
                void handleDataModeChange(e.target.value as DataMode)
              }
              className="h-9 w-[108px]"
              aria-label="Data mode"
              disabled={busy === "data-mode"}
              title="Switch between your imported ledger and bundled demo data"
            >
              <option value="live">Live</option>
              <option value="demo">Demo</option>
            </Select>
            <Select
              value={perpTreatment}
              onChange={(e) =>
                void handlePerpTreatmentChange(e.target.value as PerpTreatment)
              }
              className="h-9 w-[150px]"
              disabled={busy === "perp-treatment"}
              title="How perpetual-futures PnL is taxed"
              aria-label="Perp tax treatment"
            >
              <option value="exclude">Perps: exclude</option>
              <option value="income">Perps: income</option>
              <option value="capital_gains">Perps: capital gains</option>
            </Select>
            <Select
              value={taxJurisdiction}
              onChange={(e) =>
                void handleJurisdictionChange(e.target.value as TaxJurisdiction)
              }
              className="h-9 w-[88px]"
              aria-label="Tax jurisdiction"
              disabled={busy === "jurisdiction"}
            >
              <option value="UK">UK</option>
              <option value="US">US</option>
            </Select>
            <Select
              value={displayCurrency}
              onChange={(e) =>
                setDisplayCurrency(e.target.value as DisplayCurrency)
              }
              className="h-9 w-[90px]"
              aria-label="Display currency"
            >
              <option value="GBP">GBP</option>
              <option value="USD">USD</option>
            </Select>
            {!isUk ? (
              <Select
                value={method}
                onChange={(e) => setMethod(e.target.value as AccountingMethod)}
                className="h-9 w-[150px]"
                aria-label="Accounting method"
              >
                <option value="FIFO">FIFO</option>
                <option value="LIFO">LIFO</option>
                <option value="HIFO">HIFO</option>
              </Select>
            ) : null}
            <Button
              variant="outline"
              size="sm"
              onClick={handleRepriceWallets}
              disabled={repriceRunning || isDemo}
              title="Reprice wallet/on-chain imports using historical prices and matched transfer legs"
            >
              {repriceRunning ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Coins className="h-4 w-4" />
              )}
              Reprice wallets
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={handlePurgeSplSpam}
              disabled={busy === "spam" || isDemo}
              title="Remove unlisted SPL airdrops and swap routing noise"
            >
              {busy === "spam" ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Ban className="h-4 w-4" />
              )}
              Purge spam
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={handleDeduplicate}
              disabled={busy === "dedupe" || isDemo}
              title="Remove duplicate rows (same transaction id or content)"
            >
              {busy === "dedupe" ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <ListMinus className="h-4 w-4" />
              )}
              Remove duplicates
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={handleMatchTransfers}
              disabled={busy === "match" || isDemo}
            >
              {busy === "match" ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <GitMerge className="h-4 w-4" />
              )}
              Match Transfers
            </Button>
            {!isDemo && persistedDemoCount > 0 ? (
            <Button
              variant="outline"
              size="sm"
              onClick={handleStripDemo}
              disabled={busy === "strip"}
            >
              {busy === "strip" ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <RotateCcw className="h-4 w-4" />
              )}
              Remove demo from ledger
            </Button>
            ) : null}
            <Button
              variant="ghost"
              size="sm"
              title="Downloads a JSON backup, then replaces the live ledger with sample data"
              onClick={handleReset}
              disabled={busy === "reset" || isDemo}
            >
              {busy === "reset" ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <RotateCcw className="h-4 w-4" />
              )}
              Reset
            </Button>
            <Button
              variant="ghost"
              size="icon"
              onClick={load}
              disabled={loading}
              aria-label="Refresh"
            >
              <RefreshCw
                className={loading ? "h-4 w-4 animate-spin" : "h-4 w-4"}
              />
            </Button>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-7xl space-y-6 px-4 py-6 sm:px-6">
        {error ? (
          <div className="rounded-lg border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">
            <p className="whitespace-pre-wrap">{error}</p>
            {isBackendConnectionError(error) ? (
              <p className="mt-2 text-destructive/90">
                Is the backend running on{" "}
                <code>http://localhost:8000</code>?
              </p>
            ) : null}
          </div>
        ) : null}

        {isDemo ? (
          <div className="flex items-center gap-2 rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-950 dark:text-amber-100">
            <Badge variant="outline" className="border-amber-500/50">
              Demo
            </Badge>
            Viewing bundled sample data with verified tax figures. Switch to Live
            to work with your imported ledger.
          </div>
        ) : null}

        {notice ? (
          <div className="flex items-center gap-2 rounded-lg border border-primary/30 bg-primary/10 px-4 py-3 text-sm text-primary">
            {repriceRunning ? (
              <Loader2 className="h-4 w-4 shrink-0 animate-spin" />
            ) : (
              <Badge variant="default">Info</Badge>
            )}
            {notice}
          </div>
        ) : null}

        {loading && !summary ? (
          <div className="flex items-center justify-center py-24 text-muted-foreground">
            <Loader2 className="mr-2 h-5 w-5 animate-spin" /> Loading portfolio…
          </div>
        ) : null}

        <ImportPanel
          disabled={isDemo}
          onImported={(message) => {
            setNotice(message);
            void fetchDashboard();
          }}
          onError={setError}
        />

        {summary ? (
          <>
            <KpiRibbon
              summary={summary}
              jurisdiction={taxJurisdiction}
            />

            <TransactionList
              transactions={spotTransactions}
              assetLabels={assetLabels}
              scamAssets={scamAssets}
              importSources={importSources}
              focusAsset={ledgerFocusAsset}
              onClearFocusAsset={() => setLedgerFocusAsset(null)}
            />

            <DataHealthPanel
              dataHealth={dataHealth}
              currency={summary.display_currency}
              assetLabels={assetLabels}
              onUpdated={() => {
                void fetchDashboard().catch((e) => setError(String(e)));
              }}
              onError={setError}
            />

            <MissingDataPanel
              missingCostBasis={summary.missing_cost_basis}
              coverageGaps={coverageGaps}
              importOverlaps={importOverlaps}
              importSources={importSources}
              transactions={transactions}
              assetLabels={assetLabels}
            />

            <StakingSection
              transactions={spotTransactions}
              currency={summary.display_currency}
              assetLabels={assetLabels}
            />

            <PerpsSection
              transactions={perpTransactions}
              currency={summary.display_currency}
              assetLabels={assetLabels}
            />

            <div className="flex flex-wrap items-center justify-end gap-2">
              <label className="flex h-9 cursor-pointer items-center gap-2 rounded-md border border-input bg-background px-3 text-sm">
                <input
                  type="checkbox"
                  checked={hideStakingInPnl}
                  onChange={(e) => handleHideStakingInPnl(e.target.checked)}
                  className="h-3.5 w-3.5 rounded border-input accent-primary"
                />
                Hide staking in breakdown
              </label>
            </div>

            <div className="space-y-6">
            <PerCoinTable
              positions={summary.positions}
              holdings={summary.holdings ?? []}
              currency={summary.display_currency}
              assetLabels={assetLabels}
              pnlByAsset={pnlBreakdown?.by_asset ?? {}}
              transactions={spotTransactions}
              importSources={importSources}
              jurisdiction={taxJurisdiction}
              hideStaking={hideStakingInPnl}
              hiddenScams={scamAssets}
              onMarkScam={handleMarkScam}
              onUnmarkScam={handleUnmarkScam}
              onViewInLedger={handleViewInLedger}
            />

            <PerCoinRealizedTable
              rows={summary.realized_pnl ?? []}
              currency={summary.display_currency}
              assetLabels={assetLabels}
              jurisdiction={taxJurisdiction}
              pnlByAsset={pnlBreakdown?.by_asset ?? {}}
              transactions={spotTransactions}
              importSources={importSources}
              hideStaking={hideStakingInPnl}
            />
            </div>

            <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
              <PnlAllocationChart
                title="Unrealized Profit & Loss Allocation"
                description="Share of paper gains and losses across open positions. Slice size is the absolute P&L amount."
                currency={summary.display_currency}
                slices={summary.positions.map((p) => ({
                  name: assetLabels[p.asset]?.symbol ?? p.asset,
                  value: p.unrealized_pnl,
                }))}
                emptyMessage="No unrealized profit or loss to chart."
              />
              <PnlAllocationChart
                title="Realized Profit & Loss Allocation"
                description="Share of lifetime realized gains and losses by asset. Green slices are gains, red are losses."
                currency={summary.display_currency}
                slices={(summary.realized_pnl ?? []).map((r) => ({
                  name: assetLabels[r.asset]?.symbol ?? r.asset,
                  value: r.realized_pnl,
                }))}
                emptyMessage="No realized profit or loss to chart."
              />
              <AllocationChart
                positions={summary.positions}
                holdings={summary.holdings ?? []}
                currency={summary.display_currency}
                assetLabels={assetLabels}
              />
            </div>

            <TaxHarvestTable
              rows={summary.tax_harvest}
              currency={summary.display_currency}
              assetLabels={assetLabels}
              jurisdiction={taxJurisdiction}
              estimateRate={summary.tax_harvest_rate}
              basicRate={summary.tax_harvest_basic_rate}
              higherRate={summary.tax_harvest_higher_rate}
              ordinaryRate={summary.tax_harvest_ordinary_rate}
              ltcgRate={summary.tax_harvest_ltcg_rate}
              unusedBasicBand={summary.tax_harvest_unused_basic_band}
              bandCurrency={
                (summary.reporting_currency as DisplayCurrency) ?? "GBP"
              }
              ratesBusy={busy === "harvest-rates"}
              onRatesChange={async (update) => {
                setBusy("harvest-rates");
                setError(null);
                try {
                  await api.updateSettings(update);
                  await fetchDashboard();
                } catch (e) {
                  setError(String(e));
                } finally {
                  setBusy(null);
                }
              }}
            />

            <TaxReporter
              method={method}
              onMethodChange={setMethod}
              jurisdiction={taxJurisdiction}
              perpTreatment={perpTreatment}
            />
          </>
        ) : null}
      </main>
    </div>
  );
}
