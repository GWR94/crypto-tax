import { formatInstrument } from "@/lib/instruments";
import { perpRowNotional, resolvePerpAssetSymbol } from "@/lib/perpsDisplay";
import type { AssetLabel, Transaction, TransactionType } from "@/lib/types";
import type { LedgerRow } from "@/lib/groupTransactions";

const TYPE_PRIORITY: TransactionType[] = ["BUY", "SELL"];

function txTime(tx: Transaction): number {
  return new Date(tx.timestamp).getTime();
}

function sortNewestFirst(txs: Transaction[]): Transaction[] {
  return [...txs].sort((a, b) => txTime(b) - txTime(a));
}

function perpGroupLabel(txs: Transaction[]): string {
  const sample = txs.find((t) => t.instrument) ?? txs[0];
  const instrument = formatInstrument(sample.instrument, {
    asset: sample.asset,
    counter_asset: sample.counter_asset,
  });
  const venue = txs.find((t) => t.venue_order_type)?.venue_order_type;
  if (venue) return `${instrument} · ${venue}`;
  return `${instrument} · ${txs.length} fills`;
}

/** Group perp fills by exchange order id (trade_group_id). */
export function groupPerpTransactions(txs: Transaction[]): LedgerRow[] {
  if (!txs.length) return [];

  const byOrder = new Map<string, Transaction[]>();
  const singles: Transaction[] = [];

  for (const tx of txs) {
    if (tx.trade_group_id) {
      const key = `${tx.source ?? ""}:${tx.trade_group_id}`;
      const bucket = byOrder.get(key) ?? [];
      bucket.push(tx);
      byOrder.set(key, bucket);
    } else {
      singles.push(tx);
    }
  }

  const rows: LedgerRow[] = [];

  for (const [key, members] of byOrder) {
    const sorted = sortNewestFirst(members);
    if (sorted.length === 1) {
      rows.push({ kind: "single", tx: sorted[0] });
    } else {
      rows.push({
        kind: "group",
        id: `perp:${key}`,
        txs: sorted,
        label: perpGroupLabel(sorted),
      });
    }
  }

  for (const tx of singles) {
    rows.push({ kind: "single", tx });
  }

  rows.sort((a, b) => {
    const ta = a.kind === "single" ? txTime(a.tx) : txTime(a.txs[0]);
    const tb = b.kind === "single" ? txTime(b.tx) : txTime(b.txs[0]);
    return tb - ta;
  });

  return rows;
}

export function summarizePerpGroup(
  txs: Transaction[],
  assetLabels: Record<string, AssetLabel> = {}
) {
  let displayType: TransactionType = txs[0].transaction_type;
  for (const type of TYPE_PRIORITY) {
    if (txs.some((t) => t.transaction_type === type)) {
      displayType = type;
      break;
    }
  }

  const primary = txs.find((t) => t.transaction_type === displayType) ?? txs[0];
  const asset = primary.asset;
  const matching = txs.filter((t) => t.asset === asset);
  const totalAmount = matching.length
    ? matching.reduce((sum, t) => sum + t.amount, 0)
    : primary.amount;
  const totalNotional = txs.reduce(
    (sum, t) => sum + (perpRowNotional(t) ?? 0),
    0
  );

  return {
    displayType,
    contract: formatInstrument(primary.instrument, {
      asset: primary.asset,
      counter_asset: primary.counter_asset,
    }),
    venue: primary.venue_order_type,
    asset: primary.asset,
    assetLabel: resolvePerpAssetSymbol(primary, assetLabels),
    totalAmount,
    totalNotional,
    totalFee: txs.reduce((sum, t) => sum + t.fee_fiat, 0),
    totalPnl: txs.reduce((sum, t) => sum + (t.realized_pnl ?? 0), 0),
    hasPnl: txs.some((t) => t.realized_pnl != null),
    timestamp: primary.timestamp,
    source: primary.source,
    denom: primary.fiat_currency ?? "USD",
  };
}
