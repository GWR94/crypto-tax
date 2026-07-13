import type { Transaction } from "@/lib/types";
import type { PerpsSummary } from "@/lib/types";
import { isPerpFill, perpTradeNotional } from "@/lib/perpsDisplay";

/** Build perps KPIs from a filtered transaction list (matches API perps.py). */
export function buildPerpsSummary(transactions: Transaction[]): PerpsSummary {
  if (!transactions.length) {
    return {
      trade_count: 0,
      closed_pnl: 0,
      total_fees: 0,
      total_notional: 0,
      winning_closes: 0,
      losing_closes: 0,
    };
  }

  let closedPnl = 0;
  let winning = 0;
  let losing = 0;

  for (const tx of transactions) {
    if (tx.realized_pnl == null) continue;
    closedPnl += tx.realized_pnl;
    if (!isPerpFill(tx)) continue;
    if (tx.realized_pnl > 0) winning += 1;
    else if (tx.realized_pnl < 0) losing += 1;
  }

  const fills = transactions.filter(isPerpFill);

  return {
    trade_count: fills.length,
    closed_pnl: Math.round(closedPnl * 100) / 100,
    total_fees: Math.round(
      transactions.reduce((sum, t) => sum + t.fee_fiat, 0) * 100
    ) / 100,
    total_notional: Math.round(
      transactions.reduce((sum, t) => sum + perpTradeNotional(t), 0) * 100
    ) / 100,
    winning_closes: winning,
    losing_closes: losing,
  };
}
