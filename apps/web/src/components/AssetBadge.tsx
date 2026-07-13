import type { AssetLabel } from "@/lib/types";

export function AssetBadge({
  asset,
  labels,
  className = "",
}: {
  asset: string;
  labels?: Record<string, AssetLabel>;
  className?: string;
}) {
  const label = labels?.[asset];
  const symbol = label?.symbol ?? asset;
  const name = label?.name;
  const mint = label?.mint;
  const showName = name && name.toUpperCase() !== symbol.toUpperCase();

  return (
    <span
      className={`inline-flex flex-col ${className}`}
      title={mint ? `Mint: ${mint}` : showName ? name : undefined}
    >
      <span className="font-semibold leading-tight">{symbol}</span>
      {showName ? (
        <span className="text-[10px] font-normal leading-tight text-muted-foreground">
          {name}
        </span>
      ) : null}
    </span>
  );
}
