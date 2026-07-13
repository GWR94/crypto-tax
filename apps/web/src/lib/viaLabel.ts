import { getSourceDefinition, isExchangeSource } from "@/lib/sourceCatalog";
import { shortenAddress } from "@/lib/utils";
import type { AssetLabel, Transaction } from "@/lib/types";

function counterpartyShort(tx: Transaction): string | null {
  if (!tx.counterparty_address?.trim()) return null;
  return shortenAddress(tx.counterparty_address.trim());
}

function transferViaLabel(tx: Transaction): string {
  const cp = counterpartyShort(tx);
  const exchange = isExchangeSource(tx.source);
  const exchangeName = exchange
    ? getSourceDefinition(tx.source).label
    : null;

  if (tx.transfer_direction === "OUT") {
    if (exchange) {
      return cp
        ? `${exchangeName} transfer out · to ${cp}`
        : `${exchangeName} transfer out`;
    }
    return cp ? `To ${cp}` : "On-chain transfer out";
  }

  if (tx.transfer_direction === "IN") {
    if (exchange) {
      return cp
        ? `${exchangeName} transfer in · from ${cp}`
        : `${exchangeName} transfer in`;
    }
    return cp ? `From ${cp}` : "On-chain transfer in";
  }

  return "—";
}

/** Human-readable counterparty / route label for the Via column. */
export function viaLabel(
  tx: Transaction,
  assetLabels: Record<string, AssetLabel>
): string {
  const counterSymbol = tx.counter_asset
    ? assetLabels[tx.counter_asset]?.symbol ?? tx.counter_asset
    : null;
  const cp = counterpartyShort(tx);

  if (tx.transaction_type === "BUY" || tx.transaction_type === "SELL") {
    if (counterSymbol) {
      return tx.transaction_type === "BUY"
        ? `Swap · paid ${counterSymbol}`
        : `Swap · for ${counterSymbol}`;
    }
    if (cp) {
      return tx.transaction_type === "BUY" ? `Bought · ${cp}` : `Sold · ${cp}`;
    }
  }

  if (tx.transaction_type === "STAKING") {
    if (counterSymbol && cp) return `${counterSymbol} reward · from ${cp}`;
    if (counterSymbol) return `${counterSymbol} staking reward`;
    if (cp) return `Reward · from ${cp}`;
    return "Staking reward";
  }

  if (tx.transaction_type === "TRANSFER" || tx.transfer_direction) {
    return transferViaLabel(tx);
  }

  if (counterSymbol) return `↔ ${counterSymbol}`;
  return "—";
}

/** Full counterparty for hover tooltips. */
export function viaTooltip(tx: Transaction): string | undefined {
  const addr = tx.counterparty_address?.trim();
  if (!addr) return undefined;
  return addr;
}
