import { useMemo, useState } from "react";
import { AlertTriangle, ChevronDown, ChevronRight, HeartPulse } from "lucide-react";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { AssetBadge } from "@/components/AssetBadge";
import { SourceBadge } from "@/components/SourceBadge";
import { api } from "@/lib/api";
import type {
  AssetLabel,
  DataHealthSummary,
  LpInferenceFlag,
  ManualCostBasisOverride,
  OrphanedInflowFlag,
} from "@/lib/types";
import { formatDateTime, formatMoney, formatNumber } from "@/lib/utils";

function toDatetimeLocalValue(iso: string): string {
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function fromDatetimeLocalValue(value: string): string {
  return new Date(value).toISOString();
}

function OverrideForm({
  orphan,
  existing,
  currency,
  onSaved,
  onError,
}: {
  orphan: OrphanedInflowFlag;
  existing?: ManualCostBasisOverride;
  currency: string;
  onSaved: () => void;
  onError: (message: string) => void;
}) {
  const [acquisitionDate, setAcquisitionDate] = useState(
    existing
      ? toDatetimeLocalValue(existing.acquisition_date)
      : toDatetimeLocalValue(orphan.timestamp)
  );
  const [unitCost, setUnitCost] = useState(
    existing ? String(existing.unit_cost) : ""
  );
  const [totalSpent, setTotalSpent] = useState(
    existing ? String(existing.total_fiat_spent) : ""
  );
  const [notes, setNotes] = useState(existing?.notes ?? "");
  const [busy, setBusy] = useState(false);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      const unit = unitCost.trim() ? Number(unitCost) : undefined;
      const total = totalSpent.trim() ? Number(totalSpent) : undefined;
      if (unit === undefined && total === undefined) {
        onError("Enter a unit price or total amount spent.");
        return;
      }
      await api.upsertCostBasisOverride(orphan.transaction_id, {
        acquisition_date: fromDatetimeLocalValue(acquisitionDate),
        unit_cost: unit,
        total_fiat_spent: total,
        notes: notes.trim() || undefined,
      });
      onSaved();
    } catch (e) {
      onError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete() {
    if (!existing) return;
    setBusy(true);
    try {
      await api.deleteCostBasisOverride(orphan.transaction_id);
      onSaved();
    } catch (e) {
      onError(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      onSubmit={(e) => void handleSubmit(e)}
      className="mt-3 space-y-3 rounded-md border border-border bg-muted/30 p-3"
      onClick={(e) => e.stopPropagation()}
    >
      <p className="text-xs text-muted-foreground">
        Supply the original purchase details for this batch. Tax calculations
        will use this as the acquisition cost pool for subsequent disposals.
      </p>
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="block text-sm">
          <span className="mb-1 block text-xs font-medium text-muted-foreground">
            Acquisition date
          </span>
          <input
            type="datetime-local"
            value={acquisitionDate}
            onChange={(e) => setAcquisitionDate(e.target.value)}
            className="w-full rounded-md border border-input bg-background px-2 py-1.5 text-sm"
            required
          />
        </label>
        <label className="block text-sm">
          <span className="mb-1 block text-xs font-medium text-muted-foreground">
            Quantity (from import)
          </span>
          <input
            type="text"
            readOnly
            value={`${formatNumber(orphan.quantity)} ${orphan.asset}`}
            className="w-full rounded-md border border-input bg-muted px-2 py-1.5 text-sm text-muted-foreground"
          />
        </label>
        <label className="block text-sm">
          <span className="mb-1 block text-xs font-medium text-muted-foreground">
            Price per unit ({currency})
          </span>
          <input
            type="number"
            min="0"
            step="any"
            value={unitCost}
            onChange={(e) => setUnitCost(e.target.value)}
            placeholder="e.g. 1500"
            className="w-full rounded-md border border-input bg-background px-2 py-1.5 text-sm"
          />
        </label>
        <label className="block text-sm">
          <span className="mb-1 block text-xs font-medium text-muted-foreground">
            Original fiat spent ({currency})
          </span>
          <input
            type="number"
            min="0"
            step="any"
            value={totalSpent}
            onChange={(e) => setTotalSpent(e.target.value)}
            placeholder="e.g. 3000"
            className="w-full rounded-md border border-input bg-background px-2 py-1.5 text-sm"
          />
        </label>
      </div>
      <label className="block text-sm">
        <span className="mb-1 block text-xs font-medium text-muted-foreground">
          Notes (optional)
        </span>
        <input
          type="text"
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          placeholder="e.g. MEXC purge — bought Jan 2023 per bank statement"
          className="w-full rounded-md border border-input bg-background px-2 py-1.5 text-sm"
        />
      </label>
      <div className="flex flex-wrap gap-2">
        <Button type="submit" size="sm" disabled={busy}>
          {existing ? "Update override" : "Save cost basis"}
        </Button>
        {existing ? (
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={busy}
            onClick={() => void handleDelete()}
          >
            Remove override
          </Button>
        ) : null}
      </div>
    </form>
  );
}

export function DataHealthPanel({
  dataHealth,
  currency,
  assetLabels = {},
  onUpdated,
  onError,
}: {
  dataHealth: DataHealthSummary | null;
  currency: string;
  assetLabels?: Record<string, AssetLabel>;
  onUpdated: () => void;
  onError: (message: string) => void;
}) {
  const [collapsed, setCollapsed] = useState(false);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const overridesByAnchor = useMemo(() => {
    const map = new Map<string, ManualCostBasisOverride>();
    for (const o of dataHealth?.cost_basis_overrides ?? []) {
      map.set(o.anchor_transaction_id, o);
    }
    return map;
  }, [dataHealth]);

  const orphans = dataHealth?.orphaned_inflows ?? [];
  const savedOverrides = dataHealth?.cost_basis_overrides ?? [];
  const lpNotes = dataHealth?.lp_inference_notes ?? [];
  const issueCount = orphans.length + lpNotes.length;

  if (!issueCount && !savedOverrides.length) return null;

  return (
    <Card>
      <CardHeader
        className="flex cursor-pointer flex-col gap-3 space-y-0 sm:flex-row sm:items-center sm:justify-between"
        onClick={() => setCollapsed((v) => !v)}
        role="button"
        tabIndex={0}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
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
          <HeartPulse className="h-4 w-4 text-primary" />
          <CardTitle className="text-base">Data Health Ledger</CardTitle>
          {issueCount > 0 ? (
            <Badge variant="destructive">{issueCount}</Badge>
          ) : (
            <Badge variant="muted">OK</Badge>
          )}
        </div>
        <p className="text-sm text-muted-foreground">
          Orphaned inflows missing historical cost basis — common after exchange
          data purges.
        </p>
      </CardHeader>

      {!collapsed ? (
        <CardContent className="space-y-4">
          {issueCount > 0 ? (
            <div className="space-y-2">
              {orphans.map((orphan) => {
                const open = expandedId === orphan.transaction_id;
                const existing = overridesByAnchor.get(orphan.transaction_id);
                return (
                  <div
                    key={orphan.transaction_id}
                    className="rounded-md border border-destructive/30 bg-destructive/5 p-3"
                  >
                    <button
                      type="button"
                      className="flex w-full flex-wrap items-start justify-between gap-2 text-left"
                      onClick={() =>
                        setExpandedId(open ? null : orphan.transaction_id)
                      }
                    >
                      <div className="space-y-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <AlertTriangle className="h-4 w-4 text-destructive" />
                          <Badge variant="destructive" className="text-[10px] uppercase">
                            Missing Historical Cost Basis
                          </Badge>
                          <AssetBadge asset={orphan.asset} labels={assetLabels} />
                          <span className="text-sm font-medium">
                            {formatNumber(orphan.quantity)} {orphan.asset}
                          </span>
                        </div>
                        <p className="text-xs text-muted-foreground">
                          {orphan.message}
                        </p>
                        <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                          {orphan.source ? (
                            <SourceBadge source={orphan.source} />
                          ) : null}
                          <span>{formatDateTime(orphan.timestamp)}</span>
                        </div>
                      </div>
                      <span className="text-xs text-primary">
                        {open ? "Hide form" : "Add cost basis"}
                      </span>
                    </button>
                    {open ? (
                      <OverrideForm
                        orphan={orphan}
                        existing={existing}
                        currency={currency}
                        onSaved={onUpdated}
                        onError={onError}
                      />
                    ) : null}
                  </div>
                );
              })}
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">
              No orphaned inflows detected. Saved manual overrides remain below.
            </p>
          )}

          {lpNotes.length > 0 ? (
            <div>
              <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                Inferred LP disposals
              </p>
              <ul className="space-y-2">
                {lpNotes.map((note: LpInferenceFlag) => (
                  <li
                    key={note.transaction_id}
                    className="rounded-md border border-amber-500/30 bg-amber-500/5 p-3"
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <AlertTriangle className="h-4 w-4 text-amber-500" />
                      <Badge
                        variant={note.ambiguous ? "destructive" : "muted"}
                        className="text-[10px] uppercase"
                      >
                        {note.ambiguous ? "Verify LP match" : "Inferred LP burn"}
                      </Badge>
                      <AssetBadge asset={note.asset} labels={assetLabels} />
                      <span className="text-sm font-medium">
                        {formatMoney(note.proceeds, currency)} proceeds
                      </span>
                      <span className="text-xs text-muted-foreground">
                        {formatDateTime(note.timestamp)}
                      </span>
                    </div>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {note.message}
                    </p>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          {savedOverrides.length > 0 ? (
            <div>
              <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                Saved manual overrides
              </p>
              <ul className="space-y-2">
                {savedOverrides.map((o) => (
                  <li
                    key={o.anchor_transaction_id}
                    className="flex flex-wrap items-center justify-between gap-2 rounded-md border bg-muted/20 px-3 py-2 text-sm"
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <AssetBadge asset={o.asset} labels={assetLabels} />
                      <span>
                        {formatNumber(o.quantity)} @{" "}
                        {formatMoney(o.unit_cost, currency)}/unit
                      </span>
                      <span className="text-muted-foreground">
                        acquired {formatDateTime(o.acquisition_date)}
                      </span>
                    </div>
                    <span className="text-muted-foreground">
                      {formatMoney(o.total_fiat_spent, currency)} total
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </CardContent>
      ) : null}
    </Card>
  );
}
