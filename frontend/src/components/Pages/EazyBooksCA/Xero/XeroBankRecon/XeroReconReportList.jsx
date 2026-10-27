import React, { useEffect, useState } from "react";
import {
  ArrowLeft, Plus, Eye, Download, Pencil, Trash2,
  RefreshCw, AlertCircle, FileText,
} from "lucide-react";
import { xeroBankReconApi } from "../../../../../services/api";

const fmt = (n) =>
  n != null
    ? new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(n)
    : "—";

const STATUS_BADGE = {
  completed: { label: "Completed", classes: "bg-green-100 text-green-700" },
  in_progress: { label: "In Progress", classes: "bg-blue-100 text-blue-700" },
  draft: { label: "Draft", classes: "bg-gray-100 text-gray-600" },
  processing: { label: "Processing", classes: "bg-yellow-100 text-yellow-700" },
  awaiting_source: { label: "Awaiting Source", classes: "bg-amber-100 text-amber-700" },
  error: { label: "Error", classes: "bg-red-100 text-red-700" },
};

export default function XeroReconReportList({
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
      const data = await xeroBankReconApi.listReports(selectedAccount?.id, selectedCompany);
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
      await xeroBankReconApi.deleteReport(reportId);
      setReports((prev) => prev.filter((r) => r.report_id !== reportId));
    } catch {
      alert("Failed to delete report.");
    } finally {
      setDeletingId(null);
    }
  };

  const handleDownload = async (report) => {
    try {
      await xeroBankReconApi.downloadReport(report.report_id, report.report_name);
    } catch {
      alert("Failed to download report.");
    }
  };

  return (
    <div className="min-h-screen bg-gray-50 text-gray-900">
      <div className="max-w-6xl mx-auto px-4 py-8">
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <div className="flex items-center gap-3">
            <button
              onClick={onBack}
              className="p-2 rounded-lg hover:bg-gray-200 transition-colors"
            >
              <ArrowLeft size={20} className="text-gray-600" />
            </button>
            <div>
              <h1 className="text-xl font-bold text-gray-900">Bank Reconciliation</h1>
              <p className="text-sm text-gray-500">
                {selectedAccount?.name}
              </p>
            </div>
          </div>
          <button
            onClick={onNewReport}
            className="flex items-center gap-2 bg-orange-500 text-white px-4 py-2.5 rounded-lg hover:bg-orange-600 transition-colors text-sm font-medium shadow-sm"
          >
            <Plus size={16} />
            New Reconciliation
          </button>
        </div>

        {/* Table */}
        <div className="bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden">
          {loading ? (
            <div className="flex items-center justify-center py-16 gap-3">
              <RefreshCw size={20} className="text-orange-500 animate-spin" />
              <span className="text-gray-500 text-sm">Loading reports...</span>
            </div>
          ) : error ? (
            <div className="flex flex-col items-center justify-center py-16 gap-3">
              <AlertCircle size={40} className="text-red-400" />
              <p className="text-gray-500 text-sm">{error}</p>
              <button onClick={fetchReports} className="px-4 py-2 bg-orange-500 text-white text-sm rounded-lg hover:bg-orange-600">
                Retry
              </button>
            </div>
          ) : reports.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-20 gap-4 text-center px-6">
              <div className="w-16 h-16 rounded-full bg-orange-50 flex items-center justify-center">
                <FileText size={28} className="text-orange-400" />
              </div>
              <div>
                <p className="text-gray-700 font-medium">No reconciliation reports yet</p>
                <p className="text-gray-500 text-sm mt-1">
                  Click "New Reconciliation" to get started
                </p>
              </div>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="bg-gray-50 border-b border-gray-200">
                    <th className="text-left px-4 py-3 font-medium text-gray-600">Name</th>
                    <th className="text-left px-4 py-3 font-medium text-gray-600">Period</th>
                    <th className="text-right px-4 py-3 font-medium text-gray-600">Book OB</th>
                    <th className="text-right px-4 py-3 font-medium text-gray-600">Bank OB</th>
                    <th className="text-right px-4 py-3 font-medium text-gray-600">Book CB</th>
                    <th className="text-right px-4 py-3 font-medium text-gray-600">Bank CB</th>
                    <th className="text-center px-4 py-3 font-medium text-gray-600">FY</th>
                    <th className="text-center px-4 py-3 font-medium text-gray-600">Status</th>
                    <th className="text-center px-4 py-3 font-medium text-gray-600">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {reports.map((r) => {
                    const badge = STATUS_BADGE[r.status] || STATUS_BADGE.draft;
                    const diff = r.reconciliation_difference;
                    return (
                      <tr key={r.report_id} className="hover:bg-gray-50 transition-colors">
                        <td className="px-4 py-3">
                          <button
                            onClick={() => onOpenReport(r)}
                            className="font-medium text-gray-900 hover:text-orange-600 transition-colors text-left"
                          >
                            {r.report_name}
                          </button>
                          {diff != null && (
                            <span className={`ml-2 text-xs font-medium ${Math.abs(diff) < 0.01 ? "text-green-600" : "text-red-500"}`}>
                              {Math.abs(diff) < 0.01 ? "✓ Reconciled" : `Diff: ${fmt(diff)}`}
                            </span>
                          )}
                        </td>
                        <td className="px-4 py-3 text-gray-600 whitespace-nowrap">
                          {r.period_from} → {r.period_to}
                        </td>
                        <td className="px-4 py-3 text-right text-gray-700 font-mono">{fmt(r.book_opening_balance)}</td>
                        <td className="px-4 py-3 text-right text-gray-700 font-mono">{fmt(r.bank_opening_balance)}</td>
                        <td className="px-4 py-3 text-right text-gray-700 font-mono">{fmt(r.book_closing_balance)}</td>
                        <td className="px-4 py-3 text-right text-gray-700 font-mono">{fmt(r.bank_closing_balance)}</td>
                        <td className="px-4 py-3 text-center text-gray-500">{r.financial_year || "—"}</td>
                        <td className="px-4 py-3 text-center">
                          <span className={`inline-flex px-2 py-0.5 rounded-full text-xs font-medium ${badge.classes}`}>
                            {badge.label}
                          </span>
                        </td>
                        <td className="px-4 py-3">
                          <div className="flex items-center justify-center gap-1">
                            <button
                              onClick={() => onOpenReport(r)}
                              title="View"
                              className="p-1.5 rounded hover:bg-blue-50 text-blue-500 transition-colors"
                            >
                              <Eye size={15} />
                            </button>
                            <button
                              onClick={() => handleDownload(r)}
                              title="Download"
                              className="p-1.5 rounded hover:bg-green-50 text-green-600 transition-colors"
                            >
                              <Download size={15} />
                            </button>
                            <button
                              onClick={() => onOpenReport(r, true)}
                              title="Edit"
                              className="p-1.5 rounded hover:bg-orange-50 text-orange-500 transition-colors"
                            >
                              <Pencil size={15} />
                            </button>
                            <button
                              onClick={() => handleDelete(r.report_id, r.report_name)}
                              title="Delete"
                              disabled={deletingId === r.report_id}
                              className="p-1.5 rounded hover:bg-red-50 text-red-400 transition-colors disabled:opacity-40"
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
