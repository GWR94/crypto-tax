import {
  TrendingUp,
  TrendingDown,
  Wallet,
  Receipt,
  Gift,
  type LucideIcon,
} from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { InfoTooltip } from "@/components/ui/info-tooltip";
import { cn, formatMoney } from "@/lib/utils";
import type { DisplayCurrency, PortfolioSummary, TaxJurisdiction } from "@/lib/types";

interface KpiCardProps {
  title: string;
  value: string;
  icon: LucideIcon;
  accent?: "neutral" | "positive" | "negative";
  subtitle?: string;
  hint?: string;
}

function KpiCard({
  title,
  value,
  icon: Icon,
  accent = "neutral",
  subtitle,
  hint,
}: KpiCardProps) {
  const accentColor =
    accent === "positive"
      ? "text-success"
      : accent === "negative"
        ? "text-destructive"
        : "text-foreground";

  return (
    <Card className="overflow-hidden">
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <span className="inline-flex items-center gap-1 text-muted-foreground">
          <CardTitle className="text-sm font-medium">{title}</CardTitle>
          {hint ? <InfoTooltip text={hint} /> : null}
        </span>
        <Icon className="h-4 w-4 text-muted-foreground" />
      </CardHeader>
      <CardContent>
        <div className={cn("text-2xl font-bold tabular-nums", accentColor)}>
          {value}
        </div>
        {subtitle ? (
          <p className="mt-1 text-xs text-muted-foreground">{subtitle}</p>
        ) : null}
      </CardContent>
    </Card>
  );
}

export function KpiRibbon({
  summary,
  jurisdiction,
}: {
  summary: PortfolioSummary;
  jurisdiction: TaxJurisdiction;
}) {
  const currency = summary.display_currency as DisplayCurrency;
  const fmt = (value: number) => formatMoney(value, currency);
  const realizedPositive = summary.total_realized_gain >= 0;
  const unrealizedPositive = summary.total_unrealized_gain >= 0;
  const income = summary.income_summary;
  const basisLabel =
    jurisdiction === "UK"
      ? `HMRC · ${summary.method === "SECTION_104" ? "Section 104 pool" : summary.method}`
      : `${summary.reporting_currency} · ${summary.method}`;

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
      <KpiCard
        title="Total Portfolio Value"
        value={fmt(summary.total_portfolio_value)}
        icon={Wallet}
        subtitle={`Invested ${fmt(summary.total_invested)} · ${currency}`}
        hint="Sum of open positions at current or estimated prices, plus stablecoin balances. Excludes perpetuals and hidden scam tokens."
      />
      <KpiCard
        title="Realized Gains (Taxable)"
        value={fmt(summary.total_realized_gain)}
        icon={Receipt}
        accent={realizedPositive ? "positive" : "negative"}
        subtitle={`Tax basis: ${basisLabel}`}
        hint={
          jurisdiction === "UK"
            ? "Lifetime capital gains from disposals after HMRC share-matching. For a specific tax year and allowance, use the Capital Gains Report below."
            : "Lifetime capital gains from disposals under your accounting method. For a calendar-year Form 8949 export, use the Tax Reporter below."
        }
      />
      <KpiCard
        title="Unrealized Gains"
        value={fmt(summary.total_unrealized_gain)}
        icon={unrealizedPositive ? TrendingUp : TrendingDown}
        accent={unrealizedPositive ? "positive" : "negative"}
        subtitle={`Mark-to-market · shown in ${currency}`}
        hint="Paper profit or loss on holdings you still own. Not taxable until sold or otherwise disposed of."
      />
      <KpiCard
        title="Crypto Income Summary"
        value={fmt(income.total_income)}
        icon={Gift}
        accent="positive"
        subtitle={`Airdrops ${fmt(income.airdrop_income)} · Staking ${fmt(
          income.staking_income
        )}`}
        hint="Fair-market value of airdrops and staking rewards when received. May be reportable as income in addition to capital gains on later disposal."
      />
    </div>
  );
}
