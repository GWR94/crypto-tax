import { Fragment, useMemo, useState } from "react";
import {
  Ban,
  ChevronDown,
  ChevronRight,
  Eye,
  MapPin,
  TrendingUp,
} from "lucide-react";
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
  TableFoot,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { SectionDescription } from "@/components/ui/section-description";
import {
  cn,
  formatMoney,
  formatNumber,
  formatUnitPrice,
} from "@/lib/utils";
import type {
  AssetLabel,
  AssetPnlDetail,
  DisplayCurrency,
  HoldingRow,
  ImportSource,
  Position,
  TaxJurisdiction,
  Transaction,
} from "@/lib/types";
import { AssetBadge } from "@/components/AssetBadge";
import { PnlAmountCell } from "@/components/PnlAmountCell";
import { PnlUnrealizedDetailPanel } from "@/components/PnlDetailPanel";

const SOURCE_LABELS: Record<string, string> = {
  market: "Manual",
  live: "Live",
  dex: "DEX",
  illiquid: "No market",
  cost_basis: "At cost",
};

export function PerCoinTable({
  positions,
  holdings = [],
  currency,
  assetLabels = {},
  pnlByAsset = {},
  transactions = [],
  importSources = [],
  jurisdiction = "UK",
  hideStaking = false,
  hiddenScams = [],
  onMarkScam,
  onUnmarkScam,
  onViewInLedger,
  disabled = false,
}: {
  positions: Position[];
  holdings?: HoldingRow[];
  currency: DisplayCurrency;
  assetLabels?: Record<string, AssetLabel>;
  pnlByAsset?: Record<string, AssetPnlDetail>;
  transactions?: Transaction[];
  importSources?: ImportSource[];
  jurisdiction?: TaxJurisdiction;
  hideStaking?: boolean;
  hiddenScams?: string[];
  onMarkScam?: (asset: string) => void | Promise<void>;
  onUnmarkScam?: (asset: string) => void | Promise<void>;
  onViewInLedger?: (asset: string) => void;
  disabled?: boolean;
}) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const fmt = (value: number, opts?: Intl.NumberFormatOptions) =>
    formatMoney(value, currency, opts);

  const positionByAsset = useMemo(
    () => new Map(positions.map((p) => [p.asset, p])),
    [positions]
  );

  const rows = holdings.length > 0 ? holdings : positions.map(toHoldingFromPosition);

  const tradable = rows.filter((h) => !h.is_stablecoin);
  const totalInvested = tradable.reduce((sum, h) => sum + h.total_invested, 0);
  const totalValue = tradable.reduce((sum, h) => sum + h.current_value, 0);
  const totalPnl = tradable.reduce((sum, h) => sum + h.unrealized_pnl, 0);
  const totalPnlPct =
    totalInvested > 0 ? (totalPnl / totalInvested) * 100 : 0;

  const toggle = (asset: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(asset)) next.delete(asset);
      else next.add(asset);
      return next;
    });
  };

  const showScamActions = Boolean(onMarkScam);
  const showLedgerLink = Boolean(onViewInLedger);
  const actionColCount = (showScamActions ? 1 : 0) + (showLedgerLink ? 1 : 0);

  return (
    <Card className="h-full">
      <CardHeader className="flex flex-row items-start gap-2 space-y-0">
        <TrendingUp className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
        <div className="space-y-1">
          <CardTitle className="text-base">Unrealized Profit &amp; Loss</CardTitle>
          <SectionDescription>
            Open positions with live prices where available, otherwise estimated
            from your last trade or cost basis. Click a row to see the
            acquisition lots behind the paper P&amp;L.
          </SectionDescription>
        </div>
      </CardHeader>
      <CardContent>
        {rows.length === 0 ? (
          <p className="py-6 text-center text-sm text-muted-foreground">
            No open positions on record.
          </p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-8" />
                <TableHead>Coin</TableHead>
                <TableHead className="text-right">Holdings</TableHead>
                <TableHead className="w-[120px]">Allocation</TableHead>
                <TableHead className="text-right">Avg Cost</TableHead>
                <TableHead className="text-right">Market Price</TableHead>
                <TableHead className="text-right">Invested</TableHead>
                <TableHead className="text-right">Current Value</TableHead>
                <TableHead className="text-right">Profit &amp; Loss</TableHead>
                {actionColCount > 0 ? (
                  <TableHead className="w-[120px] text-right" />
                ) : null}
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((h) => {
                const p = positionByAsset.get(h.asset);
                const open = expanded.has(h.asset);
                const hasDetail = (pnlByAsset[h.asset]?.open_lots.length ?? 0) > 0;
                const hasValue = h.current_value > 0;
                const marketPrice =
                  p?.current_price ??
                  (h.quantity > 0 ? h.current_value / h.quantity : 0);
                const avgCost = p?.average_cost_basis ?? h.average_cost_basis;
                const colSpan = 8 + actionColCount;

                return (
                  <Fragment key={h.asset}>
                    <TableRow
                      className={hasDetail ? "cursor-pointer" : undefined}
                      onClick={() => hasDetail && toggle(h.asset)}
                    >
                      <TableCell className="w-8 pr-0">
                        {hasDetail ? (
                          open ? (
                            <ChevronDown className="h-4 w-4 text-muted-foreground" />
                          ) : (
                            <ChevronRight className="h-4 w-4 text-muted-foreground" />
                          )
                        ) : null}
                      </TableCell>
                      <TableCell>
                        <AssetMetaBadges
                          asset={h.asset}
                          holding={h}
                          assetLabels={assetLabels}
                        />
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {formatNumber(h.quantity)}
                      </TableCell>
                      <TableCell>
                        {hasValue ? (
                          <div className="flex items-center gap-2">
                            <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
                              <div
                                className={cn(
                                  "h-full rounded-full",
                                  h.is_stablecoin
                                    ? "bg-muted-foreground/50"
                                    : "bg-primary"
                                )}
                                style={{
                                  width: `${Math.min(h.portfolio_pct, 100)}%`,
                                }}
                              />
                            </div>
                            <span className="w-10 shrink-0 text-right text-xs tabular-nums text-muted-foreground">
                              {h.portfolio_pct.toFixed(1)}%
                            </span>
                          </div>
                        ) : (
                          <span className="text-xs text-muted-foreground">—</span>
                        )}
                      </TableCell>
                      <TableCell className="text-right tabular-nums text-muted-foreground">
                        {avgCost > 0
                          ? formatUnitPrice(avgCost, currency)
                          : "—"}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {hasValue && marketPrice > 0
                          ? formatUnitPrice(marketPrice, currency)
                          : "—"}
                      </TableCell>
                      <TableCell className="text-right tabular-nums text-muted-foreground">
                        {h.total_invested > 0
                          ? fmt(h.total_invested)
                          : h.is_stablecoin && hasValue
                            ? fmt(h.current_value)
                            : "—"}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {hasValue ? (
                          <span
                            className={cn(h.is_estimated && "text-muted-foreground")}
                          >
                            {h.is_estimated ? "~" : ""}
                            {fmt(h.current_value)}
                          </span>
                        ) : (
                          <span className="text-muted-foreground">—</span>
                        )}
                      </TableCell>
                      <TableCell className="text-right">
                        {(hasValue || h.total_invested > 0) &&
                        (h.total_invested > 0 || h.is_stablecoin) ? (
                          <PnlAmountCell
                            value={h.unrealized_pnl}
                            pct={h.unrealized_pnl_pct}
                            currency={currency}
                          />
                        ) : (
                          <span className="text-muted-foreground">—</span>
                        )}
                      </TableCell>
                      {actionColCount > 0 ? (
                        <TableCell className="text-right">
                          <div className="flex items-center justify-end gap-0.5">
                            {showLedgerLink ? (
                              <Button
                                type="button"
                                variant="ghost"
                                size="sm"
                                disabled={disabled}
                                className="h-7 px-2 text-xs text-muted-foreground hover:text-foreground"
                                title="View ledger rows for this asset"
                                onClick={(event) => {
                                  event.stopPropagation();
                                  onViewInLedger?.(h.asset);
                                }}
                              >
                                <MapPin className="h-3.5 w-3.5" />
                                <span className="sr-only sm:not-sr-only sm:ml-1">
                                  Location
                                </span>
                              </Button>
                            ) : null}
                            {showScamActions && !h.is_stablecoin ? (
                              <Button
                                type="button"
                                variant="ghost"
                                size="sm"
                                disabled={disabled}
                                className="h-7 px-2 text-xs text-muted-foreground hover:text-destructive"
                                title="Mark as scam and hide from portfolio"
                                onClick={(event) => {
                                  event.stopPropagation();
                                  void onMarkScam?.(h.asset);
                                }}
                              >
                                <Ban className="h-3.5 w-3.5" />
                                <span className="sr-only sm:not-sr-only sm:ml-1">
                                  Scam
                                </span>
                              </Button>
                            ) : null}
                          </div>
                        </TableCell>
                      ) : null}
                    </TableRow>
                    {open ? (
                      <TableRow className="bg-muted/10 hover:bg-muted/10">
                        <TableCell />
                        <TableCell colSpan={colSpan} className="px-4 py-3">
                          <PnlUnrealizedDetailPanel
                            asset={h.asset}
                            detail={pnlByAsset[h.asset]}
                            transactions={transactions}
                            currency={currency}
                            assetLabels={assetLabels}
                            importSources={importSources}
                            jurisdiction={jurisdiction}
                            hideStaking={hideStaking}
                          />
                        </TableCell>
                      </TableRow>
                    ) : null}
                  </Fragment>
                );
              })}
            </TableBody>
            {tradable.length > 0 ? (
              <TableFoot>
                <TableRow className="border-t-2 font-semibold">
                  <TableCell />
                  <TableCell>Total (excl. cash)</TableCell>
                  <TableCell />
                  <TableCell />
                  <TableCell />
                  <TableCell />
                  <TableCell className="text-right tabular-nums text-muted-foreground">
                    {fmt(totalInvested)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {fmt(totalValue)}
                  </TableCell>
                  <TableCell className="text-right">
                    <PnlAmountCell
                      value={totalPnl}
                      pct={totalPnlPct}
                      currency={currency}
                    />
                  </TableCell>
                  {actionColCount > 0 ? <TableCell /> : null}
                </TableRow>
              </TableFoot>
            ) : null}
          </Table>
        )}

        {hiddenScams.length > 0 ? (
          <div className="mt-4 space-y-2 rounded-lg border border-border bg-muted/10 p-3">
            <div className="flex items-center gap-2">
              <Ban className="h-4 w-4 text-destructive" />
              <p className="text-sm font-medium">Hidden scam tokens</p>
              <Badge variant="muted" className="text-[10px]">
                {hiddenScams.length}
              </Badge>
            </div>
            <p className="text-xs text-muted-foreground">
              These are excluded from portfolio totals and holdings. Transactions
              stay in your ledger for tax records.
            </p>
            <ul className="space-y-1.5">
              {hiddenScams.map((asset) => (
                <li
                  key={asset}
                  className="flex items-center justify-between gap-2 rounded-md border border-border bg-card px-2.5 py-1.5"
                >
                  <AssetBadge asset={asset} labels={assetLabels} />
                  {onUnmarkScam ? (
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      className="h-7 shrink-0 px-2 text-xs"
                      disabled={disabled}
                      onClick={() => void onUnmarkScam(asset)}
                    >
                      <Eye className="h-3.5 w-3.5" />
                      Restore
                    </Button>
                  ) : null}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

function toHoldingFromPosition(p: Position): HoldingRow {
  return {
    asset: p.asset,
    quantity: p.quantity,
    average_cost_basis: p.average_cost_basis,
    current_value: p.current_value,
    total_invested: p.total_invested,
    portfolio_pct: 0,
    is_stablecoin: false,
    unrealized_pnl: p.unrealized_pnl,
    unrealized_pnl_pct: p.unrealized_pnl_pct,
  };
}

function AssetMetaBadges({
  asset,
  holding,
  assetLabels,
}: {
  asset: string;
  holding: HoldingRow;
  assetLabels: Record<string, AssetLabel>;
}) {
  const sourceLabel =
    holding.price_source && SOURCE_LABELS[holding.price_source]
      ? SOURCE_LABELS[holding.price_source]
      : null;

  return (
    <div className="flex flex-wrap items-center gap-2">
      <AssetBadge asset={asset} labels={assetLabels} />
      {holding.is_stablecoin ? (
        <Badge variant="muted" className="text-[10px]">
          Cash
        </Badge>
      ) : null}
      {holding.is_estimated && sourceLabel ? (
        <Badge variant="outline" className="text-[10px]">
          {sourceLabel}
        </Badge>
      ) : null}
      {holding.price_source === "illiquid" ? (
        <Badge variant="destructive" className="text-[10px]">
          Rugged / illiquid
        </Badge>
      ) : null}
      {!holding.is_estimated &&
      (holding.price_source === "live" || holding.price_source === "dex") ? (
        <Badge variant="muted" className="text-[10px]">
          {holding.price_source === "dex" ? "DEX" : "Live"}
        </Badge>
      ) : null}
    </div>
  );
}
