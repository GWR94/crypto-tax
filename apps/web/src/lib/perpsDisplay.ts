import type { AssetLabel, Transaction } from "@/lib/types";

const INVALID_ASSETS = new Set(["", "NAN", "NONE", "NULL"]);

export function resolvePerpAssetSymbol(
  tx: Transaction,
  assetLabels: Record<string, AssetLabel> = {}
): string {
  const labeled = assetLabels[tx.asset]?.symbol ?? tx.asset ?? "";
  const raw = labeled.trim().toUpperCase();
  if (!INVALID_ASSETS.has(raw)) return labeled;
  if (tx.fiat_currency) return tx.fiat_currency;
  if (tx.counter_asset) return tx.counter_asset;
  return "—";
}

/** True for executed perp fills (not deposits, fees, or PnL settlements). */
export function isPerpFill(tx: Transaction): boolean {
  return (
    (tx.transaction_type === "BUY" || tx.transaction_type === "SELL") &&
    tx.amount > 0
  );
}

export function perpSideLabel(tx: Transaction): string {
  if (tx.transaction_type === "TRANSFER") {
    if (tx.transfer_direction === "IN") return "DEPOSIT";
    if (tx.transfer_direction === "OUT") return "WITHDRAW";
    return "TRANSFER";
  }
  if (tx.transaction_type === "FEE") return "FEE";
  return tx.transaction_type;
}

export function perpRowSize(
  tx: Transaction,
  assetLabels: Record<string, AssetLabel> = {}
): { amount: number; asset: string } | null {
  if (isPerpFill(tx)) {
    return { amount: tx.amount, asset: resolvePerpAssetSymbol(tx, assetLabels) };
  }
  if (tx.transaction_type === "TRANSFER" && tx.amount > 0) {
    return { amount: tx.amount, asset: resolvePerpAssetSymbol(tx, assetLabels) };
  }
  return null;
}

export function perpRowNotional(tx: Transaction): number | null {
  if (!isPerpFill(tx) || tx.fiat_value_at_trigger <= 0) return null;
  return tx.fiat_value_at_trigger;
}

export function perpTradeNotional(tx: Transaction): number {
  return perpRowNotional(tx) ?? 0;
}

export function perpCountsAsClose(tx: Transaction): boolean {
  return tx.realized_pnl != null && isPerpFill(tx);
}
