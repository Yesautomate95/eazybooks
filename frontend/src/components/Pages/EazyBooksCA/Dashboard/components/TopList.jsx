import React, { useMemo, useState } from "react";
import { motion } from "framer-motion";
import { ArrowUpDown } from "lucide-react";
import { ChartCard } from "./ChartCard";
import { formatCurrency, initialsOf } from "../utils/format";
import { cn } from "../../../../../lib/utils";

/** Pre-computed avatar tints so we don't randomize per render. */
const AVATAR_TINTS = [
  "border border-gilt-hairline bg-gilt-sunken text-gilt-brass",
];

export function TopList({ title, subtitle, items = [], emptyHint, delay = 0 }) {
  const [sortBy, setSortBy] = useState("value"); // "value" | "name"

  const sorted = useMemo(() => {
    const copy = [...items];
    if (sortBy === "name") copy.sort((a, b) => a.name.localeCompare(b.name));
    else copy.sort((a, b) => b.value - a.value);
    return copy;
  }, [items, sortBy]);

  const max = sorted[0]?.value || 1;

  return (
    <ChartCard
      title={title}
      subtitle={subtitle}
      delay={delay}
      action={
        <button
          onClick={() => setSortBy((s) => (s === "value" ? "name" : "value"))}
          className="inline-flex items-center gap-1 rounded-md border border-gilt-hairline bg-gilt-card px-2 py-1 text-xs font-medium text-gilt-soft transition-colors duration-150 hover:border-gilt-brass hover:text-gilt-brass"
          aria-label={`Sort by ${sortBy === "value" ? "name" : "value"}`}
        >
          <ArrowUpDown size={12} />
          {sortBy === "value" ? "Sort: Value" : "Sort: Name"}
        </button>
      }
    >
      {sorted.length === 0 ? (
        <p className="py-10 text-center text-sm text-gilt-soft">
          {emptyHint || "No data yet."}
        </p>
      ) : (
        <ol className="divide-y divide-gilt-hairline">
          {sorted.map((row, i) => (
            <motion.li
              key={row.id}
              className="flex items-center gap-3 py-2.5 transition-colors duration-150 hover:bg-gilt-sunken"
            >
              <div
                className={cn(
                  "flex h-8 w-8 shrink-0 items-center justify-center rounded-full font-mono text-xs font-semibold",
                  AVATAR_TINTS[i % AVATAR_TINTS.length]
                )}
                aria-hidden="true"
              >
                {initialsOf(row.name)}
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex items-baseline justify-between gap-2">
                  <p className="truncate text-sm font-medium text-gilt-ink">
                    {row.name}
                  </p>
                  <p className="shrink-0 text-right font-mono text-sm font-semibold text-gilt-ink tabular-nums">
                    {formatCurrency(row.value)}
                  </p>
                </div>
                <div className="mt-1 flex items-center gap-2">
                  <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-gilt-sunken border border-gilt-hairline">
                    <motion.div
                      style={{ width: `${(row.value / max) * 100}%` }}
                      className="h-full rounded-full bg-gilt-gold"
                    />
                  </div>
                  <span className="w-10 text-right font-mono text-xs text-gilt-soft tabular-nums">
                    {row.sharePct}%
                  </span>
                </div>
              </div>
            </motion.li>
          ))}
        </ol>
      )}
    </ChartCard>
  );
}
