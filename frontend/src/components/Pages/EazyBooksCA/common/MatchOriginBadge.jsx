/**
 * MatchOriginBadge — small inline pill that surfaces *why* a match was made.
 *
 * Sits next to ConfidenceBadge in QB views (QBBankEntriesView,
 * QBPurchaseView). Currently distinguishes "history" hits (prior
 * vendor→account memory) from regular fuzzy/llm matches; future variants
 * (rule, gstin) can be added here.
 *
 * Props:
 *   match: { method?: "exact"|"email"|"fuzzy"|"llm"|"history"|"new",
 *            usage_count?: number }
 */

import React from "react";
import { Tooltip } from "@mui/material";
import { History } from "lucide-react";

const MatchOriginBadge = ({ match }) => {
    if (!match || match.method !== "history") return null;
    const uses = match.usage_count;
    const title = uses
        ? `Auto-filled from prior usage (booked this way ${uses}×). Change the dropdown to override.`
        : "Auto-filled from prior usage. Change the dropdown to override.";
    return (
        <Tooltip title={title} arrow placement="top">
            <span className="inline-flex items-center gap-1 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-[0.14em] tabular-nums bg-transparent text-gilt-soft rounded-full border border-gilt-hairline">
                <History className="h-2.5 w-2.5" />
                history{uses ? ` ${uses}×` : ""}
            </span>
        </Tooltip>
    );
};

export default MatchOriginBadge;
