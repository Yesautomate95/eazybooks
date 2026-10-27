/**
 * Route-level authorization primitive.
 *
 * Wrap a Route element to require a permission before rendering. While the
 * permission set is still loading we render a light placeholder rather than
 * redirecting — otherwise a refresh would briefly bounce the user to /403.
 *
 * Usage:
 *   <Route element={<RouteGuard permission="users.read" />}>
 *     <Route path="/admin/users" element={<UsersPage />} />
 *   </Route>
 */
import React from "react";
import { Navigate, Outlet, useLocation } from "react-router-dom";
import { useRbac } from "./RbacContext";
import { FullPageSpinner } from "../ui/spinner";

export function RouteGuard({
  permission,
  anyOf,
  allOf,
  redirectTo = "/dashboard/403",
  children,
}) {
  const { hasPermission, hasAny, hasAll, loading } = useRbac();
  const location = useLocation();

  if (loading) {
    return <FullPageSpinner label="Checking your permissions…" />;
  }

  let allowed = true;
  if (permission) allowed = allowed && hasPermission(permission);
  if (anyOf && anyOf.length) allowed = allowed && hasAny(anyOf);
  if (allOf && allOf.length) allowed = allowed && hasAll(allOf);

  if (!allowed) {
    // Preserve the attempted location so a higher-permission user reloading
    // /admin/users gets the page instead of being stuck on /403.
    return <Navigate to={redirectTo} state={{ from: location }} replace />;
  }

  return children ? <>{children}</> : <Outlet />;
}

export default RouteGuard;
