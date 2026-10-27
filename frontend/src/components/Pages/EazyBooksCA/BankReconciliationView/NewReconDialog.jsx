import React, { useEffect, useRef, useState } from "react";
import { X, Upload, FileText, RefreshCw, Landmark, AlertCircle } from "lucide-react";
import { bankReconApi } from "../../../../services/api";

export default function NewReconDialog({ selectedCompany, selectedLedger, onClose, onCreate }) {
  const [reportName, setReportName] = useState("");
  const [periodFrom, setPeriodFrom] = useState("");
  const [periodTo, setPeriodTo] = useState("");
  const [file, setFile] = useState(null);
  const [dragging, setDragging] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const fileRef = useRef();

  // Load default report name
  useEffect(() => {
    bankReconApi.getNextReportName().then((d) => setReportName(d.report_name || "")).catch(() => {});
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
      fd.append("company_name", selectedCompany);
      fd.append("bank_ledger_name", selectedLedger);
      fd.append("report_name", reportName.trim());
      fd.append("period_from", periodFrom);
      fd.append("period_to", periodTo);
      fd.append("statement_file", file);
      const report = await bankReconApi.createReport(fd);
      onCreate(report);
    } catch (err) {
      setError(err.response?.data?.detail || "Failed to create report. Please try again.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-gilt-ink/40 backdrop-blur-sm">
      <div className="bg-gilt-card rounded-xl border border-gilt-hairline w-full max-w-lg mx-4 overflow-hidden text-gilt-ink">
        {/* Modal header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-gilt-hairline">
          <h2 className="text-lg font-semibold text-gilt-ink">New Bank Reconciliation</h2>
          <button onClick={onClose} className="p-1.5 rounded-lg hover:bg-gilt-sunken transition-colors duration-150">
            <X size={18} className="text-gilt-soft" />
          </button>
        </div>

        <div className="px-6 py-5 space-y-5">
          {/* Bank ledger (read-only) */}
          <div>
            <label className="block text-xs font-medium text-gilt-soft mb-1.5">Bank Ledger</label>
            <div className="flex items-center gap-2 px-3 py-2.5 bg-gilt-sunken border border-gilt-hairline rounded-lg">
              <Landmark size={15} className="text-gilt-brass" />
              <span className="text-sm font-medium text-gilt-ink">{selectedLedger}</span>
            </div>
          </div>

          {/* File upload */}
          <div>
            <label className="block text-xs font-medium text-gilt-soft mb-1.5">Bank Statement</label>
            {file ? (
              <div className="flex items-center justify-between px-3 py-2.5 bg-gilt-sunken border border-gilt-credit/40 rounded-lg">
                <div className="flex items-center gap-2">
                  <FileText size={15} className="text-gilt-credit" />
                  <span className="text-sm text-gilt-ink font-medium truncate max-w-xs">{file.name}</span>
                  <span className="text-xs text-gilt-credit font-mono tabular-nums">({(file.size / 1024).toFixed(0)} KB)</span>
                </div>
                <button onClick={() => setFile(null)} className="p-1 hover:bg-gilt-card rounded transition-colors duration-150">
                  <X size={14} className="text-gilt-credit" />
                </button>
              </div>
            ) : (
              <div
                onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
                onDragLeave={() => setDragging(false)}
                onDrop={handleFileDrop}
                onClick={() => fileRef.current?.click()}
                className={`border-2 border-dashed rounded-xl p-6 text-center cursor-pointer transition-colors duration-150 ${
                  dragging ? "border-gilt-brass bg-gilt-sunken" : "border-gilt-hairline hover:border-gilt-brass hover:bg-gilt-sunken/60"
                }`}
              >
                <Upload size={24} className="mx-auto text-gilt-faint mb-2" />
                <p className="text-sm text-gilt-soft">Drag & drop or <span className="text-gilt-brass font-medium">browse</span></p>
                <p className="text-xs text-gilt-faint mt-1">PDF, XLSX, or XLS</p>
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
            <label className="block text-xs font-medium text-gilt-soft mb-1.5">Report Name</label>
            <input
              type="text"
              value={reportName}
              onChange={(e) => setReportName(e.target.value)}
              className="w-full px-3 py-2.5 border border-gilt-hairline rounded-lg text-sm text-gilt-ink bg-gilt-paper placeholder:text-gilt-faint focus:outline-none focus:border-gilt-brass transition-colors duration-150"
              placeholder="e.g. BankRecon_20260405_001"
            />
          </div>

          {/* Period */}
          <div>
            <label className="block text-xs font-medium text-gilt-soft mb-1.5">Reconciliation Period</label>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-xs text-gilt-faint mb-1">From</label>
                <input
                  type="date"
                  value={periodFrom}
                  onChange={(e) => setPeriodFrom(e.target.value)}
                  className="w-full px-3 py-2.5 border border-gilt-hairline rounded-lg text-sm text-gilt-ink bg-gilt-paper font-mono tabular-nums focus:outline-none focus:border-gilt-brass transition-colors duration-150"
                />
              </div>
              <div>
                <label className="block text-xs text-gilt-faint mb-1">To</label>
                <input
                  type="date"
                  value={periodTo}
                  onChange={(e) => setPeriodTo(e.target.value)}
                  className="w-full px-3 py-2.5 border border-gilt-hairline rounded-lg text-sm text-gilt-ink bg-gilt-paper font-mono tabular-nums focus:outline-none focus:border-gilt-brass transition-colors duration-150"
                />
              </div>
            </div>
          </div>

          {/* Error */}
          {error && (
            <div className="flex items-start gap-2 px-3 py-2.5 bg-gilt-sunken border border-gilt-debit/40 rounded-lg">
              <AlertCircle size={15} className="text-gilt-debit mt-0.5 flex-shrink-0" />
              <p className="text-sm text-gilt-debit">{error}</p>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-3 px-6 py-4 border-t border-gilt-hairline bg-gilt-sunken">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm text-gilt-soft hover:text-gilt-ink transition-colors duration-150"
          >
            Cancel
          </button>
          <button
            onClick={handleProceed}
            disabled={!canProceed}
            className="flex items-center gap-2 px-5 py-2 bg-gilt-brass text-gilt-paper text-sm font-medium rounded-md hover:opacity-90 transition-opacity duration-150 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {loading && <RefreshCw size={14} className="animate-spin" />}
            {loading ? "Creating..." : "Proceed →"}
          </button>
        </div>
      </div>
    </div>
  );
}
