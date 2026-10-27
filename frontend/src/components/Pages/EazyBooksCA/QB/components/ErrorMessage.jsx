import React from "react";
import { AlertTriangle, X } from "lucide-react";
import { extractQbError } from "./qboError";

/**
 * Inline error block — bold title + detail/code underneath. Pass either an
 * axios error or a plain string; the structured rendering makes the WHY
 * obvious even when both QBO Message and Detail are present, instead of
 * collapsing them into one truncated line.
 *
 * Props:
 *   error       — axios Error, plain Error, or string. Falsy → renders nothing.
 *   prefix      — optional headline shown above the title (e.g. "Push failed").
 *   fallback    — used as the title when no message can be extracted.
 *   onDismiss   — when present, shows an X to clear the error.
 *   compact     — tighter padding/text sizes; for table-row error pills.
 */
export default function ErrorMessage({ error, prefix, fallback = "Operation failed", onDismiss, compact = false }) {
    if (!error) return null;
    const { title, detail, code } = typeof error === "object" && error !== null && "title" in error && "message" in error
        ? error  // already an extracted shape
        : extractQbError(error, fallback);

    const padCls = compact ? "px-2 py-1.5" : "px-3 py-2";
    const baseText = compact ? "text-[11px]" : "text-xs";
    const titleText = compact ? "text-[11px]" : "text-xs";
    return (
        <div className={`flex items-start gap-2 bg-gilt-card border border-gilt-debit/40 text-gilt-debit ${padCls} rounded-lg ${baseText}`}>
            <AlertTriangle className={`flex-shrink-0 mt-0.5 text-gilt-debit ${compact ? "h-3 w-3" : "h-3.5 w-3.5"}`} />
            <div className="flex-1 min-w-0 space-y-0.5">
                {prefix && <div className={`font-semibold text-gilt-debit ${titleText}`}>{prefix}</div>}
                <div className={`font-medium break-words ${titleText}`}>{title}</div>
                {detail && <div className={`break-words text-gilt-debit/80 ${compact ? "text-[10px]" : "text-[11px]"}`}>{detail}</div>}
                {code && <div className={`font-mono text-gilt-debit/80 ${compact ? "text-[9px]" : "text-[10px]"}`}>code: {code}</div>}
            </div>
            {onDismiss && (
                <button onClick={onDismiss} className="text-gilt-debit/70 hover:text-gilt-debit transition-colors duration-150 flex-shrink-0" title="Dismiss">
                    <X className={compact ? "h-3 w-3" : "h-3.5 w-3.5"} />
                </button>
            )}
        </div>
    );
}
