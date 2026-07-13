import { useEffect, useState } from "react";
import { Download, FileText, Loader2 } from "lucide-react";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { InfoTooltip, LabelWithTooltip } from "@/components/ui/info-tooltip";
import { SectionDescription } from "@/components/ui/section-description";
import { api } from "@/lib/api";
import { cn, formatMoney } from "@/lib/utils";
import {
  isUkCgtReport,
  type TaxJurisdiction,
  type AccountingMethod,
  type CgtMatchType,
  type PerpTaxSummary,
  type PerpTreatment,
  type RealizedGainsSummary,
  type TaxReport,
  type UkCgtSummary,
} from "@/lib/types";

const MATCH_LABEL: Record<CgtMatchType, string> = {
  same_day: "Same day",
  thirty_day: "30-day",
  section_104: "S.104 pool",
  unmatched: "No cost basis",
};

const UK_TERM_HINTS = {
  netGain:
    "Gains minus losses on all disposals in this tax year, after HMRC share-matching (same-day, 30-day, then Section 104 pool). Before the annual exempt amount.",
  annualAllowance:
    "The tax-free CGT allowance for this UK tax year (e.g. £3,000 for 2024/25). Only net gains above this may be taxable. Shown as a deduction.",
  taxableAfterAllowance:
    "Net gain minus the annual exempt amount (never below zero). This is the figure you would typically enter on SA108 — CGT rates are not calculated here.",
  proceeds:
    "Total sterling received from disposals (sales, crypto swaps, fee disposals), net of selling fees, converted at the FX rate on each disposal date.",
  allowableCosts:
    "Acquisition cost matched to those disposals under HMRC rules, including purchase fees, in sterling at the date each lot was acquired.",
  losses:
    "Disposals where proceeds were less than allowable cost. Losses offset gains before the annual exempt amount is applied.",
  rule:
    "Which HMRC matching rule linked this disposal to its acquisition cost.",
  matchSameDay:
    "Matched to a purchase on the same calendar day as the disposal.",
  matchThirtyDay:
    "Bed & breakfast: matched to a repurchase within 30 days after the disposal.",
  matchSection104:
    "Matched from the Section 104 average-cost pool for this asset.",
  matchUnmatched:
    "No acquisition history found for this quantity — proceeds are treated as fully taxable gain.",
  tableProceeds: "Sterling received for this disposal leg, net of fees.",
  tableCost: "Allowable acquisition cost matched to this leg.",
  tableGainLoss: "Proceeds minus allowable cost for this leg.",
} as const;

export function TaxReporter({
  method,
  onMethodChange,
  jurisdiction,
  perpTreatment = "income",
}: {
  method: AccountingMethod;
  onMethodChange: (method: AccountingMethod) => void;
  jurisdiction: TaxJurisdiction;
  perpTreatment?: PerpTreatment;
}) {
  const [years, setYears] = useState<Array<string | number>>([]);
  const [year, setYear] = useState<string | number | null>(null);
  const [report, setReport] = useState<TaxReport | null>(null);
  const [perpReport, setPerpReport] = useState<PerpTaxSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setYear(null);
    setReport(null);
    setPerpReport(null);
    api
      .getAvailableYears()
      .then((data) => {
        setYears(data);
        if (data.length) setYear(data[0]);
      })
      .catch((e) => setError(String(e)));
  }, [jurisdiction]);

  // UK tax years are string labels (e.g. "2024/25"); US years are numbers.
  const isUk = jurisdiction === "UK";
  const perpsEnabled = perpTreatment !== "exclude";

  async function generate() {
    if (year === null) return;
    setLoading(true);
    setError(null);
    try {
      const result = await api.getTaxReport(year, method);
      setReport(result);
      if (perpsEnabled) {
        const perp = await api.getPerpTaxReport(year).catch(() => null);
        setPerpReport(perp);
      } else {
        setPerpReport(null);
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }

  function download(kind: "cgt" | "income" | "perps" = "cgt") {
    if (year === null) return;
    window.open(api.downloadTaxReportUrl(year, method, kind), "_blank");
  }

  return (
    <Card>
      <CardHeader className="flex flex-row items-start gap-2 space-y-0">
        <FileText className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
        <div className="space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <CardTitle className="text-base">
              {isUk ? "Capital Gains Report · HMRC" : "Tax Reporter · IRS Form 8949"}
            </CardTitle>
            <Badge variant="muted">
              {isUk ? "HMRC · GBP" : "IRS · USD reporting"}
            </Badge>
          </div>
          <SectionDescription>
            {isUk
              ? "Build a UK tax-year capital gains summary with HMRC share-matching (same-day, 30-day, then Section 104 pool). Export SA108-ready CSVs for your Self Assessment. Figures are in sterling at historical FX rates on each disposal date."
              : "Build a calendar-year capital gains report with FIFO or HIFO lot matching. Export Form 8949 / Schedule D CSVs for your return. Figures use USD reporting currency at historical prices."}
          </SectionDescription>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <div
          className={cn(
            "grid grid-cols-1 gap-3",
            !isUk && "sm:grid-cols-2"
          )}
        >
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-muted-foreground">
              Tax Year
            </label>
            <Select
              value={year ?? ""}
              onChange={(e) => {
                const raw = e.target.value;
                const next = years.find((y) => String(y) === raw) ?? raw;
                setYear(next);
              }}
            >
              {years.map((y) => (
                <option key={String(y)} value={String(y)}>
                  {y}
                </option>
              ))}
            </Select>
          </div>
          {!isUk ? (
            <div className="space-y-1.5">
              <label className="text-xs font-medium text-muted-foreground">
                Accounting Method
              </label>
              <Select
                value={method}
                onChange={(e) =>
                  onMethodChange(e.target.value as AccountingMethod)
                }
              >
                <option value="FIFO">FIFO (First-In, First-Out)</option>
                <option value="HIFO">HIFO (Highest-In, First-Out)</option>
              </Select>
            </div>
          ) : null}
        </div>

        {isUk ? (
          <p className="text-xs text-muted-foreground">
            Gains use HMRC share-matching: same-day, then 30-day
            (bed &amp; breakfast), then the Section 104 pool.
          </p>
        ) : null}

        <div className="flex flex-wrap gap-2">
          <Button onClick={generate} disabled={loading || year === null}>
            {loading ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <FileText className="h-4 w-4" />
            )}
            Generate Report
          </Button>
          <Button
            variant="outline"
            onClick={() => download("cgt")}
            disabled={year === null}
          >
            <Download className="h-4 w-4" />
            {isUk ? "CGT CSV" : "Download CSV"}
          </Button>
          {isUk ? (
            <Button
              variant="outline"
              onClick={() => download("income")}
              disabled={year === null}
            >
              <Download className="h-4 w-4" />
              Income CSV
            </Button>
          ) : null}
          {perpsEnabled ? (
            <Button
              variant="outline"
              onClick={() => download("perps")}
              disabled={year === null}
            >
              <Download className="h-4 w-4" />
              Perp PnL CSV
            </Button>
          ) : null}
        </div>

        {error ? <p className="text-sm text-destructive">{error}</p> : null}

        {report ? (
          isUkCgtReport(report) ? (
            <UkReportView report={report} />
          ) : (
            <UsReportView report={report} />
          )
        ) : null}

        {perpReport && perpReport.event_count > 0 ? (
          <PerpReportView report={perpReport} />
        ) : null}
      </CardContent>
    </Card>
  );
}

function PerpReportView({ report }: { report: PerpTaxSummary }) {
  const ccy = report.reporting_currency;
  const isCapital = report.treatment === "capital_gains";
  const schedule = isCapital ? "Capital Gains" : "Trading Income";
  return (
    <div className="space-y-3 rounded-lg border bg-muted/30 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-sm font-semibold text-foreground">
          Perpetual Futures · {schedule}
        </p>
        <Badge variant="muted">{report.event_count} closes</Badge>
      </div>
      <p className="text-xs text-muted-foreground">
        Exchange-reported perp PnL for {report.period_label ?? "all periods"},
        converted to <strong className="text-foreground">{ccy}</strong>. Reported
        as {isCapital ? "capital gains" : "trading/ordinary income"} per your
        settings — this is configurable and not tax advice.
      </p>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Summary label="Net PnL" value={report.net_pnl} currency={ccy} emphasize />
        <Summary label="Gains" value={report.gains} currency={ccy} />
        <Summary label="Losses" value={report.losses} currency={ccy} />
        <Summary label="Fees" value={-report.total_fees} currency={ccy} />
      </div>
    </div>
  );
}

function UkReportView({ report }: { report: UkCgtSummary }) {
  const ccy = report.reporting_currency;
  return (
    <div className="space-y-3 rounded-lg border bg-muted/30 p-4">
      <p className="text-xs text-muted-foreground">
        Capital gains computed under HMRC rules and reported in{" "}
        <strong className="text-foreground">{ccy}</strong> using historical FX at
        each transaction date.
      </p>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        <Summary
          label="Net Gain"
          hint={UK_TERM_HINTS.netGain}
          value={report.net_gain}
          currency={ccy}
          emphasize
        />
        <Summary
          label="Annual Allowance"
          hint={UK_TERM_HINTS.annualAllowance}
          value={-report.annual_exempt_amount}
          currency={ccy}
        />
        <Summary
          label="Taxable After Allowance"
          hint={UK_TERM_HINTS.taxableAfterAllowance}
          value={report.taxable_gain_after_allowance}
          currency={ccy}
          emphasize
        />
        <Summary
          label="Proceeds"
          hint={UK_TERM_HINTS.proceeds}
          value={report.total_proceeds}
          currency={ccy}
        />
        <Summary
          label="Allowable Costs"
          hint={UK_TERM_HINTS.allowableCosts}
          value={report.total_allowable_costs}
          currency={ccy}
        />
        <Summary
          label="Losses"
          hint={UK_TERM_HINTS.losses}
          value={-report.total_losses}
          currency={ccy}
        />
      </div>

      {report.rows.length ? (
        <div className="max-h-72 overflow-auto rounded-md border">
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-muted/80 text-muted-foreground">
              <tr>
                <th className="px-2 py-1.5 text-left font-medium">Date</th>
                <th className="px-2 py-1.5 text-left font-medium">Asset</th>
                <th className="px-2 py-1.5 text-left font-medium">
                  <LabelWithTooltip label="Rule" hint={UK_TERM_HINTS.rule} />
                </th>
                <th className="px-2 py-1.5 text-right font-medium">
                  <span className="inline-flex items-center justify-end gap-1">
                    Proceeds
                    <InfoTooltip text={UK_TERM_HINTS.tableProceeds} side="bottom" />
                  </span>
                </th>
                <th className="px-2 py-1.5 text-right font-medium">
                  <span className="inline-flex items-center justify-end gap-1">
                    Cost
                    <InfoTooltip text={UK_TERM_HINTS.tableCost} side="bottom" />
                  </span>
                </th>
                <th className="px-2 py-1.5 text-right font-medium">
                  <span className="inline-flex items-center justify-end gap-1">
                    Gain/Loss
                    <InfoTooltip text={UK_TERM_HINTS.tableGainLoss} side="bottom" />
                  </span>
                </th>
              </tr>
            </thead>
            <tbody>
              {report.rows.map((row, i) => (
                <tr key={`${row.disposal_id}-${i}`} className="border-t">
                  <td className="px-2 py-1.5 tabular-nums text-muted-foreground">
                    {row.disposal_date.slice(0, 10)}
                  </td>
                  <td className="px-2 py-1.5">{row.asset}</td>
                  <td className="px-2 py-1.5">
                    <Badge
                      variant={
                        row.match_type === "unmatched" ? "destructive" : "muted"
                      }
                      title={
                        row.match_type === "same_day"
                          ? UK_TERM_HINTS.matchSameDay
                          : row.match_type === "thirty_day"
                            ? UK_TERM_HINTS.matchThirtyDay
                            : row.match_type === "section_104"
                              ? UK_TERM_HINTS.matchSection104
                              : UK_TERM_HINTS.matchUnmatched
                      }
                    >
                      {MATCH_LABEL[row.match_type]}
                    </Badge>
                  </td>
                  <td className="px-2 py-1.5 text-right tabular-nums">
                    {formatMoney(row.proceeds, ccy)}
                  </td>
                  <td className="px-2 py-1.5 text-right tabular-nums">
                    {formatMoney(row.allowable_cost, ccy)}
                  </td>
                  <td
                    className={cn(
                      "px-2 py-1.5 text-right tabular-nums",
                      row.gain >= 0 ? "text-success" : "text-destructive"
                    )}
                  >
                    {formatMoney(row.gain, ccy)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      <p className="text-xs text-muted-foreground">
        {report.disposal_count} disposal
        {report.disposal_count === 1 ? "" : "s"} · {report.tax_year_label ?? "all years"}.
        Allowance and rate bands are guidance only — this is not tax advice.
      </p>
    </div>
  );
}

function UsReportView({ report }: { report: RealizedGainsSummary }) {
  const ccy = report.reporting_currency;
  return (
    <div className="space-y-3 rounded-lg border bg-muted/30 p-4">
      <p className="text-xs text-muted-foreground">
        Tax figures are calculated and reported in{" "}
        <strong className="text-foreground">{ccy}</strong> using historical FX
        rates at each trade date.
      </p>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        <Summary
          label="Short-Term Gain"
          value={report.short_term_gain}
          currency={ccy}
        />
        <Summary
          label="Long-Term Gain"
          value={report.long_term_gain}
          currency={ccy}
        />
        <Summary
          label="Total Gain"
          value={report.total_gain}
          currency={ccy}
          emphasize
        />
      </div>
      <div className="flex items-center justify-between text-xs text-muted-foreground">
        <span>
          {report.rows.length} disposal line
          {report.rows.length === 1 ? "" : "s"} · {report.tax_year}
        </span>
        <Badge variant="muted">{report.method}</Badge>
      </div>
    </div>
  );
}

function Summary({
  label,
  hint,
  value,
  currency,
  emphasize = false,
}: {
  label: string;
  hint?: string;
  value: number;
  currency: string;
  emphasize?: boolean;
}) {
  const positive = value >= 0;
  return (
    <div>
      <p className="text-xs text-muted-foreground">
        {hint ? <LabelWithTooltip label={label} hint={hint} /> : label}
      </p>
      <p
        className={cn(
          "tabular-nums",
          emphasize ? "text-lg font-bold" : "text-base font-semibold",
          positive ? "text-success" : "text-destructive"
        )}
      >
        {formatMoney(value, currency)}
      </p>
    </div>
  );
}
