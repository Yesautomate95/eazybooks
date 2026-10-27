/**
 * Centralized error extraction for the QB sales / purchase / bank-statement
 * flows. Backend surfaces errors in several shapes:
 *
 *   - HTTPException(detail="string")            → { detail: "..." }
 *   - HTTPException(detail={message, error})    → { detail: { message, error } }
 *   - QBO passthrough (qbo_connector._request)  → { detail: { qbo_error, qbo_detail, qbo_code } }
 *   - Network / unhandled                       → axios Error with .message only
 *
 * extractQbError flattens these into {title, detail, code, message}:
 *   - `title`   — the headline (QBO "Message", or our own message, or err.message)
 *   - `detail`  — QBO's "Detail" (often the specific reason a write was rejected)
 *   - `code`    — QBO error code, when present
 *   - `message` — the same info joined into a single string for inline displays
 *
 * The richer surfaces (modals, push bars, top banner) render the structured
 * shape so the user sees the WHY beneath the WHAT.
 */

export function extractQbError(err, fallback = "Operation failed") {
    if (err == null) {
        return { title: fallback, detail: null, code: null, message: fallback };
    }

    // Plain string passed in directly.
    if (typeof err === "string") {
        return { title: err, detail: null, code: null, message: err };
    }

    const data = err?.response?.data;
    const detail = data?.detail;

    if (!err?.response && (err?.code === "ERR_NETWORK" || err?.message === "Network Error")) {
        const message = "QuickBooks Online is unreachable. Check your network connection and try again.";
        return { title: message, detail: null, code: null, message };
    }

    let title = null;
    let detailText = null;
    let code = null;

    if (typeof detail === "string") {
        title = detail;
    } else if (detail && typeof detail === "object") {
        // QBO's own Message is the most specific (e.g. "Business Validation
        // Error: DisplayName already in use"), so it wins over our generic
        // wrapper message.
        title = detail.qbo_error || detail.message || detail.error || null;
        detailText = detail.qbo_detail || detail.detail || null;
        code = detail.qbo_code || detail.code || null;
    } else if (typeof data === "string") {
        // Backend may return a raw string body for some errors (e.g. 502).
        title = data;
    }

    if (!title) {
        title = err?.message || fallback;
    }

    // Don't echo the same string twice if QBO repeated the message in Detail.
    const detailToShow = detailText && detailText.trim() !== title.trim() ? detailText : null;

    let message = title;
    if (detailToShow) message += ` — ${detailToShow}`;
    if (code) message += ` (code: ${code})`;

    return { title, detail: detailToShow, code, message };
}

/** Convenience: only the single-string form, for non-structured surfaces. */
export function formatQbError(err, fallback) {
    return extractQbError(err, fallback).message;
}
