import React, { useState, useContext, useMemo } from "react";
import {
  LucideChevronLeft,
  LucideMenu,
  LucideX,
  LucideLogOut,
  LayoutDashboard,
  LucideSettings,
} from "lucide-react";
import { useNavigate, useLocation } from "react-router-dom";
import { EazyBooksContext } from "../../context/EazyBooksContext";
import { logout as fullLogout } from "../../../services/auth";
import { useWorkspace } from "../../context/WorkspaceContext";
import { PLATFORM_BY_KEY, platformFromPath } from "../../../config/platforms";
import { cn } from "../../../lib/utils";
import CoinMark from "../Landing/gilt/CoinMark";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogBody,
  DialogFooter,
  DialogTitle,
  DialogDescription,
} from "../../ui/dialog";
import { Button } from "../../ui/button";
import { PressTheSeal } from "../../ui/press-the-seal";
import { useRbac } from "../../rbac";
import PlatformSwitcher from "./Workspace/PlatformSwitcher";

// The contextual sidebar shows only the ACTIVE platform's routes (from the
// platform registry) — far fewer index cuts than the old flat 13-item list.
// Analytics and Settings are app-level rails that sit below / pinned.
function NavButton({ item, active, isCollapsed, onClick }) {
  const Icon = item.icon;
  return (
    <button
      type="button"
      onClick={onClick}
      title={isCollapsed ? item.name : undefined}
      className={cn(
        "group relative flex h-9 w-full items-center rounded-md text-sm transition-colors duration-150",
        isCollapsed ? "justify-center px-0" : "gap-2.5 px-2.5",
        active
          ? "bg-gilt-sunken font-medium text-gilt-ink"
          : "text-gilt-soft hover:bg-gilt-sunken hover:text-gilt-ink"
      )}
    >
      {active && !isCollapsed && (
        <span className="absolute left-0 bottom-1.5 top-1.5 w-0.5 rounded-r-full bg-gilt-brass" />
      )}
      <Icon
        size={17}
        strokeWidth={active ? 2.25 : 2}
        className={cn(
          "shrink-0",
          active ? "text-gilt-brass" : "text-gilt-faint group-hover:text-gilt-soft"
        )}
      />
      {!isCollapsed && <span className="truncate">{item.name}</span>}
    </button>
  );
}

function Sidebar({ setSelectedComponent }) {
  const [isCollapsed, setIsCollapsed] = useState(false);
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);
  const { user } = useContext(EazyBooksContext);
  const { activePlatform } = useWorkspace();
  const { hasPermission, loading: rbacLoading } = useRbac();
  const navigate = useNavigate();
  const location = useLocation();
  const [isLoggingOut, setIsLoggingOut] = useState(false);
  const [showLogoutConfirm, setShowLogoutConfirm] = useState(false);

  // Contextual routes for the active workspace, permission-filtered (RBAC
  // loading is treated as "show it" so we don't flicker the spine empty).
  const routePlatformKey = platformFromPath(location.pathname);
  const resolvedPlatformKey =
    (activePlatform && PLATFORM_BY_KEY[activePlatform] ? activePlatform : null) ||
    routePlatformKey;
  const platform = resolvedPlatformKey ? PLATFORM_BY_KEY[resolvedPlatformKey] : null;
  const contextualItems = useMemo(
    () =>
      (platform?.nav || [])
        .filter((item) => !item.permission || rbacLoading || hasPermission(item.permission))
        .map((item) => ({ name: item.label, path: item.path, icon: item.icon })),
    [platform, hasPermission, rbacLoading]
  );

  // Per-platform Analytics (scoping to the active platform lands in the next
  // phase; the rail lives here now).
  const analyticsItem = { name: "Analytics", path: "/dashboard/analytics", icon: LayoutDashboard };
  const settingsItem = { name: "Settings", path: "/dashboard/settings", icon: LucideSettings };

  // Highlight the single most-specific matching route, so a workspace sub-view
  // (e.g. /dashboard/zoho/sales) lights its parent while /dashboard/zoho/drafts
  // lights Drafts — not both.
  const matchables = [...contextualItems, analyticsItem, settingsItem];
  const activePath = matchables
    .map((i) => i.path)
    .filter((p) => location.pathname === p || location.pathname.startsWith(p + "/"))
    .sort((a, b) => b.length - a.length)[0];

  const name = localStorage.getItem("username");
  const email = localStorage.getItem("email");

  const toggleMobileMenu = () => setIsMobileMenuOpen((v) => !v);

  const handleNavigate = (item) => {
    if (setSelectedComponent) {
      setSelectedComponent(item.name);
      localStorage.setItem("selectedComponent", item.name);
    }
    setIsMobileMenuOpen(false);
    navigate(item.path);
  };

  const handleLogoutConfirm = async () => {
    setIsLoggingOut(true);
    await new Promise((resolve) => setTimeout(resolve, 700));
    localStorage.clear();
    sessionStorage.clear();
    document.cookie.split(";").forEach((c) => {
      document.cookie = c
        .replace(/^ +/, "")
        .replace(/=.*/, "=;expires=" + new Date().toUTCString() + ";path=/");
    });
    setIsLoggingOut(false);
    setShowLogoutConfirm(false);
    // Full reload so the in-memory auth state and Firebase session are reset too;
    // a client-side navigate("/") left isAuthenticated true and bounced back to /dashboard.
    fullLogout();
  };

  const userInitial = (name || "A").trim().charAt(0).toUpperCase();

  return (
    <>
      {/* Mobile Menu Button */}
      <button
        onClick={toggleMobileMenu}
        className="md:hidden fixed top-3 right-3 z-50 p-2 rounded-lg bg-gilt-card border border-gilt-hairline hover:bg-gilt-sunken transition-colors duration-150"
        aria-label="Toggle menu"
      >
        {isMobileMenuOpen ? (
          <LucideX size={18} className="text-gilt-soft" />
        ) : (
          <LucideMenu size={18} className="text-gilt-soft" />
        )}
      </button>

      {/* Sidebar Container — the ledger spine */}
      <aside
        className={cn(
          "gl-gilt-spine min-h-screen bg-gilt-card fixed md:relative z-40",
          "transition-transform duration-300 ease-out",
          isMobileMenuOpen ? "translate-x-0" : "-translate-x-full md:translate-x-0"
        )}
      >
        <div
          className={cn(
            "h-screen flex flex-col transition-[width] duration-300 ease-out",
            isCollapsed ? "w-[68px]" : "w-[244px]"
          )}
        >
          {/* Brand + Collapse */}
          <div className="h-14 flex items-center justify-between px-3 border-b border-gilt-hairline">
            <div
              className={cn(
                "flex items-center gap-2 overflow-hidden transition-all duration-200",
                isCollapsed ? "w-0 opacity-0" : "w-auto opacity-100"
              )}
            >
              <CoinMark size={22} className="shrink-0 text-gilt-gold" />
              <span className="gl-display text-[17px] leading-none text-gilt-ink">EazyBooks</span>
            </div>
            {isCollapsed && <CoinMark size={22} className="mx-auto shrink-0 text-gilt-gold" />}
            <button
              onClick={() => setIsCollapsed((v) => !v)}
              className={cn(
                "shrink-0 h-7 w-7 inline-flex items-center justify-center rounded-md text-gilt-faint hover:bg-gilt-sunken hover:text-gilt-ink transition-colors duration-150",
                isCollapsed && "hidden"
              )}
              aria-label="Collapse sidebar"
            >
              <LucideChevronLeft size={14} />
            </button>
          </div>

          {/* Re-expand control when collapsed */}
          {isCollapsed && (
            <button
              onClick={() => setIsCollapsed(false)}
              className="mx-auto mt-2 h-7 w-7 inline-flex items-center justify-center rounded-md text-gilt-faint hover:bg-gilt-sunken hover:text-gilt-ink transition-colors duration-150"
              aria-label="Expand sidebar"
            >
              <LucideChevronLeft size={14} className="rotate-180" />
            </button>
          )}

          {/* Active-platform switcher — the ledger book */}
          <PlatformSwitcher isCollapsed={isCollapsed} />

          {/* Contextual nav for the active workspace */}
          <nav className="flex-1 overflow-y-auto px-2 pt-3 pb-3">
            {contextualItems.length > 0 && (
              <div className="mb-4">
                {!isCollapsed && (
                  <p className="px-2 mb-1.5 flex items-center gap-2">
                    <span className="font-mono text-[10px] font-medium uppercase tracking-[0.14em] text-gilt-brass">
                      {platform?.shortLabel || "Workspace"}
                    </span>
                    <span aria-hidden="true" className="h-px flex-1 bg-gilt-hairline" />
                  </p>
                )}
                <ul className="space-y-0.5">
                  {contextualItems.map((item) => (
                    <li key={item.path}>
                      <NavButton
                        item={item}
                        active={item.path === activePath}
                        isCollapsed={isCollapsed}
                        onClick={() => handleNavigate(item)}
                      />
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {/* Insights */}
            <div className="mb-2">
              {!isCollapsed && (
                <p className="px-2 mb-1.5 flex items-center gap-2">
                  <span className="font-mono text-[10px] font-medium uppercase tracking-[0.14em] text-gilt-brass">
                    Insights
                  </span>
                  <span aria-hidden="true" className="h-px flex-1 bg-gilt-hairline" />
                </p>
              )}
              <ul className="space-y-0.5">
                <li>
                  <NavButton
                    item={analyticsItem}
                    active={analyticsItem.path === activePath}
                    isCollapsed={isCollapsed}
                    onClick={() => handleNavigate(analyticsItem)}
                  />
                </li>
              </ul>
            </div>
          </nav>

          {/* Pinned bottom: Settings · account (theme toggle now lives in
              Settings → Account) */}
          <div className="border-t border-gilt-hairline p-2 space-y-0.5">
            <NavButton
              item={settingsItem}
              active={settingsItem.path === activePath}
              isCollapsed={isCollapsed}
              onClick={() => handleNavigate(settingsItem)}
            />

            <button
              onClick={() => setShowLogoutConfirm(true)}
              className={cn(
                "group w-full flex items-center gap-2.5 rounded-md p-2 hover:bg-gilt-sunken transition-colors duration-150",
                isCollapsed && "justify-center"
              )}
              title={isCollapsed ? `Sign out (${name || "Account"})` : undefined}
            >
              <div className="gl-display shrink-0 h-8 w-8 rounded-full bg-gilt-sunken text-gilt-brass flex items-center justify-center text-sm border border-gilt-hairline">
                {userInitial}
              </div>
              {!isCollapsed && (
                <>
                  <div className="min-w-0 flex-1 text-left">
                    <p className="text-[13px] font-medium text-gilt-ink truncate leading-tight">
                      {name || "Anonymous"}
                    </p>
                    <p className="text-[11px] text-gilt-faint truncate leading-tight">
                      {email || "user@example.com"}
                    </p>
                  </div>
                  <LucideLogOut
                    size={14}
                    className="text-gilt-faint group-hover:text-gilt-ink transition-colors duration-150 shrink-0"
                  />
                </>
              )}
            </button>
          </div>
        </div>
      </aside>

      {/* Mobile overlay */}
      {isMobileMenuOpen && (
        <div
          className="fixed inset-0 bg-gilt-ink/30 z-30 md:hidden animate-fade-in"
          onClick={() => setIsMobileMenuOpen(false)}
        />
      )}

      {/* Logout confirmation dialog */}
      <Dialog
        open={showLogoutConfirm}
        onOpenChange={(open) => {
          if (!isLoggingOut) setShowLogoutConfirm(open);
        }}
      >
        <DialogContent size="sm" showClose={!isLoggingOut}>
          <DialogHeader>
            <div className="mb-2.5 inline-flex h-10 w-10 items-center justify-center rounded-full border border-gilt-hairline bg-gilt-sunken text-gilt-brass">
              <LucideLogOut size={18} />
            </div>
            <DialogTitle className="gl-display text-xl">Sign out of EazyBooks?</DialogTitle>
            <DialogDescription>
              You'll be returned to the homepage. Any unsaved drafts will remain in your locker.
            </DialogDescription>
          </DialogHeader>
          <DialogBody />
          <DialogFooter>
            <Button
              variant="secondary"
              onClick={() => setShowLogoutConfirm(false)}
              disabled={isLoggingOut}
            >
              Cancel
            </Button>
            {isLoggingOut ? (
              <Button variant="secondary" loading disabled>
                Signing out…
              </Button>
            ) : (
              <PressTheSeal
                label="Hold to sign out"
                holdingLabel="Signing out…"
                onConfirm={handleLogoutConfirm}
              />
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

export default Sidebar;
