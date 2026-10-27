/**
 * Canonical platform registry — the single source of truth for the five
 * accounting workspaces (Tally, QuickBooks, Zoho, Xero, SAP).
 *
 * Why this exists: the same platform was historically referred to by three
 * different identifiers — most painfully QuickBooks, which is the route slug
 * `qb`, the backend connection id `quickbooks`, and the OAuth return param
 * `qbo`. Every consumer (sidebar, picker, switcher, analytics, OAuth landing)
 * now reads those mappings from here instead of hard-coding its own.
 *
 * Field reference:
 *   key          stable internal id (use this in app state / localStorage)
 *   routeSlug    first path segment under /dashboard (e.g. "qb")
 *   connectionId key used by dashboardApi.getConnections() (e.g. "quickbooks")
 *   oauthParam   ?<param>=connected query key on the post-OAuth landing
 *   authMethod   "oauth2" | "local-bridge"
 *   permission   RBAC permission gating the whole workspace
 *   label        full display name
 *   shortLabel   compact name for the switcher chip
 *   nav          contextual sidebar items for this workspace (in order)
 */

import {
  LucideCalculator,
  LucideBriefcase,
  LucideMessageSquare,
  LucideFileText,
} from "lucide-react";

export const PLATFORMS = [
  {
    key: "tally",
    routeSlug: "tally",
    connectionId: "tally",
    oauthParam: null, // local bridge — no cloud OAuth round-trip
    authMethod: "local-bridge",
    permission: "tally.read",
    label: "Tally Prime",
    shortLabel: "Tally",
    nav: [
      { label: "Tally", path: "/dashboard/tally", icon: LucideCalculator, permission: "tally.read" },
      { label: "Tally Chat", path: "/dashboard/tally-chat", icon: LucideMessageSquare, permission: "chat.use" },
    ],
  },
  {
    key: "quickbooks",
    routeSlug: "qb",
    connectionId: "quickbooks",
    oauthParam: "qbo",
    authMethod: "oauth2",
    permission: "quickbooks.read",
    label: "QuickBooks Online",
    shortLabel: "QuickBooks",
    // Drafts are embedded in the QB dashboard (Tally-parity), so no Drafts nav item.
    nav: [
      { label: "QuickBooks", path: "/dashboard/qb", icon: LucideBriefcase, permission: "quickbooks.read" },
      { label: "QuickBooks Chat", path: "/dashboard/qb-chat", icon: LucideMessageSquare, permission: "chat.use" },
    ],
  },
  {
    key: "zoho",
    routeSlug: "zoho",
    connectionId: "zoho",
    oauthParam: "zoho",
    authMethod: "oauth2",
    permission: "zoho.read",
    label: "Zoho Books",
    shortLabel: "Zoho",
    nav: [
      { label: "Zoho Books", path: "/dashboard/zoho", icon: LucideBriefcase, permission: "zoho.read" },
      { label: "Zoho Drafts", path: "/dashboard/zoho/drafts", icon: LucideFileText, permission: "zoho.read" },
      { label: "Zoho Chat", path: "/dashboard/zoho-chat", icon: LucideMessageSquare, permission: "chat.use" },
    ],
  },
  {
    key: "xero",
    routeSlug: "xero",
    connectionId: "xero",
    oauthParam: "xero",
    authMethod: "oauth2",
    permission: "xero.read",
    label: "Xero",
    shortLabel: "Xero",
    nav: [
      { label: "Xero", path: "/dashboard/xero", icon: LucideBriefcase, permission: "xero.read" },
      { label: "Xero Drafts", path: "/dashboard/xero/drafts", icon: LucideFileText, permission: "xero.read" },
      { label: "Xero Chat", path: "/dashboard/xero-chat", icon: LucideMessageSquare, permission: "chat.use" },
    ],
  },
  {
    key: "sap",
    routeSlug: "sap",
    connectionId: "sap",
    oauthParam: "sap",
    authMethod: "oauth2",
    permission: "sap.read",
    label: "SAP S/4HANA",
    shortLabel: "SAP",
    nav: [
      { label: "SAP S/4HANA", path: "/dashboard/sap", icon: LucideBriefcase, permission: "sap.read" },
      { label: "SAP Drafts", path: "/dashboard/sap/drafts", icon: LucideFileText, permission: "sap.read" },
      { label: "SAP Chat", path: "/dashboard/sap-chat", icon: LucideMessageSquare, permission: "chat.use" },
    ],
  },
];

export const PLATFORM_KEYS = PLATFORMS.map((p) => p.key);
export const PLATFORM_BY_KEY = Object.fromEntries(PLATFORMS.map((p) => [p.key, p]));
export const PLATFORM_BY_SLUG = Object.fromEntries(PLATFORMS.map((p) => [p.routeSlug, p]));
export const PLATFORM_BY_CONNECTION_ID = Object.fromEntries(
  PLATFORMS.map((p) => [p.connectionId, p])
);

/**
 * Resolve the active platform key from a /dashboard/... pathname.
 *
 * Handles the three URL shapes a platform can appear in:
 *   /dashboard/<slug>            → workspace          (e.g. /dashboard/qb)
 *   /dashboard/<slug>/<sub>      → sub-view           (e.g. /dashboard/zoho/drafts)
 *   /dashboard/<slug>-chat       → sibling chat route (e.g. /dashboard/tally-chat)
 *
 * Returns null for non-platform routes (/analytics, /admin, /select, index).
 */
export function platformFromPath(pathname) {
  const match = /^\/dashboard\/([^/]+)/.exec(pathname || "");
  if (!match) return null;
  // Chat lives at /dashboard/<slug>-chat, so fold "<slug>-chat" back to "<slug>".
  const slug = match[1].replace(/-chat$/, "");
  const platform = PLATFORM_BY_SLUG[slug];
  return platform ? platform.key : null;
}
