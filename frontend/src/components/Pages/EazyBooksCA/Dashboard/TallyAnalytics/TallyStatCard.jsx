import React from "react";
import { motion } from "framer-motion";
import { formatCurrency } from "../utils/format";
import { cn } from "../../../../../lib/utils";

/**
 * KPI tile for Tally analytics. Amounts render in JetBrains Mono with Indian
 * grouping. Credit/debit meaning is carried by a small Dr/Cr chip (gilt-credit
 * / gilt-debit) rather than by colouring the whole number — so the board stays
 * readable while still honouring the bookkeeping colour convention.
 */
export function TallyStatCard({ kpi, index = 0 }) {
  const kind = kpi.kind || "neutral";
  const chip =
    kind === "credit"
      ? { label: "Cr", cls: "text-gilt-credit border-gilt-credit/40" }
      : kind === "debit"
        ? { label: "Dr", cls: "text-gilt-debit border-gilt-debit/40" }
        : null;

  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.2, delay: index * 0.03 }}
      className={cn(
        "group relative overflow-hidden rounded-xl border border-gilt-hairline bg-gilt-card p-4",
        "transition-colors duration-150"
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <p className="gl-eyebrow">{kpi.label}</p>
        {chip && (
          <span
            className={cn(
              "inline-flex items-center rounded-full border px-1.5 py-0.5 font-mono text-2xs font-semibold",
              chip.cls
            )}
          >
            {chip.label}
          </span>
        )}
      </div>
      <p className="gl-double-rule mt-2 pb-1 font-mono text-xl font-semibold tracking-tight text-gilt-ink tabular-nums">
        {formatCurrency(kpi.value)}
      </p>
    </motion.div>
  );
}
