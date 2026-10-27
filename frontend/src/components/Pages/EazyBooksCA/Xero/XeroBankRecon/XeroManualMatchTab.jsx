import React, { useEffect, useState, useMemo } from "react";
import { Search, X, Link, FileText, BarChart3, RefreshCw, Clock, Undo2 } from "lucide-react";
import { xeroBankReconApi } from "../../../../../services/api";

const fmt = (n) =>
  n != null
    ? Number(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    : null;

function TxnRow({ txn, selected, onClick, source, onMarkPending }) {
  const isBank = source === "bank_statement";
  return (
    <div
      className={`w-full rounded-lg border transition-all text-xs mb-1 ${
        selected
          ? "border-orange-400 bg-orange-50 shadow-sm"
          : "border-gray-100 hover:border-orange-300 hover:bg-orange-50/50 bg-white"
      }`}
    >
      <button onClick={onClick} className="w-full text-left px-3 py-2.5">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <p className="font-medium text-gray-800 truncate">{txn.description || "(no description)"}</p>
            <p className="text-gray-500 mt-0.5">
              {txn.transaction_date}
              {txn.reference && <span className="ml-2">· {txn.reference}</span>}
            </p>
          </div>
          <div className="text-right flex-shrink-0">
            {txn.debit_amount != null && (
              <p className={`font-mono font-medium ${isBank ? "text-red-600" : "text-green-600"}`}>
                Dr {fmt(txn.debit_amount)}
              </p>
            )}
            {txn.credit_amount != null && (
              <p className={`font-mono font-medium ${isBank ? "text-green-600" : "text-red-600"}`}>
                Cr {fmt(txn.credit_amount)}
              </p>
            )}
          </div>
        </div>
      </button>
      {onMarkPending && (
        <div className="px-3 pb-1.5 -mt-1 text-right">
          <button
            onClick={(e) => { e.stopPropagation(); onMarkPending(txn); }}
            className="text-[11px] text-amber-600 hover:text-amber-800 hover:underline inline-flex items-center gap-1"
            title="Set aside for later review (no changes to Xero)"
          >
            <Clock size={11} /> Set aside
          </button>
        </div>
      )}
    </div>
  );
}

export default function XeroManualMatchTab({ reportId, onViewReport }) {
  const [bankTxns, setBankTxns] = useState([]);
  const [bookTxns, setBookTxns] = useState([]);
  const [pendingTxns, setPendingTxns] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selectedBank, setSelectedBank] = useState(null);
  const [selectedBook, setSelectedBook] = useState(null);
  const [reconciling, setReconciling] = useState(false);
  const [bankSearch, setBankSearch] = useState("");
  const [bookSearch, setBookSearch] = useState("");

  const fetchUnmatched = async () => {
    setLoading(true);
    try {
      const [bankData, bookData, pendingData] = await Promise.all([
        xeroBankReconApi.getTransactions(reportId, { source: "bank_statement", recon_status: "unmatched" }),
        xeroBankReconApi.getTransactions(reportId, { source: "book", recon_status: "unmatched" }),
        xeroBankReconApi.getTransactions(reportId, { recon_status: "pending" }),
      ]);
      setBankTxns(bankData.transactions || []);
      setBookTxns(bookData.transactions || []);
      setPendingTxns(pendingData.transactions || []);
    } catch {
      // keep stale
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchUnmatched();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reportId]);

  const filteredBank = useMemo(() => {
    if (!bankSearch) return bankTxns;
    const q = bankSearch.toLowerCase();
    return bankTxns.filter(
      (t) => t.description?.toLowerCase().includes(q) || t.reference?.toLowerCase().includes(q)
    );
  }, [bankTxns, bankSearch]);

  const filteredBook = useMemo(() => {
    if (!bookSearch) return bookTxns;
    const q = bookSearch.toLowerCase();
    return bookTxns.filter(
      (t) => t.description?.toLowerCase().includes(q) || t.reference?.toLowerCase().includes(q)
    );
  }, [bookTxns, bookSearch]);

  const handleBankSelect = (txn) => {
    setSelectedBank(txn.txn_id === selectedBank?.txn_id ? null : txn);
    setSelectedBook(null);
  };

  const handleReconcile = async () => {
    if (!selectedBank || !selectedBook) return;
    setReconciling(true);
    try {
      await xeroBankReconApi.createManualMatch(reportId, selectedBook.txn_id, selectedBank.txn_id);
      // Remove reconciled entries from lists
      setBankTxns((prev) => prev.filter((t) => t.txn_id !== selectedBank.txn_id));
      setBookTxns((prev) => prev.filter((t) => t.txn_id !== selectedBook.txn_id));
      setSelectedBank(null);
      setSelectedBook(null);
    } catch {
      alert("Failed to reconcile. Please try again.");
    } finally {
      setReconciling(false);
    }
  };

  // Set a leftover unmatched row aside for later (no Xero writes).
  const handleMarkPending = async (txn) => {
    try {
      await xeroBankReconApi.markPending(reportId, [txn.txn_id]);
      setBankTxns((prev) => prev.filter((t) => t.txn_id !== txn.txn_id));
      setBookTxns((prev) => prev.filter((t) => t.txn_id !== txn.txn_id));
      setPendingTxns((prev) => [...prev, txn]);
      if (selectedBank?.txn_id === txn.txn_id) setSelectedBank(null);
      if (selectedBook?.txn_id === txn.txn_id) setSelectedBook(null);
    } catch {
      alert("Failed to set aside. Please try again.");
    }
  };

  const handleRestorePending = async (txn) => {
    try {
      await xeroBankReconApi.unmarkPending(reportId, [txn.txn_id]);
      setPendingTxns((prev) => prev.filter((t) => t.txn_id !== txn.txn_id));
      if (txn.source === "bank_statement") setBankTxns((prev) => [...prev, txn]);
      else setBookTxns((prev) => [...prev, txn]);
    } catch {
      alert("Failed to restore. Please try again.");
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-16 gap-3">
        <RefreshCw size={20} className="text-orange-500 animate-spin" />
        <span className="text-gray-500 text-sm">Loading unmatched transactions...</span>
      </div>
    );
  }

  const bothEmpty = bankTxns.length === 0 && bookTxns.length === 0;

  return (
    <div className="space-y-4">
      {/* Action bar */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 text-sm text-gray-500 flex-wrap">
          <span className="bg-blue-100 text-blue-700 px-2.5 py-1 rounded-full text-xs font-medium">
            {bankTxns.length} unmatched bank
          </span>
          <span className="bg-blue-50 text-blue-500 px-2.5 py-1 rounded-full text-xs">
            {bankTxns.filter(t => t.debit_amount != null).length} debits · {bankTxns.filter(t => t.credit_amount != null).length} credits
          </span>
          <span className="bg-purple-100 text-purple-700 px-2.5 py-1 rounded-full text-xs font-medium">
            {bookTxns.length} unmatched book
          </span>
          <span className="bg-purple-50 text-purple-500 px-2.5 py-1 rounded-full text-xs">
            {bookTxns.filter(t => t.debit_amount != null).length} debits · {bookTxns.filter(t => t.credit_amount != null).length} credits
          </span>
          {pendingTxns.length > 0 && (
            <span className="bg-amber-100 text-amber-700 px-2.5 py-1 rounded-full text-xs font-medium inline-flex items-center gap-1">
              <Clock size={11} /> {pendingTxns.length} pending
            </span>
          )}
        </div>
        <button
          onClick={onViewReport}
          className="flex items-center gap-1.5 px-4 py-2 bg-gray-900 text-white text-sm font-medium rounded-lg hover:bg-gray-700 transition-colors"
        >
          <BarChart3 size={14} />
          View Report
        </button>
      </div>

      {/* Matching box */}
      {selectedBank && (
        <div className="bg-orange-50 border-2 border-orange-300 rounded-xl p-4">
          <div className="flex items-start justify-between gap-3">
            <div className="flex-1">
              <p className="text-xs font-semibold text-orange-600 mb-1">📌 Selected Bank Entry</p>
              <p className="font-medium text-gray-900 text-sm">{selectedBank.description}</p>
              <p className="text-xs text-gray-500 mt-0.5">
                {selectedBank.transaction_date}
                {selectedBank.reference && <span className="ml-2">· {selectedBank.reference}</span>}
                {selectedBank.debit_amount != null && <span className="ml-2 text-red-600 font-mono">Dr {fmt(selectedBank.debit_amount)}</span>}
                {selectedBank.credit_amount != null && <span className="ml-2 text-green-600 font-mono">Cr {fmt(selectedBank.credit_amount)}</span>}
              </p>
            </div>
            <button
              onClick={() => { setSelectedBank(null); setSelectedBook(null); }}
              className="p-1.5 rounded-lg hover:bg-orange-100 transition-colors"
            >
              <X size={14} className="text-orange-500" />
            </button>
          </div>
          {selectedBook && (
            <div className="mt-3 pt-3 border-t border-orange-200">
              <p className="text-xs font-semibold text-purple-600 mb-1">Matching with Book Entry</p>
              <p className="font-medium text-gray-900 text-sm">{selectedBook.description}</p>
              <p className="text-xs text-gray-500 mt-0.5">
                {selectedBook.transaction_date}
                {selectedBook.reference && <span className="ml-2">· {selectedBook.reference}</span>}
                {selectedBook.debit_amount != null && <span className="ml-2 text-green-600 font-mono">Dr {fmt(selectedBook.debit_amount)}</span>}
                {selectedBook.credit_amount != null && <span className="ml-2 text-red-600 font-mono">Cr {fmt(selectedBook.credit_amount)}</span>}
              </p>
            </div>
          )}
          <div className="mt-3 flex items-center gap-2">
            <button
              onClick={handleReconcile}
              disabled={!selectedBook || reconciling}
              className="flex items-center gap-1.5 px-4 py-2 bg-orange-500 text-white text-sm font-medium rounded-lg hover:bg-orange-600 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {reconciling ? <RefreshCw size={13} className="animate-spin" /> : <Link size={13} />}
              {reconciling ? "Reconciling..." : "Reconcile"}
            </button>
            {!selectedBook && (
              <p className="text-xs text-orange-600">Select a book entry below to reconcile</p>
            )}
          </div>
        </div>
      )}

      {bothEmpty ? (
        <div className="flex flex-col items-center justify-center py-16 text-center gap-3">
          <div className="w-14 h-14 rounded-full bg-green-100 flex items-center justify-center">
            <FileText size={24} className="text-green-500" />
          </div>
          <p className="text-gray-700 font-medium">All entries reconciled!</p>
          <p className="text-gray-500 text-sm">Click "View Report" to see the reconciliation summary.</p>
          <button
            onClick={onViewReport}
            className="mt-1 px-5 py-2 bg-orange-500 text-white text-sm font-medium rounded-lg hover:bg-orange-600 transition-colors"
          >
            View Reconciliation Report →
          </button>
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-4">
          {/* Bank entries panel */}
          <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
            <div className="px-4 py-3 bg-blue-50 border-b border-blue-100 flex items-center justify-between">
              <p className="text-xs font-semibold text-blue-700">Unmatched Bank Entries ({bankTxns.length})</p>
            </div>
            <div className="p-3">
              <div className="relative mb-2">
                <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-400" />
                <input
                  type="text"
                  placeholder="Search..."
                  value={bankSearch}
                  onChange={(e) => setBankSearch(e.target.value)}
                  className="w-full pl-7 pr-3 py-1.5 border border-gray-200 rounded-lg text-xs text-gray-900 bg-white focus:outline-none focus:ring-1 focus:ring-orange-400"
                />
              </div>
              <div className="max-h-80 overflow-y-auto">
                {filteredBank.length === 0 ? (
                  <p className="text-center text-gray-500 text-xs py-6">No entries</p>
                ) : (
                  filteredBank.map((t) => (
                    <TxnRow
                      key={t.txn_id}
                      txn={t}
                      source="bank_statement"
                      selected={selectedBank?.txn_id === t.txn_id}
                      onClick={() => handleBankSelect(t)}
                      onMarkPending={handleMarkPending}
                    />
                  ))
                )}
              </div>
            </div>
          </div>

          {/* Book entries panel */}
          <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
            <div className="px-4 py-3 bg-purple-50 border-b border-purple-100 flex items-center justify-between">
              <p className="text-xs font-semibold text-purple-700">Unmatched Book Entries ({bookTxns.length})</p>
            </div>
            <div className="p-3">
              <div className="relative mb-2">
                <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-400" />
                <input
                  type="text"
                  placeholder="Search..."
                  value={bookSearch}
                  onChange={(e) => setBookSearch(e.target.value)}
                  className="w-full pl-7 pr-3 py-1.5 border border-gray-200 rounded-lg text-xs text-gray-900 bg-white focus:outline-none focus:ring-1 focus:ring-orange-400"
                />
              </div>
              <div className="max-h-80 overflow-y-auto">
                {filteredBook.length === 0 ? (
                  <p className="text-center text-gray-500 text-xs py-6">No entries</p>
                ) : (
                  filteredBook.map((t) => (
                    <TxnRow
                      key={t.txn_id}
                      txn={t}
                      source="book"
                      selected={selectedBook?.txn_id === t.txn_id}
                      onClick={() => selectedBank && setSelectedBook(t.txn_id === selectedBook?.txn_id ? null : t)}
                      onMarkPending={handleMarkPending}
                    />
                  ))
                )}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Pending review (set-aside rows; no Xero writes) */}
      {pendingTxns.length > 0 && (
        <div className="bg-amber-50 border border-amber-200 rounded-xl overflow-hidden">
          <div className="px-4 py-3 border-b border-amber-200 flex items-center gap-2">
            <Clock size={14} className="text-amber-600" />
            <p className="text-xs font-semibold text-amber-800 uppercase tracking-wide">
              Pending review ({pendingTxns.length})
            </p>
            <span className="text-[11px] text-amber-600">— set aside, excluded from the difference</span>
          </div>
          <div className="p-3 space-y-1 max-h-60 overflow-y-auto">
            {pendingTxns.map((t) => (
              <div
                key={t.txn_id}
                className="flex items-center justify-between gap-2 bg-white border border-amber-200 rounded-lg px-3 py-2 text-xs"
              >
                <span className="inline-flex items-center gap-1 px-1.5 py-0.5 text-[10px] font-medium rounded-full bg-amber-100 text-amber-700 uppercase flex-shrink-0">
                  {t.source === "bank_statement" ? "bank" : "book"}
                </span>
                <span className="truncate flex-1 text-gray-800">
                  {t.transaction_date} · {t.description || "(no description)"}
                </span>
                <span className="font-mono text-gray-600 flex-shrink-0">
                  {t.debit_amount != null ? `Dr ${fmt(t.debit_amount)}` : `Cr ${fmt(t.credit_amount)}`}
                </span>
                <button
                  onClick={() => handleRestorePending(t)}
                  className="text-[11px] text-amber-700 hover:text-amber-900 hover:underline inline-flex items-center gap-1 flex-shrink-0"
                  title="Move back to the unmatched list"
                >
                  <Undo2 size={11} /> Restore
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {!bothEmpty && (
        <p className="text-xs text-gray-600 text-center">
          Click a bank entry to select it, then click a book entry and press Reconcile. Use "Set aside" to defer a row.
        </p>
      )}
    </div>
  );
}
