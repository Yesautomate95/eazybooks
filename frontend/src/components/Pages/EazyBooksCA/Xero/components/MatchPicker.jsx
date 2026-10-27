import React from "react";
import { Plus, Sparkles } from "lucide-react";

/**
 * MatchPicker — Tier-2 inline "did you mean…?" picker for unmatched rows.
 */
export default function MatchPicker({
  topCandidates = [],
  extractedName,
  onPickExisting,
  onCreateNew,
  label,
}) {
  const hasCandidates = topCandidates && topCandidates.length > 0;
  const truncated =
    (extractedName || "").length > 28
      ? `${(extractedName || "").slice(0, 28)}…`
      : extractedName || "";

  return (
    <div className="flex flex-wrap items-center gap-1.5 mt-1 text-xs">
      {hasCandidates && (
        <span className="inline-flex items-center gap-1 text-gilt-soft">
          <Sparkles className="h-3 w-3 text-gilt-brass" />
          Did you mean:
        </span>
      )}
      {topCandidates.map((c) => {
        const pct = Math.round((c.score || 0) * 100);
        return (
          <button
            key={c.list_id}
            type="button"
            onClick={() => onPickExisting?.(c)}
            className="inline-flex items-center gap-1 px-2 py-0.5 bg-gilt-sunken hover:border-gilt-brass text-gilt-brass rounded-full border border-gilt-brass/40 transition-colors duration-150"
            title={`${pct}% match — click to use this existing ${label || "entity"}`}
          >
            <span className="truncate max-w-[160px]">{c.name}</span>
            <span className="text-2xs font-medium text-gilt-brass font-mono tabular-nums">{pct}%</span>
          </button>
        );
      })}
      <button
        type="button"
        onClick={onCreateNew}
        className="inline-flex items-center gap-1 px-2 py-0.5 bg-transparent hover:bg-gilt-sunken text-gilt-credit rounded-full border border-gilt-credit/40 transition-colors duration-150"
        title={`Create "${extractedName}" as a new ${label || "entity"} in Xero`}
      >
        <Plus className="h-3 w-3" />
        Create "{truncated}"
      </button>
    </div>
  );
}
