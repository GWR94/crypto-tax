import { useMemo, useState } from "react";
import {
  Cell,
  Legend,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
} from "recharts";
import { PieChart as PieIcon } from "lucide-react";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { SectionDescription } from "@/components/ui/section-description";
import { formatMoney } from "@/lib/utils";
import { isStablecoin } from "@/lib/stablecoins";
import type { AssetLabel, DisplayCurrency, HoldingRow, Position } from "@/lib/types";
import { CHART_COLORS } from "@/components/chartColors";
import { ChartPieTooltip } from "@/components/ChartPieTooltip";

function isStablecoinHolding(h: HoldingRow): boolean {
  return h.is_stablecoin || isStablecoin(h.asset);
}

export function AllocationChart({
  positions,
  holdings = [],
  currency,
  assetLabels = {},
}: {
  positions: Position[];
  holdings?: HoldingRow[];
  currency: DisplayCurrency;
  assetLabels?: Record<string, AssetLabel>;
}) {
  const [includeStablecoins, setIncludeStablecoins] = useState(false);

  const stablecoinHoldings = useMemo(
    () => holdings.filter((h) => isStablecoinHolding(h) && h.current_value > 0),
    [holdings]
  );

  const data = useMemo(() => {
    const slices: { name: string; value: number }[] = [];

    for (const p of positions) {
      if (p.current_value > 0) {
        slices.push({
          name: assetLabels[p.asset]?.symbol ?? p.asset,
          value: p.current_value,
        });
      }
    }

    if (includeStablecoins) {
      for (const h of stablecoinHoldings) {
        slices.push({
          name: assetLabels[h.asset]?.symbol ?? h.asset,
          value: h.current_value,
        });
      }
    }

    return slices;
  }, [positions, stablecoinHoldings, includeStablecoins, assetLabels]);

  return (
    <Card className="h-full">
      <CardHeader className="flex flex-row items-start gap-2 space-y-0">
        <PieIcon className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
        <div className="space-y-1">
          <CardTitle className="text-base">Portfolio Allocation</CardTitle>
          <SectionDescription>
            How your portfolio is split by current market value.
            {stablecoinHoldings.length > 0
              ? " Stablecoins are hidden by default — enable below to include cash balances."
              : " Dust positions below the display threshold are excluded."}
          </SectionDescription>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        {stablecoinHoldings.length > 0 ? (
          <label className="flex w-fit cursor-pointer items-center gap-2 rounded-md border border-border px-2.5 py-1.5">
            <input
              type="checkbox"
              checked={includeStablecoins}
              onChange={(e) => setIncludeStablecoins(e.target.checked)}
              className="h-3.5 w-3.5 rounded border-input accent-primary"
            />
            <span className="text-xs text-foreground">Include stablecoins</span>
          </label>
        ) : null}

        {data.length === 0 ? (
          <p className="py-6 text-center text-sm text-muted-foreground">
            No allocation data available.
          </p>
        ) : (
          <ResponsiveContainer width="100%" height={280}>
            <PieChart>
              <Pie
                data={data}
                dataKey="value"
                nameKey="name"
                cx="50%"
                cy="50%"
                innerRadius={60}
                outerRadius={100}
                paddingAngle={2}
              >
                {data.map((_, index) => (
                  <Cell
                    key={index}
                    fill={CHART_COLORS[index % CHART_COLORS.length]}
                    stroke="transparent"
                  />
                ))}
              </Pie>
              <Tooltip
                content={({ active, payload, label }) => (
                  <ChartPieTooltip
                    active={active}
                    payload={payload}
                    label={label}
                    formatValue={(value) => formatMoney(value, currency)}
                  />
                )}
              />
              <Legend
                iconType="circle"
                wrapperStyle={{ fontSize: "0.8rem" }}
              />
            </PieChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  );
}
