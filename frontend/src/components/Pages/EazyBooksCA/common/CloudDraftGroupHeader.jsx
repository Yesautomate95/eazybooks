import React from "react";
import { Building2, Send, Loader2, AlertCircle } from "lucide-react";

/**
 * Per-company group header for the cloud draft library. Mirrors the Tally
 * DraftGroupHeader, but the push gate is simpler: cloud platforms push via the
 * API with the company id embedded, so we don't need Tally's "switch to this
 * company" guard — we only need the platform to be connected.
 */

const StatusPill = ({ label, count, tone }) => {
  if (!count) return null;
  const tones = {
    review: "text-gilt-soft border-gilt-hairline",
    draft: "text-gilt-brass border-gilt-brass/40",
    pushed: "text-gilt-credit border-gilt-credit/40",
    error: "text-gilt-debit border-gilt-debit/40",
  };
  return (
    <span className={`font-mono tabular-nums text-[10px] uppercase tracking-[0.14em] px-1.5 py-0.5 rounded border ${tones[tone] || tones.review}`}>
      {count} {label}
    </span>
  );
};

export default function CloudDraftGroupHeader({
  companyName,
  drafts,
  connected,
  isPushing,
  onBulkPush,
}) {
  const counts = drafts.reduce(
    (acc, d) => {
      const s = d.status;
      if (s === "pushed") acc.pushed += 1;
      else if (s === "error") acc.error += 1;
      else if (s === "draft") acc.draft += 1;
      else acc.review += 1;
      return acc;
    },
    { draft: 0, pushed: 0, error: 0, review: 0 }
  );

  // Only push items the user has explicitly saved as drafts.
  const pushable = drafts.filter((d) => d.status === "draft");
  const canPush = pushable.length > 0 && connected === true && !isPushing;

  let hint = null;
  if (!pushable.length) hint = "Nothing to push";
  else if (connected !== true) hint = "Reconnect to push";

  return (
    <div className="flex items-center justify-between gap-3 px-1 py-3 sticky top-0 bg-gilt-paper/95 backdrop-blur-sm z-10 mb-3 border-b border-gilt-hairline">
      <div className="flex items-center gap-2 min-w-0">
        <Building2 className="w-4 h-4 text-gilt-faint shrink-0" />
        <h3 className="text-sm font-medium text-gilt-ink truncate">
          {companyName || "Unassigned"}
        </h3>
        <div className="flex items-center gap-1.5 ml-1">
          <StatusPill label="to review" count={counts.review} tone="review" />
          <StatusPill label="draft" count={counts.draft} tone="draft" />
          <StatusPill label="pushed" count={counts.pushed} tone="pushed" />
          <StatusPill label="error" count={counts.error} tone="error" />
        </div>
      </div>
      <div className="flex items-center gap-2 shrink-0">
        {hint && !canPush && (
          <span className="text-[11px] text-gilt-faint flex items-center gap-1">
            <AlertCircle className="w-3 h-3" />
            {hint}
          </span>
        )}
        <button
          type="button"
          onClick={() => onBulkPush(pushable)}
          disabled={!canPush}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-gilt-brass text-gilt-paper rounded-md hover:bg-gilt-brass/90 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          title={canPush ? `Push ${pushable.length} draft${pushable.length === 1 ? "" : "s"}` : hint || ""}
        >
          {isPushing ? (
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
          ) : (
            <Send className="w-3.5 h-3.5" />
          )}
          {isPushing ? "Pushing…" : `Push ${pushable.length}`}
        </button>
      </div>
    </div>
  );
}
