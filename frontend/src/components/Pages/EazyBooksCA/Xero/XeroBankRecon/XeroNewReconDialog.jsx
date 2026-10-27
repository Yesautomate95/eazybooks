import React, { useEffect, useRef, useState } from "react";
import { X, Upload, FileText, RefreshCw, Landmark, AlertCircle } from "lucide-react";
import { xeroBankReconApi } from "../../../../../services/api";

export default function XeroNewReconDialog({ selectedCompany, selectedAccount, onClose, onCreate }) {
  const [reportName, setReportName] = useState("");
  const [periodFrom, setPeriodFrom] = useState("");
  const [periodTo, setPeriodTo] = useState("");
  const [closingBalance, setClosingBalance] = useState("");
  const [file, setFile] = useState(null);
  const [dragging, setDragging] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const fileRef = useRef();

  // Load default report name
  useEffect(() => {
    xeroBankReconApi.getNextReportName().then((d) => setReportName(d.report_name || "")).catch(() => {});
    // Default period: current financial year Apr 1 – Mar 31
    const today = new Date();
    const fy_start = today.getMonth() >= 3
      ? new Date(today.getFullYear(), 3, 1)
      : new Date(today.getFullYear() - 1, 3, 1);
    const fy_end = new Date(fy_start.getFullYear() + 1, 2, 31);
    setPeriodFrom(fy_start.toISOString().slice(0, 10));
    setPeriodTo(fy_end.toISOString().slice(0, 10));
  }, []);

  const handleFileDrop = (e) => {
    e.preventDefault();
    setDragging(false);
    const f = e.dataTransfer.files[0];
    if (f) validateAndSetFile(f);
  };

  const validateAndSetFile = (f) => {
    const ext = f.name.split(".").pop().toLowerCase();
    if (!["pdf", "xlsx", "xls"].includes(ext)) {
      setError("Only PDF and Excel (.xlsx, .xls) files are supported.");
      return;
    }
    setFile(f);
    setError(null);
  };

  const handleFileInput = (e) => {
    const f = e.target.files[0];
    if (f) validateAndSetFile(f);
  };

  const canProceed = file && reportName.trim() && periodFrom && periodTo && !loading;

  const handleProceed = async () => {
    if (!canProceed) return;
    if (periodFrom > periodTo) {
      setError("'From' date must be before 'To' date.");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const fd = new FormData();
      fd.append("account_id", selectedAccount?.id);
      fd.append("account_name", selectedAccount?.name || "");
      fd.append("report_name", reportName.trim());
      fd.append("period_from", periodFrom);
      fd.append("period_to", periodTo);
      fd.append("statement_file", file);
      if (selectedCompany) fd.append("tenant_id", selectedCompany);
      if (closingBalance !== "" && !Number.isNaN(Number(closingBalance))) {
        fd.append("xero_closing_balance", closingBalance);
      }
      const report = await xeroBankReconApi.createReport(fd);
      onCreate(report);
    } catch (err) {
      setError(err.response?.data?.detail || "Failed to create report. Please try again.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm">
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-lg mx-4 overflow-hidden text-gray-900">
        {/* Modal header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-100">
          <h2 className="text-lg font-semibold text-gray-900">New Bank Reconciliation</h2>
          <button onClick={onClose} className="p-1.5 rounded-lg hover:bg-gray-100 transition-colors">
            <X size={18} className="text-gray-500" />
          </button>
        </div>

        <div className="px-6 py-5 space-y-5">
          {/* Bank account (read-only) */}
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-1.5">Bank Account</label>
            <div className="flex items-center gap-2 px-3 py-2.5 bg-gray-50 border border-gray-200 rounded-lg">
              <Landmark size={15} className="text-orange-500" />
              <span className="text-sm font-medium text-gray-800">{selectedAccount?.name}</span>
            </div>
          </div>

          {/* File upload */}
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-1.5">Bank Statement</label>
            {file ? (
              <div className="flex items-center justify-between px-3 py-2.5 bg-green-50 border border-green-200 rounded-lg">
                <div className="flex items-center gap-2">
                  <FileText size={15} className="text-green-600" />
                  <span className="text-sm text-green-800 font-medium truncate max-w-xs">{file.name}</span>
                  <span className="text-xs text-green-600">({(file.size / 1024).toFixed(0)} KB)</span>
                </div>
                <button onClick={() => setFile(null)} className="p-1 hover:bg-green-100 rounded transition-colors">
                  <X size={14} className="text-green-600" />
                </button>
              </div>
            ) : (
              <div
                onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
                onDragLeave={() => setDragging(false)}
                onDrop={handleFileDrop}
                onClick={() => fileRef.current?.click()}
                className={`border-2 border-dashed rounded-xl p-6 text-center cursor-pointer transition-colors ${
                  dragging ? "border-orange-400 bg-orange-50" : "border-gray-200 hover:border-orange-300 hover:bg-orange-50/50"
                }`}
              >
                <Upload size={24} className="mx-auto text-gray-400 mb-2" />
                <p className="text-sm text-gray-600">Drag & drop or <span className="text-orange-500 font-medium">browse</span></p>
                <p className="text-xs text-gray-400 mt-1">PDF, XLSX, or XLS</p>
                <input
                  ref={fileRef}
                  type="file"
                  accept=".pdf,.xlsx,.xls"
                  className="hidden"
                  onChange={handleFileInput}
                />
              </div>
            )}
          </div>

          {/* Report name */}
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-1.5">Report Name</label>
            <input
              type="text"
              value={reportName}
              onChange={(e) => setReportName(e.target.value)}
              className="w-full px-3 py-2.5 border border-gray-200 rounded-lg text-sm text-gray-900 bg-white focus:outline-none focus:ring-2 focus:ring-orange-500 focus:border-transparent"
              placeholder="e.g. BankRecon_20260405_001"
            />
          </div>

          {/* Period */}
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-1.5">Reconciliation Period</label>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-xs text-gray-400 mb-1">From</label>
                <input
                  type="date"
                  value={periodFrom}
                  onChange={(e) => setPeriodFrom(e.target.value)}
                  className="w-full px-3 py-2.5 border border-gray-200 rounded-lg text-sm text-gray-900 bg-white focus:outline-none focus:ring-2 focus:ring-orange-500"
                />
              </div>
              <div>
                <label className="block text-xs text-gray-400 mb-1">To</label>
                <input
                  type="date"
                  value={periodTo}
                  onChange={(e) => setPeriodTo(e.target.value)}
                  className="w-full px-3 py-2.5 border border-gray-200 rounded-lg text-sm text-gray-900 bg-white focus:outline-none focus:ring-2 focus:ring-orange-500"
                />
              </div>
            </div>
          </div>

          {/* Closing balance (optional) */}
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-1.5">
              Statement Closing Balance <span className="text-gray-400">(optional)</span>
            </label>
            <input
              type="number"
              step="any"
              value={closingBalance}
              onChange={(e) => setClosingBalance(e.target.value)}
              className="w-full px-3 py-2.5 border border-gray-200 rounded-lg text-sm text-gray-900 bg-white focus:outline-none focus:ring-2 focus:ring-orange-500 focus:border-transparent"
              placeholder="e.g. 152340.00"
            />
          </div>

          {/* Error */}
          {error && (
            <div className="flex items-start gap-2 px-3 py-2.5 bg-red-50 border border-red-200 rounded-lg">
              <AlertCircle size={15} className="text-red-500 mt-0.5 flex-shrink-0" />
              <p className="text-sm text-red-700">{error}</p>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-3 px-6 py-4 border-t border-gray-100 bg-gray-50">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm text-gray-600 hover:text-gray-900 transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleProceed}
            disabled={!canProceed}
            className="flex items-center gap-2 px-5 py-2 bg-orange-500 text-white text-sm font-medium rounded-lg hover:bg-orange-600 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {loading && <RefreshCw size={14} className="animate-spin" />}
            {loading ? "Creating..." : "Proceed →"}
          </button>
        </div>
      </div>
    </div>
  );
}
