import React from "react";
import { Outlet, useLocation, useNavigate } from "react-router-dom";
import {
  Settings as SettingsIcon,
  User,
  Link2,
  LucideUserCog,
  LucideShieldCheck,
  LucideKeyRound,
  LucideActivity,
} from "lucide-react";
import { useRbac } from "../../../rbac";
import { cn } from "../../../../lib/utils";

/**
 * Settings — the account-level area that lives OUTSIDE the platform workspace
 * context (these settings aren't per-ledger). Left rail groups by concern:
 *   Account · Connections · Team (RBAC).
 *
 * The content column hosts the panels (Account, Connections) and the RBAC admin
 * pages (now folded under Team) via the route Outlet from App.js.
 */
const PRIMARY = [
  { label: "Account", to: "/dashboard/settings/account", icon: User },
  { label: "Connections", to: "/dashboard/settings/connections", icon: Link2 },
];

const TEAM = [
  { label: "Users", to: "/dashboard/settings/team/users", icon: LucideUserCog, permission: "users.read" },
  { label: "Roles", to: "/dashboard/settings/team/roles", icon: LucideShieldCheck, permission: "roles.read" },
  { label: "Permissions", to: "/dashboard/settings/team/permissions", icon: LucideKeyRound, permission: "permissions.read" },
  { label: "Audit log", to: "/dashboard/settings/team/audit", icon: LucideActivity, permission: "audit.read" },
];

function RailItem({ item, active, onClick }) {
  const Icon = item.icon;
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "group relative flex h-9 w-full items-center gap-2.5 rounded-md px-2.5 text-sm transition-colors duration-150",
        active
          ? "bg-gilt-sunken font-medium text-gilt-ink"
          : "text-gilt-soft hover:bg-gilt-sunken hover:text-gilt-ink"
      )}
    >
      {active && (
        <span className="absolute left-0 bottom-1.5 top-1.5 w-0.5 rounded-r-full bg-gilt-brass" />
      )}
      <Icon
        size={16}
        strokeWidth={active ? 2.25 : 2}
        className={cn("shrink-0", active ? "text-gilt-brass" : "text-gilt-faint group-hover:text-gilt-soft")}
      />
      <span className="truncate">{item.label}</span>
    </button>
  );
}

export default function SettingsLayout() {
  const navigate = useNavigate();
  const location = useLocation();
  const { hasPermission, loading: rbacLoading } = useRbac();

  const isActive = (to) => location.pathname === to || location.pathname.startsWith(to + "/");
  const teamItems = TEAM.filter(
    (i) => !i.permission || rbacLoading || hasPermission(i.permission)
  );

  return (
    <div className="flex min-h-screen flex-col md:flex-row">
      {/* Rail */}
      <nav className="shrink-0 border-b border-gilt-hairline p-3 md:w-60 md:border-b-0 md:border-r md:p-4">
        <div className="mb-3 flex items-center gap-2 px-1.5">
          <span className="inline-flex h-7 w-7 items-center justify-center rounded-lg border border-gilt-hairline bg-gilt-sunken text-gilt-brass">
            <SettingsIcon size={15} strokeWidth={2.25} />
          </span>
          <span className="gl-display text-[15px] text-gilt-ink">Settings</span>
        </div>

        <div className="space-y-0.5">
          {PRIMARY.map((item) => (
            <RailItem
              key={item.to}
              item={item}
              active={isActive(item.to)}
              onClick={() => navigate(item.to)}
            />
          ))}
        </div>

        {teamItems.length > 0 && (
          <div className="mt-4">
            <p className="mb-1.5 px-2 font-mono text-[10px] font-medium uppercase tracking-[0.14em] text-gilt-brass">
              Team
            </p>
            <div className="space-y-0.5">
              {teamItems.map((item) => (
                <RailItem
                  key={item.to}
                  item={item}
                  active={isActive(item.to)}
                  onClick={() => navigate(item.to)}
                />
              ))}
            </div>
          </div>
        )}
      </nav>

      {/* Content */}
      <div className="min-w-0 flex-1">
        <Outlet />
      </div>
    </div>
  );
}
