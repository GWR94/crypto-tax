import type { ReactNode } from "react";
import { ExternalLink } from "lucide-react";
import {
  explorerAddressUrl,
  explorerTokenUrl,
  explorerTxUrl,
  resolveOnChainTxId,
} from "@/lib/explorerLinks";
import { getSourceDefinition } from "@/lib/sourceCatalog";
import { formatInstrument } from "@/lib/instruments";
import type { AssetLabel, ImportSource, Transaction } from "@/lib/types";
import { formatMoney, formatNumber, shortenAddress } from "@/lib/utils";

function denomination(tx: Transaction): string {
  return tx.fiat_currency ?? (tx.source === "kraken" ? "GBP" : "USD");
}

function unitPrice(tx: Transaction): number | null {
  if (tx.amount <= 0 || tx.fiat_value_at_trigger <= 0) return null;
  return tx.fiat_value_at_trigger / tx.amount;
}

function formatUnitPrice(price: number, currency: string): string {
  const abs = Math.abs(price);
  const digits =
    abs >= 100 ? 2 : abs >= 1 ? 4 : abs >= 0.01 ? 6 : 8;
  return formatMoney(price, currency, {
    minimumFractionDigits: 2,
    maximumFractionDigits: digits,
  });
}

function DetailLink({ href, label }: { href: string; label: string }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="inline-flex items-center gap-1 text-primary hover:underline"
      onClick={(e) => e.stopPropagation()}
    >
      {label}
      <ExternalLink className="h-3 w-3 shrink-0 opacity-70" />
    </a>
  );
}

function DetailRow({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  if (!children) return null;
  return (
    <div className="grid gap-1 sm:grid-cols-[7rem_1fr] sm:gap-3">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0 break-all font-mono text-xs">{children}</dd>
    </div>
  );
}

function importLabel(
  importId: string | null | undefined,
  sources: ImportSource[]
): string | null {
  if (!importId) return null;
  const match = sources.find((s) => s.id === importId);
  return match?.label ?? importId;
}

function walletForImport(
  importId: string | null | undefined,
  sources: ImportSource[]
): { address: string; chain: string | null } | null {
  if (!importId) return null;
  const match = sources.find((s) => s.id === importId);
  if (!match?.address) return null;
  return { address: match.address, chain: match.chain ?? null };
}

export function TransactionDetails({
  tx,
  assetLabels,
  importSources = [],
}: {
  tx: Transaction;
  assetLabels: Record<string, AssetLabel>;
  importSources?: ImportSource[];
}) {
  const txId = resolveOnChainTxId(tx);
  const txUrl = explorerTxUrl(tx.source, txId);
  const wallet = walletForImport(tx.import_id, importSources);
  const walletUrl = wallet
    ? explorerAddressUrl(wallet.chain ?? tx.source, wallet.address)
    : null;
  const counterpartyUrl = explorerAddressUrl(tx.source, tx.counterparty_address);
  const mint = tx.token_mint ?? assetLabels[tx.asset]?.mint;
  const tokenUrl = explorerTokenUrl(tx.source, mint);
  const sourceLabel = getSourceDefinition(tx.source).label;
  const imp = importLabel(tx.import_id, importSources);
  const denom = denomination(tx);
  const assetSymbol = assetLabels[tx.asset]?.symbol ?? tx.asset;
  const price = unitPrice(tx);

  return (
    <dl className="space-y-2 py-2 text-sm">
      {(txUrl || walletUrl || counterpartyUrl || tokenUrl) && (
        <div className="space-y-1.5 border-b border-border pb-2">
          <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Links
          </div>
          <div className="flex flex-wrap gap-x-4 gap-y-1">
            {txUrl ? <DetailLink href={txUrl} label="View transaction" /> : null}
            {walletUrl ? (
              <DetailLink
                href={walletUrl}
                label={`Your wallet (${shortenAddress(wallet!.address)})`}
              />
            ) : null}
            {counterpartyUrl ? (
              <DetailLink
                href={counterpartyUrl}
                label={`Counterparty (${shortenAddress(tx.counterparty_address!)})`}
              />
            ) : null}
            {tokenUrl ? <DetailLink href={tokenUrl} label="Token contract" /> : null}
          </div>
        </div>
      )}

      <DetailRow label="Source">{sourceLabel}</DetailRow>
      {imp ? <DetailRow label="Import">{imp}</DetailRow> : null}
      {tx.transfer_direction ? (
        <DetailRow label="Direction">{tx.transfer_direction}</DetailRow>
      ) : null}
      {price != null ? (
        <DetailRow label="Price at event">
          {formatUnitPrice(price, denom)} / {assetSymbol}
        </DetailRow>
      ) : null}
      {tx.fiat_value_at_trigger > 0 ? (
        <DetailRow label="Total value">
          {formatMoney(tx.fiat_value_at_trigger, denom)}
        </DetailRow>
      ) : null}
      {tx.fee_fiat > 0 ? (
        <DetailRow label="Fee">{formatMoney(tx.fee_fiat, denom)}</DetailRow>
      ) : null}
      <DetailRow label="Amount">
        {formatNumber(tx.amount)} {assetSymbol}
      </DetailRow>
      {tx.counter_asset ? (
        <DetailRow label="Counter asset">
          {assetLabels[tx.counter_asset]?.symbol ?? tx.counter_asset}
        </DetailRow>
      ) : null}
      {tx.instrument ? (
        <DetailRow label="Contract">
          {formatInstrument(tx.instrument, {
            asset: tx.asset,
            counter_asset: tx.counter_asset,
          })}
        </DetailRow>
      ) : null}
      {tx.venue_order_type ? (
        <DetailRow label="Order type">{tx.venue_order_type}</DetailRow>
      ) : null}
      {tx.realized_pnl != null ? (
        <DetailRow label="Realized PnL">
          {formatMoney(tx.realized_pnl, denom)}
        </DetailRow>
      ) : null}
      {txId ? <DetailRow label="On-chain id">{txId}</DetailRow> : null}
      {tx.trade_group_id && tx.trade_group_id !== txId ? (
        <DetailRow label="Group id">{tx.trade_group_id}</DetailRow>
      ) : null}
    </dl>
  );
}
