import React, { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ChevronsUpDown, Check, Plus, CheckCircle2, Circle } from "lucide-react";
import { PLATFORMS, PLATFORM_BY_KEY } from "../../../../config/platforms";
import { useWorkspace } from "../../../context/WorkspaceContext";
import { useRbac } from "../../../rbac";
import { cn } from "../../../../lib/utils";

/**
 * The active-platform switcher pinned at the top of the sidebar spine — the
 * "ledger book". Shows the current workspace; clicking opens a popover to
 * switch platforms (with live connected/not state) or to connect a new one
 * (→ the picker). Switching is SPA navigation — no reload.
 */
export default function PlatformSwitcher({ isCollapsed }) {
  const navigate = useNavigate();
  const { activePlatform, switchPlatform, isConnected } = useWorkspace();
  const { hasPermission, loading: rbacLoading } = useRbac();
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  // Dismiss on outside-click / Escape.
  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    };
    const onKey = (e) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const active = activePlatform ? PLATFORM_BY_KEY[activePlatform] : null;
  const options = PLATFORMS.filter(
    (p) => !p.permission || rbacLoading || hasPermission(p.permission)
  );

  const choose = (p) => {
    setOpen(false);
    switchPlatform(p.key, navigate);
  };
  const addNew = () => {
    setOpen(false);
    navigate("/dashboard/select");
  };

  return (
    <div ref={ref} className="relative px-2 pt-2">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        title={isCollapsed ? active?.label || "Select a ledger" : undefined}
        aria-haspopup="menu"
        aria-expanded={open}
        className={cn(
          "group flex w-full items-center rounded-lg border bg-gilt-card transition-colors duration-150",
          open ? "border-gilt-brass" : "border-gilt-hairline hover:border-gilt-brass",
          isCollapsed ? "justify-center p-1.5" : "gap-2.5 p-2"
        )}
      >
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-gilt-hairline bg-gilt-sunken font-mono text-sm font-bold text-gilt-brass">
          {active ? active.shortLabel.charAt(0) : "—"}
        </span>
        {!isCollapsed && (
          <>
            <span className="min-w-0 flex-1 text-left">
              <span className="block truncate text-[13px] font-semibold leading-tight text-gilt-ink">
                {active ? active.label : "Select a ledger"}
              </span>
              <span className="block font-mono text-[10px] uppercase leading-tight tracking-[0.12em] text-gilt-faint">
                {active
                  ? isConnected(active.key)
                    ? "Connected"
                    : "Not linked"
                  : "Choose workspace"}
              </span>
            </span>
            <ChevronsUpDown
              size={14}
              className="shrink-0 text-gilt-faint group-hover:text-gilt-soft"
            />
          </>
        )}
      </button>

      {open && (
        <div
          role="menu"
          className={cn(
            "absolute z-50 rounded-lg border border-gilt-hairline bg-gilt-card p-1.5 shadow-gl-lift",
            isCollapsed
              ? "left-full top-2 ml-2 w-56"
              : "left-2 right-2 mt-1.5"
          )}
        >
          <p className="px-2 pb-1.5 pt-1 font-mono text-[10px] uppercase tracking-[0.14em] text-gilt-brass">
            Ledgers
          </p>
          <ul className="space-y-0.5">
            {options.map((p) => {
              const isActiveOpt = p.key === activePlatform;
              const connected = isConnected(p.key);
              return (
                <li key={p.key}>
                  <button
                    type="button"
                    role="menuitem"
                    onClick={() => choose(p)}
                    className={cn(
                      "flex w-full items-center gap-2.5 rounded-md px-2 py-1.5 text-sm transition-colors duration-150",
                      isActiveOpt
                        ? "bg-gilt-sunken text-gilt-ink"
                        : "text-gilt-soft hover:bg-gilt-sunken hover:text-gilt-ink"
                    )}
                  >
                    <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-gilt-hairline bg-gilt-sunken font-mono text-xs font-bold text-gilt-brass">
                      {p.shortLabel.charAt(0)}
                    </span>
                    <span className="min-w-0 flex-1 truncate text-left">{p.label}</span>
                    {connected ? (
                      <CheckCircle2 size={13} className="shrink-0 text-gilt-credit" aria-label="Connected" />
                    ) : (
                      <Circle size={8} className="shrink-0 text-gilt-faint" aria-label="Not linked" />
                    )}
                    {isActiveOpt && <Check size={14} className="shrink-0 text-gilt-brass" />}
                  </button>
                </li>
              );
            })}
          </ul>
          <div className="my-1 h-px bg-gilt-hairline" />
          <button
            type="button"
            role="menuitem"
            onClick={addNew}
            className="flex w-full items-center gap-2.5 rounded-md px-2 py-1.5 text-sm text-gilt-soft transition-colors duration-150 hover:bg-gilt-sunken hover:text-gilt-brass"
          >
            <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-dashed border-gilt-hairline text-gilt-faint">
              <Plus size={14} />
            </span>
            Connect a platform
          </button>
        </div>
      )}
    </div>
  );
}
