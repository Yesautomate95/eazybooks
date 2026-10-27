import React, { useEffect, useState, useMemo } from "react";
import { Check, X, RefreshCw, Search, CheckCircle, Link2, ChevronDown, ChevronRight, Undo2 } from "lucide-react";
import { qbBankReconApi } from "../../../../../services/api";
import { toast, errMsg } from "./toast";

const fmt = (n) =>
  n != null ? Number(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : "—";

// Only the engine's *uncertain* matches are reviewed here. High-confidence
// ('auto' tier) matches are auto-reconciled and appear in the final report.
const isSuggested = (m) => m.tier === "suggest" || (m.tier == null && m.match_type === "automatic");

function TxnLine({ t, isBank }) {
  return (
    <div className="flex items-start justify-between gap-2 py-0.5">
      <div className="min-w-0">
        <p className="text-gilt-ink truncate" title={t.description}>{t.description || "(no description)"}</p>
        <p className="text-gilt-faint font-mono tabular-nums text-[11px]">
          {t.transaction_date}
          {t.reference ? ` · ${t.reference}` : ""}
        </p>
      </div>
      <div className="text-right font-mono tabular-nums flex-shrink-0">
        {t.debit_amount != null && (
          <span className={isBank ? "text-gilt-debit" : "text-gilt-credit"}>Dr {fmt(t.debit_amount)}</span>
        )}
        {t.credit_amount != null && (
          <span className={isBank ? "text-gilt-credit" : "text-gilt-debit"}>Cr {fmt(t.credit_amount)}</span>
        )}
      </div>
    </div>
  );
}

function MatchCard({ m, selected, onToggleSelect, onAccept, onReject, busy }) {
  const banks = m.bank_transactions || (m.bank_transaction ? [m.bank_transaction] : []);
  const books = m.book_transactions || (m.book_transaction ? [m.book_transaction] : []);
  const isPending = m.status === "pending_review";
  const isAccepted = m.status === "accepted";
  const isRejected = m.status === "rejected";
  const isGroup = banks.length > 1 || books.length > 1;
  const conf = m.confidence_score != null ? Math.round(m.confidence_score * 100) : null;

  return (
    <div
      className={`rounded-xl border p-3 transition-colors duration-150 ${
        isAccepted ? "border-gilt-credit/40 bg-gilt-credit/10"
          : isRejected ? "border-gilt-debit/40 bg-gilt-debit/10 opacity-60"
          : selected ? "border-gilt-brass bg-gilt-sunken"
          : "border-gilt-hairline bg-gilt-card"
      }`}
    >
      <div className="flex items-center justify-between mb-2 gap-2">
        <div className="flex items-center gap-2 flex-wrap min-w-0">
          {isPending && (
            <input type="checkbox" checked={selected} onChange={onToggleSelect} className="accent-gilt-brass" />
          )}
          {isAccepted && <CheckCircle size={14} className="text-gilt-credit" />}
          {isRejected && <X size={14} className="text-gilt-debit" />}
          <span className="font-mono text-[10px] uppercase tracking-[0.14em] px-1.5 py-0.5 rounded-full border border-gilt-brass/40 text-gilt-brass">
            Suggested
          </span>
          {isGroup && (
            <span className="font-mono tabular-nums text-[10px] px-1.5 py-0.5 rounded-full border border-gilt-hairline text-gilt-soft inline-flex items-center gap-1">
              <Link2 size={10} /> {banks.length}↔{books.length}
            </span>
          )}
          {conf != null && <span className="text-[11px] text-gilt-soft font-mono tabular-nums">{conf}% confidence</span>}
          {m.match_reason && <span className="text-[11px] text-gilt-faint truncate">· {m.match_reason}</span>}
        </div>
        {isPending && (
          <div className="flex items-center gap-1 flex-shrink-0">
            <button
              onClick={onAccept}
              disabled={busy}
              title="Accept"
              className="p-1.5 rounded-md border border-gilt-credit/40 text-gilt-credit hover:bg-gilt-sunken transition-colors duration-150 disabled:opacity-50"
            >
              {busy ? <RefreshCw size={12} className="animate-spin" /> : <Check size={12} />}
            </button>
            <button
              onClick={onReject}
              disabled={busy}
              title="Reject"
              className="p-1.5 rounded-md border border-gilt-debit/40 text-gilt-debit hover:bg-gilt-sunken transition-colors duration-150 disabled:opacity-50"
            >
              <X size={12} />
            </button>
          </div>
        )}
      </div>
      <div className="grid grid-cols-2 gap-3 text-xs">
        <div className="rounded-lg bg-gilt-sunken border border-gilt-hairline p-2">
          <p className="font-mono tabular-nums text-[10px] font-medium text-gilt-brass uppercase tracking-[0.14em] mb-1">Bank ({banks.length})</p>
          {banks.map((t, i) => <TxnLine key={i} t={t} isBank />)}
        </div>
        <div className="rounded-lg bg-gilt-sunken border border-gilt-hairline p-2">
          <p className="font-mono tabular-nums text-[10px] font-medium text-gilt-brass uppercase tracking-[0.14em] mb-1">Book / QBO ({books.length})</p>
          {books.map((t, i) => <TxnLine key={i} t={t} />)}
        </div>
      </div>
    </div>
  );
}

// Compact one-line row for an auto-reconciled match group, with an Undo that
// rejects the match and returns its transactions to the unmatched lists.
function AutoMatchRow({ m, busy, onUndo }) {
  const banks = m.bank_transactions || (m.bank_transaction ? [m.bank_transaction] : []);
  const books = m.book_transactions || (m.book_transaction ? [m.book_transaction] : []);
  const first = banks[0] || books[0] || {};
  const amount = first.debit_amount ?? first.credit_amount;
  const conf = m.confidence_score != null ? Math.round(m.confidence_score * 100) : null;
  return (
    <div className="flex items-center justify-between gap-2 bg-gilt-card border border-gilt-hairline rounded-lg px-3 py-2 text-xs">
      <CheckCircle size={13} className="text-gilt-credit flex-shrink-0" />
      <span className="truncate flex-1 text-gilt-ink" title={first.description}>
        {first.transaction_date} · {first.description || "(no description)"}
      </span>
      {(banks.length > 1 || books.length > 1) && (
        <span className="font-mono tabular-nums text-[10px] px-1.5 py-0.5 rounded-full border border-gilt-hairline text-gilt-soft flex-shrink-0 inline-flex items-center gap-1">
          <Link2 size={9} /> {banks.length}↔{books.length}
        </span>
      )}
      {conf != null && <span className="text-[11px] text-gilt-faint font-mono tabular-nums flex-shrink-0">{conf}%</span>}
      <span className="font-mono tabular-nums text-gilt-ink flex-shrink-0">{fmt(amount)}</span>
      <button
        onClick={onUndo}
        disabled={busy}
        className="text-[11px] text-gilt-debit hover:underline inline-flex items-center gap-1 flex-shrink-0 disabled:opacity-50"
        title="Undo this automatic match (both sides return to unmatched)"
      >
        {busy ? <RefreshCw size={11} className="animate-spin" /> : <Undo2 size={11} />} Undo
      </button>
    </div>
  );
}

// Collapsible "what the engine already reconciled" section. Without this, a
// run where everything auto-matches renders an empty review tab and looks
// like the engine did nothing.
function AutoReconciledSection({ autoMatches, updating, onUndo }) {
  const [open, setOpen] = useState(false);
  if (autoMatches.length === 0) return null;
  return (
    <div className="bg-gilt-credit/10 border border-gilt-credit/40 rounded-xl overflow-hidden">
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-full px-4 py-3 flex items-center gap-2 text-left hover:bg-gilt-sunken transition-colors duration-150"
      >
        {open ? <ChevronDown size={14} className="text-gilt-credit" /> : <ChevronRight size={14} className="text-gilt-credit" />}
        <CheckCircle size={14} className="text-gilt-credit" />
        <p className="font-mono text-[10px] font-medium text-gilt-credit uppercase tracking-[0.14em]">
          Automatically reconciled ({autoMatches.length})
        </p>
        <span className="text-[11px] text-gilt-soft">— high-confidence matches, no review needed; expand to inspect or undo</span>
      </button>
      {open && (
        <div className="p-3 space-y-1 max-h-[50vh] overflow-y-auto border-t border-gilt-credit/40">
          {autoMatches.map((m) => (
            <AutoMatchRow
              key={m.match_id}
              m={m}
              busy={updating.has(m.match_id)}
              onUndo={() => onUndo(m.match_id)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

export default function QBAutoMatchTab({ reportId, onAllReviewed }) {
  const [matches, setMatches] = useState([]);
  const [loading, setLoading] = useState(true);
  const [updating, setUpdating] = useState(new Set());
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState(new Set());

  const fetchMatches = async () => {
    setLoading(true);
    try {
      const data = await qbBankReconApi.getMatches(reportId);
      setMatches(data.matches || []);
    } catch (e) {
      toast.error(errMsg(e, "Couldn't load suggested matches."));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchMatches();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reportId]);

  const suggested = useMemo(() => matches.filter(isSuggested), [matches]);
  const autoMatches = useMemo(
    () => matches.filter((m) => m.tier === "auto" && m.status === "accepted"),
    [matches]
  );

  const pendingCount = useMemo(() => suggested.filter((m) => m.status === "pending_review").length, [suggested]);
  const acceptedCount = useMemo(() => suggested.filter((m) => m.status === "accepted").length, [suggested]);
  const rejectedCount = useMemo(() => suggested.filter((m) => m.status === "rejected").length, [suggested]);
  const totalCount = suggested.length;
  const reviewedCount = totalCount - pendingCount;
  const progress = totalCount > 0 ? Math.round((reviewedCount / totalCount) * 100) : 0;
  const allReviewed = totalCount > 0 && pendingCount === 0;

  const updateStatus = async (matchId, status) => {
    setUpdating((prev) => new Set([...prev, matchId]));
    try {
      await qbBankReconApi.updateMatchStatus(matchId, status);
      setMatches((prev) => prev.map((m) => (m.match_id === matchId ? { ...m, status } : m)));
      setSelected((prev) => { const n = new Set(prev); n.delete(matchId); return n; });
      toast.success(status === "accepted" ? "Match accepted" : "Match rejected");
    } catch (e) {
      toast.error(errMsg(e, "Failed to update match status."));
    } finally {
      setUpdating((prev) => { const n = new Set(prev); n.delete(matchId); return n; });
    }
  };

  const handleBulk = async (status) => {
    const ids = selected.size > 0
      ? [...selected]
      : suggested.filter((m) => m.status === "pending_review").map((m) => m.match_id);
    if (ids.length === 0) return;
    setUpdating(new Set(ids));
    try {
      await qbBankReconApi.bulkUpdateMatches(ids, status);
      setMatches((prev) => prev.map((m) => (ids.includes(m.match_id) ? { ...m, status } : m)));
      setSelected(new Set());
      toast.success(`${ids.length} match${ids.length !== 1 ? "es" : ""} ${status === "accepted" ? "accepted" : "rejected"}`);
    } catch (e) {
      toast.error(errMsg(e, "Bulk update failed."));
    } finally {
      setUpdating(new Set());
    }
  };

  const toggleSelect = (id) => {
    setSelected((prev) => {
      const n = new Set(prev);
      if (n.has(id)) n.delete(id); else n.add(id);
      return n;
    });
  };

  const pendingIds = useMemo(
    () => suggested.filter((m) => m.status === "pending_review").map((m) => m.match_id),
    [suggested]
  );

  const toggleSelectAll = () => {
    if (selected.size === pendingIds.length) setSelected(new Set());
    else setSelected(new Set(pendingIds));
  };

  const filtered = useMemo(() => {
    if (!search) return suggested;
    const q = search.toLowerCase();
    const hit = (arr) => (arr || []).some(
      (t) => t.description?.toLowerCase().includes(q) || t.reference?.toLowerCase().includes(q)
    );
    return suggested.filter((m) => hit(m.bank_transactions) || hit(m.book_transactions));
  }, [suggested, search]);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-16 gap-3">
        <RefreshCw size={20} className="text-gilt-brass animate-spin" />
        <span className="text-gilt-soft text-sm">Loading suggested matches...</span>
      </div>
    );
  }

  if (totalCount === 0) {
    return (
      <div className="space-y-4">
        <AutoReconciledSection
          autoMatches={autoMatches}
          updating={updating}
          onUndo={(id) => updateStatus(id, "rejected")}
        />
        <div className="flex flex-col items-center justify-center py-14 text-center gap-3">
          <Search size={40} className="text-gilt-faint" />
          <p className="text-gilt-ink font-medium">No suggested matches to review</p>
          <p className="text-gilt-soft text-sm">
            {autoMatches.length > 0
              ? `${autoMatches.length} high-confidence match${autoMatches.length !== 1 ? "es were" : " was"} reconciled automatically (see above). Proceed to Manual Reconciliation for the rest.`
              : "Nothing matched automatically. Proceed to Manual Reconciliation."}
          </p>
          <button
            onClick={onAllReviewed}
            className="mt-2 px-5 py-2 bg-gilt-brass text-gilt-paper text-sm font-medium rounded-md hover:opacity-90 transition-opacity duration-150"
          >
            Go to Manual Reconciliation →
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <AutoReconciledSection
        autoMatches={autoMatches}
        updating={updating}
        onUndo={(id) => updateStatus(id, "rejected")}
      />
      {/* Progress + counts */}
      <div className="bg-gilt-card rounded-xl border border-gilt-hairline p-4">
        <div className="flex items-center justify-between text-sm mb-2">
          <span className="text-gilt-soft">
            <span className="font-mono tabular-nums font-medium text-gilt-ink">{reviewedCount}</span> of{" "}
            <span className="font-mono tabular-nums font-medium text-gilt-ink">{totalCount}</span> suggestions reviewed
          </span>
          <span className="text-gilt-soft font-mono tabular-nums">{progress}%</span>
        </div>
        <div className="h-2 bg-gilt-hairline/60 rounded-full overflow-hidden">
          <div className="h-full bg-gilt-gold rounded-full transition-all duration-300" style={{ width: `${progress}%` }} />
        </div>
        <div className="flex items-center gap-3 mt-3 pt-3 border-t border-gilt-hairline">
          <span className="border border-gilt-hairline text-gilt-soft px-2.5 py-1 rounded-full font-mono tabular-nums text-[10px] uppercase tracking-[0.14em]">{totalCount} Total</span>
          <span className="border border-gilt-brass/40 text-gilt-brass px-2.5 py-1 rounded-full font-mono tabular-nums text-[10px] uppercase tracking-[0.14em]">{pendingCount} Pending</span>
          <span className="border border-gilt-credit/40 text-gilt-credit px-2.5 py-1 rounded-full font-mono tabular-nums text-[10px] uppercase tracking-[0.14em]">{acceptedCount} Accepted</span>
          <span className="border border-gilt-debit/40 text-gilt-debit px-2.5 py-1 rounded-full font-mono tabular-nums text-[10px] uppercase tracking-[0.14em]">{rejectedCount} Rejected</span>
        </div>
      </div>

      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative flex-1 min-w-48">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-gilt-faint" />
          <input
            type="text"
            placeholder="Search suggestions..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full pl-8 pr-3 py-2 border border-gilt-hairline rounded-lg text-sm text-gilt-ink bg-gilt-card placeholder:text-gilt-faint focus:outline-none focus:border-gilt-brass transition-colors duration-150"
          />
        </div>
        <label className="flex items-center gap-1.5 text-sm text-gilt-soft cursor-pointer select-none">
          <input
            type="checkbox"
            checked={selected.size > 0 && selected.size === pendingIds.length}
            onChange={toggleSelectAll}
            className="accent-gilt-brass"
          />
          Select All Pending
        </label>
        <button
          onClick={() => handleBulk("accepted")}
          disabled={updating.size > 0}
          className="flex items-center gap-1.5 px-3 py-1.5 border border-gilt-credit/40 text-gilt-credit text-sm rounded-md hover:bg-gilt-sunken transition-colors duration-150 disabled:opacity-50"
        >
          <Check size={14} /> {selected.size > 0 ? `Accept (${selected.size})` : "Accept All"}
        </button>
        <button
          onClick={() => handleBulk("rejected")}
          disabled={updating.size > 0}
          className="flex items-center gap-1.5 px-3 py-1.5 border border-gilt-debit/40 text-gilt-debit text-sm rounded-md hover:bg-gilt-sunken transition-colors duration-150 disabled:opacity-50"
        >
          <X size={14} /> {selected.size > 0 ? `Reject (${selected.size})` : "Reject All"}
        </button>
      </div>

      {/* Cards */}
      <div className="space-y-2">
        {filtered.map((m) => (
          <MatchCard
            key={m.match_id}
            m={m}
            selected={selected.has(m.match_id)}
            onToggleSelect={() => toggleSelect(m.match_id)}
            onAccept={() => updateStatus(m.match_id, "accepted")}
            onReject={() => updateStatus(m.match_id, "rejected")}
            busy={updating.has(m.match_id)}
          />
        ))}
      </div>

      {/* Continue */}
      <div className="flex justify-end items-center">
        <button
          onClick={onAllReviewed}
          disabled={!allReviewed}
          className="flex items-center gap-2 px-6 py-2.5 bg-gilt-brass text-gilt-paper font-medium rounded-md hover:opacity-90 transition-opacity duration-150 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          Continue to Manual Reconciliation →
        </button>
        {!allReviewed && (
          <p className="ml-3 text-xs text-gilt-faint">
            {pendingCount} suggestion{pendingCount !== 1 ? "s" : ""} still pending
          </p>
        )}
      </div>
    </div>
  );
}
