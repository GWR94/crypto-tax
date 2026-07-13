import { useState } from "react";
import { Loader2, Mail } from "lucide-react";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import type { MexcEmailImportResult, Transaction } from "@/lib/types";

interface MexcEmailImportProps {
  disabled?: boolean;
  onImported: (message: string) => void;
  onError: (message: string | null) => void;
}

function formatTxSummary(tx: Transaction): string {
  const parts = [
    tx.timestamp.slice(0, 10),
    tx.transaction_type,
    tx.transfer_direction ?? "",
    `${tx.amount} ${tx.asset}`,
  ].filter(Boolean);
  if (tx.fiat_value_at_trigger > 0) {
    parts.push(`@ ${tx.fiat_currency ?? "GBP"} ${tx.fiat_value_at_trigger}`);
  }
  return parts.join(" · ");
}

export function MexcEmailImport({
  disabled = false,
  onImported,
  onError,
}: MexcEmailImportProps) {
  const [text, setText] = useState("");
  const [preview, setPreview] = useState<MexcEmailImportResult | null>(null);
  const [busy, setBusy] = useState<"preview" | "import" | null>(null);

  async function run(commit: boolean) {
    if (!text.trim()) {
      onError("Paste at least one MEXC email first.");
      return;
    }
    setBusy(commit ? "import" : "preview");
    onError(null);
    try {
      const result = await api.importMexcEmails(text, commit);
      if (commit) {
        onImported(result.message ?? `Imported ${result.imported} row(s).`);
        setText("");
        setPreview({
          ...result,
          transactions: result.imported > 0 ? result.transactions : [],
        });
      } else {
        setPreview(result);
      }
    } catch (err) {
      onError(err instanceof Error ? err.message : "MEXC import failed.");
    } finally {
      setBusy(null);
    }
  }

  function downloadCsv() {
    if (!preview?.csv) return;
    const blob = new Blob([preview.csv], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "mexc-emails.csv";
    link.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="relative space-y-3 rounded-lg border border-dashed border-border bg-muted/20 p-4">
      {busy === "import" ? (
        <div
          className="absolute inset-0 z-10 flex items-center justify-center rounded-lg bg-background/70 backdrop-blur-[1px]"
          aria-live="polite"
          aria-busy="true"
        >
          <div className="flex items-center gap-2 rounded-md border border-border bg-background px-4 py-3 text-sm shadow-sm">
            <Loader2 className="h-4 w-4 animate-spin text-primary" />
            <span>Importing to ledger…</span>
          </div>
        </div>
      ) : null}
      <div className="flex items-center gap-2">
        <Mail className="h-4 w-4 text-muted-foreground" />
        <p className="text-sm font-medium">MEXC email paste</p>
      </div>
      <p className="text-xs text-muted-foreground">
        Paste fiat deposit and withdrawal notification emails (multiple at once is
        fine). Deposits become BUY rows; withdrawals become TRANSFER OUT to your
        wallet. Futures SL/TP emails are detected but not imported — they only
        show an exit, not entry price or realized PnL.
      </p>
      <textarea
        value={text}
        disabled={disabled || busy !== null}
        onChange={(e) => setText(e.target.value)}
        placeholder={`Payment ID: …\nDeposit Fiat Amount: 1000 GBP\nReceived Crypto: …\n\nWithdrawal Amount:\n843.34 CROWN\nTxID: …`}
        className="min-h-[140px] w-full rounded-md border border-input bg-background px-3 py-2 font-mono text-xs"
      />
      <div className="flex flex-wrap gap-2">
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={disabled || busy !== null || !text.trim()}
          onClick={() => void run(false)}
        >
          {busy === "preview" ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : null}
          {busy === "preview" ? "Previewing…" : "Preview"}
        </Button>
        <Button
          type="button"
          size="sm"
          disabled={disabled || busy !== null || !text.trim()}
          onClick={() => void run(true)}
        >
          {busy === "import" ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : null}
          {busy === "import" ? "Importing…" : "Import to ledger"}
        </Button>
        {preview?.csv ? (
          <Button type="button" variant="ghost" size="sm" onClick={downloadCsv}>
            Download CSV
          </Button>
        ) : null}
      </div>
      {preview?.message ? (
        <p
          className={
            preview.imported > 0
              ? "text-xs text-green-700 dark:text-green-400"
              : "text-xs text-muted-foreground"
          }
        >
          {preview.message}
        </p>
      ) : null}
      {preview?.warnings.length ? (
        <ul className="space-y-1 text-xs text-amber-700 dark:text-amber-400">
          {preview.warnings.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      ) : null}
      {preview?.skipped_blocks.length ? (
        <div className="text-xs text-muted-foreground">
          <p className="font-medium">Unrecognized blocks</p>
          <ul className="list-disc pl-4">
            {preview.skipped_blocks.map((block) => (
              <li key={block}>{block}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {preview?.transactions.length ? (
        <ul className="space-y-1 text-xs">
          {preview.transactions.map((tx) => (
            <li key={tx.id} className="rounded bg-background/80 px-2 py-1">
              {formatTxSummary(tx)}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
