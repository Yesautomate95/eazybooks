import React, { useEffect, useState, useMemo } from "react";
import { Check, X, RefreshCw, Search, CheckCircle } from "lucide-react";
import { xeroBankReconApi } from "../../../../../services/api";

const fmt = (n) => (n != null ? Number(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : "—");

export default function XeroAutoMatchTab({ reportId, onAllReviewed }) {
  const [matches, setMatches] = useState([]);
  const [loading, setLoading] = useState(true);
  const [updating, setUpdating] = useState(new Set());
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState(new Set());

  const fetchMatches = async () => {
    setLoading(true);
    try {
      const data = await xeroBankReconApi.getMatches(reportId);
      // Only show automatic matches (include all statuses so user sees progress)
      setMatches((data.matches || []).filter((m) => m.match_type === "automatic"));
    } catch {
      // keep stale data
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchMatches();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reportId]);

  const pendingCount = useMemo(() => matches.filter((m) => m.status === "pending_review").length, [matches]);
  const acceptedCount = useMemo(() => matches.filter((m) => m.status === "accepted").length, [matches]);
  const rejectedCount = useMemo(() => matches.filter((m) => m.status === "rejected").length, [matches]);
  const totalCount = matches.length;
  const reviewedCount = totalCount - pendingCount;
  const progress = totalCount > 0 ? Math.round((reviewedCount / totalCount) * 100) : 0;
  const allReviewed = totalCount > 0 && pendingCount === 0;

  const updateStatus = async (matchId, status) => {
    setUpdating((prev) => new Set([...prev, matchId]));
    try {
      await xeroBankReconApi.updateMatchStatus(matchId, status);
      setMatches((prev) =>
        prev.map((m) => (m.match_id === matchId ? { ...m, status } : m))
      );
      setSelected((prev) => { const n = new Set(prev); n.delete(matchId); return n; });
    } catch {
      alert("Failed to update match status.");
    } finally {
      setUpdating((prev) => { const n = new Set(prev); n.delete(matchId); return n; });
    }
  };

  const handleBulk = async (status) => {
    const ids = selected.size > 0 ? [...selected] : matches.filter((m) => m.status === "pending_review").map((m) => m.match_id);
    if (ids.length === 0) return;
    setUpdating(new Set(ids));
    try {
      await xeroBankReconApi.bulkUpdateMatches(ids, status);
      setMatches((prev) =>
        prev.map((m) => ids.includes(m.match_id) ? { ...m, status } : m)
      );
      setSelected(new Set());
    } catch {
      alert("Bulk update failed.");
    } finally {
      setUpdating(new Set());
    }
  };

  const toggleSelect = (id) => {
    setSelected((prev) => {
      const n = new Set(prev);
      n.has(id) ? n.delete(id) : n.add(id);
      return n;
    });
  };

  const toggleSelectAll = () => {
    const pending = matches.filter((m) => m.status === "pending_review").map((m) => m.match_id);
    if (selected.size === pending.length) {
      setSelected(new Set());
    } else {
      setSelected(new Set(pending));
    }
  };

  const filtered = useMemo(() => {
    if (!search) return matches;
    const q = search.toLowerCase();
    return matches.filter((m) =>
      m.bank_transaction?.description?.toLowerCase().includes(q) ||
      m.book_transaction?.description?.toLowerCase().includes(q) ||
      m.bank_transaction?.reference?.toLowerCase().includes(q)
    );
  }, [matches, search]);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-16 gap-3">
        <RefreshCw size={20} className="text-orange-500 animate-spin" />
        <span className="text-gray-500 text-sm">Loading automatic matches...</span>
      </div>
    );
  }

  if (totalCount === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-20 text-center gap-3">
        <Search size={40} className="text-gray-300" />
        <p className="text-gray-600 font-medium">No automatic matches found</p>
        <p className="text-gray-500 text-sm">Proceed to Manual Reconciliation to match remaining entries.</p>
        <button
          onClick={onAllReviewed}
          className="mt-2 px-5 py-2 bg-orange-500 text-white text-sm font-medium rounded-lg hover:bg-orange-600 transition-colors"
        >
          Go to Manual Reconciliation →
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Progress bar + category counts */}
      <div className="bg-white rounded-xl border border-gray-200 p-4">
        <div className="flex items-center justify-between text-sm mb-2">
          <span className="text-gray-600">
            <span className="font-semibold text-gray-900">{reviewedCount}</span> of{" "}
            <span className="font-semibold text-gray-900">{totalCount}</span> reviewed
          </span>
          <span className="text-gray-500">{progress}%</span>
        </div>
        <div className="h-2 bg-gray-100 rounded-full overflow-hidden">
          <div
            className="h-full bg-orange-500 rounded-full transition-all duration-300"
            style={{ width: `${progress}%` }}
          />
        </div>
        <div className="flex items-center gap-3 mt-3 pt-3 border-t border-gray-100">
          <span className="bg-gray-100 text-gray-600 px-2.5 py-1 rounded-full text-xs font-medium">
            {totalCount} Total
          </span>
          <span className="bg-yellow-100 text-yellow-700 px-2.5 py-1 rounded-full text-xs font-medium">
            {pendingCount} Pending
          </span>
          <span className="bg-green-100 text-green-700 px-2.5 py-1 rounded-full text-xs font-medium">
            {acceptedCount} Accepted
          </span>
          <span className="bg-red-100 text-red-700 px-2.5 py-1 rounded-full text-xs font-medium">
            {rejectedCount} Rejected
          </span>
        </div>
      </div>

      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative flex-1 min-w-48">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
          <input
            type="text"
            placeholder="Search matches..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full pl-8 pr-3 py-2 border border-gray-200 rounded-lg text-sm text-gray-900 bg-white focus:outline-none focus:ring-2 focus:ring-orange-400"
          />
        </div>
        <label className="flex items-center gap-1.5 text-sm text-gray-600 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={selected.size > 0 && selected.size === matches.filter(m => m.status === "pending_review").length}
            onChange={toggleSelectAll}
            className="accent-orange-500"
          />
          Select All Pending
        </label>
        <button
          onClick={() => handleBulk("accepted")}
          disabled={updating.size > 0}
          className="flex items-center gap-1.5 px-3 py-1.5 bg-green-100 text-green-700 text-sm rounded-lg hover:bg-green-200 transition-colors disabled:opacity-50"
        >
          <Check size={14} /> {selected.size > 0 ? `Accept (${selected.size})` : "Accept All"}
        </button>
        <button
          onClick={() => handleBulk("rejected")}
          disabled={updating.size > 0}
          className="flex items-center gap-1.5 px-3 py-1.5 bg-red-100 text-red-700 text-sm rounded-lg hover:bg-red-200 transition-colors disabled:opacity-50"
        >
          <X size={14} /> {selected.size > 0 ? `Reject (${selected.size})` : "Reject All"}
        </button>
      </div>

      {/* Matches table */}
      <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="bg-gray-50 border-b border-gray-200">
                <th className="w-8 px-3 py-3"></th>
                <th colSpan={5} className="px-4 py-3 text-center font-semibold text-blue-700 bg-blue-50 border-r border-gray-200">
                  Bank Statement
                </th>
                <th colSpan={6} className="px-4 py-3 text-center font-semibold text-purple-700 bg-purple-50">
                  Xero Book
                </th>
                <th className="w-20 px-3 py-3 text-center font-medium text-gray-600 bg-gray-50">Actions</th>
              </tr>
              <tr className="bg-gray-50 border-b border-gray-200 text-gray-500">
                <th className="w-8 px-3 py-2"></th>
                <th className="px-4 py-2 text-left font-medium">Date</th>
                <th className="px-4 py-2 text-left font-medium">Description</th>
                <th className="px-4 py-2 text-left font-medium">Ref</th>
                <th className="px-4 py-2 text-right font-medium">Debit</th>
                <th className="px-4 py-2 text-right font-medium border-r border-gray-200">Credit</th>
                <th className="px-4 py-2 text-left font-medium">Date</th>
                <th className="px-4 py-2 text-left font-medium">Description</th>
                <th className="px-4 py-2 text-left font-medium">Ref</th>
                <th className="px-4 py-2 text-right font-medium">Debit</th>
                <th className="px-4 py-2 text-right font-medium">Credit</th>
                <th className="px-4 py-2 text-left font-medium">Type</th>
                <th className="w-20 px-3 py-2 text-center font-medium">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {filtered.map((m) => {
                const bt = m.bank_transaction;
                const lt = m.book_transaction;
                const isPending = m.status === "pending_review";
                const isAccepted = m.status === "accepted";
                const isRejected = m.status === "rejected";
                const isUpdating = updating.has(m.match_id);
                return (
                  <tr
                    key={m.match_id}
                    className={`transition-colors ${
                      isAccepted ? "bg-green-50" :
                      isRejected ? "bg-red-50 opacity-60" :
                      selected.has(m.match_id) ? "bg-orange-50" : "hover:bg-gray-50"
                    }`}
                  >
                    <td className="px-3 py-2.5 text-center">
                      {isPending && (
                        <input
                          type="checkbox"
                          checked={selected.has(m.match_id)}
                          onChange={() => toggleSelect(m.match_id)}
                          className="accent-orange-500"
                        />
                      )}
                      {isAccepted && <CheckCircle size={14} className="text-green-500 mx-auto" />}
                      {isRejected && <X size={14} className="text-red-400 mx-auto" />}
                    </td>
                    {/* Bank side */}
                    <td className="px-4 py-2.5 text-gray-700 whitespace-nowrap">{bt?.transaction_date}</td>
                    <td className="px-4 py-2.5 text-gray-800 max-w-36 truncate" title={bt?.description}>{bt?.description}</td>
                    <td className="px-4 py-2.5 text-gray-500 max-w-24 truncate">{bt?.reference || "—"}</td>
                    <td className="px-4 py-2.5 text-right font-mono text-red-600">{bt?.debit_amount ? fmt(bt.debit_amount) : "—"}</td>
                    <td className="px-4 py-2.5 text-right font-mono text-green-600 border-r border-gray-200">{bt?.credit_amount ? fmt(bt.credit_amount) : "—"}</td>
                    {/* Book side */}
                    <td className="px-4 py-2.5 text-gray-700 whitespace-nowrap">{lt?.transaction_date}</td>
                    <td className="px-4 py-2.5 text-gray-800 max-w-36 truncate" title={lt?.description}>{lt?.description}</td>
                    <td className="px-4 py-2.5 text-gray-500 max-w-24 truncate">{lt?.reference || "—"}</td>
                    <td className="px-4 py-2.5 text-right font-mono text-green-600">{lt?.debit_amount ? fmt(lt.debit_amount) : "—"}</td>
                    <td className="px-4 py-2.5 text-right font-mono text-red-600">{lt?.credit_amount ? fmt(lt.credit_amount) : "—"}</td>
                    <td className="px-4 py-2.5 text-gray-500 text-xs">{lt?.xero_txn_type || "—"}</td>
                    {/* Actions */}
                    <td className="px-3 py-2.5 text-center">
                      {isPending && (
                        <div className="flex items-center justify-center gap-1">
                          <button
                            onClick={() => updateStatus(m.match_id, "accepted")}
                            disabled={isUpdating}
                            title="Accept"
                            className="p-1.5 rounded-lg bg-green-100 hover:bg-green-200 text-green-700 transition-colors disabled:opacity-50"
                          >
                            {isUpdating ? <RefreshCw size={12} className="animate-spin" /> : <Check size={12} />}
                          </button>
                          <button
                            onClick={() => updateStatus(m.match_id, "rejected")}
                            disabled={isUpdating}
                            title="Reject"
                            className="p-1.5 rounded-lg bg-red-100 hover:bg-red-200 text-red-700 transition-colors disabled:opacity-50"
                          >
                            <X size={12} />
                          </button>
                        </div>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {/* Continue button */}
      <div className="flex justify-end">
        <button
          onClick={onAllReviewed}
          disabled={!allReviewed}
          className="flex items-center gap-2 px-6 py-2.5 bg-orange-500 text-white font-medium rounded-lg hover:bg-orange-600 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
        >
          Continue to Manual Reconciliation →
        </button>
        {!allReviewed && (
          <p className="ml-3 text-xs text-gray-500 self-center">
            {pendingCount} match{pendingCount !== 1 ? "es" : ""} still pending review
          </p>
        )}
      </div>
    </div>
  );
}
