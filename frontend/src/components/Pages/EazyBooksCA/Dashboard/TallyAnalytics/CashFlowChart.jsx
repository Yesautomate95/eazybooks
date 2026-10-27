import React from "react";
import {
  Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { ChartCard } from "../components/ChartCard";
import { formatCurrency } from "../utils/format";
import { EmptyHint } from "./AgeingChart";

const BRASS = "#C5972C";

/**
 * Closing cash & bank position over the trailing 12 months. Computed from
 * opening balances + cumulative net movement of cash/bank ledgers.
 */
function CashFlowChartImpl({ data = [], delay = 0 }) {
  const hasData = data.some((d) => d.value);
  return (
    <ChartCard title="Cash & bank position" subtitle="Closing balance · last 12 months" delay={delay}>
      <div className="h-64 w-full">
        {hasData ? (
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={data} margin={{ top: 8, right: 8, left: -8, bottom: 0 }}>
              <defs>
                <linearGradient id="g-cashflow" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={BRASS} stopOpacity={0.32} />
                  <stop offset="100%" stopColor={BRASS} stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="rgb(var(--gl-hairline))" vertical={false} />
              <XAxis dataKey="month" tick={{ fontSize: 11, fill: "rgb(var(--gl-soft))" }} axisLine={false} tickLine={false} />
              <YAxis
                tick={{ fontSize: 11, fill: "rgb(var(--gl-soft))", fontFamily: "JetBrains Mono" }}
                axisLine={false} tickLine={false}
                tickFormatter={(v) => formatCurrency(v, { compact: true })}
              />
              <Tooltip
                formatter={(v) => [formatCurrency(v), "Cash & bank"]}
                contentStyle={{
                  borderRadius: 10, border: "1px solid rgb(var(--gl-hairline))",
                  background: "rgb(var(--gl-card))", fontSize: 12,
                }}
              />
              <Area type="monotone" dataKey="value" stroke={BRASS} strokeWidth={2} fill="url(#g-cashflow)" />
            </AreaChart>
          </ResponsiveContainer>
        ) : (
          <EmptyHint label="No cash/bank movement in range." />
        )}
      </div>
    </ChartCard>
  );
}

export const CashFlowChart = React.memo(CashFlowChartImpl);
export default CashFlowChart;
