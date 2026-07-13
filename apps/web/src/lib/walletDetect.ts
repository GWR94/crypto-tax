import type { WalletChain } from "@/lib/types";

/** Major on-chain networks for 0x import (matches backend evm_chains.py). */
export const EVM_AUTO_IMPORT_LABEL =
  "Ethereum, BNB Chain, Arbitrum, Base, Polygon, Optimism, Avalanche";

/** Ledger `source` values from on-chain or venue wallet import. */
export const WALLET_SOURCE_HINTS = new Set<string>([
  "solana",
  "bitcoin",
  "cardano",
  "celestia",
  "hyperliquid",
  "ethereum",
  "arbitrum",
  "base",
  "optimism",
  "polygon",
  "bsc",
  "avalanche",
  "linea",
  "blast",
  "scroll",
]);

export const WALLET_CHAIN_LABELS: Record<WalletChain, string> = {
  solana: "Solana",
  ethereum: `0x address → ${EVM_AUTO_IMPORT_LABEL} + Hyperliquid`,
  bitcoin: "Bitcoin",
  cardano: "Cardano",
  celestia: "Celestia",
};

const EVM_RE = /^0x[0-9a-fA-F]{40}$/;
const BTC_RE =
  /^(?:[13][a-km-zA-HJ-NP-Z1-9]{25,34}|bc1[ac-hj-np-z02-9]{11,71}|bc1p[ac-hm-np-z02-9]{58})$/;
const CARDANO_RE = /^(?:addr1[a-z0-9]{50,}|stake1[a-z0-9]{50,})$/;
const CELESTIA_RE = /^celestia1[a-z0-9]{38,}$/;
const SOLANA_RE = /^[1-9A-HJ-NP-Za-km-z]{32,44}$/;

/** Infer chain from address shape (matches backend wallet_detect). */
export function detectWalletChain(address: string): WalletChain | null {
  const text = address.trim();
  if (!text) return null;

  if (EVM_RE.test(text)) return "ethereum";

  const lower = text.toLowerCase();
  if (lower.startsWith("celestia1")) {
    return CELESTIA_RE.test(lower) ? "celestia" : null;
  }

  if (lower.startsWith("addr1") || lower.startsWith("stake1")) {
    return CARDANO_RE.test(lower) ? "cardano" : null;
  }

  if (BTC_RE.test(text)) return "bitcoin";

  if (SOLANA_RE.test(text)) return "solana";

  return null;
}

export function walletDetectError(address: string): string | null {
  const text = address.trim();
  if (!text) return null;
  if (detectWalletChain(text)) return null;
  return (
    "Unrecognized address — use Solana, 0x… (on-chain + Hyperliquid), Bitcoin, " +
    "Cardano (addr1…), or Celestia (celestia1…)"
  );
}
