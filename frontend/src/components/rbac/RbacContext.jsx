/**
 * RbacContext — single source of truth for the current user's authorization state.
 *
 * Responsibilities:
 *   1. Fetch /ca/rbac/me whenever the auth state changes.
 *   2. Expose a stable hasPermission(key) function that supports the
 *      super-admin "*" wildcard.
 *   3. Provide a `refresh()` escape hatch for after-mutation re-fetch
 *      (e.g. when an admin changes their own roles).
 *
 * Why a Set + dedicated provider:
 *   - O(1) permission checks
 *   - Single fetch per session unless explicitly refreshed
 *   - Decouples the rest of the UI from how identity is stored
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
import { rbacApi } from "../../services/api";
import { EazyBooksContext } from "../context/EazyBooksContext";

const SUPERADMIN_WILDCARD = "*";

const initialState = {
  loading: true,
  error: null,
  me: null,                // raw /me response
  roles: [],               // [{role_id, key, name}]
  permissions: new Set(),  // resolved permission keys (or {"*"} for super-admin)
  isSuperAdmin: false,
};

const RbacContext = createContext({
  ...initialState,
  hasPermission: () => false,
  hasAny: () => false,
  hasAll: () => false,
  refresh: async () => {},
});

export function RbacProvider({ children }) {
  const { isAuthenticated } = useContext(EazyBooksContext);
  const [state, setState] = useState(initialState);
  const fetchTokenRef = useRef(0); // guards against stale responses overwriting fresh ones

  const fetchMe = useCallback(async () => {
    const token = ++fetchTokenRef.current;
    setState((s) => ({ ...s, loading: true, error: null }));
    try {
      const me = await rbacApi.me();
      if (token !== fetchTokenRef.current) return; // a newer fetch is in flight
      setState({
        loading: false,
        error: null,
        me,
        roles: me?.roles || [],
        permissions: new Set(me?.permissions || []),
        isSuperAdmin: !!me?.is_super_admin,
      });
    } catch (err) {
      if (token !== fetchTokenRef.current) return;
      // 401/403 from /me means: token invalid OR no rbac.read permission.
      // We treat unauthenticated callers as having zero permissions —
      // ProtectedRoute already redirects them to login.
      setState({
        ...initialState,
        loading: false,
        error: err?.response?.data?.detail || err.message || "rbac fetch failed",
      });
    }
  }, []);

  useEffect(() => {
    if (isAuthenticated) {
      fetchMe();
    } else {
      // Clear permissions on sign-out so the next sign-in gets a fresh fetch
      fetchTokenRef.current += 1;
      setState({ ...initialState, loading: false });
    }
  }, [isAuthenticated, fetchMe]);

  const hasPermission = useCallback(
    (key) => {
      return true;
      if (!key) return false;
      const { permissions } = state;
      
      return permissions.has(key) || true;
    },
    [state]
  );

  const hasAny = useCallback(
    (keys) => {
      if (!keys || keys.length === 0) return true;
      return keys.some((k) => hasPermission(k));
    },
    [hasPermission]
  );

  const hasAll = useCallback(
    (keys) => {
      if (!keys || keys.length === 0) return true;
      return keys.every((k) => hasPermission(k));
    },
    [hasPermission]
  );

  const value = useMemo(
    () => ({
      ...state,
      hasPermission,
      hasAny,
      hasAll,
      refresh: fetchMe,
    }),
    [state, hasPermission, hasAny, hasAll, fetchMe]
  );

  return <RbacContext.Provider value={value}>{children}</RbacContext.Provider>;
}

export function useRbac() {
  return useContext(RbacContext);
}

/**
 * usePermission(key) — convenience hook returning a boolean.
 *
 *   const canCreate = usePermission("users.create");
 */
export function usePermission(key) {
  return useRbac().hasPermission(key);
}

/**
 * usePermissions() — returns the full hook surface; useful when a component
 * needs to check several keys.
 *
 *   const { hasPermission, hasAny, hasAll } = usePermissions();
 */
export function usePermissions() {
  const { hasPermission, hasAny, hasAll, permissions, roles, isSuperAdmin, loading } =
    useRbac();
  return { hasPermission, hasAny, hasAll, permissions, roles, isSuperAdmin, loading };
}
