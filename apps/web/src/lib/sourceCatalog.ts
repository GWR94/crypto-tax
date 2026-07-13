/** Ledger source slugs → display metadata and icon files in /public/icons/exchanges/. */

export type SourceKind = "exchange" | "chain";

export interface SourceDefinition {
  id: string;
  label: string;
  kind: SourceKind;
  /** Plain-language explanation shown in source previews. */
  description?: string;
  /** File under /icons/exchanges/ (svg preferred). */
  iconFile: string;
  brandColor: string;
  /** Full-color SVG — skip monochrome tinting. */
  coloredIcon?: boolean;
}

export const SOURCE_DEFINITIONS: Record<string, SourceDefinition> = {
  binance: {
    id: "binance",
    label: "Binance",
    kind: "exchange",
    iconFile: "binance.svg",
    brandColor: "F0B90B",
    coloredIcon: true,
  },
  kraken: {
    id: "kraken",
    label: "Kraken",
    kind: "exchange",
    description:
      "Kraken exchange ledger CSV — spot trades, deposits, withdrawals, staking, and fees.",
    iconFile: "kraken.svg",
    brandColor: "7132F5",
    coloredIcon: true,
  },
  woox: {
    id: "woox",
    label: "WOO X",
    kind: "exchange",
    iconFile: "woox.svg",
    brandColor: "1CE5AF",
    coloredIcon: true,
  },
  hyperliquid: {
    id: "hyperliquid",
    label: "Hyperliquid",
    kind: "exchange",
    description:
      "Hyperliquid perp trades pulled automatically when you import a 0x wallet address.",
    iconFile: "hyperliquid.svg",
    brandColor: "97FCE4",
    coloredIcon: true,
  },
  variational: {
    id: "variational",
    label: "Variational",
    kind: "exchange",
    description:
      "Variational perps CSV — transfers, funding, realized PnL, and trade fills.",
    iconFile: "",
    brandColor: "6366F1",
  },
  cryptocom: {
    id: "cryptocom",
    label: "Crypto.com",
    kind: "exchange",
    description: "Crypto.com app transaction history CSV.",
    iconFile: "cryptocom.svg",
    brandColor: "03316C",
    coloredIcon: true,
  },
  coinbase: {
    id: "coinbase",
    label: "Coinbase",
    kind: "exchange",
    iconFile: "coinbase.svg",
    brandColor: "0052FF",
    coloredIcon: true,
  },
  okx: {
    id: "okx",
    label: "OKX",
    kind: "exchange",
    iconFile: "okx.svg",
    brandColor: "FFFFFF",
  },
  kucoin: {
    id: "kucoin",
    label: "KuCoin",
    kind: "exchange",
    iconFile: "kucoin.svg",
    brandColor: "01BC8D",
  },
  ethereum: {
    id: "ethereum",
    label: "Ethereum",
    kind: "chain",
    description:
      "On-chain Ethereum mainnet activity from a 0x wallet address import (Etherscan).",
    iconFile: "ethereum.svg",
    brandColor: "627EEA",
  },
  arbitrum: {
    id: "arbitrum",
    label: "Arbitrum",
    kind: "chain",
    iconFile: "arbitrum.svg",
    brandColor: "213147",
    coloredIcon: true,
  },
  bsc: {
    id: "bsc",
    label: "BNB Chain",
    kind: "chain",
    iconFile: "bnbchain.svg",
    brandColor: "F0B90B",
  },
  base: {
    id: "base",
    label: "Base",
    kind: "chain",
    iconFile: "base.svg",
    brandColor: "0052FF",
    coloredIcon: true,
  },
  polygon: {
    id: "polygon",
    label: "Polygon",
    kind: "chain",
    iconFile: "polygon.svg",
    brandColor: "7F3CE2",
  },
  optimism: {
    id: "optimism",
    label: "Optimism",
    kind: "chain",
    iconFile: "optimism.svg",
    brandColor: "FF0420",
  },
  avalanche: {
    id: "avalanche",
    label: "Avalanche",
    kind: "chain",
    iconFile: "avalanche.svg",
    brandColor: "E84142",
    coloredIcon: true,
  },
  solana: {
    id: "solana",
    label: "Solana",
    kind: "chain",
    description:
      "On-chain Solana wallet activity — SPL transfers, swaps, staking, and fees. From a pasted wallet address (Helius) or a Solana explorer CSV export.",
    iconFile: "solana.svg",
    brandColor: "9945FF",
  },
  bitcoin: {
    id: "bitcoin",
    label: "Bitcoin",
    kind: "chain",
    description: "On-chain Bitcoin wallet activity from a bc1/1/3 address import.",
    iconFile: "bitcoin.svg",
    brandColor: "F7931A",
  },
  cardano: {
    id: "cardano",
    label: "Cardano",
    kind: "chain",
    iconFile: "cardano.svg",
    brandColor: "3468D1",
  },
  celestia: {
    id: "celestia",
    label: "Celestia",
    kind: "chain",
    iconFile: "celestia.svg",
    brandColor: "7A2BF9",
    coloredIcon: true,
  },
};

export function normalizeSourceId(source: string | null | undefined): string {
  const raw = (source ?? "unknown").trim().toLowerCase();
  if (raw === "bnb" || raw === "binance-smart-chain") return "bsc";
  return raw;
}

export function getSourceDefinition(
  source: string | null | undefined
): SourceDefinition {
  const id = normalizeSourceId(source);
  const known = SOURCE_DEFINITIONS[id];
  if (known) return known;
  return {
    id,
    label: id === "unknown" ? "Unknown" : id.replace(/_/g, " "),
    kind: "chain",
    description:
      id === "unknown"
        ? "Transactions with no platform or chain identified."
        : `Transactions tagged with source “${id.replace(/_/g, " ")}”.`,
    iconFile: "",
    brandColor: "94A3B8",
  };
}

/** True for known exchange CSV sources (Kraken, Binance, etc.), not on-chain wallets. */
export function isExchangeSource(source: string | null | undefined): boolean {
  const id = normalizeSourceId(source);
  return SOURCE_DEFINITIONS[id]?.kind === "exchange";
}

export function sourceIconUrl(source: string | null | undefined): string | null {
  const def = getSourceDefinition(source);
  if (!def.iconFile) return null;
  return `/icons/exchanges/${def.iconFile}`;
}

export function countBySource(
  transactions: { source?: string | null }[]
): Map<string, number> {
  const counts = new Map<string, number>();
  for (const tx of transactions) {
    const id = normalizeSourceId(tx.source);
    counts.set(id, (counts.get(id) ?? 0) + 1);
  }
  return counts;
}

export function matchesSourceFilter(
  source: string | null | undefined,
  disabledSources: ReadonlySet<string>
): boolean {
  if (disabledSources.size === 0) return true;
  return !disabledSources.has(normalizeSourceId(source));
}
