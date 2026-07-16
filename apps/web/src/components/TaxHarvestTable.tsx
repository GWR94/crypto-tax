import { useEffect, useState } from "react";
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

function ratePct(rate: number): number {
  return Math.round(rate * 1000) / 10;
}

function harvestDescription(
  jurisdiction: TaxJurisdiction,
  opts: {
    basicRate: number;
    higherRate: number;
    ordinaryRate: number;
    ltcgRate: number;
    unusedBasicBand: number;
    currency: DisplayCurrency;
  }
): string {
  const {
    basicRate,
    higherRate,
    ordinaryRate,
    ltcgRate,
    unusedBasicBand,
    currency,
  } = opts;
  if (jurisdiction === "UK") {
    const basic = ratePct(basicRate);
    const higher = ratePct(higherRate);
    const band = formatMoney(unusedBasicBand, currency);
    return (
      "Tax loss harvesting means selling holdings that are underwater to crystallise a capital loss, which can offset gains in the same or future tax years. " +
      `Potential savings use ${basic}% CGT on losses up to your unused basic-rate band (${band}), then ${higher}% on the rest (largest losses first). ` +
      "Adjust the unused band below from your taxable income. If you repurchase the same asset within 30 days, HMRC bed-and-breakfast rules may reattach the loss to the new holding instead."
    );
  }
  const ordinary = ratePct(ordinaryRate);
  const ltcg = ratePct(ltcgRate);
  return (
    "Tax loss harvesting means selling positions at a loss to realise capital losses that can offset capital gains on your return. " +
    `Potential savings split open lots by IRS holding period: short-term (≤1 year) at your ordinary rate (${ordinary}%), long-term at LTCG (${ltcg}%), as if sold today. ` +
    "Wash-sale rules do not currently apply to crypto, but FIFO/LIFO/HIFO determines which lots are sold."
  );
}

function savingsHint(jurisdiction: TaxJurisdiction): string {
  if (jurisdiction === "UK") {
    return (
      "Illustrative CGT saved if this unrealised loss offsets gains: basic-rate slice first, then higher-rate. Not tax advice."
    );
  }
  return (
    "Illustrative federal tax benefit if this position were sold today, using short-term ordinary and long-term CG rates on each open lot. Not tax advice."
  );
}

export type HarvestRateUpdate = {
  uk_unused_basic_band?: number;
  us_ordinary_income_rate?: number;
  us_long_term_cg_rate?: number;
};

export function TaxHarvestTable({
  rows,
  currency,
  assetLabels = {},
  jurisdiction = "UK",
  estimateRate,
  basicRate = 0.18,
  higherRate = 0.24,
  ordinaryRate = 0.24,
  ltcgRate = 0.15,
  unusedBasicBand = 37700,
  bandCurrency = "GBP",
  onRatesChange,
  ratesBusy = false,
}: {
  rows: TaxHarvestRow[];
  currency: DisplayCurrency;
  assetLabels?: Record<string, AssetLabel>;
  jurisdiction?: TaxJurisdiction;
  estimateRate?: number;
  basicRate?: number;
  higherRate?: number;
  ordinaryRate?: number;
  ltcgRate?: number;
  unusedBasicBand?: number;
  /** Reporting currency for the unused-band setting (GBP for UK). */
  bandCurrency?: DisplayCurrency;
  onRatesChange?: (update: HarvestRateUpdate) => void | Promise<void>;
  ratesBusy?: boolean;
}) {
  const fmt = (value: number) => formatMoney(value, currency);
  const totalSavings = rows.reduce(
    (acc, r) => acc + r.potential_tax_savings,
    0
  );
  const blendedPct =
    typeof estimateRate === "number" && estimateRate > 0
      ? ratePct(estimateRate)
      : null;

  const savingsLabel =
    blendedPct != null
      ? `Potential Tax Savings (~${blendedPct}%)`
      : "Potential Tax Savings";

  const [bandDraft, setBandDraft] = useState(String(unusedBasicBand));
  const [ordinaryDraft, setOrdinaryDraft] = useState(
    String(ratePct(ordinaryRate))
  );
  const [ltcgDraft, setLtcgDraft] = useState(String(ratePct(ltcgRate)));

  useEffect(() => {
    setBandDraft(String(unusedBasicBand));
  }, [unusedBasicBand]);

  useEffect(() => {
    setOrdinaryDraft(String(ratePct(ordinaryRate)));
  }, [ordinaryRate]);

  useEffect(() => {
    setLtcgDraft(String(ratePct(ltcgRate)));
  }, [ltcgRate]);

  async function commitUkBand() {
    const parsed = Number(bandDraft.replace(/,/g, ""));
    if (!Number.isFinite(parsed) || parsed < 0 || !onRatesChange) return;
    if (parsed === unusedBasicBand) return;
    await onRatesChange({ uk_unused_basic_band: parsed });
  }

  async function commitUsRates() {
    if (!onRatesChange) return;
    const ordinary = Number(ordinaryDraft) / 100;
    const ltcg = Number(ltcgDraft) / 100;
    if (!Number.isFinite(ordinary) || !Number.isFinite(ltcg)) return;
    if (ordinary < 0 || ordinary > 1 || ltcg < 0 || ltcg > 1) return;
    const update: HarvestRateUpdate = {};
    if (Math.abs(ordinary - ordinaryRate) > 1e-9) {
      update.us_ordinary_income_rate = ordinary;
    }
    if (Math.abs(ltcg - ltcgRate) > 1e-9) {
      update.us_long_term_cg_rate = ltcg;
    }
    if (Object.keys(update).length === 0) return;
    await onRatesChange(update);
  }

  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-4 space-y-0">
        <div className="flex min-w-0 items-start gap-2">
          <TrendingDown className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
          <div className="space-y-1">
            <CardTitle className="text-base">Tax Loss Harvesting</CardTitle>
            <SectionDescription>
              {harvestDescription(jurisdiction, {
                basicRate,
                higherRate,
                ordinaryRate,
                ltcgRate,
                unusedBasicBand,
                currency: bandCurrency,
              })}
            </SectionDescription>
          </div>
        </div>
        <Badge variant="success" className="shrink-0 gap-1">
          <PiggyBank className="h-3 w-3" />
          {fmt(totalSavings)} potential savings
        </Badge>
      </CardHeader>
      <CardContent className="space-y-4">
        {onRatesChange ? (
          <div className="flex flex-wrap items-end gap-3 text-xs text-muted-foreground">
            {jurisdiction === "UK" ? (
              <label className="flex flex-col gap-1">
                <span>Unused basic-rate band ({bandCurrency})</span>
                <input
                  type="number"
                  min={0}
                  step={100}
                  value={bandDraft}
                  disabled={ratesBusy}
                  onChange={(e) => setBandDraft(e.target.value)}
                  onBlur={() => void commitUkBand()}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") void commitUkBand();
                  }}
                  className="h-8 w-36 rounded-md border border-border bg-background px-2 tabular-nums text-foreground"
                />
              </label>
            ) : (
              <>
                <label className="flex flex-col gap-1">
                  <span>Ordinary / short-term %</span>
                  <input
                    type="number"
                    min={0}
                    max={100}
                    step={1}
                    value={ordinaryDraft}
                    disabled={ratesBusy}
                    onChange={(e) => setOrdinaryDraft(e.target.value)}
                    onBlur={() => void commitUsRates()}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") void commitUsRates();
                    }}
                    className="h-8 w-28 rounded-md border border-border bg-background px-2 tabular-nums text-foreground"
                  />
                </label>
                <label className="flex flex-col gap-1">
                  <span>Long-term CG %</span>
                  <input
                    type="number"
                    min={0}
                    max={100}
                    step={1}
                    value={ltcgDraft}
                    disabled={ratesBusy}
                    onChange={(e) => setLtcgDraft(e.target.value)}
                    onBlur={() => void commitUsRates()}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") void commitUsRates();
                    }}
                    className="h-8 w-28 rounded-md border border-border bg-background px-2 tabular-nums text-foreground"
                  />
                </label>
              </>
            )}
          </div>
        ) : null}

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
                    label={savingsLabel}
                    hint={savingsHint(jurisdiction)}
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
