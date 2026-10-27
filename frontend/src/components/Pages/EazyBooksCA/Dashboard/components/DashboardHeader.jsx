import React from "react";
import { Calendar, Database, Loader2, Moon, RefreshCw, Sun } from "lucide-react";
import { cn } from "../../../../../lib/utils";
import { relativeTime } from "../utils/format";
import { PLATFORMS } from "../services/dashboardService";

const RANGES = [
  { id: "month", label: "This Month" },
  { id: "quarter", label: "Quarter" },
  { id: "year", label: "Year" },
  { id: "custom", label: "Custom" },
];

export function DashboardHeader({
  range,
  onRangeChange,
  customStart = "",
  customEnd = "",
  onCustomStartChange,
  onCustomEndChange,
  source,
  onSourceChange,
  availableSources = [],
  lastSyncedAt,
  isFetching,
  onRefresh,
  darkMode,
  onToggleDark,
  // Scope-aware extras (defaults preserve the original aggregated header).
  title = "Financial Analytics",
  subtitle,
  showSource = true,
  actions = null,
}) {
  const syncStatus = (
    <span className="inline-flex items-center gap-1">
      {isFetching ? (
        <>
          <Loader2 size={11} className="animate-spin text-gilt-brass" />
          Syncing now…
        </>
      ) : (
        <>Last synced {relativeTime(lastSyncedAt)}</>
      )}
    </span>
  );

  return (
    <header className="flex flex-col gap-3 rounded-xl border border-gilt-hairline bg-gilt-card p-4 md:flex-row md:items-center md:justify-between">
      <div className="min-w-0">
        <h1 className="gl-display text-lg text-gilt-ink">{title}</h1>
        <p className="mt-0.5 text-xs text-gilt-soft">
          {subtitle !== undefined ? (
            subtitle
          ) : availableSources.length === 0 ? (
            <>No connected sources yet — connect a platform to populate.</>
          ) : (
            <>
              Aggregated across {availableSources.length} connected source
              {availableSources.length === 1 ? "" : "s"}
              {" · "}
              {syncStatus}
            </>
          )}
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        {actions}
        {/* Date range */}
        <div className="inline-flex items-center gap-1.5 rounded-lg border border-gilt-hairline bg-gilt-card transition-colors duration-150 focus-within:border-gilt-brass">
          <span className="pl-2.5 text-gilt-faint" aria-hidden="true">
            <Calendar size={13} />
          </span>
          <select
            value={range}
            onChange={(e) => onRangeChange(e.target.value)}
            className="bg-transparent py-2 pr-3 text-xs font-medium text-gilt-ink focus:outline-none"
            aria-label="Date range"
          >
            {RANGES.map((r) => (
              <option key={r.id} value={r.id}>{r.label}</option>
            ))}
          </select>
        </div>

        {/* Custom range date pickers — only shown for the Custom option. The
            fetch waits until both ends are set (see Dashboard.jsx customReady). */}
        {range === "custom" && (
          <div className="inline-flex items-center gap-1.5 rounded-lg border border-gilt-hairline bg-gilt-card px-2 transition-colors duration-150 focus-within:border-gilt-brass">
            <input
              type="date"
              value={customStart}
              max={customEnd || undefined}
              onChange={(e) => onCustomStartChange?.(e.target.value)}
              className="bg-transparent py-2 text-xs font-medium text-gilt-ink focus:outline-none"
              aria-label="Custom range start date"
            />
            <span className="text-gilt-faint" aria-hidden="true">–</span>
            <input
              type="date"
              value={customEnd}
              min={customStart || undefined}
              onChange={(e) => onCustomEndChange?.(e.target.value)}
              className="bg-transparent py-2 text-xs font-medium text-gilt-ink focus:outline-none"
              aria-label="Custom range end date"
            />
          </div>
        )}

        {/* Source filter — hidden when the view is locked to one platform */}
        {showSource && (
          <div className="inline-flex items-center gap-1.5 rounded-lg border border-gilt-hairline bg-gilt-card transition-colors duration-150 focus-within:border-gilt-brass">
            <span className="pl-2.5 text-gilt-faint" aria-hidden="true">
              <Database size={13} />
            </span>
            <select
              value={source}
              onChange={(e) => onSourceChange(e.target.value)}
              disabled={!availableSources.length}
              className="bg-transparent py-2 pr-3 text-xs font-medium text-gilt-ink focus:outline-none disabled:cursor-not-allowed disabled:opacity-50"
              aria-label="Data source filter"
            >
              <option value="all">All sources</option>
              {availableSources.map((id) => {
                const p = PLATFORMS.find((x) => x.id === id);
                return (
                  <option key={id} value={id}>
                    {p?.name ?? id}
                  </option>
                );
              })}
            </select>
          </div>
        )}

        {/* Refresh */}
        <button
          type="button"
          onClick={onRefresh}
          disabled={isFetching}
          className={cn(
            "inline-flex items-center gap-1.5 rounded-lg border border-gilt-hairline bg-gilt-card px-3 py-2 text-xs font-medium text-gilt-soft",
            "transition-colors duration-150 hover:border-gilt-brass hover:text-gilt-brass disabled:opacity-50 disabled:cursor-not-allowed"
          )}
          aria-label="Refresh data"
        >
          <RefreshCw size={13} className={isFetching ? "animate-spin" : ""} />
          Refresh
        </button>

        {/* Dark mode toggle — hidden: the page's local dark: variants were
            replaced by gilt theme vars, so the global Lamplight toggle in the
            sidebar now controls this page too. State/handler kept upstream. */}
        {false && (
          <button
            type="button"
            onClick={onToggleDark}
            className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-gilt-hairline bg-gilt-card text-gilt-soft transition-colors duration-150 hover:border-gilt-brass hover:text-gilt-brass"
            aria-label={darkMode ? "Switch to light mode" : "Switch to dark mode"}
          >
            {darkMode ? <Sun size={14} /> : <Moon size={14} />}
          </button>
        )}
      </div>
    </header>
  );
}
