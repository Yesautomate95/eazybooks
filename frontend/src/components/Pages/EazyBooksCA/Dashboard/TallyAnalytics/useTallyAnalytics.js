import { useCallback, useEffect, useRef, useState } from "react";
import { tallyAnalyticsApi } from "../../../../../services/api";

const COMPANY_KEY = "tally.analytics.company.v1";

/**
 * Data hook for the Tally Analytics page.
 *
 * Everything here reads from OUR database (the synced mst_ / trn_ tables),
 * scoped server-side to (current user, selected company). We never live-query
 * Tally. While a sync is in flight we poll the lightweight sync-status endpoint
 * so the coin-edge dial and "last synced" stay honest, and refetch the summary
 * once a run completes.
 */
export function useTallyAnalytics(range = "month", customStart = null, customEnd = null) {
  const [companies, setCompanies] = useState([]);
  const [company, setCompany] = useState(() => {
    try { return localStorage.getItem(COMPANY_KEY) || ""; } catch { return ""; }
  });
  const [data, setData] = useState(null);
  const [sync, setSync] = useState(null);
  const [loading, setLoading] = useState(true);
  const [isFetching, setIsFetching] = useState(false);
  const [error, setError] = useState(null);
  const pollRef = useRef(null);

  // ── Companies ────────────────────────────────────────────────────────────
  const loadCompanies = useCallback(async () => {
    try {
      const res = await tallyAnalyticsApi.getCompanies();
      const list = res?.companies || [];
      setCompanies(list);
      // Pick a sensible default company if none selected (or the stored one is gone).
      setCompany((cur) => {
        if (cur && list.some((c) => c.company === cur)) return cur;
        return list[0]?.company || "";
      });
      return list;
    } catch (err) {
      setError(new Error(err?.response?.data?.detail || err?.message || "Failed to load companies"));
      return [];
    }
  }, []);

  useEffect(() => { loadCompanies(); }, [loadCompanies]);

  useEffect(() => {
    if (company) {
      try { localStorage.setItem(COMPANY_KEY, company); } catch { /* ignore */ }
    }
  }, [company]);

  // ── Summary ────────────────────────────────────────────────────────────
  const loadSummary = useCallback(async (silent = false) => {
    if (!company) {
      setData(null);
      setLoading(false);
      return;
    }
    // A custom range needs both ends before we can fetch — skip until then.
    if (range === "custom" && (!customStart || !customEnd)) {
      setLoading(false);
      setIsFetching(false);
      return;
    }
    if (!silent) setIsFetching(true);
    try {
      const payload = await tallyAnalyticsApi.getSummary({
        company, range, start: customStart, end: customEnd,
      });
      setData(payload);
      setSync(payload?.sync || null);
      setError(null);
    } catch (err) {
      setError(new Error(err?.response?.data?.detail || err?.message || "Failed to load analytics"));
    } finally {
      setLoading(false);
      setIsFetching(false);
    }
  }, [company, range, customStart, customEnd]);

  useEffect(() => { setLoading(true); loadSummary(); }, [loadSummary]);

  // ── Sync status polling ──────────────────────────────────────────────────
  const stopPolling = useCallback(() => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
  }, []);

  const pollStatus = useCallback(async () => {
    if (!company) return;
    try {
      const s = await tallyAnalyticsApi.getSyncStatus(company);
      setSync((prev) => {
        // When a run finishes (syncing → completed), refetch the data once.
        if (prev?.status === "syncing" && s?.status !== "syncing") {
          loadSummary(true);
          loadCompanies();
        }
        return s;
      });
      if (s?.status !== "syncing") stopPolling();
    } catch { /* transient — keep polling */ }
  }, [company, loadSummary, loadCompanies, stopPolling]);

  // Poll every 4s whenever a sync is in flight.
  useEffect(() => {
    if (sync?.status === "syncing" && !pollRef.current) {
      pollRef.current = setInterval(pollStatus, 4000);
    }
    return stopPolling;
  }, [sync?.status, pollStatus, stopPolling]);

  // ── Trigger a sync ────────────────────────────────────────────────────────
  const triggerSync = useCallback(async (mode = "incremental") => {
    if (!company) return;
    setSync((prev) => ({ ...(prev || {}), status: "syncing" }));
    try {
      await tallyAnalyticsApi.triggerSync({ company, mode });
      // Start polling immediately for progress.
      pollStatus();
    } catch (err) {
      setSync((prev) => ({
        ...(prev || {}),
        status: "failed",
        lastError: err?.response?.data?.detail || err?.message || "Could not start sync",
      }));
    }
  }, [company, pollStatus]);

  return {
    companies,
    company,
    setCompany,
    data,
    sync,
    loading,
    isFetching,
    error,
    refetch: () => loadSummary(false),
    triggerSync,
  };
}
