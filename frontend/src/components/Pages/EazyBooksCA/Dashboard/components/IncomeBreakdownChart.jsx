import React, { memo, useMemo } from "react";
import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";
import { ChartCard } from "./ChartCard";
import { formatCurrency } from "../utils/format";

const PIE_COLORS = ["#C5972C", "#4F9874", "#C25E4C", "#71A98A"];

function IncomeBreakdownChartImpl({ data = [], delay = 0 }) {
  const total = useMemo(() => data.reduce((s, d) => s + d.value, 0), [data]);

  return (
    <ChartCard title="Income Breakdown" subtitle="Revenue streams" delay={delay}>
      {/* Stacked when the card is narrow (mobile, and the 1/3-width lg column),
          side-by-side when it has room (full-width md range, and wide xl+). The
          pie uses percentage radii + min-w-0 so it scales with the card width. */}
      <div className="grid grid-cols-1 items-center gap-4 md:grid-cols-[1fr,auto] lg:grid-cols-1 xl:grid-cols-[1fr,auto]">
        <div className="relative h-56 w-full min-w-0">
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie
                data={data}
                cx="50%"
                cy="50%"
                innerRadius="56%"
                outerRadius="85%"
                paddingAngle={2}
                dataKey="value"
                stroke="none"
                animationDuration={600}
              >
                {data.map((_, i) => (
                  <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} />
                ))}
              </Pie>
              <Tooltip
                content={({ active, payload }) =>
                  active && payload?.[0] ? (
                    <div className="rounded-lg border border-gilt-hairline bg-gilt-card px-3 py-2 shadow-sm">
                      <p className="text-xs font-medium text-gilt-soft">{payload[0].name}</p>
                      <p className="mt-0.5 font-mono text-sm font-semibold text-gilt-ink tabular-nums">
                        {formatCurrency(payload[0].value)}
                      </p>
                      <p className="font-mono text-xs text-gilt-soft tabular-nums">
                        {((payload[0].value / total) * 100).toFixed(1)}%
                      </p>
                    </div>
                  ) : null
                }
              />
            </PieChart>
          </ResponsiveContainer>
          <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
            <p className="font-mono text-[10px] font-medium uppercase tracking-[0.14em] text-gilt-soft">
              Total
            </p>
            <p className="font-mono text-lg font-semibold text-gilt-ink tabular-nums">
              {formatCurrency(total, { compact: true })}
            </p>
          </div>
        </div>

        <ul className="space-y-2 md:min-w-[150px]">
          {data.map((d, i) => (
            <li key={d.name} className="flex items-center gap-2 text-sm">
              <span
                className="h-2.5 w-2.5 shrink-0 rounded-sm"
                style={{ backgroundColor: PIE_COLORS[i % PIE_COLORS.length] }}
              />
              <span className="min-w-0 flex-1 truncate text-gilt-ink">{d.name}</span>
              <span className="font-mono text-xs font-medium text-gilt-soft tabular-nums">
                {((d.value / total) * 100).toFixed(0)}%
              </span>
            </li>
          ))}
        </ul>
      </div>
    </ChartCard>
  );
}

const IncomeBreakdownChart = memo(IncomeBreakdownChartImpl);
export default IncomeBreakdownChart;
