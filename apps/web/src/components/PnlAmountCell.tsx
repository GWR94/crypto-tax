import { ArrowDownRight, ArrowUpRight } from "lucide-react";
import { cn, formatMoney, formatPercent } from "@/lib/utils";
import type { DisplayCurrency } from "@/lib/types";

export function PnlAmountCell({
  value,
  pct,
  currency,
}: {
  value: number;
  pct?: number;
  currency: DisplayCurrency;
}) {
  const positive = value >= 0;
  return (
    <div
      className={cn(
        "flex flex-nowrap items-center justify-end gap-1 font-semibold tabular-nums",
        positive ? "text-success" : "text-destructive"
      )}
    >
      {positive ? (
        <ArrowUpRight className="h-3.5 w-3.5 shrink-0" />
      ) : (
        <ArrowDownRight className="h-3.5 w-3.5 shrink-0" />
      )}
      <span className="whitespace-nowrap">
        {formatMoney(Math.abs(value), currency)}
      </span>
      {pct !== undefined ? (
        <span className="whitespace-nowrap text-xs font-normal opacity-80">
          ({formatPercent(pct)})
        </span>
      ) : null}
    </div>
  );
}
