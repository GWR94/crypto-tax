import { Fragment, useMemo, useState } from "react";
import {
  ArrowDownRight,
  ArrowUpRight,
  ChevronDown,
  ChevronRight,
  LineChart,
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
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import type { AssetLabel, DisplayCurrency, Transaction } from "@/lib/types";
import { SourceBadge } from "@/components/SourceBadge";
import { SourceIcon } from "@/components/icons/SourceIcon";
import {
  groupPerpTransactions,
  summarizePerpGroup,
} from "@/lib/groupPerpTransactions";
import { buildPerpsSummary } from "@/lib/perpsSummary";
import {
  countBySource,
  getSourceDefinition,
  normalizeSourceId,
} from "@/lib/sourceCatalog";
import { cn, formatDateTime, formatMoney, formatNumber } from "@/lib/utils";
import { formatInstrument } from "@/lib/instruments";
import {
  perpRowNotional,
  perpRowSize,
  perpSideLabel,
} from "@/lib/perpsDisplay";

const SIDE_VARIANT = {
  BUY: "success",
  SELL: "destructive",
  DEPOSIT: "outline",
  WITHDRAW: "outline",
  TRANSFER: "outline",
  FEE: "muted",
} as const;

function contractLabel(tx: Transaction): string {
  return formatInstrument(tx.instrument, {
    asset: tx.asset,
    counter_asset: tx.counter_asset,
  });
}

function PerpRowCells({
  tx,
  assetLabels,
  currency,
  nested = false,
}: {
  tx: Transaction;
  assetLabels: Record<string, AssetLabel>;
  currency: DisplayCurrency;
  nested?: boolean;
}) {
  const side = perpSideLabel(tx);
  const pnl = tx.realized_pnl;
  const denom = tx.fiat_currency ?? currency;
  const size = perpRowSize(tx, assetLabels);
  const notional = perpRowNotional(tx);

  return (
    <>
      <TableCell
        className={cn(
          "whitespace-nowrap text-muted-foreground",
          nested && "pl-8"
        )}
      >
        {formatDateTime(tx.timestamp)}
      </TableCell>
      <TableCell className="font-medium">{contractLabel(tx)}</TableCell>
      <TableCell className="text-muted-foreground">
        {tx.venue_order_type ?? "—"}
      </TableCell>
      <TableCell>
        <Badge
          variant={SIDE_VARIANT[side as keyof typeof SIDE_VARIANT] ?? "outline"}
        >
          {side}
        </Badge>
      </TableCell>
      <TableCell className="text-right tabular-nums">
        {size ? (
          <>
            {formatNumber(size.amount)} {size.asset}
          </>
        ) : (
          "—"
        )}
      </TableCell>
      <TableCell className="text-right tabular-nums">
        {notional != null ? formatMoney(notional, denom) : "—"}
      </TableCell>
      <TableCell className="text-right tabular-nums">
        {pnl == null ? (
          "—"
        ) : (
          <span
            className={cn(
              "font-medium",
              pnl >= 0 ? "text-success" : "text-destructive"
            )}
          >
            {formatMoney(pnl, denom)}
          </span>
        )}
      </TableCell>
      <TableCell className="text-right tabular-nums text-muted-foreground">
        {tx.fee_fiat > 0 ? formatMoney(tx.fee_fiat, denom) : "—"}
      </TableCell>
      <TableCell className="text-muted-foreground">
        <SourceBadge source={tx.source} />
      </TableCell>
    </>
  );
}

export function PerpsSection({
  transactions,
  currency,
  assetLabels = {},
}: {
  transactions: Transaction[];
  currency: DisplayCurrency;
  assetLabels?: Record<string, AssetLabel>;
}) {
  const [collapsed, setCollapsed] = useState(false);
  const [disabledExchanges, setDisabledExchanges] = useState<Set<string>>(
    new Set()
  );
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set());

  const fmt = (value: number, opts?: Intl.NumberFormatOptions) =>
    formatMoney(value, currency, opts);

  const exchangeIds = useMemo(() => {
    const counts = countBySource(transactions);
    return [...counts.keys()].sort((a, b) =>
      getSourceDefinition(a).label.localeCompare(getSourceDefinition(b).label)
    );
  }, [transactions]);

  const filtered = useMemo(
    () =>
      transactions.filter(
        (t) => !disabledExchanges.has(normalizeSourceId(t.source))
      ),
    [transactions, disabledExchanges]
  );

  const summary = useMemo(() => buildPerpsSummary(filtered), [filtered]);
  const rows = useMemo(() => groupPerpTransactions(filtered), [filtered]);
  const pnlPositive = summary.closed_pnl >= 0;
  const filtering = disabledExchanges.size > 0;

  function toggleExchange(sourceId: string) {
    setDisabledExchanges((prev) => {
      const next = new Set(prev);
      if (next.has(sourceId)) next.delete(sourceId);
      else next.add(sourceId);
      return next;
    });
  }

  function toggleGroup(id: string) {
    setExpandedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  return (
    <Card>
      <CardHeader
        className="flex cursor-pointer flex-col gap-3 space-y-0 sm:flex-row sm:items-center sm:justify-between"
        onClick={() => setCollapsed((v) => !v)}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            setCollapsed((v) => !v);
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
          <LineChart className="h-4 w-4 text-primary" />
          <CardTitle className="text-base">Perpetuals</CardTitle>
          <Badge variant="muted">{summary.trade_count} fills</Badge>
        </div>
        <p className="text-sm text-muted-foreground">
          Derivatives are separate from spot holdings and FIFO tax lots.
        </p>
      </CardHeader>

      {!collapsed ? (
        <CardContent className="space-y-4">
          {transactions.length === 0 ? (
            <p className="py-4 text-center text-sm text-muted-foreground">
              No perpetual futures trades yet. Re-import a{" "}
              <code className="text-xs">0x…</code> wallet for Hyperliquid, or
              import a WOO X perp CSV.
            </p>
          ) : (
            <>
              {exchangeIds.length > 1 ? (
                <div className="space-y-2" onClick={(e) => e.stopPropagation()}>
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-xs text-muted-foreground">
                      Exchanges
                    </span>
                    {filtering ? (
                      <button
                        type="button"
                        onClick={() => setDisabledExchanges(new Set())}
                        className="text-xs text-primary underline-offset-2 hover:underline"
                      >
                        Show all
                      </button>
                    ) : (
                      <span className="text-xs text-muted-foreground">
                        · click to hide
                      </span>
                    )}
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {exchangeIds.map((id) => {
                      const def = getSourceDefinition(id);
                      const count = countBySource(transactions).get(id) ?? 0;
                      const active = !disabledExchanges.has(id);
                      return (
                        <button
                          key={id}
                          type="button"
                          aria-pressed={active}
                          title={`${def.label} (${count})`}
                          onClick={() => toggleExchange(id)}
                          className={cn(
                            "inline-flex items-center gap-2 rounded-lg border px-2.5 py-1.5 text-sm transition-colors",
                            active
                              ? "border-border bg-card hover:border-primary/40"
                              : "border-border/50 bg-muted/10 opacity-50"
                          )}
                        >
                          <SourceIcon source={id} muted={!active} />
                          <span
                            className={cn(
                              "font-medium",
                              !active && "line-through"
                            )}
                          >
                            {def.label}
                          </span>
                          <span className="text-xs text-muted-foreground tabular-nums">
                            {count}
                          </span>
                        </button>
                      );
                    })}
                  </div>
                </div>
              ) : null}

              {summary.trade_count === 0 ? (
                <p className="py-4 text-center text-sm text-muted-foreground">
                  No fills match the selected exchanges.
                </p>
              ) : (
                <>
                  <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                    <div className="rounded-lg border border-border bg-muted/20 px-3 py-2">
                      <p className="text-xs text-muted-foreground">Closed PnL</p>
                      <p
                        className={cn(
                          "flex items-center gap-1 text-lg font-semibold tabular-nums",
                          pnlPositive ? "text-success" : "text-destructive"
                        )}
                      >
                        {pnlPositive ? (
                          <ArrowUpRight className="h-4 w-4" />
                        ) : (
                          <ArrowDownRight className="h-4 w-4" />
                        )}
                        {fmt(Math.abs(summary.closed_pnl))}
                      </p>
                    </div>
                    <div className="rounded-lg border border-border bg-muted/20 px-3 py-2">
                      <p className="text-xs text-muted-foreground">Fees</p>
                      <p className="text-lg font-semibold tabular-nums">
                        {fmt(summary.total_fees)}
                      </p>
                    </div>
                    <div className="rounded-lg border border-border bg-muted/20 px-3 py-2">
                      <p className="text-xs text-muted-foreground">Notional</p>
                      <p className="text-lg font-semibold tabular-nums">
                        {fmt(summary.total_notional)}
                      </p>
                    </div>
                    <div className="rounded-lg border border-border bg-muted/20 px-3 py-2">
                      <p className="text-xs text-muted-foreground">
                        Wins / Losses
                      </p>
                      <p className="text-lg font-semibold tabular-nums">
                        {summary.winning_closes} / {summary.losing_closes}
                      </p>
                    </div>
                  </div>

                  <div className="max-h-[360px] overflow-auto rounded-md border">
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead className="w-8" />
                          <TableHead>Date</TableHead>
                          <TableHead>Contract</TableHead>
                          <TableHead>Order</TableHead>
                          <TableHead>Side</TableHead>
                          <TableHead className="text-right">Size</TableHead>
                          <TableHead className="text-right">Notional</TableHead>
                          <TableHead className="text-right">
                            Realized PnL
                          </TableHead>
                          <TableHead className="text-right">Fee</TableHead>
                          <TableHead>Source</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {rows.map((row) => {
                          if (row.kind === "single") {
                            return (
                              <TableRow key={row.tx.id}>
                                <TableCell />
                                <PerpRowCells
                                  tx={row.tx}
                                  assetLabels={assetLabels}
                                  currency={currency}
                                />
                              </TableRow>
                            );
                          }

                          const expanded = expandedGroups.has(row.id);
                          const summaryRow = summarizePerpGroup(row.txs, assetLabels);

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
                                <TableCell className="whitespace-nowrap text-muted-foreground">
                                  {formatDateTime(summaryRow.timestamp)}
                                </TableCell>
                                <TableCell className="font-medium">
                                  {summaryRow.contract}
                                </TableCell>
                                <TableCell className="text-muted-foreground">
                                  {summaryRow.venue ?? "—"}
                                </TableCell>
                                <TableCell>
                                  <Badge
                                    variant={
                                      SIDE_VARIANT[
                                        summaryRow.displayType as keyof typeof SIDE_VARIANT
                                      ] ?? "outline"
                                    }
                                  >
                                    {summaryRow.displayType}
                                  </Badge>
                                </TableCell>
                                <TableCell className="text-right tabular-nums">
                                  {summaryRow.totalAmount > 0 ? (
                                    <>
                                      {formatNumber(summaryRow.totalAmount)}{" "}
                                      {summaryRow.assetLabel}
                                    </>
                                  ) : (
                                    "—"
                                  )}
                                </TableCell>
                                <TableCell className="text-right tabular-nums">
                                  {summaryRow.totalNotional > 0
                                    ? formatMoney(
                                        summaryRow.totalNotional,
                                        summaryRow.denom
                                      )
                                    : "—"}
                                </TableCell>
                                <TableCell className="text-right tabular-nums">
                                  {!summaryRow.hasPnl ? (
                                    "—"
                                  ) : (
                                    <span
                                      className={cn(
                                        "font-medium",
                                        summaryRow.totalPnl >= 0
                                          ? "text-success"
                                          : "text-destructive"
                                      )}
                                    >
                                      {formatMoney(
                                        summaryRow.totalPnl,
                                        summaryRow.denom
                                      )}
                                    </span>
                                  )}
                                </TableCell>
                                <TableCell className="text-right tabular-nums text-muted-foreground">
                                  {summaryRow.totalFee > 0
                                    ? formatMoney(
                                        summaryRow.totalFee,
                                        summaryRow.denom
                                      )
                                    : "—"}
                                </TableCell>
                                <TableCell className="text-muted-foreground">
                                  <SourceBadge source={summaryRow.source} />
                                </TableCell>
                              </TableRow>
                              {expanded
                                ? row.txs.map((tx) => (
                                    <TableRow key={tx.id} className="bg-muted/10">
                                      <TableCell />
                                      <PerpRowCells
                                        tx={tx}
                                        assetLabels={assetLabels}
                                        currency={currency}
                                        nested
                                      />
                                    </TableRow>
                                  ))
                                : null}
                            </Fragment>
                          );
                        })}
                      </TableBody>
                    </Table>
                  </div>
                </>
              )}
            </>
          )}
        </CardContent>
      ) : null}
    </Card>
  );
}
