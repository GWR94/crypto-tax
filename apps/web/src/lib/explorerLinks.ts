import { normalizeSourceId } from "@/lib/sourceCatalog";

const EVM_EXPLORERS: Record<string, string> = {
  ethereum: "https://etherscan.io",
  arbitrum: "https://arbiscan.io",
  base: "https://basescan.org",
  optimism: "https://optimistic.etherscan.io",
  polygon: "https://polygonscan.com",
  bsc: "https://bscscan.com",
  avalanche: "https://snowtrace.io",
};

function chainExplorerBase(source: string | null | undefined): string | null {
  const id = normalizeSourceId(source);
  if (id === "solana") return "https://solscan.io";
  if (id === "bitcoin") return "https://mempool.space";
  if (id === "cardano") return "https://cardanoscan.io";
  if (id === "celestia") return "https://www.mintscan.io/celestia";
  return EVM_EXPLORERS[id] ?? null;
}

export function explorerTxUrl(
  source: string | null | undefined,
  txId: string | null | undefined
): string | null {
  const id = txId?.trim();
  if (!id) return null;
  const base = chainExplorerBase(source);
  if (!base) return null;
  if (normalizeSourceId(source) === "celestia") {
    return `${base}/tx/${encodeURIComponent(id)}`;
  }
  return `${base}/tx/${id}`;
}

export function explorerAddressUrl(
  source: string | null | undefined,
  address: string | null | undefined
): string | null {
  const addr = address?.trim();
  if (!addr) return null;
  const chain = normalizeSourceId(source);
  const base = chainExplorerBase(source);
  if (!base) return null;
  if (chain === "solana") {
    return `${base}/account/${addr}`;
  }
  return `${base}/address/${addr}`;
}

export function explorerTokenUrl(
  source: string | null | undefined,
  mint: string | null | undefined
): string | null {
  const token = mint?.trim();
  if (!token) return null;
  const chain = normalizeSourceId(source);
  if (chain === "solana") {
    return `https://solscan.io/token/${token}`;
  }
  const base = chainExplorerBase(source);
  if (!base) return null;
  if (token.startsWith("0x")) {
    return `${base}/token/${token}`;
  }
  return null;
}

/** Prefer stored on_chain_tx_id, then full Solana trade_group_id. */
export function resolveOnChainTxId(tx: {
  on_chain_tx_id?: string | null;
  trade_group_id?: string | null;
  source?: string | null;
}): string | null {
  if (tx.on_chain_tx_id?.trim()) return tx.on_chain_tx_id.trim();
  const gid = tx.trade_group_id?.trim();
  if (!gid) return null;
  if (gid.startsWith("0x") && gid.length === 66) return gid;
  if (normalizeSourceId(tx.source) === "solana" && gid.length >= 32 && !gid.startsWith("0x")) {
    return gid;
  }
  if (normalizeSourceId(tx.source) === "bitcoin" && gid.length >= 32) return gid;
  return null;
}
