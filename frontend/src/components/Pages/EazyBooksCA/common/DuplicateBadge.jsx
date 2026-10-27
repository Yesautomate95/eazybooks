/**
 * DuplicateBadge — surface a duplicate-detection verdict from the backend.
 *
 * Props:
 *   check: {
 *     is_duplicate: bool,
 *     confidence: number (1.0 / 0.7 / 0.4 / 0.0),
 *     source: "local" | "qbo" | "none" | "skipped",
 *     reason: string,
 *     matches: [{ qbo_entity_type, qbo_id, doc_number, date, amount, party_name }],
 *   }
 *   size: "sm" | "md"
 *
 * Three visual tiers driven by confidence:
 *   ≥ 0.9  → red "Duplicate" (definite)
 *   ≥ 0.6  → amber "Likely duplicate" (strong)
 *   > 0    → amber-light "Possible duplicate"
 *   0      → nothing
 */

import React from "react";
import { Tooltip } from "@mui/material";
import { Copy, AlertTriangle } from "lucide-react";

function fmtMatches(matches = []) {
    if (!matches.length) return "";
    return matches.slice(0, 3).map((m) => {
        const parts = [m.qbo_entity_type, m.doc_number ? `#${m.doc_number}` : null, m.date, m.amount != null ? Number(m.amount).toFixed(2) : null, m.party_name];
        return parts.filter(Boolean).join(" · ");
    }).join("\n");
}

const DuplicateBadge = ({ check, size = "sm" }) => {
    if (!check || !check.is_duplicate) return null;
    const conf = Number(check.confidence) || 0;

    const tier =
        conf >= 0.9 ? "definite"
        : conf >= 0.6 ? "strong"
        : "possible";

    const tone = tier === "definite"
        ? "bg-transparent border-gilt-debit/40 text-gilt-debit"
        : tier === "strong"
            ? "bg-gilt-sunken border-gilt-brass/40 text-gilt-brass"
            : "bg-transparent border-gilt-brass/40 text-gilt-brass";

    const label = tier === "definite" ? "Duplicate"
        : tier === "strong" ? "Likely duplicate"
        : "Possible duplicate";

    const pad = size === "md" ? "px-2 py-0.5 text-xs" : "px-1.5 py-0.5 text-[10px]";

    const matchesText = fmtMatches(check.matches);
    const title = [
        check.reason || "Duplicate detected",
        check.source ? `Source: ${check.source}` : null,
        matchesText ? `\n${matchesText}` : null,
    ].filter(Boolean).join("\n");

    const Icon = tier === "definite" ? AlertTriangle : Copy;

    return (
        <Tooltip title={<pre className="whitespace-pre-wrap text-[11px]">{title}</pre>} arrow placement="top">
            <span className={`inline-flex items-center gap-1 font-mono uppercase tracking-[0.14em] rounded-full border ${pad} ${tone}`}>
                <Icon className={size === "md" ? "h-3 w-3" : "h-2.5 w-2.5"} />
                {label}
            </span>
        </Tooltip>
    );
};

export default DuplicateBadge;
