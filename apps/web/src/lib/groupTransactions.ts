import type { Transaction } from "@/lib/types";
import {
  isUnstakeGroup,
  unstakePrincipal,
  unstakeReward,
} from "@/lib/unstake";

/** Max gap between legs to treat as one on-chain event (swap + fee). */
const CLUSTER_MS = 90_000;

export type LedgerRow =
  | { kind: "single"; tx: Transaction }
  | { kind: "group"; id: string; txs: Transaction[]; label: string };

function txTime(tx: Transaction): number {
  return new Date(tx.timestamp).getTime();
}

function sortNewestFirst(txs: Transaction[]): Transaction[] {
  return [...txs].sort((a, b) => txTime(b) - txTime(a));
}

function inferGroupKey(tx: Transaction): string | null {
  if (tx.trade_group_id) return `tg:${tx.trade_group_id}`;
  const hashMatch = tx.id.match(/-(0x[a-fA-F0-9]{8,})-/);
  if (hashMatch) return `hash:${hashMatch[1].toLowerCase()}`;
  const solMatch = tx.id.match(/^sol-([A-Za-z0-9]{8,})-/);
  if (solMatch) return `sol:${solMatch[1]}`;
  return null;
}

function groupLabel(txs: Transaction[]): string {
  if (isUnstakeGroup(txs)) {
    const principal = unstakePrincipal(txs);
    const reward = unstakeReward(txs);
    if (principal && reward) {
      return `Unstake · ${principal.asset} + ${reward.asset} reward`;
    }
    return "Unstake";
  }

  const types = new Set(txs.map((t) => t.transaction_type));
  const assets = [
    ...new Set(
      txs.flatMap((t) => [t.asset, t.counter_asset].filter(Boolean) as string[])
    ),
  ];

  if (types.has("BUY") && types.has("SELL")) {
    const primary = assets.slice(0, 2).join(" → ");
    return primary ? `Swap · ${primary}` : "Swap";
  }
  if (types.size === 1 && types.has("FEE")) {
    return `Fees · ${txs.length} legs`;
  }
  if (types.has("TRANSFER")) {
    const outs = txs.filter((t) => t.transfer_direction === "OUT");
    const ins = txs.filter((t) => t.transfer_direction === "IN");
    if (
      outs.length === 1 &&
      ins.length === 1 &&
      outs[0].asset === ins[0].asset
    ) {
      return "Internal transfer";
    }
    return `Transfer · ${txs.length} legs`;
  }
  if (types.size === 1 && (types.has("BUY") || types.has("SELL"))) {
    const side = types.has("BUY") ? "BUY" : "SELL";
    const asset = txs[0].asset;
    return `${side} · ${asset} · ${txs.length} fills`;
  }
  return `${txs.length} related · ${[...types].join(", ")}`;
}

function isFiatPurchase(tx: Transaction): boolean {
  return tx.id.includes("crypto_purchase");
}

function isExchange(tx: Transaction): boolean {
  return (
    tx.id.includes("crypto_exchange") ||
    tx.id.includes("crypto_viban_exchange")
  );
}

function isSwapPair(a: Transaction, b: Transaction): boolean {
  if (a.counter_asset === b.asset || b.counter_asset === a.asset) {
    return true;
  }
  if (
    a.trade_group_id &&
    b.trade_group_id &&
    a.trade_group_id === b.trade_group_id
  ) {
    return true;
  }
  const types = new Set([a.transaction_type, b.transaction_type]);
  if (types.has("BUY") && types.has("SELL")) {
    const assets = new Set(
      [a.asset, a.counter_asset, b.asset, b.counter_asset].filter(
        Boolean
      ) as string[]
    );
    if (assets.size <= 3) return true;
  }
  return false;
}

function isExchangeFillCluster(a: Transaction, b: Transaction): boolean {
  if (a.source !== b.source) return false;
  if (a.asset !== b.asset) return false;
  if (a.transaction_type !== b.transaction_type) return false;
  if (a.transaction_type !== "BUY" && a.transaction_type !== "SELL") return false;
  // Kraken partial fills share the exact ledger timestamp.
  return a.timestamp === b.timestamp;
}

function shouldCluster(a: Transaction, b: Transaction): boolean {
  if (a.source !== b.source) return false;
  if (a.timestamp.slice(0, 10) !== b.timestamp.slice(0, 10)) return false;
  if (Math.abs(txTime(a) - txTime(b)) > CLUSTER_MS) return false;
  if (isFiatPurchase(a) && isExchange(b)) return false;
  if (isFiatPurchase(b) && isExchange(a)) return false;
  return isSwapPair(a, b) || isExchangeFillCluster(a, b);
}

function clusterByTime(txs: Transaction[]): Transaction[][] {
  const sorted = sortNewestFirst(txs);
  const clusters: Transaction[][] = [];
  const used = new Set<string>();

  for (let i = 0; i < sorted.length; i++) {
    if (used.has(sorted[i].id)) continue;
    const anchor = sorted[i];
    const cluster = [anchor];
    used.add(anchor.id);

    for (let j = i + 1; j < sorted.length; j++) {
      const candidate = sorted[j];
      if (used.has(candidate.id)) continue;
      if (!shouldCluster(anchor, candidate)) continue;
      cluster.push(candidate);
      used.add(candidate.id);
    }

    clusters.push(cluster);
  }

  return clusters;
}

/** Collapse swap legs and other near-simultaneous rows into expandable groups. */
export function groupTransactions(txs: Transaction[]): LedgerRow[] {
  if (!txs.length) return [];

  const consumed = new Set<string>();
  const rows: LedgerRow[] = [];

  const byKey = new Map<string, Transaction[]>();
  for (const tx of txs) {
    const key = inferGroupKey(tx);
    if (!key) continue;
    const bucket = byKey.get(key) ?? [];
    bucket.push(tx);
    byKey.set(key, bucket);
  }

  for (const [key, members] of byKey) {
    if (members.length >= 2) {
      const sorted = sortNewestFirst(members);
      sorted.forEach((t) => consumed.add(t.id));
      rows.push({
        kind: "group",
        id: key,
        txs: sorted,
        label: groupLabel(sorted),
      });
    }
  }

  const stillUngrouped = txs.filter((t) => !consumed.has(t.id));
  for (const cluster of clusterByTime(stillUngrouped)) {
    if (cluster.length >= 2) {
      const sorted = sortNewestFirst(cluster);
      sorted.forEach((t) => consumed.add(t.id));
      rows.push({
        kind: "group",
        id: `time:${sorted[0].id}`,
        txs: sorted,
        label: groupLabel(sorted),
      });
    }
  }

  for (const tx of txs) {
    if (!consumed.has(tx.id)) {
      rows.push({ kind: "single", tx });
    }
  }

  rows.sort((a, b) => {
    const ta = a.kind === "single" ? txTime(a.tx) : txTime(a.txs[0]);
    const tb = b.kind === "single" ? txTime(b.tx) : txTime(b.txs[0]);
    return tb - ta;
  });

  return rows;
}
