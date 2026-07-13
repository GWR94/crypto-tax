import { Fragment, useEffect, useMemo, useState } from "react";
import { ChevronDown, ChevronRight, Info, ListOrdered, Search, X } from "lucide-react";
import { Button } from "@/components/ui/button";
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
import { SectionDescription } from "@/components/ui/section-description";
import { groupTransactions } from "@/lib/groupTransactions";
import { CheckboxFilterDropdown } from "@/components/CheckboxFilterDropdown";
import {
  isUnstakeGroup,
  unstakePrincipal,
  unstakeReward,
} from "@/lib/unstake";
import { formatDateTime, formatMoney, formatNumber, isDustTransaction, shortenAddress } from "@/lib/utils";
import { viaLabel, viaTooltip } from "@/lib/viaLabel";
import { matchesSourceFilter, countBySource } from "@/lib/sourceCatalog";
import { AssetBadge } from "@/components/AssetBadge";
import { LedgerSourceFilters } from "@/components/LedgerSourceFilters";
import { LedgerSourcesOverview } from "@/components/LedgerSourcesOverview";
import { SourceBadge } from "@/components/SourceBadge";
import { TransactionDetails } from "@/components/TransactionDetails";
import type { AssetLabel, ImportSource, Transaction, TransactionType } from "@/lib/types";

const TYPE_VARIANT: Record<
  TransactionType,
  "default" | "success" | "destructive" | "muted" | "outline"
> = {
  BUY: "success",
  SELL: "destructive",
  AIRDROP: "default",
  STAKING: "default",
  FEE: "muted",
  TRANSFER: "outline",
};

const TYPE_ORDER: TransactionType[] = [
  "BUY",
  "SELL",
  "AIRDROP",
  "STAKING",
  "TRANSFER",
  "FEE",
];

function matchesSearch(
  tx: Transaction,
  query: string,
  assetLabels: Record<string, AssetLabel>
): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;

  const assetLabel = assetLabels[tx.asset];
  const counterLabel = tx.counter_asset ? assetLabels[tx.counter_asset] : undefined;

  const haystack = [
    tx.id,
    tx.asset,
    assetLabel?.symbol,
    assetLabel?.name,
    assetLabel?.mint,
    tx.counter_asset,
    counterLabel?.symbol,
    counterLabel?.name,
    tx.transaction_type,
    tx.source,
    tx.transfer_direction,
    tx.trade_group_id,
    tx.on_chain_tx_id,
    tx.import_id,
    tx.token_mint,
    tx.counterparty_address,
    tx.fiat_currency,
    tx.timestamp,
    String(tx.amount),
    String(tx.fiat_value_at_trigger),
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();

  return haystack.includes(q);
}

function denomination(tx: Transaction): string {
  return tx.fiat_currency ?? (tx.source === "kraken" ? "GBP" : "USD");
}

function isInternalTransferGroup(txs: Transaction[]): boolean {
  const transfers = txs.filter((t) => t.transaction_type === "TRANSFER");
  if (transfers.length < 2) return false;
  const outs = transfers.filter((t) => t.transfer_direction === "OUT");
  const ins = transfers.filter((t) => t.transfer_direction === "IN");
  if (outs.length !== 1 || ins.length !== 1) return false;
  const out = outs[0];
  const inn = ins[0];
  if (out.asset !== inn.asset) return false;
  const rel = Math.abs(out.amount - inn.amount) / Math.max(out.amount, inn.amount);
  return rel <= 0.02;
}

function summarizeGroup(
  txs: Transaction[],
  assetLabels: Record<string, AssetLabel>
) {
  if (isUnstakeGroup(txs)) {
    const principal = unstakePrincipal(txs)!;
    const reward = unstakeReward(txs);
    const denom = denomination(principal);
    const totalValue = txs.reduce((sum, t) => sum + t.fiat_value_at_trigger, 0);
    const totalFee = txs.reduce((sum, t) => sum + t.fee_fiat, 0);
    const cp = principal.counterparty_address
      ? shortenAddress(principal.counterparty_address)
      : null;
    let via = cp ? `From ${cp}` : viaLabel(principal, assetLabels);
    if (reward) {
      const sym = assetLabels[reward.asset]?.symbol ?? reward.asset;
      via += ` · +${formatNumber(reward.amount)} ${sym} reward`;
    }
    return {
      displayType: "STAKING" as TransactionType,
      primaryAsset: principal.asset,
      totalAmount: principal.amount,
      totalValue,
      totalFee,
      denom,
      via,
      timestamp: principal.timestamp,
      source: principal.source,
    };
  }

  if (isInternalTransferGroup(txs)) {
    const out =
      txs.find((t) => t.transfer_direction === "OUT") ??
      txs.find((t) => t.transaction_type === "TRANSFER") ??
      txs[0];
    const inn = txs.find((t) => t.transfer_direction === "IN");
    const denom = denomination(out);
    const cp = out.counterparty_address
      ? shortenAddress(out.counterparty_address)
      : inn?.counterparty_address
        ? shortenAddress(inn.counterparty_address)
        : null;
    const via = cp ? `${out.transfer_direction === "OUT" ? "To" : "From"} ${cp}` : viaLabel(out, assetLabels);
    return {
      displayType: "TRANSFER" as TransactionType,
      primaryAsset: out.asset,
      totalAmount: out.amount,
      totalValue: out.fiat_value_at_trigger,
      totalFee: txs.reduce((sum, t) => sum + t.fee_fiat, 0),
      denom,
      via,
      timestamp: out.timestamp,
      source: out.source,
    };
  }

  let displayType: TransactionType = txs[0].transaction_type;
  for (const type of TYPE_ORDER) {
    if (txs.some((t) => t.transaction_type === type)) {
      displayType = type;
      break;
    }
  }

  const primary =
    txs.find((t) => t.transaction_type === displayType) ?? txs[0];
  const primaryAsset = primary.asset;
  const denom = denomination(primary);

  const matchingAmount = txs.filter((t) => t.asset === primaryAsset);
  const totalAmount = matchingAmount.length
    ? matchingAmount.reduce((sum, t) => sum + t.amount, 0)
    : primary.amount;
  const totalValue = txs.reduce((sum, t) => sum + t.fiat_value_at_trigger, 0);
  const totalFee = txs.reduce((sum, t) => sum + t.fee_fiat, 0);

  let via = viaLabel(primary, assetLabels);
  if (via === "—") {
    const transfer = txs.find((t) => t.transfer_direction);
    if (transfer) via = viaLabel(transfer, assetLabels);
  }

  return {
    displayType,
    primaryAsset,
    totalAmount,
    totalValue,
    totalFee,
    denom,
    via,
    timestamp: primary.timestamp,
    source: primary.source,
  };
}

function TransactionCells({
  tx,
  assetLabels,
  importSources = [],
  transactions = [],
  nested = false,
  detailsOpen,
  onToggleDetails,
}: {
  tx: Transaction;
  assetLabels: Record<string, AssetLabel>;
  importSources?: ImportSource[];
  transactions?: Transaction[];
  nested?: boolean;
  detailsOpen: boolean;
  onToggleDetails: () => void;
}) {
  const denom = denomination(tx);
  return (
    <>
      <TableCell className="w-8 p-1">
        <button
          type="button"
          className={`rounded p-1 hover:bg-muted ${detailsOpen ? "text-primary" : "text-muted-foreground"}`}
          title="Transaction details"
          aria-expanded={detailsOpen}
          onClick={(e) => {
            e.stopPropagation();
            onToggleDetails();
          }}
        >
          <Info className="h-4 w-4" />
        </button>
      </TableCell>
      <TableCell
        className={`whitespace-nowrap text-muted-foreground ${nested ? "pl-4" : ""}`}
      >
        {formatDateTime(tx.timestamp)}
      </TableCell>
      <TableCell>
        <Badge variant={TYPE_VARIANT[tx.transaction_type]}>
          {tx.transaction_type}
        </Badge>
      </TableCell>
      <TableCell>
        <AssetBadge asset={tx.asset} labels={assetLabels} />
      </TableCell>
      <TableCell className="text-muted-foreground" title={viaTooltip(tx)}>
        {viaLabel(tx, assetLabels)}
      </TableCell>
      <TableCell className="text-right tabular-nums">
        {formatNumber(tx.amount)} {assetLabels[tx.asset]?.symbol ?? tx.asset}
      </TableCell>
      <TableCell className="text-right tabular-nums">
        {tx.fiat_value_at_trigger > 0
          ? formatMoney(tx.fiat_value_at_trigger, denom)
          : "—"}
      </TableCell>
      <TableCell className="text-right tabular-nums text-muted-foreground">
        {tx.fee_fiat > 0 ? formatMoney(tx.fee_fiat, denom) : "—"}
      </TableCell>
      <TableCell className="text-muted-foreground">
        <SourceBadge
          source={tx.source}
          importId={tx.import_id}
          importSources={importSources}
          transactions={transactions}
        />
      </TableCell>
    </>
  );
}

export function TransactionList({
  transactions,
  assetLabels = {},
  scamAssets = [],
  importSources = [],
  disabledSources = new Set(),
  onToggleSource,
  onToggleAllSources,
  focusAsset = null,
  onClearFocusAsset,
}: {
  transactions: Transaction[];
  assetLabels?: Record<string, AssetLabel>;
  scamAssets?: string[];
  importSources?: ImportSource[];
  disabledSources?: ReadonlySet<string>;
  onToggleSource?: (sourceId: string) => void;
  onToggleAllSources?: () => void;
  /** When set, show only rows for this asset (and scroll target from parent). */
  focusAsset?: string | null;
  onClearFocusAsset?: () => void;
}) {
  const [localDisabledSources, setLocalDisabledSources] = useState<Set<string>>(
    new Set()
  );
  const sourceFilterControlled = onToggleSource !== undefined;
  const activeDisabledSources = sourceFilterControlled
    ? disabledSources
    : localDisabledSources;

  function toggleSource(sourceId: string) {
    if (onToggleSource) {
      onToggleSource(sourceId);
      return;
    }
    setLocalDisabledSources((prev) => {
      const next = new Set(prev);
      if (next.has(sourceId)) next.delete(sourceId);
      else next.add(sourceId);
      return next;
    });
  }

  function toggleAllSources() {
    if (onToggleAllSources) {
      onToggleAllSources();
      return;
    }
    setLocalDisabledSources((prev) => {
      if (prev.size === 0) {
        return new Set(countBySource(transactions).keys());
      }
      return new Set();
    });
  }
  const [hiddenTypes, setHiddenTypes] = useState<Set<TransactionType>>(new Set());
  const [hiddenAssets, setHiddenAssets] = useState<Set<string>>(new Set());
  const [hideDust, setHideDust] = useState(true);
  const [hideScams, setHideScams] = useState(true);
  const [search, setSearch] = useState("");
  const [pinnedAsset, setPinnedAsset] = useState<string | null>(null);
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set());
  const [expandedDetails, setExpandedDetails] = useState<Set<string>>(new Set());

  useEffect(() => {
    if (!focusAsset) return;
    setPinnedAsset(focusAsset);
    setSearch("");
    setHiddenAssets(new Set());
    setHiddenTypes(new Set());
  }, [focusAsset]);

  function clearAssetPin() {
    setPinnedAsset(null);
    onClearFocusAsset?.();
  }

  const toggleDetails = (id: string) => {
    setExpandedDetails((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const scamSet = useMemo(() => new Set(scamAssets), [scamAssets]);

  const isScamTransaction = (tx: Transaction) =>
    scamSet.has(tx.asset) || (tx.counter_asset ? scamSet.has(tx.counter_asset) : false);

  const assets = useMemo(
    () =>
      [...new Set(transactions.map((t) => t.asset))].sort((a, b) =>
        a.localeCompare(b)
      ),
    [transactions]
  );

  const types = useMemo(
    () =>
      [...new Set(transactions.map((t) => t.transaction_type))].sort(),
    [transactions]
  ) as TransactionType[];

  const typeCounts = useMemo(() => {
    const counts = new Map<TransactionType, number>();
    for (const tx of transactions) {
      counts.set(tx.transaction_type, (counts.get(tx.transaction_type) ?? 0) + 1);
    }
    return counts;
  }, [transactions]);

  const assetCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const tx of transactions) {
      counts.set(tx.asset, (counts.get(tx.asset) ?? 0) + 1);
    }
    return counts;
  }, [transactions]);

  const filtered = useMemo(() => {
    return transactions
      .filter((t) => matchesSourceFilter(t.source, activeDisabledSources))
      .filter((t) => matchesSearch(t, search, assetLabels))
      .filter((t) => !hiddenTypes.has(t.transaction_type))
      .filter((t) => !hiddenAssets.has(t.asset))
      .filter(
        (t) =>
          !pinnedAsset ||
          t.asset === pinnedAsset ||
          t.counter_asset === pinnedAsset
      )
      .filter((t) => !hideDust || !isDustTransaction(t))
      .filter((t) => !hideScams || !isScamTransaction(t))
      .sort(
        (a, b) =>
          new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime()
      );
  }, [
    transactions,
    activeDisabledSources,
    search,
    assetLabels,
    hiddenTypes,
    hiddenAssets,
    pinnedAsset,
    hideDust,
    hideScams,
    scamSet,
  ]);

  const hiddenTypeCount = hiddenTypes.size;
  const hiddenAssetCount = hiddenAssets.size;

  function toggleAllTypes() {
    setHiddenTypes((prev) => {
      if (prev.size === 0) return new Set(types);
      return new Set();
    });
  }

  function toggleType(type: TransactionType) {
    setHiddenTypes((prev) => {
      const next = new Set(prev);
      if (next.has(type)) next.delete(type);
      else next.add(type);
      return next;
    });
  }

  function toggleAllAssets() {
    setHiddenAssets((prev) => {
      if (prev.size === 0) return new Set(assets);
      return new Set();
    });
  }

  function toggleAsset(asset: string) {
    setHiddenAssets((prev) => {
      const next = new Set(prev);
      if (next.has(asset)) next.delete(asset);
      else next.add(asset);
      return next;
    });
  }

  const sortedTypes = useMemo(
    () => TYPE_ORDER.filter((t) => types.includes(t)),
    [types]
  );

  const typeFilterItems = useMemo(
    () =>
      sortedTypes.map((type) => ({
        id: type,
        label: type,
        count: typeCounts.get(type),
      })),
    [sortedTypes, typeCounts]
  );

  const assetFilterItems = useMemo(
    () =>
      assets.map((asset) => ({
        id: asset,
        label: assetLabels[asset]?.symbol ?? asset,
        count: assetCounts.get(asset),
      })),
    [assets, assetLabels, assetCounts]
  );

  const rows = useMemo(() => groupTransactions(filtered), [filtered]);

  const hiddenDustCount = useMemo(() => {
    if (!hideDust) return 0;
    return transactions.filter(isDustTransaction).length;
  }, [transactions, hideDust]);

  const hiddenScamCount = useMemo(() => {
    if (!hideScams) return 0;
    return transactions.filter(isScamTransaction).length;
  }, [transactions, hideScams, scamSet]);

  const hiddenSourceCount = activeDisabledSources.size;

  function toggleGroup(id: string) {
    setExpandedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  const pinnedLabel = pinnedAsset
    ? (assetLabels[pinnedAsset]?.symbol ?? pinnedAsset)
    : null;

  return (
    <Card id="transaction-ledger">
      <CardHeader className="flex flex-col gap-3 space-y-0 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0 space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <ListOrdered className="h-4 w-4 text-primary" />
            <CardTitle className="text-base">Transaction Ledger</CardTitle>
            <Badge variant="muted">
              {filtered.length} tx
              {hiddenTypeCount > 0 ? ` · ${hiddenTypeCount} type(s) hidden` : ""}
              {hiddenAssetCount > 0 ? ` · ${hiddenAssetCount} asset(s) hidden` : ""}
              {hiddenDustCount > 0 ? ` · ${hiddenDustCount} dust hidden` : ""}
              {hiddenScamCount > 0 ? ` · ${hiddenScamCount} scam hidden` : ""}
              {hiddenSourceCount > 0
                ? ` · ${hiddenSourceCount} source(s) hidden`
                : ""}
            </Badge>
          </div>
          <SectionDescription>
            Every imported movement — buys, sells, transfers, staking, and swaps.
            Filters and search only affect the view; cost basis and tax figures
            are recalculated from the full ledger on refresh.
          </SectionDescription>
          {pinnedAsset ? (
            <div className="flex flex-wrap items-center gap-2 pt-1">
              <Badge variant="outline" className="text-xs">
                Showing {pinnedLabel} only
              </Badge>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-7 px-2 text-xs"
                onClick={clearAssetPin}
              >
                <X className="mr-1 h-3.5 w-3.5" />
                Clear filter
              </Button>
            </div>
          ) : null}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative min-w-[200px] flex-1 sm:max-w-xs">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
            <input
              type="text"
              role="searchbox"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search ledger…"
              aria-label="Search transactions"
              className="h-9 w-full rounded-md border border-input bg-background pl-8 pr-8 text-sm leading-9 placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
            />
            {search ? (
              <button
                type="button"
                onClick={() => setSearch("")}
                className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-0.5 text-muted-foreground hover:text-foreground"
                aria-label="Clear search"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            ) : null}
          </div>
          <label className="flex h-9 cursor-pointer items-center gap-2 rounded-md border border-input bg-background px-3 text-sm">
            <input
              type="checkbox"
              checked={hideDust}
              onChange={(e) => setHideDust(e.target.checked)}
              className="h-3.5 w-3.5 rounded border-input accent-primary"
            />
            Hide dust
          </label>
          {scamAssets.length > 0 ? (
            <label className="flex h-9 cursor-pointer items-center gap-2 rounded-md border border-input bg-background px-3 text-sm">
              <input
                type="checkbox"
                checked={hideScams}
                onChange={(e) => setHideScams(e.target.checked)}
                className="h-3.5 w-3.5 rounded border-input accent-primary"
              />
              Hide scams
            </label>
          ) : null}
          <CheckboxFilterDropdown
            allLabel="All types"
            items={typeFilterItems}
            hiddenIds={hiddenTypes}
            onToggleAll={toggleAllTypes}
            onToggleItem={(id) => toggleType(id as TransactionType)}
            className="w-[140px] shrink-0"
          />
          <CheckboxFilterDropdown
            allLabel="All assets"
            items={assetFilterItems}
            hiddenIds={hiddenAssets}
            onToggleAll={toggleAllAssets}
            onToggleItem={toggleAsset}
            className="w-[140px] shrink-0"
            menuClassName="min-w-[220px]"
          />
          <LedgerSourceFilters
            transactions={transactions}
            importSources={importSources}
            disabledSources={activeDisabledSources}
            onToggleSource={toggleSource}
            onToggleAllSources={toggleAllSources}
            className="w-[150px] shrink-0"
          />
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <LedgerSourcesOverview
          transactions={transactions}
          importSources={importSources}
        />
        {filtered.length === 0 ? (
          <p className="py-6 text-center text-sm text-muted-foreground">
            {search.trim()
              ? `No transactions match "${search.trim()}".`
              : "No transactions match the current filters."}
          </p>
        ) : (
          <div className="max-h-[420px] overflow-auto rounded-md border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-8" />
                  <TableHead className="w-8" />
                  <TableHead>Date</TableHead>
                  <TableHead>Type</TableHead>
                  <TableHead>Asset</TableHead>
                  <TableHead>Via</TableHead>
                  <TableHead className="text-right">Amount</TableHead>
                  <TableHead className="text-right">Value</TableHead>
                  <TableHead className="text-right">Fee</TableHead>
                  <TableHead>Source</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((row) => {
                  if (row.kind === "single") {
                    const detailsOpen = expandedDetails.has(row.tx.id);
                    return (
                      <Fragment key={row.tx.id}>
                        <TableRow>
                          <TableCell />
                          <TransactionCells
                            tx={row.tx}
                            assetLabels={assetLabels}
                            importSources={importSources}
                            transactions={transactions}
                            detailsOpen={detailsOpen}
                            onToggleDetails={() => toggleDetails(row.tx.id)}
                          />
                        </TableRow>
                        {detailsOpen ? (
                          <TableRow className="bg-muted/20 hover:bg-muted/20">
                            <TableCell />
                            <TableCell colSpan={9} className="px-4 py-0">
                              <TransactionDetails
                                tx={row.tx}
                                assetLabels={assetLabels}
                                importSources={importSources}
                              />
                            </TableCell>
                          </TableRow>
                        ) : null}
                      </Fragment>
                    );
                  }

                  const expanded = expandedGroups.has(row.id);
                  const summary = summarizeGroup(row.txs, assetLabels);

                  return (
                    <Fragment key={row.id}>
                      <TableRow
                        className="cursor-pointer"
                        onClick={() => toggleGroup(row.id)}
                      >
                        <TableCell className="w-8 pr-0">
                          {expanded ? (
                            <ChevronDown className="h-4 w-4 text-muted-foreground" />
                          ) : (
                            <ChevronRight className="h-4 w-4 text-muted-foreground" />
                          )}
                        </TableCell>
                        <TableCell className="w-8" />
                        <TableCell className="whitespace-nowrap text-muted-foreground">
                          {formatDateTime(summary.timestamp)}
                        </TableCell>
                        <TableCell>
                          <Badge variant={TYPE_VARIANT[summary.displayType]}>
                            {summary.displayType}
                          </Badge>
                          <span className="ml-2 text-xs text-muted-foreground">
                            {row.label}
                          </span>
                        </TableCell>
                        <TableCell>
                          <AssetBadge
                            asset={summary.primaryAsset}
                            labels={assetLabels}
                          />
                        </TableCell>
                        <TableCell className="text-muted-foreground">
                          {summary.via}
                        </TableCell>
                        <TableCell className="text-right tabular-nums">
                          {formatNumber(summary.totalAmount)}{" "}
                          {assetLabels[summary.primaryAsset]?.symbol ??
                            summary.primaryAsset}
                        </TableCell>
                        <TableCell className="text-right tabular-nums">
                          {summary.totalValue > 0
                            ? formatMoney(summary.totalValue, summary.denom)
                            : "—"}
                        </TableCell>
                        <TableCell className="text-right tabular-nums text-muted-foreground">
                          {summary.totalFee > 0
                            ? formatMoney(summary.totalFee, summary.denom)
                            : "—"}
                        </TableCell>
                        <TableCell className="text-muted-foreground">
                          <SourceBadge
                            source={summary.source}
                            importId={row.txs[0]?.import_id}
                            importSources={importSources}
                          />
                        </TableCell>
                      </TableRow>
                      {expanded
                        ? row.txs.map((tx) => {
                            const detailsOpen = expandedDetails.has(tx.id);
                            return (
                              <Fragment key={tx.id}>
                                <TableRow className="bg-muted/10">
                                  <TableCell />
                                  <TransactionCells
                                    tx={tx}
                                    assetLabels={assetLabels}
                                    importSources={importSources}
                                    transactions={transactions}
                                    nested
                                    detailsOpen={detailsOpen}
                                    onToggleDetails={() => toggleDetails(tx.id)}
                                  />
                                </TableRow>
                                {detailsOpen ? (
                                  <TableRow className="bg-muted/20 hover:bg-muted/20">
                                    <TableCell />
                                    <TableCell colSpan={9} className="px-4 py-0">
                                      <TransactionDetails
                                        tx={tx}
                                        assetLabels={assetLabels}
                                        importSources={importSources}
                                      />
                                    </TableCell>
                                  </TableRow>
                                ) : null}
                              </Fragment>
                            );
                          })
                        : null}
                    </Fragment>
                  );
                })}
              </TableBody>
            </Table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
