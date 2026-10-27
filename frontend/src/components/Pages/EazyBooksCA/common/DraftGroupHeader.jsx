import React from "react";
import { Building2, Send, Loader2, AlertCircle } from "lucide-react";

// Header for a per-company group in the Tally library. Shows pill counts and
// the bulk-push button. The button is only enabled when the user's current
// Tally company matches this group's company (so we don't accidentally push
// invoices into the wrong company).

const StatusPill = ({ label, count, tone }) => {
  if (!count) return null;
  const toneClasses = {
    draft: "text-gilt-brass border-gilt-brass/40",
    pushed: "text-gilt-credit border-gilt-credit/40",
    error: "text-gilt-debit border-gilt-debit/40",
    other: "text-gilt-soft border-gilt-hairline",
  };
  return (
    <span
      className={`font-mono tabular-nums text-[10px] uppercase tracking-[0.14em] px-1.5 py-0.5 rounded border ${
        toneClasses[tone] || toneClasses.other
      }`}
    >
      {count} {label}
    </span>
  );
};

export default function DraftGroupHeader({
  company,
  drafts,
  currentCompany,
  tallyConnected,
  isPushing,
  onBulkPush,
}) {
  const counts = drafts.reduce(
    (acc, d) => {
      const s = d.status;
      if (s === "pushed") acc.pushed += 1;
      else if (s === "error") acc.error += 1;
      else acc.pending += 1; // uploaded / extracting / extracted / draft
      return acc;
    },
    { pending: 0, pushed: 0, error: 0 }
  );
  // Push-eligible: both 'extracted' (AI-extracted, awaiting first push) and
  // 'draft' (human-reviewed) qualify. 'Save Draft' is no longer a prerequisite
  // — the explicit review remains available but isn't a gate.
  const pushable = drafts.filter(
    (d) => d.status === "draft" || d.status === "extracted"
  );
  const companyMatches = !!currentCompany && currentCompany === company;
  // "Unassigned" is a synthetic bucket for drafts without a company_name; no
  // Tally company can ever match it, so don't pretend a bulk push is possible.
  const isUnassigned = !company;
  const canPush =
    !isUnassigned &&
    pushable.length > 0 &&
    companyMatches &&
    tallyConnected === true &&
    !isPushing;

  let hint = null;
  if (isUnassigned) {
    hint = "Assign a company before pushing";
  } else if (!pushable.length) {
    hint = counts.pushed > 0 ? "All pushed" : null;
  } else if (!currentCompany) {
    hint = "Open Tally to push";
  } else if (!companyMatches) {
    hint = `Switch Tally to '${company}' to push`;
  } else if (tallyConnected !== true) {
    hint = "Waiting for Tally";
  }

  return (
    <div className="flex items-center justify-between gap-3 px-1 py-3 sticky top-0 bg-gilt-paper/95 backdrop-blur-sm z-10 mb-3 border-b border-gilt-hairline">
      <div className="flex items-center gap-2 min-w-0">
        <Building2 className="w-4 h-4 text-gilt-faint shrink-0" />
        <h3 className="text-sm font-medium text-gilt-ink truncate">
          {company || "Unassigned"}
        </h3>
        <div className="flex items-center gap-1.5 ml-1">
          <StatusPill label="pending" count={counts.pending} tone="draft" />
          <StatusPill label="pushed" count={counts.pushed} tone="pushed" />
          <StatusPill label="errors" count={counts.error} tone="error" />
        </div>
      </div>
      <div className="flex items-center gap-2 shrink-0">
        {hint && !canPush && (
          <span className="text-[11px] text-gilt-faint flex items-center gap-1">
            <AlertCircle className="w-3 h-3" />
            {hint}
          </span>
        )}
        {!isUnassigned && (
          <button
            onClick={() => onBulkPush(company, pushable)}
            disabled={!canPush}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-gilt-brass text-gilt-paper rounded-md hover:bg-gilt-brass/90 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            title={canPush ? `Push ${pushable.length} item${pushable.length === 1 ? "" : "s"}` : hint || ""}
          >
            {isPushing ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <Send className="w-3.5 h-3.5" />
            )}
            {isPushing ? "Pushing…" : `Push ${pushable.length}`}
          </button>
        )}
      </div>
    </div>
  );
}
