import React, { useEffect, useState } from "react";
import {
  ArrowLeft, Download, Save, Hand, Eye, EyeOff,
  ChevronDown, ChevronRight, RefreshCw, CheckCircle, AlertTriangle
} from "lucide-react";
import { zohoBankReconApi } from "../../../../../services/api";
import { ToastHost, toast, errMsg } from "./toast";

const fmt = (n, abs = false) => {
  const val = abs ? Math.abs(n) : n;
  return new Intl.NumberFormat(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(val);
};

// A = auto-reconciled (high confidence), S = suggested then accepted, M = manual.
const matchBadge = (m) => {
  if (m.match_type === "manual") return { label: "Manual", cls: "border-gilt-hairline text-gilt-soft" };
  if (m.tier === "auto") return { label: "Auto", cls: "border-gilt-credit/40 text-gilt-credit" };
  return { label: "Suggested", cls: "border-gilt-brass/40 text-gilt-brass" };
};

function SummaryRow({ label, sign, amount, clickable, expanded, onToggle, children }) {
  const isAdd = sign === "(+)";
  const isSub = sign === "(-)";
  return (
    <div>
      <div
        className={`flex items-center justify-between py-3 px-4 ${
          clickable ? "cursor-pointer hover:bg-gilt-sunken rounded-lg transition-colors duration-150" : ""
        }`}
        onClick={clickable ? onToggle : undefined}
      >
        <div className="flex items-center gap-2">
          {clickable && (
            expanded
              ? <ChevronDown size={14} className="text-gilt-faint" />
              : <ChevronRight size={14} className="text-gilt-faint" />
          )}
          <span className={`text-sm ${clickable ? "text-gilt-brass hover:underline" : "text-gilt-soft"}`}>
            {label}
          </span>
          {sign && (
            <span className={`font-mono tabular-nums text-[10px] px-1.5 py-0.5 rounded border ${
              isAdd ? "border-gilt-credit/40 text-gilt-credit" : "border-gilt-debit/40 text-gilt-debit"
            }`}>
              {sign}
            </span>
          )}
        </div>
        <span className={`font-mono tabular-nums text-sm font-medium ${
          isAdd ? "text-gilt-credit" : isSub ? "text-gilt-debit" : "text-gilt-ink"
        }`}>
          {fmt(amount)}
        </span>
      </div>
      {expanded && children && (
        <div className="mx-4 mb-2 rounded-lg border border-gilt-hairline overflow-hidden">
          {children}
        </div>
      )}
    </div>
  );
}

function TxnDetailTable({ txns }) {
  if (!txns || txns.length === 0) {
    return <p className="text-center text-gilt-soft text-xs py-4">No transactions</p>;
  }
  return (
    <table className="w-full text-xs">
      <thead className="bg-gilt-sunken">
        <tr className="border-b border-gilt-hairline">
          <th className="px-3 py-2 text-left font-mono text-[10px] uppercase tracking-wider font-medium text-gilt-soft">Date</th>
          <th className="px-3 py-2 text-left font-mono text-[10px] uppercase tracking-wider font-medium text-gilt-soft">Description</th>
          <th className="px-3 py-2 text-left font-mono text-[10px] uppercase tracking-wider font-medium text-gilt-soft">Reference</th>
          <th className="px-3 py-2 text-right font-mono text-[10px] uppercase tracking-wider font-medium text-gilt-soft">Debit</th>
          <th className="px-3 py-2 text-right font-mono text-[10px] uppercase tracking-wider font-medium text-gilt-soft">Credit</th>
        </tr>
      </thead>
      <tbody className="divide-y divide-gilt-hairline">
        {txns.map((t) => (
          <tr key={t.txn_id} className="hover:bg-gilt-sunken transition-colors duration-150">
            <td className="px-3 py-2 text-gilt-soft font-mono tabular-nums whitespace-nowrap">{t.transaction_date}</td>
            <td className="px-3 py-2 text-gilt-ink max-w-xs truncate">{t.description}</td>
            <td className="px-3 py-2 text-gilt-faint font-mono">{t.reference || "—"}</td>
            <td className="px-3 py-2 text-right font-mono tabular-nums text-gilt-credit">
              {t.debit_amount ? fmt(t.debit_amount) : "—"}
            </td>
            <td className="px-3 py-2 text-right font-mono tabular-nums text-gilt-debit">
              {t.credit_amount ? fmt(t.credit_amount) : "—"}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function ZohoReconReportSummary({ report, onBack, onManualRecon }) {
  const [summary, setSummary] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [expandedCategories, setExpandedCategories] = useState({});
  const [categoryTxns, setCategoryTxns] = useState({});
  const [loadingCategory, setLoadingCategory] = useState({});
  const [showReconciled, setShowReconciled] = useState(false);
  const [reconciledMatches, setReconciledMatches] = useState([]);

  const fetchSummary = async () => {
    setLoading(true);
    try {
      const data = await zohoBankReconApi.getSummary(report.report_id);
      setSummary(data);
    } catch {
      // keep stale
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchSummary();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [report.report_id]);

  // CATEGORY_MAP maps each adjustment row to the source + direction of the
  // unmatched transactions it expands. The shared calculator names its book
  // side "book" on the transactions endpoint.
  const CATEGORY_MAP = {
    cat1: { source: "book", field: "credit_amount" },
    cat2: { source: "book", field: "debit_amount" },
    cat3: { source: "bank_statement", field: "credit_amount" },
    cat4: { source: "bank_statement", field: "debit_amount" },
  };

  const toggleCategory = async (cat) => {
    const isExpanded = expandedCategories[cat];
    setExpandedCategories((prev) => ({ ...prev, [cat]: !isExpanded }));

    if (!isExpanded && !categoryTxns[cat]) {
      setLoadingCategory((prev) => ({ ...prev, [cat]: true }));
      try {
        // Fetch unmatched txns for the relevant source/direction
        const { source, field } = CATEGORY_MAP[cat];
        const data = await zohoBankReconApi.getTransactions(report.report_id, {
          source,
          recon_status: "unmatched",
        });
        // Filter by direction
        const filtered = (data.transactions || []).filter((t) => t[field] != null && t[field] > 0);
        setCategoryTxns((prev) => ({ ...prev, [cat]: filtered }));
      } catch {
        setCategoryTxns((prev) => ({ ...prev, [cat]: [] }));
      } finally {
        setLoadingCategory((prev) => ({ ...prev, [cat]: false }));
      }
    }
  };

  const toggleReconciled = async () => {
    if (!showReconciled && reconciledMatches.length === 0) {
      const data = await zohoBankReconApi.getMatches(report.report_id, "accepted");
      setReconciledMatches(data.matches || []);
    }
    setShowReconciled((v) => !v);
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      await zohoBankReconApi.saveReport(report.report_id);
      fetchSummary();
      toast.success("Report saved");
    } catch (e) {
      toast.error(errMsg(e, "Failed to save report."));
    } finally {
      setSaving(false);
    }
  };

  const handleDownload = async () => {
    setDownloading(true);
    try {
      await zohoBankReconApi.downloadReport(report.report_id, report.report_name);
      toast.success("Report downloaded");
    } catch (e) {
      toast.error(errMsg(e, "Failed to download report."));
    } finally {
      setDownloading(false);
    }
  };

  if (loading) {
    return (
      <div className="min-h-screen text-gilt-ink flex items-center justify-center gap-3">
        <RefreshCw size={20} className="text-gilt-brass animate-spin" />
        <span className="text-gilt-soft">Loading reconciliation summary...</span>
      </div>
    );
  }

  if (!summary) return null;

  const s = summary.summary || {};
  const diff = s.reconciliation_difference ?? 0;
  const isReconciled = Math.abs(diff) < 0.01;

  return (
    <div className="min-h-screen text-gilt-ink">
      <ToastHost />
      <div className="max-w-3xl mx-auto px-4 py-8">
        {/* Header */}
        <div className="flex items-center gap-3 mb-6">
          <button onClick={onBack} className="p-2 rounded-lg hover:bg-gilt-sunken transition-colors duration-150">
            <ArrowLeft size={20} className="text-gilt-soft" />
          </button>
          <div className="flex-1">
            <div className="flex items-center gap-3">
              <h1 className="gl-display text-xl text-gilt-ink">Reconciliation Report</h1>
              {isReconciled ? (
                <span className="flex items-center gap-1.5 px-2.5 py-1 border border-gilt-credit/40 text-gilt-credit font-mono text-[10px] uppercase tracking-[0.14em] rounded-full">
                  <CheckCircle size={12} /> Fully Reconciled
                </span>
              ) : (
                <span className="flex items-center gap-1.5 px-2.5 py-1 border border-gilt-brass/40 text-gilt-brass font-mono text-[10px] uppercase tracking-[0.14em] rounded-full">
                  <AlertTriangle size={12} /> Partially Reconciled
                </span>
              )}
            </div>
            <p className="text-sm text-gilt-soft mt-0.5">
              {report.report_name} · {summary.period_from} → {summary.period_to}
            </p>
            {summary.counts?.automatic_pct != null && (
              <p className="text-xs text-gilt-soft mt-0.5">
                Automatic reconciliation:{" "}
                <span className="font-mono tabular-nums font-medium text-gilt-ink">{summary.counts.automatic_pct}%</span>
              </p>
            )}
          </div>
        </div>

        {/* Action buttons */}
        <div className="flex flex-wrap gap-2 mb-6">
          <button
            onClick={toggleReconciled}
            className="flex items-center gap-2 px-4 py-2 bg-gilt-card border border-gilt-hairline text-gilt-soft text-sm rounded-lg hover:border-gilt-brass hover:text-gilt-brass transition-colors duration-150"
          >
            {showReconciled ? <EyeOff size={14} /> : <Eye size={14} />}
            {showReconciled ? "Hide" : "View"} Reconciled ({summary.counts?.accepted || 0})
          </button>
          <button
            onClick={onManualRecon}
            className="flex items-center gap-2 px-4 py-2 bg-gilt-card border border-gilt-hairline text-gilt-soft text-sm rounded-lg hover:border-gilt-brass hover:text-gilt-brass transition-colors duration-150"
          >
            <Hand size={14} />
            Manual Reconciliation
          </button>
          <button
            onClick={handleDownload}
            disabled={downloading}
            className="flex items-center gap-2 px-4 py-2 bg-gilt-card border border-gilt-hairline text-gilt-soft text-sm rounded-lg hover:border-gilt-brass hover:text-gilt-brass transition-colors duration-150 disabled:opacity-50"
          >
            {downloading ? <RefreshCw size={14} className="animate-spin" /> : <Download size={14} />}
            Download Excel
          </button>
          <button
            onClick={handleSave}
            disabled={saving}
            className="flex items-center gap-2 px-4 py-2 bg-gilt-brass text-gilt-paper text-sm font-medium rounded-md hover:opacity-90 transition-opacity duration-150 disabled:opacity-50"
          >
            {saving ? <RefreshCw size={14} className="animate-spin" /> : <Save size={14} />}
            {saving ? "Saving..." : "Save Report"}
          </button>
        </div>

        {/* Reconciled transactions (collapsible) */}
        {showReconciled && (
          <div className="bg-gilt-card rounded-xl border border-gilt-hairline mb-6 overflow-hidden">
            <div className="px-4 py-3 bg-gilt-sunken border-b border-gilt-hairline">
              <p className="font-mono tabular-nums text-[10px] uppercase tracking-[0.14em] font-medium text-gilt-credit">Reconciled Transactions ({reconciledMatches.length})</p>
            </div>
            <div className="overflow-x-auto max-h-64 overflow-y-auto">
              <table className="w-full text-xs">
                <thead className="bg-gilt-sunken sticky top-0">
                  <tr className="border-b border-gilt-hairline">
                    <th className="px-3 py-2 text-left font-mono text-[10px] uppercase tracking-wider text-gilt-soft">Bank Date</th>
                    <th className="px-3 py-2 text-left font-mono text-[10px] uppercase tracking-wider text-gilt-soft">Bank Description</th>
                    <th className="px-3 py-2 text-right font-mono text-[10px] uppercase tracking-wider text-gilt-soft">Amount</th>
                    <th className="px-3 py-2 text-left font-mono text-[10px] uppercase tracking-wider text-gilt-soft">Book Date</th>
                    <th className="px-3 py-2 text-left font-mono text-[10px] uppercase tracking-wider text-gilt-soft">Book Description</th>
                    <th className="px-3 py-2 text-center font-mono text-[10px] uppercase tracking-wider text-gilt-soft">Type</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gilt-hairline">
                  {reconciledMatches.flatMap((m) => {
                    const banks = m.bank_transactions || (m.bank_transaction ? [m.bank_transaction] : []);
                    const books = m.book_transactions || (m.book_transaction ? [m.book_transaction] : []);
                    const rows = Math.max(banks.length, books.length, 1);
                    const badge = matchBadge(m);
                    return Array.from({ length: rows }).map((_, i) => {
                      const bt = banks[i];
                      const kt = books[i];
                      return (
                        <tr key={`${m.match_id}-${i}`} className="hover:bg-gilt-sunken transition-colors duration-150">
                          <td className="px-3 py-2 text-gilt-soft font-mono tabular-nums">{bt?.transaction_date || ""}</td>
                          <td className="px-3 py-2 text-gilt-ink max-w-xs truncate">{bt?.description || ""}</td>
                          <td className="px-3 py-2 text-right font-mono tabular-nums text-gilt-ink">
                            {bt ? fmt(bt.credit_amount || bt.debit_amount || 0) : ""}
                          </td>
                          <td className="px-3 py-2 text-gilt-soft font-mono tabular-nums">{kt?.transaction_date || ""}</td>
                          <td className="px-3 py-2 text-gilt-ink max-w-xs truncate">{kt?.description || ""}</td>
                          <td className="px-3 py-2 text-center">
                            {i === 0 && (
                              <span className={`font-mono text-[10px] uppercase tracking-[0.14em] px-1.5 py-0.5 rounded-full border ${badge.cls}`}>{badge.label}</span>
                            )}
                          </td>
                        </tr>
                      );
                    });
                  })}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* Main reconciliation statement */}
        <div className="bg-gilt-card rounded-xl border border-gilt-hairline overflow-hidden">
          <div className="px-5 py-4 bg-gilt-sunken border-b border-gilt-hairline">
            <h2 className="font-semibold text-gilt-ink">Reconciliation Statement</h2>
            <p className="text-xs text-gilt-soft mt-0.5">{summary.account_name}</p>
          </div>

          <div className="divide-y divide-gilt-hairline">
            {/* Book CB */}
            <div className="flex items-center justify-between py-3.5 px-5">
              <span className="text-sm font-semibold text-gilt-ink">Book Closing Balance</span>
              <span className="font-mono tabular-nums text-sm font-bold text-gilt-ink">{fmt(summary.book_closing_balance)}</span>
            </div>

            <div className="px-2 py-1">
              <p className="gl-eyebrow px-2 py-1">Adjustments</p>
            </div>

            {/* Category 1: Book Credits not in Bank */}
            <SummaryRow
              label="Book Credits not in Bank Statement"
              sign="(+)"
              amount={s.ledger_credits_not_in_bank}
              clickable
              expanded={expandedCategories.cat1}
              onToggle={() => toggleCategory("cat1")}
            >
              {loadingCategory.cat1
                ? <div className="flex justify-center py-3"><RefreshCw size={14} className="animate-spin text-gilt-faint" /></div>
                : <TxnDetailTable txns={categoryTxns.cat1} />}
            </SummaryRow>

            {/* Category 2: Book Debits not in Bank */}
            <SummaryRow
              label="Book Debits not in Bank Statement"
              sign="(-)"
              amount={s.ledger_debits_not_in_bank}
              clickable
              expanded={expandedCategories.cat2}
              onToggle={() => toggleCategory("cat2")}
            >
              {loadingCategory.cat2
                ? <div className="flex justify-center py-3"><RefreshCw size={14} className="animate-spin text-gilt-faint" /></div>
                : <TxnDetailTable txns={categoryTxns.cat2} />}
            </SummaryRow>

            {/* Category 3: Bank Credits not in Book */}
            <SummaryRow
              label="Bank Credits not in Book"
              sign="(+)"
              amount={s.bank_credits_not_in_ledger}
              clickable
              expanded={expandedCategories.cat3}
              onToggle={() => toggleCategory("cat3")}
            >
              {loadingCategory.cat3
                ? <div className="flex justify-center py-3"><RefreshCw size={14} className="animate-spin text-gilt-faint" /></div>
                : <TxnDetailTable txns={categoryTxns.cat3} />}
            </SummaryRow>

            {/* Category 4: Bank Debits not in Book */}
            <SummaryRow
              label="Bank Debits not in Book"
              sign="(-)"
              amount={s.bank_debits_not_in_ledger}
              clickable
              expanded={expandedCategories.cat4}
              onToggle={() => toggleCategory("cat4")}
            >
              {loadingCategory.cat4
                ? <div className="flex justify-center py-3"><RefreshCw size={14} className="animate-spin text-gilt-faint" /></div>
                : <TxnDetailTable txns={categoryTxns.cat4} />}
            </SummaryRow>

            {/* Divider */}
            <div className="border-t border-gilt-hairline mx-5" />

            {/* Reconciled Amount */}
            <div className="flex items-center justify-between py-3.5 px-5 bg-gilt-sunken">
              <span className="text-sm font-semibold text-gilt-ink">Reconciled Amount</span>
              <span className="font-mono tabular-nums text-sm font-bold text-gilt-ink">{fmt(s.reconciled_amount)}</span>
            </div>

            {/* Bank CB */}
            <div className="flex items-center justify-between py-3.5 px-5">
              <span className="text-sm font-semibold text-gilt-ink">Bank Statement Closing Balance</span>
              <span className="font-mono tabular-nums text-sm font-bold text-gilt-ink">{fmt(summary.bank_closing_balance)}</span>
            </div>

            {/* Double line */}
            <div className="gl-double-rule mx-5" aria-hidden="true" />

            {/* Reconciliation Difference */}
            <div className={`flex items-center justify-between py-4 px-5 ${isReconciled ? "bg-gilt-credit/10" : "bg-gilt-debit/10"}`}>
              <div className="flex items-center gap-2">
                <span className="text-sm font-bold text-gilt-ink">Reconciliation Difference</span>
                {isReconciled && <CheckCircle size={16} className="text-gilt-credit" />}
                {!isReconciled && <AlertTriangle size={16} className="text-gilt-brass" />}
              </div>
              <span className={`font-mono tabular-nums text-lg font-bold ${isReconciled ? "text-gilt-credit" : "text-gilt-debit"}`}>
                {fmt(diff)}
              </span>
            </div>
          </div>
        </div>

        {/* Opening balances footnote */}
        <div className="mt-4 bg-gilt-card rounded-xl border border-gilt-hairline px-5 py-3">
          <p className="font-mono text-[10px] uppercase tracking-[0.14em] font-medium text-gilt-soft mb-2">Opening Balances</p>
          <div className="grid grid-cols-2 gap-4 text-xs">
            <div>
              <span className="text-gilt-soft">Book OB: </span>
              <span className="font-mono tabular-nums font-medium text-gilt-ink">{fmt(summary.book_opening_balance)}</span>
            </div>
            <div>
              <span className="text-gilt-soft">Bank OB: </span>
              <span className="font-mono tabular-nums font-medium text-gilt-ink">{fmt(summary.bank_opening_balance)}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
