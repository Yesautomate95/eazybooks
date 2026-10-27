import React from "react";
import {
  Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { ChartCard } from "../components/ChartCard";
import { EmptyHint } from "./AgeingChart";

const BRASS = "#C5972C";

/**
 * Voucher activity by type for the selected period — a count of posted
 * vouchers grouped by voucher type. Doubles as a freshness/activity signal.
 */
function VoucherActivityChartImpl({ data = [], delay = 0 }) {
  const hasData = data.length > 0;
  return (
    <ChartCard title="Voucher activity" subtitle="Posted vouchers by type" delay={delay}>
      <div className="h-64 w-full">
        {hasData ? (
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={data} layout="vertical" margin={{ top: 4, right: 12, left: 8, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgb(var(--gl-hairline))" horizontal={false} />
              <XAxis type="number" tick={{ fontSize: 11, fill: "rgb(var(--gl-soft))", fontFamily: "JetBrains Mono" }} axisLine={false} tickLine={false} allowDecimals={false} />
              <YAxis type="category" dataKey="name" width={96} tick={{ fontSize: 11, fill: "rgb(var(--gl-soft))" }} axisLine={false} tickLine={false} />
              <Tooltip
                formatter={(v) => [v, "Vouchers"]}
                contentStyle={{
                  borderRadius: 10, border: "1px solid rgb(var(--gl-hairline))",
                  background: "rgb(var(--gl-card))", fontSize: 12,
                }}
              />
              <Bar dataKey="value" fill={BRASS} radius={[0, 3, 3, 0]} />
            </BarChart>
          </ResponsiveContainer>
        ) : (
          <EmptyHint label="No vouchers in this period." />
        )}
      </div>
    </ChartCard>
  );
}

export const VoucherActivityChart = React.memo(VoucherActivityChartImpl);
export default VoucherActivityChart;
