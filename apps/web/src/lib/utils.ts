import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

export function formatCurrency(
  value: number,
  options: Intl.NumberFormatOptions = {}
): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
    ...options,
  }).format(Number.isFinite(value) ? value : 0);
}

/** Format tiny crypto amounts without rounding sub-dust rewards to "0". */
export function formatCryptoAmount(value: number): string {
  if (!Number.isFinite(value) || value <= 0) return "0";
  if (value < 1e-6) return "<0.000001";
  if (value < 0.001) {
    return new Intl.NumberFormat("en-US", {
      minimumSignificantDigits: 3,
      maximumSignificantDigits: 4,
    }).format(value);
  }
  return formatNumber(value, 6);
}

export function formatNumber(value: number, maxFractionDigits = 6): string {
  return new Intl.NumberFormat("en-US", {
    maximumFractionDigits: maxFractionDigits,
  }).format(Number.isFinite(value) ? value : 0);
}

export function formatPercent(value: number): string {
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(2)}%`;
}

/** Shorten hex addresses for display: 0x1a2b…9f3e */
export function shortenAddress(address: string): string {
  const trimmed = address.trim();
  if (trimmed.length < 12) return trimmed;
  if (trimmed.startsWith("0x") && trimmed.length >= 10) {
    return `${trimmed.slice(0, 6)}…${trimmed.slice(-4)}`;
  }
  if (trimmed.length > 12) {
    return `${trimmed.slice(0, 4)}…${trimmed.slice(-4)}`;
  }
  return trimmed;
}

const FIAT_ISO = new Set(["USD", "EUR", "GBP", "CAD", "AUD", "JPY", "CHF", "NZD"]);

/** Format a fiat or quote-currency amount with the correct symbol/denomination. */
export function formatMoney(
  value: number,
  currency = "USD",
  options: Intl.NumberFormatOptions = {}
): string {
  const code = currency.toUpperCase();
  if (FIAT_ISO.has(code)) {
    const locale = code === "GBP" ? "en-GB" : "en-US";
    return new Intl.NumberFormat(locale, {
      style: "currency",
      currency: code,
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
      ...options,
    }).format(Number.isFinite(value) ? value : 0);
  }
  return `${formatNumber(value, 4)} ${code}`;
}

/** Per-coin unit price — extra precision for micro-cap ticks. */
export function formatUnitPrice(
  value: number,
  currency = "USD"
): string {
  if (!Number.isFinite(value) || value <= 0) {
    return formatMoney(0, currency);
  }
  if (value < 0.01) {
    return formatMoney(value, currency, {
      minimumFractionDigits: 4,
      maximumFractionDigits: 6,
    });
  }
  return formatMoney(value, currency, { maximumFractionDigits: 4 });
}

export function formatDateTime(iso: string): string {
  return new Intl.DateTimeFormat("en-GB", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(iso));
}

/** Hide negligible ledger rows (e.g. Kraken 0 SOL staking reward dust). */
export function isDustTransaction(tx: {
  amount: number;
  fiat_value_at_trigger: number;
  source?: string | null;
  transaction_type?: string;
  transfer_direction?: string | null;
  asset?: string;
}): boolean {
  const DUST_AMOUNT = 1e-6;
  const DUST_VALUE = 0.5;
  const SOL_RENT_CRUMB_MAX = 0.0001;
  if (tx.amount < DUST_AMOUNT) return true;
  if (tx.fiat_value_at_trigger > 0 && tx.fiat_value_at_trigger < DUST_VALUE) {
    return true;
  }
  if (
    tx.source === "solana" &&
    tx.transaction_type === "TRANSFER" &&
    tx.transfer_direction === "IN" &&
    (tx.asset === "SOL" || tx.asset === "WSOL") &&
    tx.amount > 0 &&
    tx.amount <= SOL_RENT_CRUMB_MAX &&
    tx.fiat_value_at_trigger <= 0
  ) {
    return true;
  }
  return false;
}

/** Turn FastAPI JSON error bodies into readable text (filenames per failed import). */
export function formatApiError(body: string, status?: number): string {
  const trimmed = body.trim();
  if (!trimmed) {
    return status ? `Request failed (HTTP ${status})` : "Request failed";
  }

  try {
    const parsed = JSON.parse(trimmed) as {
      detail?: unknown;
      message?: string;
    };
    const detail = parsed.detail ?? parsed;

    if (typeof detail === "object" && detail !== null) {
      const record = detail as { message?: string; errors?: string[] };
      if (Array.isArray(record.errors) && record.errors.length > 0) {
        const header = record.message ?? "One or more files failed to import";
        const files = record.errors.map((entry) => {
          const splitAt = entry.indexOf(": ");
          if (splitAt === -1) {
            return `• ${entry}`;
          }
          const filename = entry.slice(0, splitAt);
          const reason = entry.slice(splitAt + 2);
          return `• ${filename}\n  ${reason}`;
        });
        return `${header}:\n\n${files.join("\n\n")}`;
      }
      if (typeof record.message === "string") {
        return record.message;
      }
    }

    if (typeof detail === "string") {
      return detail;
    }
  } catch {
    // Not JSON — return raw body below.
  }

  if (trimmed.startsWith("API ")) {
    return trimmed;
  }

  return status ? `HTTP ${status}: ${trimmed}` : trimmed;
}

/** True when the error is likely a dead API, not a parse/validation problem. */
export function isBackendConnectionError(message: string): boolean {
  if (
    message.includes("failed to parse") ||
    message.includes("failed to import") ||
    message.includes("• ")
  ) {
    return false;
  }
  return /failed to fetch|networkerror|econnrefused|502|503|504/i.test(message);
}
