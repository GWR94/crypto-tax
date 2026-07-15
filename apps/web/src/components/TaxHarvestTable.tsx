import { TrendingDown, PiggyBank } from "lucide-react";
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
import { LabelWithTooltip } from "@/components/ui/info-tooltip";
import { SectionDescription } from "@/components/ui/section-description";
import { formatMoney, formatNumber } from "@/lib/utils";
import type {
  AssetLabel,
  DisplayCurrency,
  TaxHarvestRow,
  TaxJurisdiction,
} from "@/lib/types";
import { AssetBadge } from "@/components/AssetBadge";

const HARVEST_RATE_PCT = 20;

function harvestDescription(jurisdiction: TaxJurisdiction): string {
  if (jurisdiction === "UK") {
    return (
      "Tax loss harvesting means selling holdings that are underwater to crystallise a capital loss, which can offset gains in the same or future tax years. " +
      "Potential savings are estimated as the unrealised loss × 20% — a simplified figure using the higher-rate CGT rate. " +
      "Your actual rate may be lower (10% basic rate). If you repurchase the same asset within 30 days, HMRC bed-and-breakfast rules may reattach the loss to the new holding instead."
    );
  }
  return (
    "Tax loss harvesting means selling positions at a loss to realise capital losses that can offset capital gains on your return. " +
    "Potential savings are estimated as the unrealised loss × 20% — a simplified long-term capital gains rate. " +
    "Your marginal rate may differ. Wash-sale rules do not currently apply to crypto, but FIFO/LIFO/HIFO determines which lots are sold."
  );
}

const SAVINGS_HINT =
  "Unrealised loss on this position multiplied by 20%. This is an illustrative tax benefit if the loss offsets gains taxed at 20%; it is not a guarantee of savings and not tax advice.";

export function TaxHarvestTable({
  rows,
  currency,
  assetLabels = {},
  jurisdiction = "UK",
}: {
  rows: TaxHarvestRow[];
  currency: DisplayCurrency;
  assetLabels?: Record<string, AssetLabel>;
  jurisdiction?: TaxJurisdiction;
}) {
  const fmt = (value: number) => formatMoney(value, currency);
  const totalSavings = rows.reduce(
    (acc, r) => acc + r.potential_tax_savings,
    0
  );

  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-4 space-y-0">
        <div className="flex min-w-0 items-start gap-2">
          <TrendingDown className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
          <div className="space-y-1">
            <CardTitle className="text-base">Tax Loss Harvesting</CardTitle>
            <SectionDescription>{harvestDescription(jurisdiction)}</SectionDescription>
          </div>
        </div>
        <Badge variant="success" className="shrink-0 gap-1">
          <PiggyBank className="h-3 w-3" />
          {fmt(totalSavings)} potential savings
        </Badge>
      </CardHeader>
      <CardContent>
        {rows.length === 0 ? (
          <p className="py-6 text-center text-sm text-muted-foreground">
            No positions currently in the red. Nothing to harvest.
          </p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Asset</TableHead>
                <TableHead className="text-right">Current Bags</TableHead>
                <TableHead className="text-right">Current Value</TableHead>
                <TableHead className="text-right">Unrealized Loss</TableHead>
                <TableHead className="text-right">
                  <LabelWithTooltip
                    label={`Potential Tax Savings (${HARVEST_RATE_PCT}%)`}
                    hint={SAVINGS_HINT}
                  />
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row) => (
                <TableRow key={row.asset}>
                  <TableCell>
                    <AssetBadge asset={row.asset} labels={assetLabels} />
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {formatNumber(row.current_bags)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {fmt(row.current_value)}
                  </TableCell>
                  <TableCell className="text-right font-medium tabular-nums text-destructive">
                    -{fmt(row.unrealized_loss)}
                  </TableCell>
                  <TableCell className="text-right font-semibold tabular-nums text-success">
                    {fmt(row.potential_tax_savings)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}
