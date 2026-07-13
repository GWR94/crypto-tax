import { AlertTriangle } from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { formatNumber } from "@/lib/utils";
import type { AssetLabel, MissingCostBasisFlag } from "@/lib/types";
import { AssetBadge } from "@/components/AssetBadge";

export function MissingCostBasisAlert({
  flags,
  assetLabels = {},
}: {
  flags: MissingCostBasisFlag[];
  assetLabels?: Record<string, AssetLabel>;
}) {
  if (!flags.length) return null;

  return (
    <Alert variant="destructive">
      <AlertTriangle className="h-4 w-4" />
      <AlertTitle>
        Missing cost basis on {flags.length} disposal
        {flags.length > 1 ? "s" : ""}
      </AlertTitle>
      <AlertDescription>
        <p className="mb-2">
          The following sells have no matching historical purchase log. Their
          cost basis defaulted to $0 (taxed as full gain). Import the missing
          acquisition history to resolve.
        </p>
        <ul className="space-y-1">
          {flags.map((flag) => (
            <li
              key={flag.disposal_id}
              className="flex flex-wrap items-center gap-x-2 rounded-md bg-destructive/10 px-3 py-1.5 text-sm"
            >
              <AssetBadge asset={flag.asset} labels={assetLabels} />
              <span className="text-muted-foreground">
                tx {flag.disposal_id}
              </span>
              <span>
                · uncovered {formatNumber(flag.uncovered_amount)}{" "}
                {assetLabels[flag.asset]?.symbol ?? flag.asset}
              </span>
            </li>
          ))}
        </ul>
      </AlertDescription>
    </Alert>
  );
}
