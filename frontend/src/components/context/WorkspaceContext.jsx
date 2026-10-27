/**
 * WorkspaceContext — the active accounting platform is the user's primary
 * workspace context (think Slack workspace / Linear team). A user works inside
 * ONE platform at a time and switches between them.
 *
 * Responsibilities:
 *   1. Hold the `activePlatform` (a platform key), persisted to localStorage so
 *      returning users land back in their last-used workspace.
 *   2. Keep `activePlatform` in sync with the URL, so deep links and
 *      back/forward navigation update the workspace (and the switcher).
 *   3. Expose live per-platform connection state (connected vs not) sourced
 *      from the backend — reused by the post-login picker and the switcher.
 *
 * This is a frontend-only concern: connection truth lives server-side
 * (dashboardApi.getConnections); the active platform is a client preference.
 */
import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useLocation } from "react-router-dom";
import {
  PLATFORM_BY_KEY,
  PLATFORM_KEYS,
  platformFromPath,
} from "../../config/platforms";
import { getConnectionStatus } from "../Pages/EazyBooksCA/Dashboard/services/dashboardService";
import { tallyApi } from "../../services/api";

const STORAGE_KEY = "eazybooks.activePlatform";

const WorkspaceContext = createContext(null);

function readStoredPlatform() {
  try {
    const v = localStorage.getItem(STORAGE_KEY);
    return v && PLATFORM_KEYS.includes(v) ? v : null;
  } catch {
    return null;
  }
}

export function WorkspaceProvider({ children }) {
  const location = useLocation();
  const [activePlatform, setActivePlatformState] = useState(readStoredPlatform);
  const [connections, setConnections] = useState({}); // keyed by connectionId
  const [loadingConnections, setLoadingConnections] = useState(true);
  const [tallyLiveConnected, setTallyLiveConnected] = useState(false);
  const mountedRef = useRef(true);

  const setActivePlatform = useCallback((key) => {
    if (key && !PLATFORM_KEYS.includes(key)) return;
    setActivePlatformState(key);
    try {
      if (key) localStorage.setItem(STORAGE_KEY, key);
      else localStorage.removeItem(STORAGE_KEY);
    } catch {
      // localStorage can be unavailable (Safari private mode) — non-fatal.
    }
  }, []);

  // Keep the active platform aligned with the current route. Navigating
  // directly to /dashboard/xero (deep link, bookmark, back button) makes Xero
  // the active workspace. Non-platform routes (/analytics, /admin, /select)
  // resolve to null and intentionally leave the last platform untouched.
  useEffect(() => {
    const fromPath = platformFromPath(location.pathname);
    if (fromPath && fromPath !== activePlatform) {
      setActivePlatform(fromPath);
    }
  }, [location.pathname, activePlatform, setActivePlatform]);

  const refreshConnections = useCallback(async () => {
    setLoadingConnections(true);
    try {
      const s = await getConnectionStatus(); // { connectionId: {status, ...} }
      if (mountedRef.current) setConnections(s || {});
    } finally {
      if (mountedRef.current) setLoadingConnections(false);
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    refreshConnections();
    let cancelled = false;
    const checkTally = async () => {
      try {
        const status = await tallyApi.checkDirectConnection();
        if (!cancelled && status?.connected) setTallyLiveConnected(true);
        else if (!cancelled) setTallyLiveConnected(false);
      } catch {
        if (!cancelled) setTallyLiveConnected(false);
      }
    };
    checkTally();
    const tallyTimer = setInterval(checkTally, 30000);
    return () => {
      mountedRef.current = false;
      cancelled = true;
      clearInterval(tallyTimer);
    };
  }, [refreshConnections]);

  const isConnected = useCallback(
    (key) => {
      const p = PLATFORM_BY_KEY[key];
      if (!p) return false;
      if (key === "tally" && tallyLiveConnected) return true;
      return connections[p.connectionId]?.status === "connected";
    },
    [connections, tallyLiveConnected]
  );

  const connectedKeys = useMemo(
    () => PLATFORM_KEYS.filter((k) => isConnected(k)),
    [isConnected]
  );

  // Switch to a platform's workspace. `navigate` is injected by the caller so
  // the provider stays router-agnostic and we move via SPA navigation (no
  // full-page reload). Lands on the platform's first contextual nav route.
  const switchPlatform = useCallback(
    (key, navigate) => {
      const p = PLATFORM_BY_KEY[key];
      if (!p) return;
      setActivePlatform(key);
      const dest = p.nav?.[0]?.path || `/dashboard/${p.routeSlug}`;
      if (navigate) navigate(dest);
    },
    [setActivePlatform]
  );

  const value = useMemo(
    () => ({
      activePlatform,
      activePlatformMeta: activePlatform ? PLATFORM_BY_KEY[activePlatform] : null,
      setActivePlatform,
      switchPlatform,
      connections,
      loadingConnections,
      isConnected,
      connectedKeys,
      refreshConnections,
    }),
    [
      activePlatform,
      setActivePlatform,
      switchPlatform,
      connections,
      loadingConnections,
      isConnected,
      connectedKeys,
      refreshConnections,
    ]
  );

  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>;
}

/**
 * useWorkspace — returns the active-platform/connection surface. Falls back to
 * a safe no-op shape when used outside the provider so components can adopt the
 * hook incrementally without crashing.
 */
export function useWorkspace() {
  const ctx = useContext(WorkspaceContext);
  if (!ctx) {
    return {
      activePlatform: null,
      activePlatformMeta: null,
      setActivePlatform: () => {},
      switchPlatform: () => {},
      connections: {},
      loadingConnections: false,
      isConnected: () => false,
      connectedKeys: [],
      refreshConnections: () => {},
    };
  }
  return ctx;
}
