import React, { Suspense, lazy, useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { motion } from "framer-motion";
import { LayoutDashboard, AlertTriangle, X, Layers, ArrowLeft } from "lucide-react";
import { DashboardHeader } from "./components/DashboardHeader";
import { ConnectionTab } from "./components/ConnectionTab";
import { PlatformButton } from "./components/PlatformButton";
import { StatCard } from "./components/StatCard";
import { TopList } from "./components/TopList";
import {
  ChartCardSkeleton,
  ListSkeleton,
  StatCardSkeleton,
} from "./components/Skeleton";
import { useConnections } from "./hooks/useConnections";
import { useSyncedData } from "./hooks/useSyncedData";
import TallyAnalytics from "./TallyAnalytics/TallyAnalytics";
import { getDashboardData, PLATFORMS } from "./services/dashboardService";
import { useWorkspace } from "../../../context/WorkspaceContext";
import { PLATFORM_BY_KEY } from "../../../../config/platforms";

// Code-split heavy chart bundles — they only ship if the user reaches the dashboard.
const IncomeExpensesChart = lazy(() => import("./components/IncomeExpensesChart"));
const ExpensesBarChart = lazy(() => import("./components/ExpensesBarChart"));
const IncomeBreakdownChart = lazy(() => import("./components/IncomeBreakdownChart"));

const DARK_KEY = "dashboard.darkMode.v1";

export default function Dashboard({ scope = "platform" }) {
  const navigate = useNavigate();
  const { activePlatform } = useWorkspace();
  const isPlatformScope = scope === "platform";
  const activePlatformMeta = activePlatform ? PLATFORM_BY_KEY[activePlatform] : null;
  const activeConn = activePlatformMeta?.connectionId || null;
  const platformLabel = activePlatformMeta?.label || "your ledger";

  // Scoped view with no workspace chosen → send the user to pick one.
  useEffect(() => {
    if (isPlatformScope && !activePlatform) {
      navigate("/dashboard/select", { replace: true });
    }
  }, [isPlatformScope, activePlatform, navigate]);

  const [range, setRange] = useState("month");
  // Custom-range bounds (ISO YYYY-MM-DD); only meaningful when range === "custom".
  const [customStart, setCustomStart] = useState("");
  const [customEnd, setCustomEnd] = useState("");
  const [source, setSource] = useState("all");
  const [darkMode, setDarkMode] = useState(() => {
    try {
      return localStorage.getItem(DARK_KEY) === "1";
    } catch {
      return false;
    }
  });
  const [busyIds, setBusyIds] = useState(() => new Set());

  // Toggle .dark on a scoped wrapper rather than <html> so we don't leak
  // dark styling into the rest of the (currently light-only) app shell.
  const rootClass = darkMode ? "dark" : "";

  const { statuses, connectedIds, connect, disconnect, resync, refresh: refreshConnections } =
    useConnections();

  // Post-OAuth landing detection. Each provider's callback redirects via
  // /dashboard/{qb|zoho|xero}, which then bounces back here with a
  // ?{provider}=connected param (see OAUTH_RETURN_KEY hand-off). When we
  // detect it, strip the param and pump an extra refresh after a beat —
  // protects against the race where we arrive a hair before the backend
  // finishes committing tokens.
  const [searchParams, setSearchParams] = useSearchParams();
  useEffect(() => {
    const justConnected = ["qbo", "zoho", "xero", "sap"].some(
      (k) => searchParams.get(k) === "connected"
    );
    if (!justConnected) return undefined;

    const next = new URLSearchParams(searchParams);
    ["qbo", "zoho", "xero", "sap", "realmId", "organization_id", "tenant_id", "company_code"].forEach(
      (k) => next.delete(k)
    );
    setSearchParams(next, { replace: true });

    refreshConnections();
    const t = setTimeout(() => refreshConnections(), 1500);
    return () => clearTimeout(t);
  }, [searchParams, setSearchParams, refreshConnections]);

  // Filter the source dropdown so it never offers a not-connected platform.
  const availableSources = connectedIds;
  useEffect(() => {
    if (source !== "all" && !availableSources.includes(source)) setSource("all");
  }, [availableSources, source]);

  // Platform scope locks the data to the active workspace's single source;
  // "all" scope keeps the aggregated/selectable behaviour.
  const effectiveSources = isPlatformScope
    ? activeConn
      ? [activeConn]
      : []
    : source === "all"
      ? availableSources
      : [source];
  // A custom range needs both ends before we fetch — otherwise we'd thrash the
  // backend (and hit its 90-day fallback) while the user is still picking dates.
  const customReady = range !== "custom" || (!!customStart && !!customEnd);

  // Sort for stable cache keys regardless of connection order. Custom bounds are
  // part of the key so changing either date triggers a fresh fetch.
  const rangeKey = range === "custom" ? `custom:${customStart}:${customEnd}` : range;
  const dataKey = `dashboard:${rangeKey}:${[...effectiveSources].sort().join(",")}`;

  // Stable fetcher reference for the hook (parameters baked into the key).
  const fetcher = useCallback(
    () => getDashboardData({ range, start: customStart, end: customEnd, sources: effectiveSources }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [dataKey]
  );

  // Tally is served by its own company-scoped page (TallyAnalytics) which reads
  // our synced DB — so we skip the generic live-aggregation fetch for it.
  const isTallyScope = isPlatformScope && activePlatform === "tally";

  const { data, loading, isFetching, error, refetch } = useSyncedData(dataKey, fetcher, {
    refetchInterval: 60_000,
    refetchOnWindowFocus: true,
    staleTime: 30_000,
    enabled: !isTallyScope && customReady, // even with 0 sources we fetch the "empty" payload — it's cheap
  });

  const wrap = useCallback(
    async (id, fn) => {
      setBusyIds((prev) => new Set(prev).add(id));
      try {
        await fn();
      } finally {
        setBusyIds((prev) => {
          const next = new Set(prev);
          next.delete(id);
          return next;
        });
        refetch();
      }
    },
    [refetch]
  );

  const handleConnect = useCallback((id) => wrap(id, () => connect(id)), [connect, wrap]);
  // Disconnect confirmation is handled by the tile's press-the-seal gesture.
  const handleDisconnect = useCallback((id) => wrap(id, () => disconnect(id)), [disconnect, wrap]);
  const handleResync = useCallback((id) => wrap(id, () => resync(id)), [resync, wrap]);

  const toggleDark = useCallback(() => {
    setDarkMode((v) => {
      const next = !v;
      try { localStorage.setItem(DARK_KEY, next ? "1" : "0"); } catch {}
      return next;
    });
  }, []);

  const hasConnections = connectedIds.length > 0;
  const isPlatformConnected = activeConn ? connectedIds.includes(activeConn) : false;
  const dsPlatform = activeConn ? PLATFORMS.find((p) => p.id === activeConn) : null;
  const kpis = data?.kpis || [];
  const showLoadingShell = loading && !data;

  // Tally has a dedicated, company-scoped analytics page that reads our synced
  // database (never live Tally). Render it instead of the generic OAuth board.
  if (isTallyScope) {
    return (
      <div className={rootClass}>
        <div className="min-h-screen text-gilt-ink transition-colors">
          <TallyAnalytics connected={isPlatformConnected} />
        </div>
      </div>
    );
  }

  return (
    <div className={rootClass}>
      <div className="min-h-screen text-gilt-ink transition-colors">
        <div className="mx-auto max-w-[1440px] px-4 py-5 sm:px-6 lg:px-8 space-y-5">
          {/* Title strip */}
          <motion.div
            className="flex items-center gap-2"
          >
            <span className="inline-flex h-9 w-9 items-center justify-center rounded-xl border border-gilt-hairline bg-gilt-sunken text-gilt-brass">
              <LayoutDashboard size={18} strokeWidth={2.25} />
            </span>
            <div>
              <p className="gl-eyebrow">Analytics</p>
              <p className="text-sm font-semibold text-gilt-ink">
                {isPlatformScope ? platformLabel : "All ledgers"}
              </p>
            </div>
          </motion.div>

          {/* Connection panel. All-ledgers shows every platform; a scoped
              workspace shows just its own connect card when it isn't linked
              (so the user can connect in place — full management lives in
              Settings → Connections). */}
          {isPlatformScope ? (
            !isPlatformConnected && dsPlatform ? (
              <div className="max-w-md">
                <PlatformButton
                  platform={dsPlatform}
                  state={statuses[activeConn]}
                  busy={busyIds.has(activeConn)}
                  onConnect={handleConnect}
                  onDisconnect={handleDisconnect}
                  onResync={handleResync}
                />
              </div>
            ) : null
          ) : (
            <ConnectionTab
              statuses={statuses}
              busyIds={busyIds}
              onConnect={handleConnect}
              onDisconnect={handleDisconnect}
              onResync={handleResync}
            />
          )}

          {/* Global controls */}
          <DashboardHeader
            range={range}
            onRangeChange={setRange}
            customStart={customStart}
            customEnd={customEnd}
            onCustomStartChange={setCustomStart}
            onCustomEndChange={setCustomEnd}
            source={source}
            onSourceChange={setSource}
            availableSources={availableSources}
            lastSyncedAt={data?.generatedAt}
            isFetching={isFetching}
            onRefresh={() => {
              refreshConnections();
              refetch();
            }}
            darkMode={darkMode}
            onToggleDark={toggleDark}
            title={isPlatformScope ? `${platformLabel} Analytics` : "All Ledgers"}
            showSource={!isPlatformScope}
            subtitle={
              isPlatformScope ? (
                <>
                  Showing <span className="font-medium text-gilt-ink">{platformLabel}</span>
                  {" · "}
                  {isPlatformConnected
                    ? isFetching
                      ? "Syncing now…"
                      : "Connected"
                    : "not connected — empty preview"}
                </>
              ) : undefined
            }
            actions={
              isPlatformScope
                ? connectedIds.length > 1 && (
                    <button
                      type="button"
                      onClick={() => navigate("/dashboard/analytics/all")}
                      className="inline-flex items-center gap-1.5 rounded-lg border border-gilt-hairline bg-gilt-card px-3 py-2 text-xs font-medium text-gilt-soft transition-colors duration-150 hover:border-gilt-brass hover:text-gilt-brass"
                    >
                      <Layers size={13} />
                      All ledgers
                    </button>
                  )
                : activePlatform && (
                    <button
                      type="button"
                      onClick={() => navigate("/dashboard/analytics")}
                      className="inline-flex items-center gap-1.5 rounded-lg border border-gilt-hairline bg-gilt-card px-3 py-2 text-xs font-medium text-gilt-soft transition-colors duration-150 hover:border-gilt-brass hover:text-gilt-brass"
                    >
                      <ArrowLeft size={13} />
                      Back to {platformLabel}
                    </button>
                  )
            }
          />

          {/* Error banner */}
          {error && (
            <div className="flex items-start gap-2 rounded-xl border border-gilt-debit/40 bg-gilt-sunken px-3 py-2 text-sm text-gilt-debit">
              <AlertTriangle size={16} className="mt-0.5 shrink-0" />
              <span className="flex-1">{error.message || "Failed to load dashboard data."}</span>
              <button
                onClick={() => refetch()}
                className="font-semibold underline-offset-2 hover:underline"
              >
                Retry
              </button>
            </div>
          )}

          {/* Empty state when no connections (all-ledgers view only — a scoped
              workspace shows its own connect card + empty preview instead). */}
          {!isPlatformScope && !hasConnections && !loading && !error && (
            <EmptyState />
          )}

          {/* KPI grid */}
          <section
            aria-label="Key metrics"
            className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-5"
          >
            {showLoadingShell || !data
              ? Array.from({ length: 5 }).map((_, i) => <StatCardSkeleton key={i} />)
              : kpis.map((kpi, i) => <StatCard key={kpi.id} kpi={kpi} index={i} />)}
          </section>

          {/* Charts */}
          <section className="grid grid-cols-1 gap-4 lg:grid-cols-3">
            <div className="lg:col-span-2">
              <Suspense fallback={<ChartCardSkeleton height={288} />}>
                {showLoadingShell ? (
                  <ChartCardSkeleton height={288} />
                ) : (
                  <IncomeExpensesChart data={data?.monthly || []} delay={0.05} />
                )}
              </Suspense>
            </div>
            <div>
              <Suspense fallback={<ChartCardSkeleton height={240} />}>
                {showLoadingShell ? (
                  <ChartCardSkeleton height={240} />
                ) : (
                  <IncomeBreakdownChart data={data?.incomeBreakdown || []} delay={0.1} />
                )}
              </Suspense>
            </div>
          </section>

          <section>
            <Suspense fallback={<ChartCardSkeleton height={288} />}>
              {showLoadingShell ? (
                <ChartCardSkeleton height={288} />
              ) : (
                <ExpensesBarChart
                  monthly={data?.monthly || []}
                  categories={data?.expenseCategories || []}
                  delay={0.05}
                />
              )}
            </Suspense>
          </section>

          {/* Top lists */}
          <section className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            {showLoadingShell ? (
              <>
                <ListSkeleton />
                <ListSkeleton />
              </>
            ) : (
              <>
                <TopList
                  title="Top 10 Customers"
                  subtitle="By revenue contribution"
                  items={data?.topCustomers || []}
                  emptyHint="Connect a platform to see your customers."
                />
                <TopList
                  title="Top 10 Vendors"
                  subtitle="By purchase value"
                  items={data?.topVendors || []}
                  emptyHint="Connect a platform to see your vendors."
                  delay={0.05}
                />
              </>
            )}
          </section>

          <footer className="pb-2 pt-2 text-center text-2xs text-gilt-faint">
            Data refreshes every 60 seconds and on window focus.
          </footer>
        </div>
      </div>
    </div>
  );
}

function EmptyState() {
  return (
    <motion.div
      className="rounded-xl border border-dashed border-gilt-hairline bg-gilt-card p-8 text-center"
    >
      <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full border border-gilt-hairline bg-gilt-sunken text-gilt-brass">
        <LayoutDashboard size={22} />
      </div>
      <h3 className="mt-3 text-sm font-semibold text-gilt-ink">
        No platforms connected yet
      </h3>
      <p className="mx-auto mt-1 max-w-md text-xs text-gilt-soft">
        Connect Tally, QuickBooks, Zoho Books, or Xero above to pull in income, expenses,
        customers and vendors. Your dashboard will start filling in within seconds.
      </p>
    </motion.div>
  );
}
