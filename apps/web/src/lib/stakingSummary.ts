import type { StakingEvent, StakingPosition, StakingSummary, Transaction } from "@/lib/types";
import { isDustTransaction } from "@/lib/utils";
import { unstakeGroupIds, unstakePrincipal, unstakeReward } from "@/lib/unstake";

const LST_ASSETS = new Set(["MSOL", "JITOSOL", "BSOL"]);
const SOL_ASSETS = new Set(["SOL", "WSOL"]);

function sym(asset: string): string {
  return asset.trim().toUpperCase();
}

function isLst(asset: string): boolean {
  return LST_ASSETS.has(sym(asset));
}

function isSol(asset: string): boolean {
  return SOL_ASSETS.has(sym(asset));
}

function groupByGid(transactions: Transaction[]): Map<string, Transaction[]> {
  const groups = new Map<string, Transaction[]>();
  for (const tx of transactions) {
    if (!tx.trade_group_id) continue;
    const bucket = groups.get(tx.trade_group_id) ?? [];
    bucket.push(tx);
    groups.set(tx.trade_group_id, bucket);
  }
  return groups;
}

function txValue(tx: Transaction): number {
  return tx.fiat_value_at_trigger ?? 0;
}

function isMeaningfulStakingReward(tx: Transaction): boolean {
  return !isDustTransaction(tx);
}

/** Aggregate staking flows, liquid staking, and reward income from spot ledger rows. */
export function buildStakingSummary(transactions: Transaction[]): StakingSummary {
  const empty: StakingSummary = {
    total_income: 0,
    reward_count: 0,
    unstake_count: 0,
    liquid_stake_count: 0,
    liquid_unstake_count: 0,
    event_count: 0,
    hidden_dust_count: 0,
    total_staked_lst: 0,
    positions: [],
    income_by_asset: {},
    events: [],
  };

  if (!transactions.length) return empty;

  const unstakeGids = unstakeGroupIds(transactions);
  const byGid = groupByGid(transactions);
  const usedIds = new Set<string>();
  const events: StakingEvent[] = [];

  for (const gid of unstakeGids) {
    const group = byGid.get(gid) ?? [];
    const principal = unstakePrincipal(group);
    const reward = unstakeReward(group);
    if (!principal) continue;
    for (const tx of group) usedIds.add(tx.id);
    events.push({
      id: `unstake-${gid}`,
      kind: "unstake",
      timestamp: principal.timestamp,
      asset: principal.asset,
      source: principal.source,
      principal_amount: principal.amount,
      reward_amount: reward?.amount,
      reward_asset: reward?.asset,
      income: reward ? txValue(reward) : 0,
      fiat_currency: reward?.fiat_currency ?? principal.fiat_currency,
      counterparty: principal.counterparty_address,
      trade_group_id: gid,
      transaction_ids: group.map((tx) => tx.id),
    });
  }

  for (const [gid, group] of byGid) {
    if (unstakeGids.has(gid)) continue;

    const lstBuy = group.find(
      (tx) => tx.transaction_type === "BUY" && isLst(tx.asset) && !usedIds.has(tx.id)
    );
    const solSell = group.find(
      (tx) =>
        tx.transaction_type === "SELL" && isSol(tx.asset) && !usedIds.has(tx.id)
    );
    const lstSell = group.find(
      (tx) => tx.transaction_type === "SELL" && isLst(tx.asset) && !usedIds.has(tx.id)
    );
    const solBuy = group.find(
      (tx) => tx.transaction_type === "BUY" && isSol(tx.asset) && !usedIds.has(tx.id)
    );
    const yieldRow = group.find(
      (tx) =>
        !usedIds.has(tx.id) &&
        (tx.id.endsWith("-lst-yield") ||
          (tx.transaction_type === "STAKING" && isSol(tx.asset)))
    );

    if (lstBuy && solSell) {
      for (const tx of [lstBuy, solSell]) usedIds.add(tx.id);
      events.push({
        id: `lst-stake-${gid}`,
        kind: "liquid_stake",
        timestamp: lstBuy.timestamp,
        asset: solSell.asset,
        lst_asset: lstBuy.asset,
        staked_amount: solSell.amount,
        lst_amount: lstBuy.amount,
        income: 0,
        fiat_currency: lstBuy.fiat_currency ?? solSell.fiat_currency,
        source: lstBuy.source,
        trade_group_id: gid,
        transaction_ids: [lstBuy.id, solSell.id],
      });
    }

    if (lstSell && solBuy) {
      const related = [lstSell, solBuy];
      if (yieldRow) related.push(yieldRow);
      for (const tx of related) usedIds.add(tx.id);
      events.push({
        id: `lst-unstake-${gid}`,
        kind: "liquid_unstake",
        timestamp: lstSell.timestamp,
        asset: solBuy.asset,
        lst_asset: lstSell.asset,
        principal_amount: solBuy.amount,
        lst_amount: lstSell.amount,
        reward_amount: yieldRow?.amount,
        reward_asset: yieldRow?.asset,
        income: yieldRow ? txValue(yieldRow) : 0,
        fiat_currency: yieldRow?.fiat_currency ?? solBuy.fiat_currency,
        source: lstSell.source,
        trade_group_id: gid,
        transaction_ids: related.map((tx) => tx.id),
      });
    }
  }

  let hiddenDustCount = 0;

  for (const tx of transactions) {
    if (usedIds.has(tx.id)) continue;
    if (tx.transaction_type !== "STAKING") continue;
    if (!isMeaningfulStakingReward(tx)) {
      hiddenDustCount += 1;
      continue;
    }
    usedIds.add(tx.id);
    events.push({
      id: `reward-${tx.id}`,
      kind: "reward",
      timestamp: tx.timestamp,
      asset: tx.asset,
      reward_amount: tx.amount,
      income: txValue(tx),
      fiat_currency: tx.fiat_currency,
      source: tx.source,
      counterparty: tx.counterparty_address,
      trade_group_id: tx.trade_group_id,
      transaction_ids: [tx.id],
    });
  }

  events.sort(
    (a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime()
  );

  const netLst = new Map<string, number>();
  for (const tx of transactions) {
    if (!isLst(tx.asset)) continue;
    const key = sym(tx.asset);
    const current = netLst.get(key) ?? 0;
    if (tx.transaction_type === "BUY") netLst.set(key, current + tx.amount);
    else if (tx.transaction_type === "SELL") netLst.set(key, current - tx.amount);
  }

  const incomeByAsset: Record<string, number> = {};
  let totalIncome = 0;
  for (const event of events) {
    totalIncome += event.income;
    if (event.income <= 0) continue;
    const incomeAsset = event.reward_asset ?? event.asset;
    incomeByAsset[incomeAsset] = (incomeByAsset[incomeAsset] ?? 0) + event.income;
  }

  const positions: StakingPosition[] = [];
  for (const [asset, net] of netLst) {
    if (net <= 1e-8) continue;
    const lstIncome = events
      .filter(
        (event) =>
          event.lst_asset === asset ||
          (event.kind === "reward" && sym(event.asset) === asset)
      )
      .reduce((sum, event) => sum + event.income, 0);
    positions.push({
      asset,
      net_amount: net,
      kind: "liquid_staking",
      total_income: Math.round(lstIncome * 100) / 100,
    });
  }
  positions.sort((a, b) => b.net_amount - a.net_amount);

  return {
    total_income: Math.round(totalIncome * 100) / 100,
    reward_count: events.filter((e) => e.kind === "reward").length,
    unstake_count: events.filter((e) => e.kind === "unstake").length,
    liquid_stake_count: events.filter((e) => e.kind === "liquid_stake").length,
    liquid_unstake_count: events.filter((e) => e.kind === "liquid_unstake").length,
    event_count: events.length,
    hidden_dust_count: hiddenDustCount,
    total_staked_lst: positions.reduce((sum, p) => sum + p.net_amount, 0),
    positions,
    income_by_asset: Object.fromEntries(
      Object.entries(incomeByAsset).map(([asset, value]) => [
        asset,
        Math.round(value * 100) / 100,
      ])
    ),
    events,
  };
}

export function stakingEventLabel(kind: StakingEvent["kind"]): string {
  switch (kind) {
    case "reward":
      return "Staking reward";
    case "unstake":
      return "Validator unstake";
    case "liquid_stake":
      return "Liquid stake";
    case "liquid_unstake":
      return "Liquid unstake";
    default:
      return kind;
  }
}
