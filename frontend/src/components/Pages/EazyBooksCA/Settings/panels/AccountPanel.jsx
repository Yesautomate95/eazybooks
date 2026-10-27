import React from "react";
import { useNavigate } from "react-router-dom";
import { Sun, Moon, LogOut } from "lucide-react";
import { useGiltThemeCtx } from "../../../../context/GiltThemeContext";
import { logout as fullLogout } from "../../../../../services/auth";
import { cn } from "../../../../../lib/utils";

function SectionHeader({ children }) {
  return (
    <p className="mb-2 flex items-center gap-2">
      <span className="font-mono text-[10px] font-medium uppercase tracking-[0.14em] text-gilt-brass">
        {children}
      </span>
      <span aria-hidden="true" className="h-px flex-1 bg-gilt-hairline" />
    </p>
  );
}

export default function AccountPanel() {
  const navigate = useNavigate();
  const { theme, toggleTheme } = useGiltThemeCtx();

  const name = localStorage.getItem("username") || "Anonymous";
  const email = localStorage.getItem("email") || "user@example.com";
  const userInitial = name.trim().charAt(0).toUpperCase();

  // useGiltTheme exposes a toggle; only flip when the target mode differs.
  const setMode = (mode) => {
    if ((mode === "dark") !== (theme === "dark")) toggleTheme();
  };

  const signOut = () => {
    localStorage.clear();
    sessionStorage.clear();
    document.cookie.split(";").forEach((c) => {
      document.cookie = c
        .replace(/^ +/, "")
        .replace(/=.*/, "=;expires=" + new Date().toUTCString() + ";path=/");
    });
    fullLogout();
  };

  const MODES = [
    { id: "light", label: "Daylight", icon: Sun },
    { id: "dark", label: "Lamplight", icon: Moon },
  ];

  return (
    <div className="px-4 py-6 sm:px-6">
      <div className="mx-auto max-w-2xl">
        {/* Profile */}
        <section className="mb-7">
          <SectionHeader>Profile</SectionHeader>
          <div className="flex items-center gap-3 rounded-xl border border-gilt-hairline bg-gilt-card p-4">
            <div className="gl-display flex h-12 w-12 shrink-0 items-center justify-center rounded-full border border-gilt-hairline bg-gilt-sunken text-lg text-gilt-brass">
              {userInitial}
            </div>
            <div className="min-w-0">
              <p className="truncate text-sm font-semibold text-gilt-ink">{name}</p>
              <p className="truncate text-xs text-gilt-soft">{email}</p>
            </div>
          </div>
        </section>

        {/* Appearance */}
        <section className="mb-7">
          <SectionHeader>Appearance</SectionHeader>
          <div className="rounded-xl border border-gilt-hairline bg-gilt-card p-4">
            <p className="mb-3 text-sm font-medium text-gilt-ink">Theme</p>
            <div className="inline-flex rounded-lg border border-gilt-hairline p-0.5">
              {MODES.map((m) => {
                const Icon = m.icon;
                const active = (m.id === "dark") === (theme === "dark");
                return (
                  <button
                    key={m.id}
                    type="button"
                    onClick={() => setMode(m.id)}
                    className={cn(
                      "inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-colors duration-150",
                      active
                        ? "bg-gilt-sunken text-gilt-brass"
                        : "text-gilt-soft hover:text-gilt-ink"
                    )}
                  >
                    <Icon size={14} />
                    {m.label}
                  </button>
                );
              })}
            </div>
          </div>
        </section>

        {/* Session */}
        <section>
          <SectionHeader>Session</SectionHeader>
          <div className="flex items-center justify-between gap-3 rounded-xl border border-gilt-hairline bg-gilt-card p-4">
            <div className="min-w-0">
              <p className="text-sm font-medium text-gilt-ink">Sign out</p>
              <p className="text-xs text-gilt-soft">End your session and return to the homepage.</p>
            </div>
            <button
              type="button"
              onClick={signOut}
              className="inline-flex shrink-0 items-center gap-1.5 rounded-md border border-gilt-hairline bg-gilt-card px-3 py-2 text-xs font-medium text-gilt-soft transition-colors duration-150 hover:border-gilt-brass hover:text-gilt-brass"
            >
              <LogOut size={14} />
              Sign out
            </button>
          </div>
        </section>
      </div>
    </div>
  );
}
