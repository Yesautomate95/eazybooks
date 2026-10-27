import React from "react";
import { RefreshCw, CheckCircle2, AlertTriangle, Clock } from "lucide-react";
import { relativeTime } from "../utils/format";
import { cn } from "../../../../../lib/utils";

/**
 * Honest sync status. While syncing, the minted coin-edge dial (gl-coin-dial)
 * spins. Note the theme rule: red is reserved for DEBIT figures — so a failed
 * or stale sync uses brass/amber (gilt-brass), never a semantic red.
 */
export function SyncStatusDial({ sync, isFetching, onSync, onReconcile, disabled }) {
  const status = sync?.status || "idle";
  const syncing = status === "syncing" || isFetching;

  let icon;
  let text;
  let tone = "text-gilt-soft";

  if (syncing) {
    icon = <span className="gl-coin-dial" style={{ width: 14, height: 14 }} aria-hidden />;
    text = "Syncing from Tally…";
    tone = "text-gilt-brass";
  } else if (status === "failed") {
    // Failed = brass/amber, NOT red (red means debit).
    icon = <AlertTriangle size={14} className="text-gilt-brass" />;
    text = sync?.lastError ? `Sync failed — ${sync.lastError}` : "Last sync failed";
    tone = "text-gilt-brass";
  } else if (status === "completed" && sync?.lastSyncedAt) {
    icon = <CheckCircle2 size={14} className="text-gilt-credit" />;
    text = `Synced ${relativeTime(sync.lastSyncedAt)}`;
    tone = "text-gilt-soft";
  } else {
    icon = <Clock size={14} className="text-gilt-faint" />;
    text = "Not synced yet";
    tone = "text-gilt-faint";
  }

  return (
    <div className="flex items-center gap-2">
      <span className={cn("inline-flex items-center gap-1.5 text-xs font-medium", tone)}>
        {icon}
        <span className="truncate max-w-[280px]">{text}</span>
      </span>

      <button
        type="button"
        onClick={onSync}
        disabled={disabled || syncing}
        className={cn(
          "inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors duration-150",
          "border-gilt-hairline bg-gilt-card text-gilt-soft hover:border-gilt-brass hover:text-gilt-brass",
          (disabled || syncing) && "opacity-50 cursor-not-allowed"
        )}
        title="Pull only what changed since the last sync"
      >
        <RefreshCw size={13} className={cn(syncing && "animate-spin")} />
        Sync now
      </button>

      {onReconcile && (
        <button
          type="button"
          onClick={onReconcile}
          disabled={disabled || syncing}
          className={cn(
            "inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors duration-150",
            "border-gilt-hairline bg-gilt-card text-gilt-faint hover:border-gilt-brass hover:text-gilt-brass",
            (disabled || syncing) && "opacity-50 cursor-not-allowed"
          )}
          title="Full re-import — corrects any drift or missed deletions"
        >
          Reconcile
        </button>
      )}
    </div>
  );
}
