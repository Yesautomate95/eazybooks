import { useCallback, useEffect, useState } from "react";
import {
  connectPlatform,
  disconnectPlatform,
  getConnectionStatus,
  triggerSync,
} from "../services/dashboardService";
import { invalidate } from "./useSyncedData";

/**
 * State machine for the platform connection tiles.
 *
 * Status values per platform:
 *   - "disconnected" → button shows "Connect"
 *   - "connecting"   → OAuth handshake in flight
 *   - "syncing"      → initial historical import (or manual re-sync)
 *   - "connected"    → tile shows "Connected ✓ — Last synced …"
 *   - "failed"       → tile surfaces the error + Retry CTA
 */
export function useConnections() {
  const [statuses, setStatuses] = useState({});
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    setLoading(true);
    const s = await getConnectionStatus();
    setStatuses(s);
    setLoading(false);
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Tab regaining focus is a common trigger for "I just connected on
  // another page — show me the latest" — mirror useSyncedData's behaviour
  // so the connection tiles stay fresh without manual reload.
  useEffect(() => {
    const onFocus = () => refresh();
    const onVisible = () => {
      if (document.visibilityState === "visible") refresh();
    };
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [refresh]);

  const updateOne = useCallback((id, patch) => {
    setStatuses((prev) => ({ ...prev, [id]: { ...prev[id], ...patch } }));
  }, []);

  const connect = useCallback(
    async (id) => {
      try {
        await connectPlatform(id, {
          onStateChange: (s) => updateOne(id, s),
        });
        // Newly-connected source changes the aggregate — drop dashboard caches.
        invalidate("dashboard:");
      } catch (err) {
        // connectPlatform already updates state to "failed"; nothing else to do.
      }
    },
    [updateOne]
  );

  const disconnect = useCallback(
    async (id) => {
      await disconnectPlatform(id);
      updateOne(id, { status: "disconnected", lastSyncedAt: null, error: null });
      invalidate("dashboard:");
    },
    [updateOne]
  );

  const resync = useCallback(
    async (id) => {
      try {
        await triggerSync(id, { onStateChange: (s) => updateOne(id, s) });
        invalidate("dashboard:");
      } catch (err) {
        updateOne(id, { status: "failed", error: err.message });
      }
    },
    [updateOne]
  );

  const connectedIds = Object.entries(statuses)
    .filter(([, s]) => s?.status === "connected")
    .map(([id]) => id);

  return { statuses, connectedIds, loading, connect, disconnect, resync, refresh };
}
