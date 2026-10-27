/**
 * Safely turn an axios/fetch error into a human-readable string.
 *
 * The backend surfaces error bodies in several shapes. Critically, RBAC
 * denials return `detail` as an OBJECT, e.g.
 *   { code: "permission_denied", required: "quickbooks.read" }
 *   { code: "permission_denied", required_any: ["...", "..."] }
 *
 * Passing that object straight to setError() and rendering it as `{error}`
 * throws React error #31 ("Objects are not valid as a React child") and
 * white-screens the page. Every error surface must funnel through here so a
 * string — never an object — reaches the JSX.
 */
export function errorText(err, fallback = "Something went wrong") {
    if (err == null) return fallback;
    if (typeof err === "string") return err;

    const data = err?.response?.data;
    const detail = data?.detail;

    if (typeof detail === "string" && detail.trim()) return detail;

    if (detail && typeof detail === "object") {
        // RBAC permission denial — tell the user exactly what's missing so an
        // admin knows which permission to grant the role.
        if (detail.code === "permission_denied") {
            const needs =
                detail.required ||
                (Array.isArray(detail.required_any) && detail.required_any.join(" or ")) ||
                (Array.isArray(detail.required_all) && detail.required_all.join(" and "));
            return needs
                ? `You don't have permission for this (requires: ${needs}). Ask an admin to grant it to your role.`
                : "You don't have permission for this. Ask an admin to grant access to your role.";
        }
        const msg =
            detail.message || detail.error || detail.qbo_error || detail.detail;
        if (typeof msg === "string" && msg.trim()) return msg;
    }

    if (typeof data === "string" && data.trim()) return data;

    return err?.message || fallback;
}
