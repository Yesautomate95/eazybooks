import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Stale-while-revalidate data hook.
 *
 *   - First mount with an empty cache → shows `loading: true`, fetches.
 *   - Revisit / re-render with a populated cache → returns cached value
 *     immediately and refetches in the background (`isFetching: true`).
 *   - Auto-refreshes on a configurable interval and when the tab regains
 *     focus, mirroring TanStack Query's defaults.
 *
 * Abstraction note: this hook is the seam where a websocket / SSE push
 * channel will replace polling. To upgrade, swap the interval block for
 * a subscription to a server-sent stream that calls `setData(newData)`.
 * Widgets won't need to change.
 *
 * Designed to be drop-in replaceable by TanStack Query's `useQuery` later
 * — the returned shape is intentionally similar.
 */

const cache = new Map(); // key → { data, fetchedAt }
const inflight = new Map(); // key → Promise

export function useSyncedData(key, fetcher, options = {}) {
  const {
    refetchInterval = 60_000,
    refetchOnWindowFocus = true,
    staleTime = 30_000,
    enabled = true,
  } = options;

  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  const cached = cache.get(key);
  const [data, setData] = useState(cached?.data ?? null);
  const [error, setError] = useState(null);
  const [isFetching, setIsFetching] = useState(false);
  const [loading, setLoading] = useState(!cached);

  const run = useCallback(
    async ({ background = false } = {}) => {
      if (!enabled) return;
      if (!background) setLoading((prev) => prev && !cache.has(key));
      setIsFetching(true);
      setError(null);

      // De-dupe concurrent requests for the same key
      let promise = inflight.get(key);
      if (!promise) {
        promise = Promise.resolve()
          .then(() => fetcherRef.current())
          .finally(() => inflight.delete(key));
        inflight.set(key, promise);
      }

      try {
        const next = await promise;
        cache.set(key, { data: next, fetchedAt: Date.now() });
        setData(next);
      } catch (err) {
        setError(err);
      } finally {
        setIsFetching(false);
        setLoading(false);
      }
    },
    [key, enabled]
  );

  // Initial fetch + revalidation when key changes
  useEffect(() => {
    if (!enabled) return;
    const entry = cache.get(key);
    const isStale = !entry || Date.now() - entry.fetchedAt > staleTime;
    if (entry) {
      setData(entry.data);
      setLoading(false);
    } else {
      setData(null);
      setLoading(true);
    }
    if (isStale) {
      run({ background: !!entry });
    }
  }, [key, enabled, staleTime, run]);

  // Interval-based background refetch
  useEffect(() => {
    if (!enabled || !refetchInterval) return undefined;
    const id = setInterval(() => run({ background: true }), refetchInterval);
    return () => clearInterval(id);
  }, [enabled, refetchInterval, run]);

  // Refetch on window focus
  useEffect(() => {
    if (!enabled || !refetchOnWindowFocus) return undefined;
    const onFocus = () => run({ background: true });
    const onVisible = () => {
      if (document.visibilityState === "visible") onFocus();
    };
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [enabled, refetchOnWindowFocus, run]);

  const refetch = useCallback(() => run({ background: !!cache.get(key) }), [run, key]);

  return { data, error, loading, isFetching, refetch };
}

/** Invalidate one cached key — use after a mutation (e.g. disconnect, manual sync). */
export function invalidate(prefix) {
  for (const key of Array.from(cache.keys())) {
    if (key.startsWith(prefix)) cache.delete(key);
  }
}
