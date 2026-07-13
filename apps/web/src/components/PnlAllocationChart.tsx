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
import type { DisplayCurrency } from "@/lib/types";
import { GAIN_CHART_COLORS, LOSS_CHART_COLORS } from "@/components/chartColors";
import { ChartPieTooltip } from "@/components/ChartPieTooltip";

export type PnlSlice = {
  name: string;
  value: number;
};

export function PnlAllocationChart({
  title,
  description,
  slices,
  currency,
  emptyMessage = "No profit & loss data available.",
}: {
  title: string;
  description?: string;
  slices: PnlSlice[];
  currency: DisplayCurrency;
  emptyMessage?: string;
}) {
  const data = slices
    .filter((s) => Math.abs(s.value) > 0.005)
    .map((s) => ({
      name: s.name,
      value: s.value,
      magnitude: Math.abs(s.value),
    }));

  const total = data.reduce((sum, row) => sum + row.value, 0);

  return (
    <Card className="h-full">
      <CardHeader className="flex flex-row items-start gap-2 space-y-0">
        <PieIcon className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
        <div className="space-y-1">
          <CardTitle className="text-base">{title}</CardTitle>
          {description ? (
            <SectionDescription>{description}</SectionDescription>
          ) : null}
        </div>
      </CardHeader>
      <CardContent>
        {data.length === 0 ? (
          <p className="py-6 text-center text-sm text-muted-foreground">
            {emptyMessage}
          </p>
        ) : (
          <>
            <ResponsiveContainer width="100%" height={280}>
              <PieChart>
                <Pie
                  data={data}
                  dataKey="magnitude"
                  nameKey="name"
                  cx="50%"
                  cy="50%"
                  innerRadius={60}
                  outerRadius={100}
                  paddingAngle={2}
                >
                  {data.map((entry, index) => (
                    <Cell
                      key={entry.name}
                      fill={
                        entry.value >= 0
                          ? GAIN_CHART_COLORS[index % GAIN_CHART_COLORS.length]
                          : LOSS_CHART_COLORS[index % LOSS_CHART_COLORS.length]
                      }
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
                      getSignedValue={(_magnitude, payload) =>
                        Number((payload as { value?: number })?.value ?? 0)
                      }
                      formatValue={(_magnitude, _name, payload) => {
                        const signed = (payload as { value?: number })?.value;
                        return formatMoney(signed ?? _magnitude, currency);
                      }}
                    />
                  )}
                />
                <Legend
                  iconType="circle"
                  wrapperStyle={{ fontSize: "0.8rem" }}
                />
              </PieChart>
            </ResponsiveContainer>
            <p className="mt-2 text-center text-sm text-muted-foreground">
              Total{" "}
              <span
                className={
                  total >= 0
                    ? "font-semibold text-success"
                    : "font-semibold text-destructive"
                }
              >
                {formatMoney(total, currency)}
              </span>
            </p>
          </>
        )}
      </CardContent>
    </Card>
  );
}
