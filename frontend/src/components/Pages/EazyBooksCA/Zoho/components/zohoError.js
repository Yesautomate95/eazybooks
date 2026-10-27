/**
 * Centralized error extraction for the Zoho sales / purchase / bank flows — the
 * Zoho twin of QB/components/qboError.js. Backend surfaces errors in several shapes:
 *
 *   - HTTPException(detail="string")              → { detail: "..." }
 *   - HTTPException(detail={message, error})      → { detail: { message, error } }
 *   - Zoho passthrough (zoho_connector._request)  → { detail: { zoho_error, zoho_code } }
 *   - Network / unhandled                         → axios Error with .message only
 *
 * extractZohoError flattens these into {title, detail, code, message}.
 */

export function extractZohoError(err, fallback = "Operation failed") {
    if (err == null) {
        return { title: fallback, detail: null, code: null, message: fallback };
    }
    if (typeof err === "string") {
        return { title: err, detail: null, code: null, message: err };
    }

    const data = err?.response?.data;
    const detail = data?.detail;

    let title = null;
    let detailText = null;
    let code = null;

    if (typeof detail === "string") {
        title = detail;
    } else if (detail && typeof detail === "object") {
        // Zoho's own message is the most specific (e.g. "The account name already
        // exists"), so it wins over our generic wrapper message.
        title = detail.zoho_error || detail.message || detail.error || null;
        detailText = detail.zoho_detail || detail.detail || null;
        code = detail.zoho_code || detail.code || null;
    } else if (typeof data === "string") {
        title = data;
    }

    if (!title) {
        title = err?.message || fallback;
    }

    const detailToShow = detailText && detailText.trim() !== title.trim() ? detailText : null;

    let message = title;
    if (detailToShow) message += ` — ${detailToShow}`;
    if (code) message += ` (code: ${code})`;

    return { title, detail: detailToShow, code, message };
}

/** Convenience: only the single-string form, for non-structured surfaces. */
export function formatZohoError(err, fallback) {
    return extractZohoError(err, fallback).message;
}
