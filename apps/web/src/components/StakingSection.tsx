import { useMemo, useState } from "react";
import { ChevronDown, ChevronRight, Coins } from "lucide-react";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { AssetBadge } from "@/components/AssetBadge";
import { SourceBadge } from "@/components/SourceBadge";
import type {
  AssetLabel,
  DisplayCurrency,
  StakingEvent,
  Transaction,
} from "@/lib/types";
import {
  buildStakingSummary,
  stakingEventLabel,
} from "@/lib/stakingSummary";
import {
  countBySource,
  getSourceDefinition,
  normalizeSourceId,
} from "@/lib/sourceCatalog";
import { SourceIcon } from "@/components/icons/SourceIcon";
import { cn, formatCryptoAmount, formatDateTime, formatMoney, formatNumber, shortenAddress } from "@/lib/utils";

function eventAmounts(event: StakingEvent): string {
  switch (event.kind) {
    case "liquid_stake":
      return event.staked_amount != null && event.lst_amount != null && event.lst_asset
        ? `${formatNumber(event.staked_amount)} ${event.asset} → ${formatNumber(event.lst_amount)} ${event.lst_asset}`
        : "—";
    case "liquid_unstake":
      return event.lst_amount != null && event.lst_asset && event.principal_amount != null
        ? `${formatNumber(event.lst_amount)} ${event.lst_asset} → ${formatNumber(event.principal_amount)} ${event.asset}`
        : "—";
    case "unstake":
      return event.principal_amount != null
        ? `${formatNumber(event.principal_amount)} ${event.asset} returned`
        : "—";
    case "reward":
      return event.reward_amount != null
        ? `${formatCryptoAmount(event.reward_amount)} ${event.asset}`
        : "—";
    default:
      return "—";
  }
}

function eventIncome(
  event: StakingEvent,
  currency: DisplayCurrency
): string {
  if (event.income <= 0) return "—";
  const denom = (event.fiat_currency as DisplayCurrency | null) ?? currency;
  return formatMoney(event.income, denom);
}

export function StakingSection({
  transactions,
  currency,
  assetLabels = {},
}: {
  transactions: Transaction[];
  currency: DisplayCurrency;
  assetLabels?: Record<string, AssetLabel>;
}) {
  const [collapsed, setCollapsed] = useState(false);
  const [disabledSources, setDisabledSources] = useState<Set<string>>(new Set());

  const sourceIds = useMemo(() => {
    const counts = countBySource(
      transactions.filter(
        (tx) =>
          tx.transaction_type === "STAKING" ||
          tx.trade_group_id != null
      )
    );
    return [...counts.keys()].sort((a, b) =>
      getSourceDefinition(a).label.localeCompare(getSourceDefinition(b).label)
    );
  }, [transactions]);

  const filtered = useMemo(
    () =>
      transactions.filter(
        (tx) => !disabledSources.has(normalizeSourceId(tx.source))
      ),
    [transactions, disabledSources]
  );

  const summary = useMemo(() => buildStakingSummary(filtered), [filtered]);
  const filtering = disabledSources.size > 0;

  const fmt = (value: number, denom?: string | null) =>
    formatMoney(value, (denom as DisplayCurrency) ?? currency);

  function toggleSource(sourceId: string) {
    setDisabledSources((prev) => {
      const next = new Set(prev);
      if (next.has(sourceId)) next.delete(sourceId);
      else next.add(sourceId);
      return next;
    });
  }

  if (summary.event_count === 0 && summary.positions.length === 0) {
    return null;
  }

  return (
    <Card>
      <CardHeader
        className="flex cursor-pointer flex-col gap-3 space-y-0 sm:flex-row sm:items-center sm:justify-between"
        onClick={() => setCollapsed((v) => !v)}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            setCollapsed((v) => !v);
          }
        }}
        aria-expanded={!collapsed}
      >
        <div className="flex items-center gap-2">
          {collapsed ? (
            <ChevronRight className="h-4 w-4 text-muted-foreground" />
          ) : (
            <ChevronDown className="h-4 w-4 text-muted-foreground" />
          )}
          <Coins className="h-4 w-4 text-primary" />
          <CardTitle className="text-base">Staking</CardTitle>
          <Badge variant="muted">{summary.event_count} events</Badge>
        </div>
        <p className="text-sm text-muted-foreground">
          Rewards, liquid staking (mSOL / bSOL), and validator unstakes.
        </p>
      </CardHeader>

      {!collapsed ? (
        <CardContent className="space-y-4">
          {sourceIds.length > 1 ? (
            <div className="space-y-2" onClick={(e) => e.stopPropagation()}>
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-xs text-muted-foreground">Sources</span>
                {filtering ? (
                  <button
                    type="button"
                    onClick={() => setDisabledSources(new Set())}
                    className="text-xs text-primary underline-offset-2 hover:underline"
                  >
                    Show all
                  </button>
                ) : (
                  <span className="text-xs text-muted-foreground">
                    · click to hide
                  </span>
                )}
              </div>
              <div className="flex flex-wrap gap-2">
                {sourceIds.map((id) => {
                  const def = getSourceDefinition(id);
                  const count = countBySource(transactions).get(id) ?? 0;
                  const active = !disabledSources.has(id);
                  return (
                    <button
                      key={id}
                      type="button"
                      aria-pressed={active}
                      title={`${def.label} (${count})`}
                      onClick={() => toggleSource(id)}
                      className={cn(
                        "inline-flex items-center gap-2 rounded-lg border px-2.5 py-1.5 text-sm transition-colors",
                        active
                          ? "border-border bg-card hover:border-primary/40"
                          : "border-border/50 bg-muted/10 opacity-50"
                      )}
                    >
                      <SourceIcon source={id} muted={!active} />
                      <span
                        className={cn("font-medium", !active && "line-through")}
                      >
                        {def.label}
                      </span>
                    </button>
                  );
                })}
              </div>
            </div>
          ) : null}

          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <div className="rounded-lg border border-border bg-muted/20 px-3 py-2">
              <p className="text-xs text-muted-foreground">Total income</p>
              <p className="text-lg font-semibold tabular-nums">
                {fmt(summary.total_income)}
              </p>
            </div>
            <div className="rounded-lg border border-border bg-muted/20 px-3 py-2">
              <p className="text-xs text-muted-foreground">Reward payouts</p>
              <p className="text-lg font-semibold tabular-nums">
                {summary.reward_count}
              </p>
            </div>
            <div className="rounded-lg border border-border bg-muted/20 px-3 py-2">
              <p className="text-xs text-muted-foreground">Liquid stake / unstake</p>
              <p className="text-lg font-semibold tabular-nums">
                {summary.liquid_stake_count} / {summary.liquid_unstake_count}
              </p>
            </div>
            <div className="rounded-lg border border-border bg-muted/20 px-3 py-2">
              <p className="text-xs text-muted-foreground">Net LST held</p>
              <p className="text-lg font-semibold tabular-nums">
                {summary.positions.length
                  ? formatNumber(summary.total_staked_lst)
                  : "—"}
              </p>
            </div>
          </div>

          {summary.positions.length > 0 ? (
            <div className="space-y-2">
              <p className="text-sm font-medium">Open liquid staking</p>
              <div className="overflow-auto rounded-md border">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Token</TableHead>
                      <TableHead className="text-right">Net amount</TableHead>
                      <TableHead className="text-right">Income earned</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {summary.positions.map((position) => (
                      <TableRow key={position.asset}>
                        <TableCell>
                          <AssetBadge
                            asset={position.asset}
                            labels={assetLabels}
                          />
                        </TableCell>
                        <TableCell className="text-right tabular-nums">
                          {formatNumber(position.net_amount)}
                        </TableCell>
                        <TableCell className="text-right tabular-nums">
                          {position.total_income > 0
                            ? fmt(position.total_income)
                            : "—"}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            </div>
          ) : null}

          {Object.keys(summary.income_by_asset).length > 0 ? (
            <div className="space-y-2">
              <p className="text-sm font-medium">Income by asset</p>
              <div className="flex flex-wrap gap-2">
                {Object.entries(summary.income_by_asset)
                  .sort(([, a], [, b]) => b - a)
                  .map(([asset, value]) => (
                    <div
                      key={asset}
                      className="rounded-md border border-border bg-muted/10 px-3 py-1.5 text-sm"
                    >
                      <AssetBadge asset={asset} labels={assetLabels} className="mr-2" />
                      <span className="tabular-nums font-medium">
                        {fmt(value)}
                      </span>
                    </div>
                  ))}
              </div>
            </div>
          ) : null}

          {summary.hidden_dust_count > 0 ? (
            <p className="text-xs text-muted-foreground">
              Hiding {summary.hidden_dust_count} negligible exchange dust rewards
              (e.g. Kraken 0.0000000001 SOL placeholders with no value). Run{" "}
              <strong>Reprice wallets</strong> to fill USD values on meaningful
              Binance/Kraken micro-rewards.
            </p>
          ) : null}

          {summary.events.length > 0 ? (
            <div className="max-h-[420px] overflow-auto rounded-md border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Date</TableHead>
                    <TableHead>Type</TableHead>
                    <TableHead>Amounts</TableHead>
                    <TableHead className="text-right">Income</TableHead>
                    <TableHead>Reward</TableHead>
                    <TableHead>Source</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {summary.events.map((event) => (
                    <TableRow key={event.id}>
                      <TableCell className="whitespace-nowrap text-xs text-muted-foreground">
                        {formatDateTime(event.timestamp)}
                      </TableCell>
                      <TableCell>
                        <Badge variant="outline" className="text-[10px] uppercase">
                          {stakingEventLabel(event.kind)}
                        </Badge>
                      </TableCell>
                      <TableCell className="max-w-[240px] text-sm">
                        {eventAmounts(event)}
                        {event.counterparty ? (
                          <span className="mt-0.5 block truncate font-mono text-[11px] text-muted-foreground">
                            {shortenAddress(event.counterparty)}
                          </span>
                        ) : null}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {eventIncome(event, currency)}
                      </TableCell>
                      <TableCell className="text-sm tabular-nums">
                        {event.reward_amount != null && event.reward_asset ? (
                          <>
                            +{formatCryptoAmount(event.reward_amount)}{" "}
                            <span className="text-muted-foreground">
                              {event.reward_asset}
                            </span>
                          </>
                        ) : (
                          "—"
                        )}
                      </TableCell>
                      <TableCell>
                        <SourceBadge source={event.source} />
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          ) : (
            <p className="py-4 text-center text-sm text-muted-foreground">
              No staking events match the selected sources.
            </p>
          )}
        </CardContent>
      ) : null}
    </Card>
  );
}
