import type { Transaction } from "@/lib/types";
import { unstakeGroupIds } from "@/lib/unstake";

/** Sources whose rows are perps when no explicit instrument_kind was recorded. */
const LEGACY_PERP_SOURCES = new Set(["woox", "hyperliquid", "variational"]);

/** Perpetual-futures rows are kept out of the spot ledger and FIFO engine. */
export function isPerpTransaction(tx: Transaction): boolean {
  if (tx.instrument_kind === "perp") return true;
  if (tx.instrument_kind === "spot") return false;
  if (LEGACY_PERP_SOURCES.has(tx.source ?? "")) return true;
  return false;
}

const EXCHANGE_SOURCES = new Set(["binance", "cryptocom", "exchange"]);
const STAKING_ECHO_MAX_HOURS = 48;
const AMOUNT_REL_TOL = 0.02;

function isStakingEchoTransfer(staking: Transaction, transfer: Transaction): boolean {
  if (staking.transaction_type !== "STAKING") return false;
  if (transfer.transaction_type !== "TRANSFER") return false;
  if (!EXCHANGE_SOURCES.has(transfer.source ?? "")) return false;
  if ((staking.source ?? "") !== (transfer.source ?? "")) return false;
  if (staking.asset !== transfer.asset) return false;
  if (staking.amount <= 0 || transfer.amount <= 0) return false;

  const rel =
    Math.abs(transfer.amount - staking.amount) /
    Math.max(staking.amount, transfer.amount);
  if (rel > AMOUNT_REL_TOL) return false;

  const deltaH =
    (new Date(transfer.timestamp).getTime() - new Date(staking.timestamp).getTime()) /
    3_600_000;
  return deltaH > 0 && deltaH <= STAKING_ECHO_MAX_HOURS;
}

/** Hide exchange staking rewards; keep on-chain unstake reward legs grouped with principal. */
export function filterExcludeStaking(transactions: Transaction[]): Transaction[] {
  const keepUnstake = unstakeGroupIds(transactions);
  const stakingRows = transactions.filter(
    (t) =>
      t.transaction_type === "STAKING" &&
      !(t.trade_group_id && keepUnstake.has(t.trade_group_id))
  );
  const dropIds = new Set(stakingRows.map((t) => t.id));

  for (const transfer of transactions) {
    if (transfer.transaction_type !== "TRANSFER") continue;
    for (const staking of stakingRows) {
      if (isStakingEchoTransfer(staking, transfer)) {
        dropIds.add(transfer.id);
        break;
      }
    }
  }

  return transactions.filter((t) => !dropIds.has(t.id));
}

export function splitLedger(transactions: Transaction[]): {
  spot: Transaction[];
  perps: Transaction[];
} {
  const spot: Transaction[] = [];
  const perps: Transaction[] = [];
  for (const tx of transactions) {
    if (isPerpTransaction(tx)) perps.push(tx);
    else spot.push(tx);
  }
  return { spot, perps };
}
