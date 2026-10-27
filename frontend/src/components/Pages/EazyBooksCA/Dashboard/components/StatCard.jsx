import React, { memo, useMemo } from "react";
import { motion } from "framer-motion";
import { ArrowDownRight, ArrowUpRight, Minus } from "lucide-react";
import {
  Area,
  AreaChart,
  ResponsiveContainer,
  Tooltip,
} from "recharts";
import { cn } from "../../../../../lib/utils";
import { formatCurrency, formatPercent } from "../utils/format";

const COLORS = {
  income: { stroke: "#C5972C", fill: "#C5972C" },
  purchases: { stroke: "#71A98A", fill: "#71A98A" },
  expenses: { stroke: "#C25E4C", fill: "#C25E4C" },
  bank: { stroke: "#4F9874", fill: "#4F9874" },
  stock: { stroke: "#B08A2E", fill: "#B08A2E" },
};

function StatCardImpl({ kpi, index = 0 }) {
  const palette = COLORS[kpi.id] || COLORS.income;
  const Arrow = kpi.deltaPct > 0 ? ArrowUpRight : kpi.deltaPct < 0 ? ArrowDownRight : Minus;
  const gradientId = useMemo(() => `g-${kpi.id}`, [kpi.id]);

  return (
    <motion.div
      className={cn(
        "group relative overflow-hidden rounded-xl border border-gilt-hairline bg-gilt-card p-5",
        "transition-colors duration-150"
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <p className="gl-eyebrow">
          {kpi.label}
        </p>
        <span
          className={cn(
            "inline-flex items-center gap-0.5 rounded-full border px-2 py-0.5 font-mono text-xs font-semibold tabular-nums",
            kpi.deltaPct > 0
              ? "text-gilt-credit border-gilt-credit/40"
              : kpi.deltaPct < 0
                ? "text-gilt-debit border-gilt-debit/40"
                : "text-gilt-soft border-gilt-hairline"
          )}
        >
          <Arrow size={12} strokeWidth={2.4} />
          {formatPercent(Math.abs(kpi.deltaPct))}
        </span>
      </div>

      <p className="gl-double-rule mt-3 pb-1 font-mono text-2xl font-semibold tracking-tight text-gilt-ink tabular-nums">
        {formatCurrency(kpi.value)}
      </p>

      <div className="mt-3 h-12 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={kpi.spark} margin={{ top: 4, right: 0, left: 0, bottom: 0 }}>
            <defs>
              <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={palette.fill} stopOpacity={0.32} />
                <stop offset="100%" stopColor={palette.fill} stopOpacity={0} />
              </linearGradient>
            </defs>
            <Tooltip
              cursor={false}
              content={({ active, payload }) =>
                active && payload?.[0] ? (
                  <div className="rounded-md border border-gilt-hairline bg-gilt-card px-2 py-1 font-mono text-xs font-medium text-gilt-ink tabular-nums shadow-sm">
                    {formatCurrency(payload[0].value)}
                  </div>
                ) : null
              }
            />
            <Area
              type="monotone"
              dataKey="v"
              stroke={palette.stroke}
              strokeWidth={2}
              fill={`url(#${gradientId})`}
              isAnimationActive={false}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </motion.div>
  );
}

/** Memoized — these get rendered in a tight grid and shouldn't repaint on header changes. */
export const StatCard = memo(StatCardImpl);
