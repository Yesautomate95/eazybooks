import React, { useEffect, useState } from "react";
import {
  ArrowLeft, Download, Save, Hand, Eye, EyeOff,
  ChevronDown, ChevronRight, RefreshCw, CheckCircle, AlertTriangle
} from "lucide-react";
import { xeroBankReconApi } from "../../../../../services/api";

const fmt = (n, abs = false) => {
  const val = abs ? Math.abs(n) : n;
  return new Intl.NumberFormat(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(val);
};

function SummaryRow({ label, sign, amount, clickable, expanded, onToggle, children }) {
  const isAdd = sign === "(+)";
  const isSub = sign === "(-)";
  return (
    <div>
      <div
        className={`flex items-center justify-between py-3 px-4 ${
          clickable ? "cursor-pointer hover:bg-gray-50 rounded-lg transition-colors" : ""
        }`}
        onClick={clickable ? onToggle : undefined}
      >
        <div className="flex items-center gap-2">
          {clickable && (
            expanded
              ? <ChevronDown size={14} className="text-gray-400" />
              : <ChevronRight size={14} className="text-gray-400" />
          )}
          <span className={`text-sm ${clickable ? "text-blue-700 hover:underline" : "text-gray-700"}`}>
            {label}
          </span>
          {sign && (
            <span className={`text-xs font-semibold px-1.5 py-0.5 rounded ${
              isAdd ? "bg-green-100 text-green-700" : "bg-red-100 text-red-700"
            }`}>
              {sign}
            </span>
          )}
        </div>
        <span className={`font-mono text-sm font-medium ${
          isAdd ? "text-green-700" : isSub ? "text-red-700" : "text-gray-900"
        }`}>
          {fmt(amount)}
        </span>
      </div>
      {expanded && children && (
        <div className="mx-4 mb-2 rounded-lg border border-gray-100 overflow-hidden">
          {children}
        </div>
      )}
    </div>
  );
}

function TxnDetailTable({ txns }) {
  if (!txns || txns.length === 0) {
    return <p className="text-center text-gray-500 text-xs py-4">No transactions</p>;
  }
  return (
    <table className="w-full text-xs">
      <thead className="bg-gray-50">
        <tr>
          <th className="px-3 py-2 text-left font-medium text-gray-500">Date</th>
          <th className="px-3 py-2 text-left font-medium text-gray-500">Description</th>
          <th className="px-3 py-2 text-left font-medium text-gray-500">Reference</th>
          <th className="px-3 py-2 text-right font-medium text-gray-500">Debit</th>
          <th className="px-3 py-2 text-right font-medium text-gray-500">Credit</th>
        </tr>
      </thead>
      <tbody className="divide-y divide-gray-50">
        {txns.map((t) => (
          <tr key={t.txn_id} className="hover:bg-gray-50">
            <td className="px-3 py-2 text-gray-600 whitespace-nowrap">{t.transaction_date}</td>
            <td className="px-3 py-2 text-gray-800 max-w-xs truncate">{t.description}</td>
            <td className="px-3 py-2 text-gray-500">{t.reference || "—"}</td>
            <td className="px-3 py-2 text-right font-mono text-green-700">
              {t.debit_amount ? fmt(t.debit_amount) : "—"}
            </td>
            <td className="px-3 py-2 text-right font-mono text-red-700">
              {t.credit_amount ? fmt(t.credit_amount) : "—"}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function XeroReconReportSummary({ report, onBack, onManualRecon }) {
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
      const data = await xeroBankReconApi.getSummary(report.report_id);
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
        const data = await xeroBankReconApi.getTransactions(report.report_id, {
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
      const data = await xeroBankReconApi.getMatches(report.report_id, "accepted");
      setReconciledMatches(data.matches || []);
    }
    setShowReconciled((v) => !v);
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      await xeroBankReconApi.saveReport(report.report_id);
      fetchSummary();
    } catch {
      alert("Failed to save report.");
    } finally {
      setSaving(false);
    }
  };

  const handleDownload = async () => {
    setDownloading(true);
    try {
      await xeroBankReconApi.downloadReport(report.report_id, report.report_name);
    } catch {
      alert("Failed to download report.");
    } finally {
      setDownloading(false);
    }
  };

  if (loading) {
    return (
      <div className="min-h-screen bg-gray-50 text-gray-900 flex items-center justify-center gap-3">
        <RefreshCw size={20} className="text-orange-500 animate-spin" />
        <span className="text-gray-500">Loading reconciliation summary...</span>
      </div>
    );
  }

  if (!summary) return null;

  const s = summary.summary || {};
  const diff = s.reconciliation_difference ?? 0;
  const isReconciled = Math.abs(diff) < 0.01;

  return (
    <div className="min-h-screen bg-gray-50 text-gray-900">
      <div className="max-w-3xl mx-auto px-4 py-8">
        {/* Header */}
        <div className="flex items-center gap-3 mb-6">
          <button onClick={onBack} className="p-2 rounded-lg hover:bg-gray-200 transition-colors">
            <ArrowLeft size={20} className="text-gray-600" />
          </button>
          <div className="flex-1">
            <div className="flex items-center gap-3">
              <h1 className="text-xl font-bold text-gray-900">Reconciliation Report</h1>
              {isReconciled ? (
                <span className="flex items-center gap-1.5 px-2.5 py-1 bg-green-100 text-green-700 text-xs font-semibold rounded-full">
                  <CheckCircle size={12} /> Fully Reconciled
                </span>
              ) : (
                <span className="flex items-center gap-1.5 px-2.5 py-1 bg-amber-100 text-amber-700 text-xs font-semibold rounded-full">
                  <AlertTriangle size={12} /> Partially Reconciled
                </span>
              )}
            </div>
            <p className="text-sm text-gray-500 mt-0.5">
              {report.report_name} · {summary.period_from} → {summary.period_to}
            </p>
          </div>
        </div>

        {/* Action buttons */}
        <div className="flex flex-wrap gap-2 mb-6">
          <button
            onClick={toggleReconciled}
            className="flex items-center gap-2 px-4 py-2 bg-white border border-gray-200 text-gray-700 text-sm rounded-lg hover:bg-gray-50 transition-colors"
          >
            {showReconciled ? <EyeOff size={14} /> : <Eye size={14} />}
            {showReconciled ? "Hide" : "View"} Reconciled ({summary.counts?.accepted || 0})
          </button>
          <button
            onClick={onManualRecon}
            className="flex items-center gap-2 px-4 py-2 bg-white border border-gray-200 text-gray-700 text-sm rounded-lg hover:bg-gray-50 transition-colors"
          >
            <Hand size={14} />
            Manual Reconciliation
          </button>
          <button
            onClick={handleDownload}
            disabled={downloading}
            className="flex items-center gap-2 px-4 py-2 bg-white border border-gray-200 text-gray-700 text-sm rounded-lg hover:bg-gray-50 transition-colors disabled:opacity-50"
          >
            {downloading ? <RefreshCw size={14} className="animate-spin" /> : <Download size={14} />}
            Download Excel
          </button>
          <button
            onClick={handleSave}
            disabled={saving}
            className="flex items-center gap-2 px-4 py-2 bg-orange-500 text-white text-sm font-medium rounded-lg hover:bg-orange-600 transition-colors disabled:opacity-50"
          >
            {saving ? <RefreshCw size={14} className="animate-spin" /> : <Save size={14} />}
            {saving ? "Saving..." : "Save Report"}
          </button>
        </div>

        {/* Reconciled transactions (collapsible) */}
        {showReconciled && (
          <div className="bg-white rounded-xl border border-gray-200 mb-6 overflow-hidden">
            <div className="px-4 py-3 bg-green-50 border-b border-green-100">
              <p className="text-sm font-semibold text-green-700">Reconciled Transactions ({reconciledMatches.length})</p>
            </div>
            <div className="overflow-x-auto max-h-64 overflow-y-auto">
              <table className="w-full text-xs">
                <thead className="bg-gray-50 sticky top-0">
                  <tr>
                    <th className="px-3 py-2 text-left text-gray-500">Bank Date</th>
                    <th className="px-3 py-2 text-left text-gray-500">Bank Description</th>
                    <th className="px-3 py-2 text-right text-gray-500">Amount</th>
                    <th className="px-3 py-2 text-left text-gray-500">Book Date</th>
                    <th className="px-3 py-2 text-left text-gray-500">Book Description</th>
                    <th className="px-3 py-2 text-center text-gray-500">Type</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-50">
                  {reconciledMatches.map((m) => (
                    <tr key={m.match_id} className="hover:bg-gray-50">
                      <td className="px-3 py-2 text-gray-600">{m.bank_transaction?.transaction_date}</td>
                      <td className="px-3 py-2 text-gray-800 max-w-xs truncate">{m.bank_transaction?.description}</td>
                      <td className="px-3 py-2 text-right font-mono text-gray-700">
                        {fmt(m.bank_transaction?.credit_amount || m.bank_transaction?.debit_amount || 0)}
                      </td>
                      <td className="px-3 py-2 text-gray-600">{m.book_transaction?.transaction_date}</td>
                      <td className="px-3 py-2 text-gray-800 max-w-xs truncate">{m.book_transaction?.description}</td>
                      <td className="px-3 py-2 text-center">
                        <span className={`text-xs px-1.5 py-0.5 rounded-full ${
                          m.match_type === "automatic" ? "bg-blue-100 text-blue-600" : "bg-purple-100 text-purple-600"
                        }`}>
                          {m.match_type === "automatic" ? "Auto" : "Manual"}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* Main reconciliation statement */}
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
          <div className="px-5 py-4 bg-gray-50 border-b border-gray-200">
            <h2 className="font-semibold text-gray-900">Reconciliation Statement</h2>
            <p className="text-xs text-gray-500 mt-0.5">{summary.account_name}</p>
          </div>

          <div className="divide-y divide-gray-100">
            {/* Book CB */}
            <div className="flex items-center justify-between py-3.5 px-5">
              <span className="text-sm font-semibold text-gray-900">Book Closing Balance</span>
              <span className="font-mono text-sm font-bold text-gray-900">{fmt(summary.book_closing_balance)}</span>
            </div>

            <div className="px-2 py-1">
              <p className="text-xs text-gray-500 px-2 py-1">Adjustments</p>
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
                ? <div className="flex justify-center py-3"><RefreshCw size={14} className="animate-spin text-gray-400" /></div>
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
                ? <div className="flex justify-center py-3"><RefreshCw size={14} className="animate-spin text-gray-400" /></div>
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
                ? <div className="flex justify-center py-3"><RefreshCw size={14} className="animate-spin text-gray-400" /></div>
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
                ? <div className="flex justify-center py-3"><RefreshCw size={14} className="animate-spin text-gray-400" /></div>
                : <TxnDetailTable txns={categoryTxns.cat4} />}
            </SummaryRow>

            {/* Divider */}
            <div className="border-t-2 border-gray-200 mx-5" />

            {/* Reconciled Amount */}
            <div className="flex items-center justify-between py-3.5 px-5 bg-blue-50">
              <span className="text-sm font-semibold text-blue-900">Reconciled Amount</span>
              <span className="font-mono text-sm font-bold text-blue-900">{fmt(s.reconciled_amount)}</span>
            </div>

            {/* Bank CB */}
            <div className="flex items-center justify-between py-3.5 px-5">
              <span className="text-sm font-semibold text-gray-900">Bank Statement Closing Balance</span>
              <span className="font-mono text-sm font-bold text-gray-900">{fmt(summary.bank_closing_balance)}</span>
            </div>

            {/* Double line */}
            <div className="border-t-2 border-gray-800 mx-5" />
            <div className="border-t-2 border-gray-800 mx-5 mb-0" />

            {/* Reconciliation Difference */}
            <div className={`flex items-center justify-between py-4 px-5 ${isReconciled ? "bg-green-50" : "bg-red-50"}`}>
              <div className="flex items-center gap-2">
                <span className="text-sm font-bold text-gray-900">Reconciliation Difference</span>
                {isReconciled && <CheckCircle size={16} className="text-green-500" />}
                {!isReconciled && <AlertTriangle size={16} className="text-amber-500" />}
              </div>
              <span className={`font-mono text-lg font-bold ${isReconciled ? "text-green-600" : "text-red-600"}`}>
                {fmt(diff)}
              </span>
            </div>
          </div>
        </div>

        {/* Opening balances footnote */}
        <div className="mt-4 bg-white rounded-xl border border-gray-200 px-5 py-3">
          <p className="text-xs font-medium text-gray-500 mb-2">Opening Balances</p>
          <div className="grid grid-cols-2 gap-4 text-xs">
            <div>
              <span className="text-gray-500">Book OB: </span>
              <span className="font-mono font-medium text-gray-700">{fmt(summary.book_opening_balance)}</span>
            </div>
            <div>
              <span className="text-gray-500">Bank OB: </span>
              <span className="font-mono font-medium text-gray-700">{fmt(summary.bank_opening_balance)}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
