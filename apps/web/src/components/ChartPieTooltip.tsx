import { cn } from "@/lib/utils";

type TooltipEntry = {
  value?: unknown;
  name?: unknown;
  payload?: unknown;
};

type ChartPieTooltipProps = {
  active?: boolean;
  payload?: readonly TooltipEntry[];
  label?: string | number;
  formatValue: (value: number, name: string, payload: unknown) => string;
  /** Signed amount used for gain/loss colouring (defaults to the chart value). */
  getSignedValue?: (value: number, payload: unknown) => number;
};

export function ChartPieTooltip({
  active,
  payload,
  label,
  formatValue,
  getSignedValue,
}: ChartPieTooltipProps) {
  if (!active || !payload?.length) return null;

  const entry = payload[0];
  const name = String(label ?? entry.name ?? "");
  const raw = Number(entry.value ?? 0);
  const signed = getSignedValue?.(raw, entry.payload) ?? raw;
  const display = formatValue(raw, name, entry.payload);

  return (
    <div className="rounded-lg border border-border bg-card px-3 py-2 text-sm shadow-lg">
      <p className="font-medium text-card-foreground">{name}</p>
      <p
        className={cn(
          "mt-0.5 tabular-nums font-semibold",
          signed >= 0 ? "text-success" : "text-destructive"
        )}
      >
        {display}
      </p>
    </div>
  );
}
