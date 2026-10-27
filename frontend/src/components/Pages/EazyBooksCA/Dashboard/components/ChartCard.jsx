import React from "react";
import { motion } from "framer-motion";
import { cn } from "../../../../../lib/utils";

/**
 * Card wrapper for chart sections. Keeps spacing/border/shadow consistent
 * across the dashboard so charts only worry about their data viz.
 */
export function ChartCard({ title, subtitle, action, children, className, delay = 0 }) {
  return (
    <motion.section
      className={cn(
        "rounded-xl border border-gilt-hairline bg-gilt-card",
        className
      )}
    >
      <header className="flex items-start justify-between gap-3 px-5 pt-4 pb-2">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold text-gilt-ink">{title}</h2>
          {subtitle && (
            <p className="mt-0.5 text-xs text-gilt-soft">{subtitle}</p>
          )}
        </div>
        {action && <div className="shrink-0">{action}</div>}
      </header>
      <div className="px-5 pb-5 pt-2">{children}</div>
    </motion.section>
  );
}
