/**
 * Component-level authorization primitive.
 *
 * Usage:
 *   <PermissionGuard permission="users.create">
 *     <CreateUserButton />
 *   </PermissionGuard>
 *
 *   <PermissionGuard anyOf={["roles.update","roles.create"]} fallback={<HiddenHint />}>
 *     <RoleEditor />
 *   </PermissionGuard>
 *
 *   <PermissionGuard allOf={["reports.read","reports.export"]}>
 *     <ExportButton />
 *   </PermissionGuard>
 *
 * Renders nothing (or `fallback`) when the user lacks the required permission.
 *
 * Why not just hide via CSS:
 *   - Removing from the DOM prevents accidental clicks (e.g. via keyboard nav)
 *   - Frees the component from rendering data the user can't see
 */
import React from "react";
import { useRbac } from "./RbacContext";

export function PermissionGuard({
  permission,
  anyOf,
  allOf,
  fallback = null,
  children,
}) {
  const { hasPermission, hasAny, hasAll, loading } = useRbac();

  // While we're still resolving the user's permissions, render nothing
  // rather than briefly flashing the protected content.
  if (loading) return null;

  let allowed = true;
  if (permission) allowed = allowed && hasPermission(permission);
  if (anyOf && anyOf.length) allowed = allowed && hasAny(anyOf);
  if (allOf && allOf.length) allowed = allowed && hasAll(allOf);

  return allowed ? <>{children}</> : <>{fallback}</>;
}

export default PermissionGuard;
