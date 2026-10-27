import React, { useMemo } from "react";
import {
  Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { ChartCard } from "../components/ChartCard";
import { formatCurrency } from "../utils/format";

// Receivables are debit-natured (gilt-debit); payables credit-natured (gilt-credit).
const RECEIVABLE = "#C25E4C";
const PAYABLE = "#71A98A";

/**
 * Receivables vs payables ageing — outstanding bill amounts bucketed by age.
 * Sourced from trn_bill outstanding nets aged by voucher date.
 */
function AgeingChartImpl({ ageing, delay = 0 }) {
  const data = useMemo(() => {
    const r = ageing?.receivable || [];
    const p = ageing?.payable || [];
    const buckets = ["0-30", "31-60", "61-90", "90+"];
    return buckets.map((b) => ({
      bucket: b,
      receivable: r.find((x) => x.bucket === b)?.value || 0,
      payable: p.find((x) => x.bucket === b)?.value || 0,
    }));
  }, [ageing]);

  const hasData = data.some((d) => d.receivable || d.payable);

  return (
    <ChartCard
      title="Receivables vs Payables ageing"
      subtitle="Outstanding by age (days)"
      delay={delay}
    >
      <div className="h-64 w-full">
        {hasData ? (
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={data} margin={{ top: 8, right: 8, left: -8, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgb(var(--gl-hairline))" vertical={false} />
              <XAxis dataKey="bucket" tick={{ fontSize: 11, fill: "rgb(var(--gl-soft))" }} axisLine={false} tickLine={false} />
              <YAxis
                tick={{ fontSize: 11, fill: "rgb(var(--gl-soft))", fontFamily: "JetBrains Mono" }}
                axisLine={false} tickLine={false}
                tickFormatter={(v) => formatCurrency(v, { compact: true })}
              />
              <Tooltip
                formatter={(v, n) => [formatCurrency(v), n === "receivable" ? "Receivable" : "Payable"]}
                contentStyle={{
                  borderRadius: 10, border: "1px solid rgb(var(--gl-hairline))",
                  background: "rgb(var(--gl-card))", fontSize: 12,
                }}
              />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <Bar dataKey="receivable" name="Receivable" fill={RECEIVABLE} radius={[3, 3, 0, 0]} />
              <Bar dataKey="payable" name="Payable" fill={PAYABLE} radius={[3, 3, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        ) : (
          <EmptyHint label="No outstanding bills to age." />
        )}
      </div>
    </ChartCard>
  );
}

export function EmptyHint({ label }) {
  return (
    <div className="flex h-full w-full items-center justify-center text-center text-xs text-gilt-faint">
      {label}
    </div>
  );
}

export const AgeingChart = React.memo(AgeingChartImpl);
export default AgeingChart;
