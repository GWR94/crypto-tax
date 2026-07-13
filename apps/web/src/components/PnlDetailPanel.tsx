import { Fragment, useMemo, useState } from "react";
import { Info } from "lucide-react";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { TransactionDetails } from "@/components/TransactionDetails";
import { formatDateTime, formatMoney, formatNumber, isDustTransaction } from "@/lib/utils";
import { viaLabel } from "@/lib/viaLabel";
import { SourceBadge } from "@/components/SourceBadge";
import { PnlAmountCell } from "@/components/PnlAmountCell";
import type {
  AssetLabel,
  AssetPnlDetail,
  DisplayCurrency,
  ImportSource,
  PnlOpenLotLine,
  PnlRealizedDisposalLine,
  TaxJurisdiction,
  Transaction,
} from "@/lib/types";

const ACQUISITION_TYPES = new Set(["BUY", "AIRDROP", "STAKING", "TRANSFER"]);

function lookupTx(
  transactions: Transaction[],
  id: string
): Transaction | undefined {
  if (id.startsWith("section-104:")) return undefined;
  return transactions.find((t) => t.id === id);
}

function useExpandedDetails() {
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  return {
    isOpen: (id: string) => expanded.has(id),
    toggle: (id: string) =>
      setExpanded((prev) => {
        const next = new Set(prev);
        if (next.has(id)) next.delete(id);
        else next.add(id);
        return next;
      }),
  };
}

function DetailToggle({
  txId,
  open,
  onToggle,
}: {
  txId: string | undefined;
  open: boolean;
  onToggle: () => void;
}) {
  if (!txId) {
    return <TableCell className="w-8 p-1" />;
  }
  return (
    <TableCell className="w-8 p-1">
      <button
        type="button"
        className={`rounded p-1 hover:bg-muted ${open ? "text-primary" : "text-muted-foreground"}`}
        title="View transaction"
        aria-expanded={open}
        aria-label="View transaction"
        onClick={(e) => {
          e.stopPropagation();
          onToggle();
        }}
      >
        <Info className="h-4 w-4" />
      </button>
    </TableCell>
  );
}

function DetailExpansion({
  tx,
  colSpan,
  assetLabels,
  importSources,
}: {
  tx: Transaction | undefined;
  colSpan: number;
  assetLabels: Record<string, AssetLabel>;
  importSources: ImportSource[];
}) {
  if (!tx) return null;
  return (
    <TableRow className="bg-muted/20 hover:bg-muted/20">
      <TableCell />
      <TableCell colSpan={colSpan} className="px-4 py-0">
        <TransactionDetails
          tx={tx}
          assetLabels={assetLabels}
          importSources={importSources}
        />
      </TableCell>
    </TableRow>
  );
}

function RealizedDisposalRows({
  lines,
  transactions,
  currency,
  assetLabels,
  importSources,
  hideStaking = false,
}: {
  lines: PnlRealizedDisposalLine[];
  transactions: Transaction[];
  currency: DisplayCurrency;
  assetLabels: Record<string, AssetLabel>;
  importSources: ImportSource[];
  hideStaking?: boolean;
}) {
  const fmt = (value: number) => formatMoney(value, currency);
  const { isOpen, toggle } = useExpandedDetails();
  const visibleLines = hideStaking
    ? lines.filter((line) => {
        const tx = lookupTx(transactions, line.transaction_id);
        return tx?.transaction_type !== "STAKING";
      })
    : lines;

  if (visibleLines.length === 0) {
    return (
      <p className="py-3 text-sm text-muted-foreground">
        No disposal trades for this asset.
      </p>
    );
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead className="w-8" />
          <TableHead>Date</TableHead>
          <TableHead>Type</TableHead>
          <TableHead>Via</TableHead>
          <TableHead className="text-right">Qty</TableHead>
          <TableHead className="text-right">Proceeds</TableHead>
          <TableHead className="text-right">Cost</TableHead>
          <TableHead className="text-right">P&amp;L</TableHead>
          <TableHead>Source</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {visibleLines.map((line) => {
          const tx = lookupTx(transactions, line.transaction_id);
          const detailsOpen = isOpen(line.transaction_id);
          return (
            <Fragment key={`${line.transaction_id}-${line.disposed_at}`}>
              <TableRow>
                <DetailToggle
                  txId={tx?.id}
                  open={detailsOpen}
                  onToggle={() => toggle(line.transaction_id)}
                />
                <TableCell className="whitespace-nowrap text-muted-foreground">
                  {formatDateTime(line.disposed_at)}
                </TableCell>
                <TableCell>
                  <Badge variant="destructive">
                    {tx?.transaction_type ?? "SELL"}
                  </Badge>
                </TableCell>
                <TableCell className="text-muted-foreground">
                  {tx ? viaLabel(tx, assetLabels) : "—"}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {formatNumber(line.quantity)}
                </TableCell>
                <TableCell className="text-right tabular-nums text-muted-foreground">
                  {fmt(line.proceeds)}
                </TableCell>
                <TableCell className="text-right tabular-nums text-muted-foreground">
                  {fmt(line.cost_basis)}
                </TableCell>
                <TableCell className="text-right">
                  <PnlAmountCell value={line.gain_loss} currency={currency} />
                </TableCell>
                <TableCell>
                  {tx ? (
                    <SourceBadge
                      source={tx.source}
                      importId={tx.import_id}
                      importSources={importSources}
                    />
                  ) : (
                    "—"
                  )}
                </TableCell>
              </TableRow>
              {detailsOpen ? (
                <DetailExpansion
                  tx={tx}
                  colSpan={9}
                  assetLabels={assetLabels}
                  importSources={importSources}
                />
              ) : null}
            </Fragment>
          );
        })}
      </TableBody>
    </Table>
  );
}

function AcquisitionTxTable({
  txs,
  currency,
  assetLabels,
  importSources,
}: {
  txs: Transaction[];
  currency: DisplayCurrency;
  assetLabels: Record<string, AssetLabel>;
  importSources: ImportSource[];
}) {
  const { isOpen, toggle } = useExpandedDetails();
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead className="w-8" />
          <TableHead>Date</TableHead>
          <TableHead>Type</TableHead>
          <TableHead className="text-right">Qty</TableHead>
          <TableHead className="text-right">Cost</TableHead>
          <TableHead>Source</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {txs.map((tx) => {
          const detailsOpen = isOpen(tx.id);
          return (
            <Fragment key={tx.id}>
              <TableRow>
                <DetailToggle
                  txId={tx.id}
                  open={detailsOpen}
                  onToggle={() => toggle(tx.id)}
                />
                <TableCell className="whitespace-nowrap text-muted-foreground">
                  {formatDateTime(tx.timestamp)}
                </TableCell>
                <TableCell>
                  <Badge variant="outline">{tx.transaction_type}</Badge>
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {formatNumber(tx.amount)}
                </TableCell>
                <TableCell className="text-right tabular-nums text-muted-foreground">
                  {tx.fiat_value_at_trigger > 0
                    ? formatMoney(tx.fiat_value_at_trigger, currency)
                    : "—"}
                </TableCell>
                <TableCell>
                  <SourceBadge
                    source={tx.source}
                    importId={tx.import_id}
                    importSources={importSources}
                  />
                </TableCell>
              </TableRow>
              {detailsOpen ? (
                <DetailExpansion
                  tx={tx}
                  colSpan={5}
                  assetLabels={assetLabels}
                  importSources={importSources}
                />
              ) : null}
            </Fragment>
          );
        })}
      </TableBody>
    </Table>
  );
}

function UnrealizedLotRows({
  lines,
  transactions,
  asset,
  currency,
  assetLabels,
  importSources,
  jurisdiction,
  hideStaking = false,
}: {
  lines: PnlOpenLotLine[];
  transactions: Transaction[];
  asset: string;
  currency: DisplayCurrency;
  assetLabels: Record<string, AssetLabel>;
  importSources: ImportSource[];
  jurisdiction: TaxJurisdiction;
  hideStaking?: boolean;
}) {
  const fmt = (value: number) => formatMoney(value, currency);
  const { isOpen, toggle } = useExpandedDetails();
  const pooled = lines.some((line) => line.is_pooled);
  const acquisitionTxs = useMemo(
    () =>
      transactions
        .filter(
          (t) =>
            t.asset === asset &&
            ACQUISITION_TYPES.has(t.transaction_type) &&
            (t.transaction_type !== "TRANSFER" || t.transfer_direction === "IN") &&
            (!hideStaking || t.transaction_type !== "STAKING") &&
            !isDustTransaction(t)
        )
        .sort(
          (a, b) =>
            new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime()
        ),
    [transactions, asset, hideStaking]
  );

  if (pooled && jurisdiction === "UK") {
    const pool = lines[0];
    return (
      <div className="space-y-3">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Lot</TableHead>
              <TableHead className="text-right">Qty</TableHead>
              <TableHead className="text-right">Cost</TableHead>
              <TableHead className="text-right">Value</TableHead>
              <TableHead className="text-right">P&amp;L</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            <TableRow>
              <TableCell className="text-muted-foreground">
                Section 104 pool (average cost)
              </TableCell>
              <TableCell className="text-right tabular-nums">
                {formatNumber(pool.quantity)}
              </TableCell>
              <TableCell className="text-right tabular-nums text-muted-foreground">
                {fmt(pool.cost_basis)}
              </TableCell>
              <TableCell className="text-right tabular-nums">
                {fmt(pool.current_value)}
              </TableCell>
              <TableCell className="text-right">
                <PnlAmountCell value={pool.unrealized_pnl} currency={currency} />
              </TableCell>
            </TableRow>
          </TableBody>
        </Table>
        {acquisitionTxs.length > 0 ? (
          <div>
            <p className="mb-2 text-xs font-medium text-muted-foreground">
              Acquisition history (pooled under HMRC rules)
            </p>
            <AcquisitionTxTable
              txs={acquisitionTxs}
              currency={currency}
              assetLabels={assetLabels}
              importSources={importSources}
            />
          </div>
        ) : null}
      </div>
    );
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead className="w-8" />
          <TableHead>Acquired</TableHead>
          <TableHead>Type</TableHead>
          <TableHead>Via</TableHead>
          <TableHead className="text-right">Qty</TableHead>
          <TableHead className="text-right">Cost</TableHead>
          <TableHead className="text-right">Value</TableHead>
          <TableHead className="text-right">P&amp;L</TableHead>
          <TableHead>Source</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {lines
          .filter((line) => {
            if (line.quantity < 1e-6) return false;
            if (!hideStaking) return true;
            const tx = lookupTx(transactions, line.transaction_id);
            return tx?.transaction_type !== "STAKING";
          })
          .map((line) => {
          const tx = lookupTx(transactions, line.transaction_id);
          const detailsOpen = isOpen(line.transaction_id);
          return (
            <Fragment key={line.transaction_id}>
              <TableRow>
                <DetailToggle
                  txId={tx?.id}
                  open={detailsOpen}
                  onToggle={() => toggle(line.transaction_id)}
                />
                <TableCell className="whitespace-nowrap text-muted-foreground">
                  {formatDateTime(line.acquired_at)}
                </TableCell>
                <TableCell>
                  <Badge variant="default">{tx?.transaction_type ?? "BUY"}</Badge>
                </TableCell>
                <TableCell className="text-muted-foreground">
                  {tx ? viaLabel(tx, assetLabels) : "—"}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {formatNumber(line.quantity)}
                </TableCell>
                <TableCell className="text-right tabular-nums text-muted-foreground">
                  {fmt(line.cost_basis)}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {fmt(line.current_value)}
                </TableCell>
                <TableCell className="text-right">
                  <PnlAmountCell value={line.unrealized_pnl} currency={currency} />
                </TableCell>
                <TableCell>
                  {tx ? (
                    <SourceBadge
                      source={tx.source}
                      importId={tx.import_id}
                      importSources={importSources}
                    />
                  ) : (
                    "—"
                  )}
                </TableCell>
              </TableRow>
              {detailsOpen ? (
                <DetailExpansion
                  tx={tx}
                  colSpan={9}
                  assetLabels={assetLabels}
                  importSources={importSources}
                />
              ) : null}
            </Fragment>
          );
        })}
      </TableBody>
    </Table>
  );
}

export function PnlRealizedDetailPanel({
  detail,
  transactions,
  currency,
  assetLabels,
  importSources,
  hideStaking = false,
}: {
  detail: AssetPnlDetail | undefined;
  transactions: Transaction[];
  currency: DisplayCurrency;
  assetLabels: Record<string, AssetLabel>;
  importSources: ImportSource[];
  hideStaking?: boolean;
}) {
  if (!detail?.disposals.length) {
    return (
      <p className="py-3 text-sm text-muted-foreground">
        No disposal trades for this asset.
      </p>
    );
  }
  return (
    <RealizedDisposalRows
      lines={detail.disposals}
      transactions={transactions}
      currency={currency}
      assetLabels={assetLabels}
      importSources={importSources}
      hideStaking={hideStaking}
    />
  );
}

export function PnlUnrealizedDetailPanel({
  asset,
  detail,
  transactions,
  currency,
  assetLabels,
  importSources,
  jurisdiction,
  hideStaking = false,
}: {
  asset: string;
  detail: AssetPnlDetail | undefined;
  transactions: Transaction[];
  currency: DisplayCurrency;
  assetLabels: Record<string, AssetLabel>;
  importSources: ImportSource[];
  jurisdiction: TaxJurisdiction;
  hideStaking?: boolean;
}) {
  if (!detail?.open_lots.length) {
    return (
      <p className="py-3 text-sm text-muted-foreground">
        No open lots for this asset.
      </p>
    );
  }
  return (
    <UnrealizedLotRows
      lines={detail.open_lots}
      transactions={transactions}
      asset={asset}
      currency={currency}
      assetLabels={assetLabels}
      importSources={importSources}
      jurisdiction={jurisdiction}
      hideStaking={hideStaking}
    />
  );
}
