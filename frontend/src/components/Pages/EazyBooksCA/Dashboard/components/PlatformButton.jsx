import React, { useState } from "react";
import { motion } from "framer-motion";
import {
  CheckCircle2,
  Loader2,
  Plug,
  RefreshCw,
  Unplug,
  XCircle,
  AlertTriangle,
} from "lucide-react";
import { cn } from "../../../../../lib/utils";
import { relativeTime } from "../utils/format";
import { PressTheSeal } from "../../../../ui/press-the-seal";

/**
 * One platform tile. Renders every state mandated by the spec:
 *   disconnected · connecting · syncing (with progress) · connected · failed
 *
 * Parent owns the state machine (useConnections) — this component is pure.
 */
export function PlatformButton({ platform, state, onConnect, onDisconnect, onResync, busy }) {
  const status = state?.status ?? "disconnected";
  const progress = state?.progress ?? 0;
  const error = state?.error;
  const [confirmingDisconnect, setConfirmingDisconnect] = useState(false);

  const statusMeta = STATUS_META[status] || STATUS_META.disconnected;

  const handlePrimary = () => {
    if (status === "disconnected" || status === "failed") onConnect?.(platform.id);
  };

  return (
    <motion.div
      className={cn(
        "group relative overflow-hidden rounded-xl border p-4 transition-colors duration-150",
        status === "connected"
          ? "border-gilt-brass/60 bg-gilt-sunken"
          : status === "failed"
            ? "border-gilt-debit/40 bg-gilt-card"
            : "border-gilt-hairline bg-gilt-card hover:border-gilt-brass"
      )}
    >
      <div className="relative flex items-start justify-between gap-3">
        <div className="flex items-center gap-3 min-w-0">
          <span
            className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-gilt-hairline bg-gilt-sunken font-mono font-bold text-gilt-brass"
            aria-hidden="true"
          >
            {platform.name.charAt(0)}
          </span>
          <div className="min-w-0">
            <p className={cn(
              "truncate text-sm font-semibold",
              status === "connected" ? "text-gilt-brass" : "text-gilt-ink"
            )}>
              {platform.name}
            </p>
            <p className="truncate text-xs text-gilt-soft">
              {platform.description}
            </p>
          </div>
        </div>
        <StatusBadge meta={statusMeta} />
      </div>

      {/* Progress bar when syncing */}
      {status === "syncing" && (
        <div className="relative mt-4">
          <div className="h-1.5 w-full overflow-hidden rounded-full bg-gilt-sunken border border-gilt-hairline">
            <motion.div
              initial={{ width: 0 }}
              animate={{ width: `${progress}%` }}
              transition={{ duration: 0.4, ease: "linear" }}
              className="h-full bg-gilt-gold"
            />
          </div>
          <p className="mt-1.5 font-mono text-xs font-medium text-gilt-soft tabular-nums">
            Importing data… {progress}%
          </p>
        </div>
      )}

      {/* Last-synced / error / hint line */}
      {status === "connected" && (
        <p className="relative mt-3 text-xs text-gilt-soft">
          Last synced{" "}
          <span className="font-medium text-gilt-ink">
            {relativeTime(state.lastSyncedAt)}
          </span>
        </p>
      )}
      {status === "failed" && error && (
        <p className="relative mt-3 flex items-start gap-1.5 text-xs text-gilt-debit">
          <AlertTriangle size={12} className="mt-0.5 shrink-0" />
          {error}
        </p>
      )}
      {status === "disconnected" && (
        <p className="relative mt-3 text-xs text-gilt-soft">
          {platform.authMethod === "oauth2"
            ? "OAuth 2.0 — sign in with the provider."
            : "Local bridge — uses the Tally agent on this machine."}
        </p>
      )}

      {/* Actions */}
      <div className="relative mt-4 flex items-center gap-2">
        {(status === "disconnected" || status === "failed") && (
          <button
            type="button"
            onClick={handlePrimary}
            disabled={busy}
            className={cn(
              "inline-flex flex-1 items-center justify-center gap-1.5 rounded-md bg-gilt-brass px-3 py-2 text-xs font-semibold text-gilt-paper transition-colors duration-150",
              "hover:bg-gilt-brass/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-gilt-brass",
              "disabled:opacity-50 disabled:cursor-not-allowed"
            )}
          >
            <Plug size={12} />
            {status === "failed" ? "Retry connection" : "Connect"}
          </button>
        )}

        {(status === "connecting" || status === "syncing") && (
          <button
            type="button"
            disabled
            className="inline-flex flex-1 items-center justify-center gap-1.5 rounded-md border border-gilt-hairline bg-gilt-sunken px-3 py-2 text-xs font-semibold text-gilt-soft cursor-wait"
          >
            <Loader2 size={12} className="animate-spin" />
            {status === "connecting" ? "Connecting…" : "Syncing…"}
          </button>
        )}

        {status === "connected" &&
          (confirmingDisconnect ? (
            // Destructive confirm via the seal — no red, deliberate hold.
            <div className="flex w-full items-center gap-2">
              <PressTheSeal
                label="Hold to disconnect"
                holdingLabel="Disconnecting…"
                className="flex-1 px-3 py-2 text-xs"
                disabled={busy}
                onConfirm={() => {
                  setConfirmingDisconnect(false);
                  onDisconnect?.(platform.id);
                }}
              />
              <button
                type="button"
                onClick={() => setConfirmingDisconnect(false)}
                className="inline-flex items-center rounded-md border border-gilt-hairline bg-gilt-card px-2.5 py-2 text-xs font-medium text-gilt-soft transition-colors duration-150 hover:border-gilt-brass hover:text-gilt-brass"
              >
                Cancel
              </button>
            </div>
          ) : (
            <>
              <button
                type="button"
                onClick={() => onResync?.(platform.id)}
                disabled={busy}
                className="inline-flex items-center gap-1.5 rounded-md border border-gilt-hairline bg-gilt-card px-2.5 py-2 text-xs font-medium text-gilt-soft transition-colors duration-150 hover:border-gilt-brass hover:text-gilt-brass disabled:opacity-50"
                title="Re-sync now"
              >
                <RefreshCw size={12} />
                Re-sync
              </button>
              <button
                type="button"
                onClick={() => setConfirmingDisconnect(true)}
                disabled={busy}
                className="inline-flex items-center gap-1.5 rounded-md border border-gilt-hairline bg-gilt-card px-2.5 py-2 text-xs font-medium text-gilt-soft transition-colors duration-150 hover:border-gilt-ink hover:text-gilt-ink disabled:opacity-50"
                title="Disconnect"
              >
                <Unplug size={12} />
                Disconnect
              </button>
            </>
          ))}
      </div>
    </motion.div>
  );
}

const STATUS_META = {
  disconnected: { label: "Not Connected", tone: "neutral", Icon: XCircle },
  connecting: { label: "Connecting…", tone: "info", Icon: Loader2, spin: true },
  syncing: { label: "Syncing…", tone: "info", Icon: Loader2, spin: true },
  connected: { label: "Connected", tone: "success", Icon: CheckCircle2 },
  failed: { label: "Sync Failed", tone: "danger", Icon: AlertTriangle },
};

function StatusBadge({ meta }) {
  const Icon = meta.Icon;
  const toneClass = {
    neutral: "border-gilt-hairline text-gilt-soft",
    info: "border-gilt-brass/40 text-gilt-brass",
    success: "border-gilt-credit/40 text-gilt-credit",
    danger: "border-gilt-debit/40 text-gilt-debit",
  }[meta.tone];

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 font-mono text-[10px] uppercase tracking-[0.14em] whitespace-nowrap",
        toneClass
      )}
    >
      <Icon size={11} className={meta.spin ? "animate-spin" : ""} />
      {meta.label}
    </span>
  );
}
