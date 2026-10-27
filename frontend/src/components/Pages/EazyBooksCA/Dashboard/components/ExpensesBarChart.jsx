import React, { memo, useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Layers, ListFilter } from "lucide-react";
import { ChartCard } from "./ChartCard";
import { formatCurrency } from "../utils/format";
import { cn } from "../../../../../lib/utils";

/**
 * Monthly expenses — supports a "by month" view (12 bars summing categories)
 * or a "by category" breakdown view, depending on user toggle.
 */
const CATEGORY_PALETTE = [
  "#C5972C", "#4F9874", "#C25E4C", "#B08A2E", "#8A7F6A", "#71A98A",
];

function ExpensesBarChartImpl({ monthly = [], categories = [], delay = 0 }) {
  const [mode, setMode] = useState("month");

  const monthData = useMemo(
    () => monthly.map((m) => ({ name: m.month, value: m.expenses })),
    [monthly]
  );
  const catData = useMemo(
    () => [...categories].sort((a, b) => b.value - a.value).map((c) => ({ name: c.category, value: c.value })),
    [categories]
  );

  const data = mode === "month" ? monthData : catData;

  return (
    <ChartCard
      title="Monthly Expenses"
      subtitle={mode === "month" ? "Last 12 months" : "Breakdown by category"}
      delay={delay}
      action={
        <div className="inline-flex rounded-lg border border-gilt-hairline bg-gilt-card p-0.5">
          <ToggleBtn active={mode === "month"} onClick={() => setMode("month")}>
            <Layers size={12} /> By month
          </ToggleBtn>
          <ToggleBtn active={mode === "category"} onClick={() => setMode("category")}>
            <ListFilter size={12} /> By category
          </ToggleBtn>
        </div>
      }
    >
      <div className="h-72 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} margin={{ top: 8, right: 8, left: -8, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#A99C84" strokeOpacity={0.35} vertical={false} />
            <XAxis
              dataKey="name"
              stroke="#8A7F6A"
              tick={{ fill: "#8A7F6A" }}
              tickLine={false}
              axisLine={false}
              fontSize={11}
              interval={0}
              angle={mode === "category" ? -20 : 0}
              dy={mode === "category" ? 8 : 0}
              height={mode === "category" ? 48 : 24}
            />
            <YAxis
              stroke="#8A7F6A"
              tick={{ fill: "#8A7F6A" }}
              tickLine={false}
              axisLine={false}
              fontSize={11}
              tickFormatter={(v) => formatCurrency(v, { compact: true })}
              width={68}
            />
            <Tooltip
              cursor={{ fill: "#A99C84", fillOpacity: 0.08 }}
              content={({ active, payload, label }) =>
                active && payload?.[0] ? (
                  <div className="rounded-lg border border-gilt-hairline bg-gilt-card px-3 py-2 shadow-sm">
                    <p className="mb-0.5 font-mono text-[10px] font-semibold uppercase tracking-[0.14em] text-gilt-soft">
                      {label}
                    </p>
                    <p className="font-mono text-sm font-semibold text-gilt-ink tabular-nums">
                      {formatCurrency(payload[0].value)}
                    </p>
                  </div>
                ) : null
              }
            />
            <Bar dataKey="value" radius={[6, 6, 0, 0]} animationDuration={600}>
              {data.map((_, i) => (
                <Cell
                  key={i}
                  fill={mode === "month" ? "#C5972C" : CATEGORY_PALETTE[i % CATEGORY_PALETTE.length]}
                />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </ChartCard>
  );
}

function ToggleBtn({ active, onClick, children }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium transition-colors duration-150",
        active
          ? "bg-gilt-sunken text-gilt-brass"
          : "text-gilt-soft hover:text-gilt-ink"
      )}
    >
      {children}
    </button>
  );
}

const ExpensesBarChart = memo(ExpensesBarChartImpl);
export default ExpensesBarChart;
