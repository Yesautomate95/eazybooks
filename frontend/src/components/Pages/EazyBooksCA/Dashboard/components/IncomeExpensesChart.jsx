import React, { memo, useMemo, useState } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { BarChart3, LineChart as LineIcon } from "lucide-react";
import { ChartCard } from "./ChartCard";
import { formatCurrency } from "../utils/format";
import { cn } from "../../../../../lib/utils";

/**
 * Month-on-month Income vs Expenses.
 * Toggle: stacked area (default) ↔ overlaid lines.
 */
function IncomeExpensesChartImpl({ data = [], delay = 0 }) {
  const [variant, setVariant] = useState("area"); // "area" | "line"

  const total = useMemo(
    () =>
      data.reduce(
        (acc, d) => ({ income: acc.income + d.income, expenses: acc.expenses + d.expenses }),
        { income: 0, expenses: 0 }
      ),
    [data]
  );

  return (
    <ChartCard
      title="Income vs Expenses"
      subtitle={`Last 12 months · Net ${formatCurrency(total.income - total.expenses, { compact: true })}`}
      delay={delay}
      action={
        <div className="inline-flex rounded-lg border border-gilt-hairline bg-gilt-card p-0.5">
          <ToggleBtn active={variant === "area"} onClick={() => setVariant("area")}>
            <BarChart3 size={12} />
            Area
          </ToggleBtn>
          <ToggleBtn active={variant === "line"} onClick={() => setVariant("line")}>
            <LineIcon size={12} />
            Line
          </ToggleBtn>
        </div>
      }
    >
      <div className="h-72 w-full">
        <ResponsiveContainer width="100%" height="100%">
          {variant === "area" ? (
            <AreaChart data={data} margin={{ top: 8, right: 8, left: -8, bottom: 0 }}>
              <defs>
                <linearGradient id="g-income" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#C5972C" stopOpacity={0.35} />
                  <stop offset="100%" stopColor="#C5972C" stopOpacity={0} />
                </linearGradient>
                <linearGradient id="g-expenses" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#C25E4C" stopOpacity={0.3} />
                  <stop offset="100%" stopColor="#C25E4C" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#A99C84" strokeOpacity={0.35} vertical={false} />
              <XAxis dataKey="month" stroke="#8A7F6A" tick={{ fill: "#8A7F6A" }} tickLine={false} axisLine={false} fontSize={11} />
              <YAxis
                stroke="#8A7F6A"
                tick={{ fill: "#8A7F6A" }}
                tickLine={false}
                axisLine={false}
                fontSize={11}
                tickFormatter={(v) => formatCurrency(v, { compact: true })}
                width={68}
              />
              <Tooltip content={<CustomTooltip />} cursor={{ stroke: "#A99C84", strokeOpacity: 0.4 }} />
              <Legend
                iconType="circle"
                wrapperStyle={{ fontSize: 12, paddingTop: 8 }}
                formatter={(v) => <span className="text-gilt-soft">{v}</span>}
              />
              <Area
                name="Income"
                type="monotone"
                dataKey="income"
                stroke="#C5972C"
                strokeWidth={2.2}
                fill="url(#g-income)"
                animationDuration={600}
              />
              <Area
                name="Expenses"
                type="monotone"
                dataKey="expenses"
                stroke="#C25E4C"
                strokeWidth={2.2}
                fill="url(#g-expenses)"
                animationDuration={600}
              />
            </AreaChart>
          ) : (
            <LineChart data={data} margin={{ top: 8, right: 8, left: -8, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#A99C84" strokeOpacity={0.35} vertical={false} />
              <XAxis dataKey="month" stroke="#8A7F6A" tick={{ fill: "#8A7F6A" }} tickLine={false} axisLine={false} fontSize={11} />
              <YAxis
                stroke="#8A7F6A"
                tick={{ fill: "#8A7F6A" }}
                tickLine={false}
                axisLine={false}
                fontSize={11}
                tickFormatter={(v) => formatCurrency(v, { compact: true })}
                width={68}
              />
              <Tooltip content={<CustomTooltip />} cursor={{ stroke: "#A99C84", strokeOpacity: 0.4 }} />
              <Legend
                iconType="circle"
                wrapperStyle={{ fontSize: 12, paddingTop: 8 }}
                formatter={(v) => <span className="text-gilt-soft">{v}</span>}
              />
              <Line
                name="Income"
                type="monotone"
                dataKey="income"
                stroke="#C5972C"
                strokeWidth={2.5}
                dot={{ r: 3, strokeWidth: 0, fill: "#C5972C" }}
                activeDot={{ r: 5 }}
                animationDuration={600}
              />
              <Line
                name="Expenses"
                type="monotone"
                dataKey="expenses"
                stroke="#C25E4C"
                strokeWidth={2.5}
                dot={{ r: 3, strokeWidth: 0, fill: "#C25E4C" }}
                activeDot={{ r: 5 }}
                animationDuration={600}
              />
            </LineChart>
          )}
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

function CustomTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-lg border border-gilt-hairline bg-gilt-card px-3 py-2 shadow-sm">
      <p className="mb-1 font-mono text-[10px] font-semibold uppercase tracking-[0.14em] text-gilt-soft">
        {label}
      </p>
      {payload.map((p) => (
        <div key={p.dataKey} className="flex items-center gap-2 text-sm">
          <span className="h-2 w-2 rounded-full" style={{ backgroundColor: p.color }} />
          <span className="text-gilt-soft">{p.name}</span>
          <span className="ml-auto font-mono font-semibold tabular-nums text-gilt-ink">
            {formatCurrency(p.value)}
          </span>
        </div>
      ))}
    </div>
  );
}

const IncomeExpensesChart = memo(IncomeExpensesChartImpl);
export default IncomeExpensesChart;
