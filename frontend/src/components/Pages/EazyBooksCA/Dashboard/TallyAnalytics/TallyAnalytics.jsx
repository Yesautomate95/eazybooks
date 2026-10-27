import React, { Suspense, lazy, useMemo, useState } from "react";
import { motion } from "framer-motion";
import { AlertTriangle, Calculator, Database, CalendarRange } from "lucide-react";

import { useTallyAnalytics } from "./useTallyAnalytics";
import { CompanySelect } from "./CompanySelect";
import { SyncStatusDial } from "./SyncStatusDial";
import { TallyStatCard } from "./TallyStatCard";
import { AgeingChart } from "./AgeingChart";
import { CashFlowChart } from "./CashFlowChart";
import { VoucherActivityChart } from "./VoucherActivityChart";
import { GstCard } from "./GstCard";

import { TopList } from "../components/TopList";
import { ChartCardSkeleton, ListSkeleton, StatCardSkeleton } from "../components/Skeleton";
import { cn } from "../../../../../lib/utils";

// Reuse the themed multi-platform charts — our payload matches their shapes.
const IncomeExpensesChart = lazy(() => import("../components/IncomeExpensesChart"));
const ExpensesBarChart = lazy(() => import("../components/ExpensesBarChart"));
const IncomeBreakdownChart = lazy(() => import("../components/IncomeBreakdownChart"));

const RANGES = [
  { id: "month", label: "Month" },
  { id: "quarter", label: "Quarter" },
  { id: "year", label: "Year" },
  { id: "custom", label: "Custom" },
];

// True when the selected window has no posted activity (all monthly buckets flat).
function windowHasNoActivity(data) {
  const monthly = data?.monthly || [];
  if (!monthly.length) return true;
  return monthly.every((m) => !m.income && !m.expenses);
}

function fmtDay(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleDateString(undefined, { day: "2-digit", month: "short", year: "numeric" });
}

/**
 * Tally Analytics page. Reads exclusively from OUR synced database, scoped to
 * (current user, selected company). The desktop bridge syncs Tally → DB in the
 * background; this page never live-queries Tally.
 */
export default function TallyAnalytics({ connected = false }) {
  const [range, setRange] = useState("month");
  const [customStart, setCustomStart] = useState("");
  const [customEnd, setCustomEnd] = useState("");
  const {
    companies, company, setCompany,
    data, sync, loading, isFetching, error,
    triggerSync,
  } = useTallyAnalytics(range, customStart, customEnd);

  const hasCompanies = companies.length > 0;
  const showLoadingShell = loading && !data;
  const kpis = data?.kpis || [];

  const dataRange = data?.dataRange;
  const hasSyncedData = (dataRange?.voucherCount || 0) > 0;
  // Company exists in the list but nothing has ever been synced into the DB.
  const neverSynced =
    hasCompanies && !showLoadingShell && data && !hasSyncedData && !sync?.lastSyncedAt;
  // Data exists, but the chosen window shows no activity (common for historical FYs).
  const emptyWindow = useMemo(
    () => hasSyncedData && !showLoadingShell && windowHasNoActivity(data),
    [hasSyncedData, showLoadingShell, data]
  );
  // Custom range chosen but not yet fully specified.
  const customIncomplete = range === "custom" && (!customStart || !customEnd);

  // Switch to a custom range covering the company's full synced history.
  const jumpToData = () => {
    if (!dataRange?.start || !dataRange?.end) return;
    setCustomStart(dataRange.start);
    setCustomEnd(dataRange.end);
    setRange("custom");
  };

  return (
    <div className="mx-auto max-w-[1440px] px-4 py-5 sm:px-6 lg:px-8 space-y-5">
      {/* Title strip */}
      <div className="flex items-center gap-2">
        <span className="inline-flex h-9 w-9 items-center justify-center rounded-xl border border-gilt-hairline bg-gilt-sunken text-gilt-brass">
          <Calculator size={18} strokeWidth={2.25} />
        </span>
        <div>
          <p className="gl-eyebrow">Analytics</p>
          <p className="text-sm font-semibold text-gilt-ink">Tally Prime</p>
        </div>
      </div>

      {/* Controls bar: company · range · sync */}
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-gilt-hairline bg-gilt-card px-4 py-3">
        <div className="flex flex-wrap items-center gap-3">
          <CompanySelect companies={companies} value={company} onChange={setCompany} />
          <div className="inline-flex rounded-lg border border-gilt-hairline bg-gilt-sunken p-0.5">
            {RANGES.map((r) => (
              <button
                key={r.id}
                type="button"
                onClick={() => setRange(r.id)}
                className={cn(
                  "rounded-md px-3 py-1 text-xs font-medium transition-colors duration-150",
                  range === r.id ? "bg-gilt-card text-gilt-brass shadow-sm" : "text-gilt-soft hover:text-gilt-ink"
                )}
              >
                {r.label}
              </button>
            ))}
          </div>

          {/* Custom date pickers — only when "Custom" is active */}
          {range === "custom" && (
            <div className="inline-flex items-center gap-1.5 rounded-lg border border-gilt-hairline bg-gilt-sunken px-2 py-1">
              <CalendarRange size={14} className="text-gilt-brass" />
              <input
                type="date"
                value={customStart}
                max={customEnd || undefined}
                onChange={(e) => setCustomStart(e.target.value)}
                aria-label="Start date"
                className="bg-transparent text-xs text-gilt-ink outline-none [color-scheme:light]"
              />
              <span className="text-gilt-faint">→</span>
              <input
                type="date"
                value={customEnd}
                min={customStart || undefined}
                onChange={(e) => setCustomEnd(e.target.value)}
                aria-label="End date"
                className="bg-transparent text-xs text-gilt-ink outline-none [color-scheme:light]"
              />
            </div>
          )}
        </div>
        <SyncStatusDial
          sync={sync}
          isFetching={isFetching}
          disabled={!company || !connected}
          onSync={() => triggerSync("incremental")}
          onReconcile={() => triggerSync("full")}
        />
      </div>

      {/* Bridge-not-connected hint (sync needs the desktop app) */}
      {!connected && (
        <div className="flex items-start gap-2 rounded-xl border border-gilt-brass/30 bg-gilt-sunken px-3 py-2 text-sm text-gilt-brass">
          <AlertTriangle size={16} className="mt-0.5 shrink-0" />
          <span className="flex-1">
            The Tally desktop bridge isn't connected. You can still view the last synced data below;
            open the desktop app (with Tally Prime running) to sync fresh figures.
          </span>
        </div>
      )}

      {/* Error banner */}
      {error && (
        <div className="flex items-start gap-2 rounded-xl border border-gilt-brass/40 bg-gilt-sunken px-3 py-2 text-sm text-gilt-brass">
          <AlertTriangle size={16} className="mt-0.5 shrink-0" />
          <span className="flex-1">{error.message}</span>
        </div>
      )}

      {/* No companies synced yet */}
      {!hasCompanies && !loading && !error && <EmptyState connected={connected} />}

      {/* Custom range chosen but dates not yet picked */}
      {hasCompanies && customIncomplete && (
        <div className="flex items-start gap-2 rounded-xl border border-gilt-hairline bg-gilt-card px-3 py-2 text-sm text-gilt-soft">
          <CalendarRange size={16} className="mt-0.5 shrink-0 text-gilt-brass" />
          <span className="flex-1">
            Pick a <span className="font-medium text-gilt-ink">start</span> and{" "}
            <span className="font-medium text-gilt-ink">end</span> date above to view a custom range.
            {dataRange?.start && dataRange?.end && (
              <>
                {" "}This company's data spans {fmtDay(dataRange.start)} – {fmtDay(dataRange.end)} —{" "}
                <button type="button" onClick={jumpToData} className="font-medium text-gilt-brass underline-offset-2 hover:underline">
                  use that range
                </button>.
              </>
            )}
          </span>
        </div>
      )}

      {/* Company is listed but nothing has been synced into the DB yet */}
      {neverSynced && <NotSyncedState connected={connected} onSync={() => triggerSync("incremental")} />}

      {/* Data exists, but the selected window has no posted activity */}
      {emptyWindow && !customIncomplete && !neverSynced && (
        <div className="flex items-start gap-2 rounded-xl border border-gilt-brass/30 bg-gilt-sunken px-3 py-2 text-sm text-gilt-brass">
          <AlertTriangle size={16} className="mt-0.5 shrink-0" />
          <span className="flex-1">
            No posted activity in the selected period
            {data?.window && <> ({fmtDay(data.window.start)} – {fmtDay(data.window.end)})</>}.
            {dataRange?.start && dataRange?.end && (
              <>
                {" "}Your synced books run {fmtDay(dataRange.start)} – {fmtDay(dataRange.end)}.{" "}
                <button type="button" onClick={jumpToData} className="font-semibold underline-offset-2 hover:underline">
                  Jump to your data
                </button>.
              </>
            )}
          </span>
        </div>
      )}

      {hasCompanies && !customIncomplete && !neverSynced && (
        <>
          {/* KPI grid */}
          <section aria-label="Key metrics" className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {showLoadingShell
              ? Array.from({ length: 8 }).map((_, i) => <StatCardSkeleton key={i} />)
              : kpis.map((kpi, i) => <TallyStatCard key={kpi.id} kpi={kpi} index={i} />)}
          </section>

          {/* Income vs expense + income breakdown */}
          <section className="grid grid-cols-1 gap-4 lg:grid-cols-3">
            <div className="lg:col-span-2">
              <Suspense fallback={<ChartCardSkeleton height={288} />}>
                {showLoadingShell ? <ChartCardSkeleton height={288} /> : (
                  <IncomeExpensesChart data={data?.monthly || []} />
                )}
              </Suspense>
            </div>
            <div>
              <Suspense fallback={<ChartCardSkeleton height={240} />}>
                {showLoadingShell ? <ChartCardSkeleton height={240} /> : (
                  <IncomeBreakdownChart data={data?.incomeBreakdown || []} />
                )}
              </Suspense>
            </div>
          </section>

          {/* Cash flow + ageing */}
          <section className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            {showLoadingShell ? <ChartCardSkeleton height={264} /> : <CashFlowChart data={data?.cashFlow || []} />}
            {showLoadingShell ? <ChartCardSkeleton height={264} /> : <AgeingChart ageing={data?.ageing} />}
          </section>

          {/* Expense breakdown */}
          <section>
            <Suspense fallback={<ChartCardSkeleton height={288} />}>
              {showLoadingShell ? <ChartCardSkeleton height={288} /> : (
                <ExpensesBarChart monthly={data?.monthly || []} categories={data?.expenseCategories || []} />
              )}
            </Suspense>
          </section>

          {/* Voucher activity + GST */}
          <section className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            {showLoadingShell ? <ChartCardSkeleton height={264} /> : <VoucherActivityChart data={data?.voucherActivity || []} />}
            {showLoadingShell ? <ChartCardSkeleton height={160} /> : <GstCard gst={data?.gst} />}
          </section>

          {/* Top ledgers */}
          <section className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            {showLoadingShell ? (
              <>
                <ListSkeleton />
                <ListSkeleton />
              </>
            ) : (
              <>
                <TopList
                  title="Top Receivables"
                  subtitle="Sundry debtors by outstanding balance"
                  items={data?.topCustomers || []}
                  emptyHint="No debtor balances synced."
                />
                <TopList
                  title="Top Payables"
                  subtitle="Sundry creditors by outstanding balance"
                  items={data?.topVendors || []}
                  emptyHint="No creditor balances synced."
                />
              </>
            )}
          </section>

          <footer className="pb-2 pt-2 text-center text-2xs text-gilt-faint">
            Figures read from your last sync — open the desktop bridge to refresh. Sync is incremental
            (only what changed); use Reconcile for a full re-import.
          </footer>
        </>
      )}
    </div>
  );
}

function NotSyncedState({ connected, onSync }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      className="rounded-xl border border-dashed border-gilt-hairline bg-gilt-card p-8 text-center"
    >
      <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full border border-gilt-hairline bg-gilt-sunken text-gilt-brass">
        <Database size={22} />
      </div>
      <h3 className="mt-3 text-sm font-semibold text-gilt-ink">This company hasn't been synced yet</h3>
      <p className="mx-auto mt-1 max-w-md text-xs text-gilt-soft">
        Analytics read from your last sync into EazyBooks — not live from Tally. There's no synced
        data for this company yet, so every figure would read zero.
      </p>
      {connected ? (
        <button
          type="button"
          onClick={onSync}
          className="mt-4 inline-flex items-center gap-1.5 rounded-lg border border-gilt-brass/40 bg-gilt-sunken px-3 py-1.5 text-xs font-medium text-gilt-brass hover:bg-gilt-brass/10"
        >
          <Database size={14} /> Run first sync
        </button>
      ) : (
        <p className="mx-auto mt-3 max-w-md text-2xs text-gilt-faint">
          Open the EazyBooks desktop bridge (with Tally Prime running) to sync this company.
        </p>
      )}
    </motion.div>
  );
}

function EmptyState({ connected }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      className="rounded-xl border border-dashed border-gilt-hairline bg-gilt-card p-8 text-center"
    >
      <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full border border-gilt-hairline bg-gilt-sunken text-gilt-brass">
        <Database size={22} />
      </div>
      <h3 className="mt-3 text-sm font-semibold text-gilt-ink">No Tally data synced yet</h3>
      <p className="mx-auto mt-1 max-w-md text-xs text-gilt-soft">
        {connected
          ? "Pick a company in the Tally desktop bridge and run a sync. Your analytics will fill in within a minute or two."
          : "Open the EazyBooks desktop bridge with Tally Prime running, select a company, and sync. Your books will appear here once the first sync completes."}
      </p>
    </motion.div>
  );
}
