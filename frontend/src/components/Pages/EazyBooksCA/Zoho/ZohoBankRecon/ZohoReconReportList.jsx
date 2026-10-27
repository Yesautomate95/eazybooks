import React, { useEffect, useState } from "react";
import {
  ArrowLeft, Plus, Eye, Download, Pencil, Trash2,
  RefreshCw, AlertCircle, FileText,
} from "lucide-react";
import { zohoBankReconApi } from "../../../../../services/api";
import { ToastHost, toast, errMsg } from "./toast";

const fmt = (n) =>
  n != null
    ? new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(n)
    : "—";

const STATUS_BADGE = {
  completed: { label: "Completed", classes: "border-gilt-credit/40 text-gilt-credit" },
  in_progress: { label: "In Progress", classes: "border-gilt-brass/40 text-gilt-brass" },
  draft: { label: "Draft", classes: "border-gilt-hairline text-gilt-soft" },
  processing: { label: "Processing", classes: "border-gilt-brass/40 text-gilt-brass" },
  awaiting_source: { label: "Awaiting Source", classes: "border-gilt-brass/40 text-gilt-brass" },
  error: { label: "Error", classes: "border-gilt-debit/40 text-gilt-debit" },
};

export default function ZohoReconReportList({
  selectedCompany,
  selectedAccount,
  onBack,
  onNewReport,
  onOpenReport,
}) {
  const [reports, setReports] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [deletingId, setDeletingId] = useState(null);

  const fetchReports = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await zohoBankReconApi.listReports(selectedAccount?.id, selectedCompany);
      setReports(data.reports || []);
    } catch (err) {
      setError("Failed to load reports.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchReports();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedCompany, selectedAccount]);

  const handleDelete = async (reportId, reportName) => {
    if (!window.confirm(`Delete "${reportName}"? This cannot be undone.`)) return;
    setDeletingId(reportId);
    try {
      await zohoBankReconApi.deleteReport(reportId);
      setReports((prev) => prev.filter((r) => r.report_id !== reportId));
      toast.success("Report deleted");
    } catch (e) {
      toast.error(errMsg(e, "Failed to delete report."));
    } finally {
      setDeletingId(null);
    }
  };

  const handleDownload = async (report) => {
    try {
      await zohoBankReconApi.downloadReport(report.report_id, report.report_name);
      toast.success("Report downloaded");
    } catch (e) {
      toast.error(errMsg(e, "Failed to download report."));
    }
  };

  return (
    <div className="min-h-screen text-gilt-ink">
      <ToastHost />
      <div className="max-w-6xl mx-auto px-4 py-8">
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <div className="flex items-center gap-3">
            <button
              onClick={onBack}
              className="p-2 rounded-lg hover:bg-gilt-sunken transition-colors duration-150"
            >
              <ArrowLeft size={20} className="text-gilt-soft" />
            </button>
            <div>
              <h1 className="gl-display text-xl text-gilt-ink">Bank Reconciliation</h1>
              <p className="text-sm text-gilt-soft">
                {selectedAccount?.name}
              </p>
            </div>
          </div>
          <button
            onClick={onNewReport}
            className="flex items-center gap-2 bg-gilt-brass text-gilt-paper px-4 py-2.5 rounded-md hover:opacity-90 transition-opacity duration-150 text-sm font-medium"
          >
            <Plus size={16} />
            New Reconciliation
          </button>
        </div>

        {/* Table */}
        <div className="bg-gilt-card rounded-xl border border-gilt-hairline overflow-hidden">
          {loading ? (
            <div className="flex items-center justify-center py-16 gap-3">
              <RefreshCw size={20} className="text-gilt-brass animate-spin" />
              <span className="text-gilt-soft text-sm">Loading reports...</span>
            </div>
          ) : error ? (
            <div className="flex flex-col items-center justify-center py-16 gap-3">
              <AlertCircle size={40} className="text-gilt-debit" />
              <p className="text-gilt-soft text-sm">{error}</p>
              <button onClick={fetchReports} className="px-4 py-2 bg-gilt-brass text-gilt-paper text-sm rounded-md hover:opacity-90 transition-opacity duration-150">
                Retry
              </button>
            </div>
          ) : reports.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-20 gap-4 text-center px-6">
              <div className="w-16 h-16 rounded-full bg-gilt-sunken border border-gilt-hairline flex items-center justify-center">
                <FileText size={28} className="text-gilt-brass" />
              </div>
              <div>
                <p className="text-gilt-ink font-medium">No reconciliation reports yet</p>
                <p className="text-gilt-soft text-sm mt-1">
                  Click "New Reconciliation" to get started
                </p>
              </div>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-gilt-hairline font-mono text-[10px] uppercase tracking-wider text-gilt-soft">
                    <th className="text-left px-4 py-3 font-medium">Name</th>
                    <th className="text-left px-4 py-3 font-medium">Period</th>
                    <th className="text-right px-4 py-3 font-medium">Book OB</th>
                    <th className="text-right px-4 py-3 font-medium">Bank OB</th>
                    <th className="text-right px-4 py-3 font-medium">Book CB</th>
                    <th className="text-right px-4 py-3 font-medium">Bank CB</th>
                    <th className="text-center px-4 py-3 font-medium">FY</th>
                    <th className="text-center px-4 py-3 font-medium">Status</th>
                    <th className="text-center px-4 py-3 font-medium">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gilt-hairline">
                  {reports.map((r) => {
                    const badge = STATUS_BADGE[r.status] || STATUS_BADGE.draft;
                    const diff = r.reconciliation_difference;
                    return (
                      <tr key={r.report_id} className="hover:bg-gilt-sunken transition-colors duration-150">
                        <td className="px-4 py-3">
                          <button
                            onClick={() => onOpenReport(r)}
                            className="font-medium text-gilt-ink hover:text-gilt-brass transition-colors duration-150 text-left"
                          >
                            {r.report_name}
                          </button>
                          {diff != null && (
                            <span className={`ml-2 text-xs font-mono tabular-nums font-medium ${Math.abs(diff) < 0.01 ? "text-gilt-credit" : "text-gilt-debit"}`}>
                              {Math.abs(diff) < 0.01 ? "✓ Reconciled" : `Diff: ${fmt(diff)}`}
                            </span>
                          )}
                        </td>
                        <td className="px-4 py-3 text-gilt-soft font-mono tabular-nums whitespace-nowrap">
                          {r.period_from} → {r.period_to}
                        </td>
                        <td className="px-4 py-3 text-right text-gilt-soft font-mono tabular-nums">{fmt(r.book_opening_balance)}</td>
                        <td className="px-4 py-3 text-right text-gilt-soft font-mono tabular-nums">{fmt(r.bank_opening_balance)}</td>
                        <td className="px-4 py-3 text-right text-gilt-soft font-mono tabular-nums">{fmt(r.book_closing_balance)}</td>
                        <td className="px-4 py-3 text-right text-gilt-soft font-mono tabular-nums">{fmt(r.bank_closing_balance)}</td>
                        <td className="px-4 py-3 text-center text-gilt-soft font-mono tabular-nums">{r.financial_year || "—"}</td>
                        <td className="px-4 py-3 text-center">
                          <span className={`inline-flex px-2 py-0.5 rounded-full font-mono text-[10px] uppercase tracking-[0.14em] border ${badge.classes}`}>
                            {badge.label}
                          </span>
                        </td>
                        <td className="px-4 py-3">
                          <div className="flex items-center justify-center gap-1">
                            <button
                              onClick={() => onOpenReport(r)}
                              title="View"
                              className="p-1.5 rounded hover:bg-gilt-sunken text-gilt-soft hover:text-gilt-brass transition-colors duration-150"
                            >
                              <Eye size={15} />
                            </button>
                            <button
                              onClick={() => handleDownload(r)}
                              title="Download"
                              className="p-1.5 rounded hover:bg-gilt-sunken text-gilt-soft hover:text-gilt-brass transition-colors duration-150"
                            >
                              <Download size={15} />
                            </button>
                            <button
                              onClick={() => onOpenReport(r, true)}
                              title="Edit"
                              className="p-1.5 rounded hover:bg-gilt-sunken text-gilt-soft hover:text-gilt-brass transition-colors duration-150"
                            >
                              <Pencil size={15} />
                            </button>
                            <button
                              onClick={() => handleDelete(r.report_id, r.report_name)}
                              title="Delete"
                              disabled={deletingId === r.report_id}
                              className="p-1.5 rounded hover:bg-gilt-sunken text-gilt-debit transition-colors duration-150 disabled:opacity-40"
                            >
                              {deletingId === r.report_id
                                ? <RefreshCw size={15} className="animate-spin" />
                                : <Trash2 size={15} />}
                            </button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
