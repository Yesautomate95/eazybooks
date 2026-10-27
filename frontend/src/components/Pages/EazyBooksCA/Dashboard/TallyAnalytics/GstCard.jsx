import React from "react";
import { Info } from "lucide-react";
import { ChartCard } from "../components/ChartCard";
import { formatCurrency } from "../utils/format";

/**
 * GST liability snapshot derived from "Duties & Taxes" ledger balances.
 * This is a ledger-derived figure, NOT a filed GSTR-3B — labelled as such so
 * a CA never mistakes it for a return value.
 */
function GstCardImpl({ gst, delay = 0 }) {
  const output = gst?.output || 0;
  const input = gst?.input || 0;
  const net = gst?.net || 0;
  const payable = net >= 0;

  return (
    <ChartCard title="GST liability (net)" subtitle="From Duties & Taxes ledgers" delay={delay}>
      <div className="grid grid-cols-3 gap-3">
        <Figure label="Output tax" value={output} tone="text-gilt-credit" />
        <Figure label="Input credit" value={input} tone="text-gilt-debit" />
        <Figure
          label={payable ? "Net payable" : "Net credit"}
          value={Math.abs(net)}
          tone={payable ? "text-gilt-credit" : "text-gilt-debit"}
          emphasis
        />
      </div>
      <p className="mt-3 flex items-start gap-1.5 text-2xs text-gilt-faint">
        <Info size={12} className="mt-0.5 shrink-0" />
        Ledger-derived snapshot from Duties &amp; Taxes balances — not a filed GSTR.
      </p>
    </ChartCard>
  );
}

function Figure({ label, value, tone, emphasis }) {
  return (
    <div className="rounded-lg border border-gilt-hairline bg-gilt-sunken px-3 py-2.5">
      <p className="gl-eyebrow">{label}</p>
      <p className={`mt-1 font-mono ${emphasis ? "text-lg" : "text-base"} font-semibold tabular-nums ${tone}`}>
        {formatCurrency(value)}
      </p>
    </div>
  );
}

export const GstCard = React.memo(GstCardImpl);
export default GstCard;
