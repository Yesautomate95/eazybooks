/**
 * Dashboard service layer.
 *
 * Talks to the backend's unified `/ca/dashboard/*` endpoints. All connection
 * state — OAuth tokens, last-synced timestamps — lives server-side. The
 * frontend never persists any of that itself.
 *
 *   GET    /ca/dashboard/connections     → unified status per platform
 *   GET    /ca/dashboard/summary         → aggregated KPIs, charts, lists
 *   POST   /ca/dashboard/disconnect/{p}  → revoke tokens
 *
 * OAuth handshakes are kicked off via the existing per-platform endpoints
 * (`qboOAuthApi.getAuthUrl()`, etc.) which return the provider consent URL
 * we redirect to. The redirect-back flow already exists for each platform.
 *
 * Tally is the odd one out — there is no cloud OAuth. Connecting Tally
 * means launching the desktop bridge, which is a user-side action; we
 * surface a clear hint and reuse the existing connection probe.
 */

import {
  dashboardApi,
  qboOAuthApi,
  zohoOAuthApi,
  xeroOAuthApi,
  sapOAuthApi,
} from "../../../../../services/api";

// ── Platform registry (UI-only metadata) ──────────────────────────────────

export const PLATFORMS = [
  {
    id: "tally",
    name: "Tally Prime",
    /** Tally has no cloud OAuth — we connect through a local bridge agent. */
    authMethod: "local-bridge",
    accent: "#E72E2E",
    softAccent: "from-[#FFE5E5] to-[#FFF2F2]",
    description: "Local ERP via desktop bridge",
  },
  {
    id: "quickbooks",
    name: "QuickBooks",
    authMethod: "oauth2",
    accent: "#2CA01C",
    softAccent: "from-[#E6F6E1] to-[#F4FBF1]",
    description: "Intuit QuickBooks Online",
  },
  {
    id: "zoho",
    name: "Zoho Books",
    authMethod: "oauth2",
    accent: "#E42527",
    softAccent: "from-[#FFE9E9] to-[#FFF4F4]",
    description: "Zoho Finance Suite",
  },
  {
    id: "xero",
    name: "Xero",
    authMethod: "oauth2",
    accent: "#13B5EA",
    softAccent: "from-[#E0F5FD] to-[#F1FAFE]",
    description: "Xero accounting",
  },
  {
    id: "sap",
    name: "SAP S/4HANA",
    authMethod: "oauth2",
    accent: "#0FAAFF",
    softAccent: "from-[#E1F4FF] to-[#F1FAFE]",
    description: "SAP S/4HANA Cloud",
  },
];

export const PLATFORM_BY_ID = Object.fromEntries(PLATFORMS.map((p) => [p.id, p]));

// Maps platform id → the OAuth API object that owns its consent flow.
const OAUTH_APIS = {
  quickbooks: qboOAuthApi,
  zoho: zohoOAuthApi,
  xero: xeroOAuthApi,
  sap: sapOAuthApi,
};

// Each provider's OAuth callback redirects to a hardcoded per-platform page
// (configured server-side as QBO_FRONTEND_RETURN_URL etc). When the user
// kicked off the flow from /dashboard/analytics, we want them to land
// back here — not on /dashboard/qb. Stash the origin path before redirect;
// the per-platform dashboard reads it and bounces them.
export const OAUTH_RETURN_KEY = "dashboard:oauth-return";

// ── Connection status ─────────────────────────────────────────────────────

/**
 * Returns the connection status for every platform — { id: {status, lastSyncedAt, error} }.
 * Source of truth is the backend.
 */
export async function getConnectionStatus() {
  try {
    const data = await dashboardApi.getConnections();
    const out = {};
    for (const p of PLATFORMS) {
      out[p.id] = data?.[p.id] || { status: "disconnected", lastSyncedAt: null, error: null };
    }
    return out;
  } catch (err) {
    // Backend unreachable / endpoint missing / 5xx — surface as a `failed`
    // tile with the actual error so the user can see what's going wrong
    // (otherwise it looks identical to "the user never connected").
    const reason = err?.response?.data?.detail || err?.message || "Unable to reach backend";
    const status = err?.response?.status;
    const detail = status ? `${reason} (HTTP ${status})` : reason;
    const fallback = {};
    for (const p of PLATFORMS) {
      fallback[p.id] = { status: "failed", lastSyncedAt: null, error: detail };
    }
    return fallback;
  }
}

// ── Connect ───────────────────────────────────────────────────────────────

/**
 * Start the platform-specific connect flow.
 *
 *   - QuickBooks / Zoho / Xero  → fetch the provider consent URL and
 *     redirect the whole window to it. Backend handles the OAuth callback,
 *     stores tokens, and bounces back to the dashboard.
 *   - Tally                     → no cloud OAuth. Throw a clear message so
 *     the caller can show "launch the Tally desktop bridge" guidance.
 *
 * The `onStateChange` callback exists so the tile can flip into
 * "connecting…" while the redirect happens.
 */
export async function connectPlatform(platformId, { onStateChange } = {}) {
  const platform = PLATFORM_BY_ID[platformId];
  if (!platform) throw new Error(`Unknown platform: ${platformId}`);

  if (platformId === "tally") {
    onStateChange?.({ status: "failed", error: "Launch the Tally desktop bridge to connect." });
    throw new Error(
      "Tally connects through the EazyBooks desktop bridge. Open the desktop app and ensure Tally Prime is running."
    );
  }

  const oauth = OAUTH_APIS[platformId];
  if (!oauth) throw new Error(`No OAuth flow registered for ${platformId}`);

  onStateChange?.({ status: "connecting", lastSyncedAt: null, error: null });

  // Remember where we started so the per-platform landing page can
  // bounce us back here after the OAuth round-trip.
  try {
    sessionStorage.setItem(OAUTH_RETURN_KEY, window.location.pathname + window.location.search);
  } catch {
    // sessionStorage can be unavailable (Safari private mode, etc.) — non-fatal.
  }

  const res = await oauth.getAuthUrl();

  // Some platforms (e.g. SAP S/4HANA in client_credentials mode) connect
  // server-side without a user consent redirect — the connect call returns
  // { mode: 'client_credentials', connected: true } and we're done.
  if (res?.connected === true || res?.mode === "client_credentials") {
    onStateChange?.({ status: "connected", error: null });
    return { status: "connected" };
  }

  const url = res?.auth_url || res?.authUrl;
  if (!url) throw new Error("Backend did not return an OAuth URL");

  // Hand off to the provider — this navigates away from the SPA.
  window.location.href = url;
  // Resolve so the caller's `await` doesn't hang forever before the redirect.
  return { status: "connecting" };
}

// ── Disconnect / Sync ─────────────────────────────────────────────────────

export async function disconnectPlatform(platformId) {
  await dashboardApi.disconnect(platformId);
  return { status: "disconnected" };
}

/**
 * Force a re-sync. There's no separate "refetch from provider" endpoint
 * yet — the dashboard endpoint always returns fresh live data — so for
 * v1 we just re-fetch the status. The frontend's useSyncedData will then
 * invalidate and re-pull the summary on its next cycle (or sooner via
 * manual refresh).
 */
export async function triggerSync(platformId, { onStateChange } = {}) {
  onStateChange?.({ status: "syncing", progress: 50 });
  const all = await getConnectionStatus();
  const next = all[platformId] || { status: "disconnected", lastSyncedAt: null, error: null };
  onStateChange?.(next);
  return next;
}

// ── Dashboard data ────────────────────────────────────────────────────────

/**
 * Fetch the aggregated dashboard for the current user.
 *
 * `sources` is a list of platform ids to include — pass an empty array to
 * include every connected platform.
 *
 * Returns the shape:
 *   { generatedAt, sources[], kpis[], monthly[], expenseCategories[],
 *     incomeBreakdown[], topCustomers[], topVendors[], errors? }
 *
 * Empty when no platforms are connected.
 */
export async function getDashboardData({ range = "month", start, end, sources = [] } = {}) {
  try {
    return await dashboardApi.getSummary({ range, start, end, sources });
  } catch (err) {
    const detail = err?.response?.data?.detail || err?.message || "Dashboard fetch failed";
    const e = new Error(detail);
    e.cause = err;
    throw e;
  }
}
