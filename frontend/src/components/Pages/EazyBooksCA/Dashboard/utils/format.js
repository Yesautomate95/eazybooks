/**
 * Reusable formatters for the financial dashboard.
 * Currency defaults to INR with Indian number grouping (1,00,000 not 100,000).
 */

const INR = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 0,
});

const INR_COMPACT = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  notation: "compact",
  maximumFractionDigits: 1,
});

const PCT = new Intl.NumberFormat("en-IN", {
  style: "percent",
  maximumFractionDigits: 1,
  signDisplay: "exceptZero",
});

export function formatCurrency(value, { compact = false } = {}) {
  if (value === null || value === undefined || Number.isNaN(value)) return "₹—";
  return compact ? INR_COMPACT.format(value) : INR.format(value);
}

export function formatPercent(value) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return PCT.format(value / 100);
}

export function getTrendColor(value) {
  if (value === null || value === undefined || Number.isNaN(value)) return "text-ink-500";
  if (value > 0) return "text-success-600 dark:text-success-500";
  if (value < 0) return "text-danger-600 dark:text-danger-500";
  return "text-ink-500 dark:text-ink-400";
}

export function getTrendBg(value) {
  if (value === null || value === undefined || Number.isNaN(value)) return "bg-ink-50 dark:bg-ink-800";
  if (value > 0) return "bg-success-50 dark:bg-success-500/10";
  if (value < 0) return "bg-danger-50 dark:bg-danger-500/10";
  return "bg-ink-50 dark:bg-ink-800";
}

export function relativeTime(iso) {
  if (!iso) return "never";
  const then = new Date(iso).getTime();
  const diffMs = Date.now() - then;
  const sec = Math.round(diffMs / 1000);
  if (sec < 5) return "just now";
  if (sec < 60) return `${sec}s ago`;
  const min = Math.round(sec / 60);
  if (min < 60) return `${min} min ago`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.round(hr / 24);
  return `${day}d ago`;
}

export function initialsOf(name) {
  if (!name) return "?";
  return name
    .split(/\s+/)
    .slice(0, 2)
    .map((s) => s[0]?.toUpperCase() || "")
    .join("");
}
