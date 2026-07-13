import type { WalletChain } from "@/lib/types";
import { detectWalletChain } from "@/lib/walletDetect";

const STORAGE_KEY = "crypto-tax-saved-wallets";

export interface SavedWallet {
  address: string;
  chain: WalletChain;
  label?: string;
  saved_at: string;
}

function walletKey(address: string, chain: WalletChain): string {
  return `${chain}:${address.trim().toLowerCase()}`;
}

export function loadSavedWallets(): SavedWallet[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as SavedWallet[];
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (entry) =>
        typeof entry.address === "string" &&
        typeof entry.chain === "string" &&
        typeof entry.saved_at === "string"
    );
  } catch {
    return [];
  }
}

export function saveSavedWallets(wallets: SavedWallet[]): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(wallets));
  } catch {
    /* ignore quota errors */
  }
}

export function upsertSavedWallet(
  address: string,
  chain?: WalletChain | null,
  label?: string
): SavedWallet[] {
  const trimmed = address.trim();
  const resolvedChain = chain ?? detectWalletChain(trimmed);
  if (!trimmed || !resolvedChain) return loadSavedWallets();

  const nextEntry: SavedWallet = {
    address: trimmed,
    chain: resolvedChain,
    label: label?.trim() || undefined,
    saved_at: new Date().toISOString(),
  };
  const key = walletKey(trimmed, resolvedChain);
  const without = loadSavedWallets().filter(
    (entry) => walletKey(entry.address, entry.chain) !== key
  );
  const next = [nextEntry, ...without];
  saveSavedWallets(next);
  return next;
}

export function removeSavedWallet(address: string, chain: WalletChain): SavedWallet[] {
  const key = walletKey(address, chain);
  const next = loadSavedWallets().filter(
    (entry) => walletKey(entry.address, entry.chain) !== key
  );
  saveSavedWallets(next);
  return next;
}

export function syncSavedWalletsFromSources(
  sources: Array<{ kind: string; address?: string | null; chain?: string | null; label?: string }>
): SavedWallet[] {
  let wallets = loadSavedWallets();
  for (const source of sources) {
    if (source.kind !== "wallet" || !source.address) continue;
    const chain = detectWalletChain(source.address);
    if (!chain) continue;
    wallets = upsertSavedWallet(source.address, chain, source.label);
  }
  return wallets;
}
