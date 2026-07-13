import type { Transaction } from "@/lib/types";

/** trade_group_ids pairing principal return (TRANSFER IN) with a STAKING reward. */
export function unstakeGroupIds(transactions: Transaction[]): Set<string> {
  const byGroup = new Map<string, Transaction[]>();
  for (const tx of transactions) {
    if (!tx.trade_group_id) continue;
    const bucket = byGroup.get(tx.trade_group_id) ?? [];
    bucket.push(tx);
    byGroup.set(tx.trade_group_id, bucket);
  }

  const ids = new Set<string>();
  for (const [gid, group] of byGroup) {
    const hasPrincipal = group.some(
      (t) => t.transaction_type === "TRANSFER" && t.transfer_direction === "IN"
    );
    const hasReward = group.some((t) => t.transaction_type === "STAKING");
    if (hasPrincipal && hasReward) ids.add(gid);
  }
  return ids;
}

export function isUnstakeGroup(txs: Transaction[]): boolean {
  if (!txs.length) return false;
  const gid = txs[0].trade_group_id;
  if (!gid) return false;
  const hasPrincipal = txs.some(
    (t) => t.transaction_type === "TRANSFER" && t.transfer_direction === "IN"
  );
  const hasReward = txs.some((t) => t.transaction_type === "STAKING");
  return hasPrincipal && hasReward;
}

export function unstakePrincipal(txs: Transaction[]): Transaction | undefined {
  return txs.find(
    (t) => t.transaction_type === "TRANSFER" && t.transfer_direction === "IN"
  );
}

export function unstakeReward(txs: Transaction[]): Transaction | undefined {
  return txs.find((t) => t.transaction_type === "STAKING");
}
