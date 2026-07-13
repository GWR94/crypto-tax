import { useEffect, useState } from "react";
import { ChevronDown, ChevronRight, Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import type { ImportFilePreview, ImportSnippet } from "@/lib/types";

type InlinePreview = Pick<
  ImportFilePreview,
  | "csv_columns"
  | "csv_sample_rows"
  | "csv_total_rows"
  | "csv_total_columns"
  | "csv_truncated_columns"
>;

interface ImportPreviewSnippetProps {
  importId?: string;
  inlinePreview?: InlinePreview;
  buttonLabel?: string;
}

function inlineToSnippet(preview: InlinePreview): ImportSnippet {
  return {
    columns: preview.csv_columns ?? [],
    rows: preview.csv_sample_rows ?? [],
    total_rows: preview.csv_total_rows ?? 0,
    total_columns: preview.csv_total_columns ?? 0,
    truncated_columns: preview.csv_truncated_columns ?? false,
    preview_from: "csv_file",
  };
}

export function ImportPreviewSnippet({
  importId,
  inlinePreview,
  buttonLabel = "Show CSV preview",
}: ImportPreviewSnippetProps) {
  const [open, setOpen] = useState(false);
  const [loaded, setLoaded] = useState<ImportSnippet | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const inlineData =
    inlinePreview && (inlinePreview.csv_columns?.length ?? 0) > 0
      ? inlineToSnippet(inlinePreview)
      : null;

  useEffect(() => {
    if (!open || inlineData || !importId || loaded || loading) return;

    setLoading(true);
    setError(null);
    void api
      .getImportSourceSnippet(importId)
      .then((snippet) => {
        setLoaded(snippet);
      })
      .catch(() => {
        setError("Could not load preview.");
      })
      .finally(() => {
        setLoading(false);
      });
  }, [open, importId, inlineData, loaded, loading]);

  if (!importId && !inlineData) return null;

  const data = inlineData ?? loaded;
  const sampleCount = data?.rows.length ?? 0;
  const totalRows = data?.total_rows ?? sampleCount;
  const totalColumns = data?.total_columns ?? data?.columns.length ?? 0;

  return (
    <div className="mt-1.5">
      <button
        type="button"
        className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
        onClick={(event) => {
          event.stopPropagation();
          setOpen((value) => !value);
        }}
      >
        {open ? (
          <ChevronDown className="h-3 w-3" />
        ) : (
          <ChevronRight className="h-3 w-3" />
        )}
        {open ? buttonLabel.replace("Show ", "Hide ") : buttonLabel}
      </button>

      {open ? (
        <div
          className="mt-1.5 overflow-hidden rounded-md border border-border bg-background/90"
          onClick={(event) => event.stopPropagation()}
        >
          {loading ? (
            <div className="flex items-center gap-2 px-2 py-3 text-xs text-muted-foreground">
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              Loading preview…
            </div>
          ) : error ? (
            <p className="px-2 py-3 text-xs text-destructive">{error}</p>
          ) : data && data.columns.length ? (
            <>
              <div className="overflow-x-auto">
                <table className="w-full min-w-max text-left text-[11px]">
                  <thead className="border-b border-border bg-muted/40">
                    <tr>
                      {data.columns.map((column) => (
                        <th
                          key={column}
                          className="whitespace-nowrap px-2 py-1.5 font-medium text-foreground/90"
                        >
                          {column}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {data.rows.map((row, rowIndex) => (
                      <tr
                        key={`row-${rowIndex}`}
                        className="border-b border-border/60 last:border-0"
                      >
                        {row.map((cell, cellIndex) => (
                          <td
                            key={`${rowIndex}-${cellIndex}`}
                            className="max-w-[12rem] truncate whitespace-nowrap px-2 py-1.5 text-muted-foreground"
                            title={cell}
                          >
                            {cell || "—"}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="border-t border-border px-2 py-1 text-[10px] text-muted-foreground">
                Showing {sampleCount} of {totalRows.toLocaleString()} row
                {totalRows === 1 ? "" : "s"}
                {" · "}
                {data.columns.length} of {totalColumns} column
                {totalColumns === 1 ? "" : "s"}
                {data.truncated_columns ? " (preview truncated)" : ""}
                {data.note ? ` · ${data.note}` : ""}
              </p>
            </>
          ) : (
            <p className="px-2 py-3 text-xs text-muted-foreground">
              No preview available for this import.
            </p>
          )}
        </div>
      ) : null}
    </div>
  );
}

/** @deprecated Use ImportPreviewSnippet */
export const CsvPreviewSnippet = ImportPreviewSnippet;
