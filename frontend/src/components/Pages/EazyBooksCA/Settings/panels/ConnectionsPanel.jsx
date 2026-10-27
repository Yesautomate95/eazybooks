import React, { useCallback, useState } from "react";
import { Loader2 } from "lucide-react";
import { PLATFORMS } from "../../Dashboard/services/dashboardService";
import { useConnections } from "../../Dashboard/hooks/useConnections";
import { relativeTime } from "../../Dashboard/utils/format";
import { PressTheSeal } from "../../../../ui/press-the-seal";
import { cn } from "../../../../../lib/utils";

/**
 * Settings → Connections — the account-level home for managing every platform
 * connection. A clean full-width list (one row per platform) rather than a
 * cramped tile grid, so the connected-state actions never overflow.
 */
export default function ConnectionsPanel() {
  const { statuses, connect, disconnect, resync } = useConnections();
  const [busyIds, setBusyIds] = useState(() => new Set());

  const wrap = useCallback(async (id, fn) => {
    setBusyIds((prev) => new Set(prev).add(id));
    try {
      await fn();
    } finally {
      setBusyIds((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
    }
  }, []);

  const handleConnect = useCallback((id) => wrap(id, () => connect(id)), [connect, wrap]);
  const handleResync = useCallback((id) => wrap(id, () => resync(id)), [resync, wrap]);
  const handleDisconnect = useCallback((id) => wrap(id, () => disconnect(id)), [disconnect, wrap]);

  return (
    <div className="px-4 py-6 sm:px-6">
      <div className="mx-auto max-w-2xl">
        <header className="mb-4">
          <h1 className="gl-display text-lg text-gilt-ink">Connections</h1>
          <p className="mt-0.5 text-sm text-gilt-soft">
            Link your accounting platforms — data flows into your workspaces and analytics automatically.
          </p>
        </header>

        <div className="divide-y divide-gilt-hairline overflow-hidden rounded-xl border border-gilt-hairline bg-gilt-card">
          {PLATFORMS.map((p) => (
            <ConnectionRow
              key={p.id}
              platform={p}
              state={statuses[p.id]}
              busy={busyIds.has(p.id)}
              onConnect={handleConnect}
              onResync={handleResync}
              onDisconnect={handleDisconnect}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

function ConnectionRow({ platform, state, busy, onConnect, onResync, onDisconnect }) {
  const status = state?.status ?? "disconnected";
  const isOAuth = platform.authMethod === "oauth2";
  const [confirming, setConfirming] = useState(false);

  const statusLine = (() => {
    switch (status) {
      case "connected":
        return (
          <>
            <span className="text-gilt-credit">Connected</span>
            {" · synced "}
            {relativeTime(state?.lastSyncedAt)}
          </>
        );
      case "connecting":
        return "Connecting…";
      case "syncing":
        return `Importing data… ${state?.progress ?? 0}%`;
      case "failed":
        return <span className="text-gilt-debit">{state?.error || "Connection failed"}</span>;
      default:
        return isOAuth ? "OAuth 2.0 — not connected" : "Local desktop bridge";
    }
  })();

  return (
    <div className="flex items-center gap-3 px-4 py-3.5">
      <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-gilt-hairline bg-gilt-sunken font-mono text-sm font-bold text-gilt-brass">
        {platform.name.charAt(0)}
      </span>

      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-semibold text-gilt-ink">{platform.name}</p>
        <p className="truncate text-xs text-gilt-soft">{statusLine}</p>
      </div>

      <div className="flex shrink-0 items-center gap-2">
        {confirming && status === "connected" ? (
          <>
            <PressTheSeal
              label="Hold to disconnect"
              holdingLabel="Disconnecting…"
              className="px-3 py-1.5 text-xs"
              disabled={busy}
              onConfirm={() => {
                setConfirming(false);
                onDisconnect(platform.id);
              }}
            />
            <button
              type="button"
              onClick={() => setConfirming(false)}
              className="rounded-md border border-gilt-hairline bg-gilt-card px-2.5 py-1.5 text-xs font-medium text-gilt-soft transition-colors duration-150 hover:border-gilt-brass hover:text-gilt-brass"
            >
              Cancel
            </button>
          </>
        ) : status === "connected" ? (
          <>
            <button
              type="button"
              onClick={() => onResync(platform.id)}
              disabled={busy}
              className="rounded-md border border-gilt-hairline bg-gilt-card px-2.5 py-1.5 text-xs font-medium text-gilt-soft transition-colors duration-150 hover:border-gilt-brass hover:text-gilt-brass disabled:opacity-50"
            >
              Re-sync
            </button>
            <button
              type="button"
              onClick={() => setConfirming(true)}
              disabled={busy}
              className="rounded-md border border-gilt-hairline bg-gilt-card px-2.5 py-1.5 text-xs font-medium text-gilt-soft transition-colors duration-150 hover:border-gilt-ink hover:text-gilt-ink disabled:opacity-50"
            >
              Disconnect
            </button>
          </>
        ) : status === "connecting" || status === "syncing" ? (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-1.5 text-xs font-medium text-gilt-soft">
            <Loader2 size={13} className="animate-spin" />
            {status === "connecting" ? "Connecting" : "Syncing"}
          </span>
        ) : isOAuth ? (
          <button
            type="button"
            onClick={() => onConnect(platform.id)}
            disabled={busy}
            className={cn(
              "inline-flex items-center gap-1.5 rounded-md bg-gilt-brass px-3 py-1.5 text-xs font-semibold text-gilt-paper",
              "transition-colors duration-150 hover:bg-gilt-brass/90 disabled:opacity-50 disabled:cursor-not-allowed"
            )}
          >
            {busy && <Loader2 size={13} className="animate-spin" />}
            {status === "failed" ? "Retry" : "Connect"}
          </button>
        ) : (
          <span className="px-1 text-xs text-gilt-faint">Via desktop app</span>
        )}
      </div>
    </div>
  );
}
