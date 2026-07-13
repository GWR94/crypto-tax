import { Fragment, useState } from "react";
import { ChevronDown, ChevronRight, Receipt } from "lucide-react";
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
import { SectionDescription } from "@/components/ui/section-description";
import { formatMoney, formatNumber } from "@/lib/utils";
import type {
  AssetLabel,
  AssetPnlDetail,
  DisplayCurrency,
  ImportSource,
  RealizedPnlRow,
  TaxJurisdiction,
  Transaction,
} from "@/lib/types";
import { AssetBadge } from "@/components/AssetBadge";
import { PnlAmountCell } from "@/components/PnlAmountCell";
import { PnlRealizedDetailPanel } from "@/components/PnlDetailPanel";

export function PerCoinRealizedTable({
  rows,
  currency,
  assetLabels = {},
  jurisdiction,
  pnlByAsset = {},
  transactions = [],
  importSources = [],
  hideStaking = false,
}: {
  rows: RealizedPnlRow[];
  currency: DisplayCurrency;
  assetLabels?: Record<string, AssetLabel>;
  jurisdiction: TaxJurisdiction;
  pnlByAsset?: Record<string, AssetPnlDetail>;
  transactions?: Transaction[];
  importSources?: ImportSource[];
  hideStaking?: boolean;
}) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const fmt = (value: number, opts?: Intl.NumberFormatOptions) =>
    formatMoney(value, currency, opts);

  const toggle = (asset: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(asset)) next.delete(asset);
      else next.add(asset);
      return next;
    });
  };
  const basisNote =
    jurisdiction === "UK"
      ? "HMRC share-matching (same-day, 30-day, Section 104)"
      : "FIFO or HIFO lot matching";

  const realizedDescription =
    jurisdiction === "UK"
      ? "Lifetime disposals grouped by asset — sales, swaps, and other taxable events. Proceeds minus allowable cost under HMRC matching rules. Click a row to see the trades. Use the Capital Gains Report below for a specific UK tax year."
      : "Lifetime disposals grouped by asset — sales, swaps, and other taxable events. Proceeds minus matched cost basis under your chosen accounting method. Click a row to see the trades. Use the Tax Reporter below for a calendar-year Form 8949 export.";

  const totalDisposals = rows.reduce((sum, r) => sum + r.disposal_count, 0);
  const totalProceeds = rows.reduce((sum, r) => sum + r.proceeds, 0);
  const totalCost = rows.reduce((sum, r) => sum + r.cost_basis, 0);
  const totalPnl = rows.reduce((sum, r) => sum + r.realized_pnl, 0);
  const totalPnlPct = totalCost > 0 ? (totalPnl / totalCost) * 100 : 0;

  return (
    <Card className="h-full">
      <CardHeader className="flex flex-row items-start gap-2 space-y-0">
        <Receipt className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
        <div className="space-y-1">
          <CardTitle className="text-base">Realized Profit &amp; Loss</CardTitle>
          <SectionDescription>{realizedDescription}</SectionDescription>
          <p className="text-xs text-muted-foreground">{basisNote}</p>
        </div>
      </CardHeader>
      <CardContent>
        {rows.length === 0 ? (
          <p className="py-6 text-center text-sm text-muted-foreground">
            No realized disposals recorded yet.
          </p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-8" />
                <TableHead>Coin</TableHead>
                <TableHead className="text-right">Disposals</TableHead>
                <TableHead className="text-right">Qty Sold</TableHead>
                <TableHead className="text-right">Proceeds</TableHead>
                <TableHead className="text-right">Cost Basis</TableHead>
                <TableHead className="text-right">Profit &amp; Loss</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((r) => {
                const open = expanded.has(r.asset);
                const hasDetail = (pnlByAsset[r.asset]?.disposals.length ?? 0) > 0;
                return (
                  <Fragment key={r.asset}>
                    <TableRow
                      className={hasDetail ? "cursor-pointer" : undefined}
                      onClick={() => hasDetail && toggle(r.asset)}
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
                        <AssetBadge asset={r.asset} labels={assetLabels} />
                      </TableCell>
                      <TableCell className="text-right tabular-nums text-muted-foreground">
                        {r.disposal_count}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {formatNumber(r.quantity_disposed)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums text-muted-foreground">
                        {fmt(r.proceeds)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums text-muted-foreground">
                        {fmt(r.cost_basis)}
                      </TableCell>
                      <TableCell className="text-right">
                        <PnlAmountCell
                          value={r.realized_pnl}
                          pct={r.realized_pnl_pct}
                          currency={currency}
                        />
                      </TableCell>
                    </TableRow>
                    {open ? (
                      <TableRow className="bg-muted/10 hover:bg-muted/10">
                        <TableCell />
                        <TableCell colSpan={6} className="px-4 py-3">
                          <PnlRealizedDetailPanel
                            detail={pnlByAsset[r.asset]}
                            transactions={transactions}
                            currency={currency}
                            assetLabels={assetLabels}
                            importSources={importSources}
                            hideStaking={hideStaking}
                          />
                        </TableCell>
                      </TableRow>
                    ) : null}
                  </Fragment>
                );
              })}
            </TableBody>
            <TableFoot>
              <TableRow className="border-t-2 font-semibold">
                <TableCell />
                <TableCell>Total</TableCell>
                <TableCell className="text-right tabular-nums">
                  {totalDisposals}
                </TableCell>
                <TableCell />
                <TableCell className="text-right tabular-nums">
                  {fmt(totalProceeds)}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {fmt(totalCost)}
                </TableCell>
                <TableCell className="text-right">
                  <PnlAmountCell
                    value={totalPnl}
                    pct={totalPnlPct}
                    currency={currency}
                  />
                </TableCell>
              </TableRow>
            </TableFoot>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}
