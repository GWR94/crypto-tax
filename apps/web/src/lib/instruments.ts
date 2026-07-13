/** Display perp / spot contract names (e.g. SOL - USDC). */

const PERP_PREFIX = /^PERP_([^_]+)_(.+)$/i;

function cleanSymbol(value: string | null | undefined): string {
  const text = (value ?? "").trim().toUpperCase();
  if (!text || text === "NAN" || text === "NONE" || text === "NULL") return "";
  return text;
}

export function formatInstrument(
  instrument?: string | null,
  fallback?: { asset: string; counter_asset?: string | null }
): string {
  const raw = (instrument ?? "").trim();
  if (raw) {
    const perp = PERP_PREFIX.exec(raw);
    if (perp) {
      const base = cleanSymbol(perp[1]);
      const quote = cleanSymbol(perp[2]);
      if (base && quote) return `${base} - ${quote}`;
      return base || quote || raw;
    }
    if (raw.includes(" - ")) return raw;
  }

  const base = cleanSymbol(fallback?.asset);
  const quote = cleanSymbol(fallback?.counter_asset);
  if (base && quote) return `${base} - ${quote}`;
  return base || quote || raw || "—";
}
