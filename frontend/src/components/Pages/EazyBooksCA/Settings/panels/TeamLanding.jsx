import React from "react";
import { Navigate } from "react-router-dom";
import { ShieldOff } from "lucide-react";
import { useRbac } from "../../../../rbac";
import { FullPageSpinner } from "../../../../ui/spinner";

/**
 * /dashboard/settings/team landing — sends the user to the first Team page they
 * can actually access, so someone with only audit.read doesn't bounce off a
 * guarded Users page into /403.
 */
const TEAM_ROUTES = [
  { to: "/dashboard/settings/team/overview", permission: "data.read" },
  { to: "/dashboard/settings/team/users", permission: "users.read" },
  { to: "/dashboard/settings/team/roles", permission: "roles.read" },
  { to: "/dashboard/settings/team/permissions", permission: "permissions.read" },
  { to: "/dashboard/settings/team/audit", permission: "audit.read" },
];

export default function TeamLanding() {
  const { hasPermission, loading } = useRbac();

  if (loading) return <FullPageSpinner label="Checking your permissions…" />;

  const first = TEAM_ROUTES.find((r) => hasPermission(r.permission));
  if (first) return <Navigate to={first.to} replace />;

  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center px-6 text-center">
      <span className="mb-3 inline-flex h-11 w-11 items-center justify-center rounded-full border border-gilt-hairline bg-gilt-sunken text-gilt-faint">
        <ShieldOff size={20} />
      </span>
      <p className="text-sm font-semibold text-gilt-ink">No team access</p>
      <p className="mt-1 max-w-sm text-xs text-gilt-soft">
        You don't have permission to view team management. Ask an administrator if you need access.
      </p>
    </div>
  );
}
