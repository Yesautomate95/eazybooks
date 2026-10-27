import React from "react";
import { Navigate } from "react-router-dom";
import { useWorkspace } from "../../../context/WorkspaceContext";
import { PLATFORM_BY_KEY } from "../../../../config/platforms";

/**
 * The /dashboard landing decision (post-login entry point — all auth flows
 * navigate to /dashboard).
 *
 *   - Returning user with a remembered workspace → straight into it.
 *   - First-time / no active platform              → the "choose your ledger"
 *                                                     picker.
 *
 * Rendered as the index route inside the EazyBooksCA shell; it immediately
 * redirects, so the shell never really paints here.
 */
export default function WorkspaceGate() {
  const { activePlatform } = useWorkspace();
  const platform = activePlatform ? PLATFORM_BY_KEY[activePlatform] : null;

  if (platform) {
    const dest = platform.nav?.[0]?.path || `/dashboard/${platform.routeSlug}`;
    return <Navigate to={dest} replace />;
  }
  return <Navigate to="/dashboard/select" replace />;
}
