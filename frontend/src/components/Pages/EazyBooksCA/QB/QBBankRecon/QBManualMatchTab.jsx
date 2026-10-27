import React, { useEffect, useState, useMemo, useRef } from "react";
import { Search, X, Link, FileText, BarChart3, RefreshCw, Clock, Undo2, Plus, ChevronDown, Pencil, Trash2 } from "lucide-react";
import { qbBankReconApi } from "../../../../../services/api";
import { toast, errMsg } from "./toast";

const fmt = (n) =>
  n != null
    ? Number(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    : null;

function TxnRow({ txn, selected, onClick, source, onMarkPending, onEdit, onDelete }) {
  const isBank = source === "bank_statement";
  return (
    <div
      className={`w-full rounded-lg border transition-colors duration-150 text-xs mb-1 ${
        selected
          ? "border-gilt-brass bg-gilt-sunken"
          : "border-gilt-hairline hover:border-gilt-brass hover:bg-gilt-sunken bg-gilt-card"
      }`}
    >
      <button onClick={onClick} className="w-full text-left px-3 py-2.5">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0 flex items-start gap-2">
            <input type="checkbox" readOnly checked={selected} className="accent-gilt-brass mt-0.5" />
            <div className="min-w-0">
              <p className="font-medium text-gilt-ink truncate">
                {txn.description || "(no description)"}
                {txn.is_manual && (
                  <span className="ml-1.5 px-1.5 py-0.5 font-mono text-[10px] tracking-[0.14em] rounded-full border border-gilt-hairline text-gilt-soft uppercase align-middle">manual</span>
                )}
                {txn.is_adjustment && (
                  <span className="ml-1.5 px-1.5 py-0.5 font-mono text-[10px] tracking-[0.14em] rounded-full border border-gilt-brass/40 text-gilt-brass uppercase align-middle">adjustment</span>
                )}
              </p>
              <p className="text-gilt-soft font-mono tabular-nums mt-0.5">
                {txn.transaction_date}
                {txn.reference && <span className="ml-2">· {txn.reference}</span>}
              </p>
            </div>
          </div>
          <div className="text-right flex-shrink-0">
            {txn.debit_amount != null && (
              <p className={`font-mono tabular-nums font-medium ${isBank ? "text-gilt-debit" : "text-gilt-credit"}`}>Dr {fmt(txn.debit_amount)}</p>
            )}
            {txn.credit_amount != null && (
              <p className={`font-mono tabular-nums font-medium ${isBank ? "text-gilt-credit" : "text-gilt-debit"}`}>Cr {fmt(txn.credit_amount)}</p>
            )}
          </div>
        </div>
      </button>
      {(onMarkPending || onEdit || onDelete) && (
        <div className="px-3 pb-1.5 -mt-1 flex items-center justify-end gap-3">
          {onEdit && (
            <button
              onClick={(e) => { e.stopPropagation(); onEdit(txn); }}
              className="text-[11px] text-gilt-soft hover:text-gilt-brass hover:underline transition-colors duration-150 inline-flex items-center gap-1"
              title="Edit this manually added entry"
            >
              <Pencil size={11} /> Edit
            </button>
          )}
          {onDelete && (
            <button
              onClick={(e) => { e.stopPropagation(); onDelete(txn); }}
              className="text-[11px] text-gilt-debit hover:underline inline-flex items-center gap-1"
              title="Delete this manually added entry"
            >
              <Trash2 size={11} /> Delete
            </button>
          )}
          {onMarkPending && (
            <button
              onClick={(e) => { e.stopPropagation(); onMarkPending(txn); }}
              className="text-[11px] text-gilt-brass hover:underline inline-flex items-center gap-1"
              title="Set aside for later review (no changes to QuickBooks)"
            >
              <Clock size={11} /> Set aside
            </button>
          )}
        </div>
      )}
    </div>
  );
}

const VOUCHER_TYPES = ["Payment", "Receipt", "Contra", "Journal"];

// Searchable account picker for the adjusting entry. Selecting an existing
// account sets both the name and the QBO id; typing a name that isn't in the
// list is allowed (the backend creates it on post).
function AccountSelect({ accounts, value, onChange, field }) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState(value?.party_ledger || "");
  const wrapRef = useRef(null);

  useEffect(() => { setQuery(value?.party_ledger || ""); }, [value?.party_ledger]);

  useEffect(() => {
    const onDoc = (e) => { if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false); };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    const list = q ? accounts.filter((a) => a.name.toLowerCase().includes(q)) : accounts;
    return list.slice(0, 50);
  }, [accounts, query]);

  const known = accounts.some((a) => a.name.toLowerCase() === query.trim().toLowerCase());

  const pick = (a) => {
    onChange({ party_ledger: a.name, party_ledger_id: a.id });
    setQuery(a.name);
    setOpen(false);
  };

  const onType = (v) => {
    setQuery(v);
    setOpen(true);
    // free text => treat as a new account (no id)
    onChange({ party_ledger: v, party_ledger_id: null });
  };

  return (
    <div className="relative" ref={wrapRef}>
      <div className="relative">
        <input
          type="text"
          value={query}
          onChange={(e) => onType(e.target.value)}
          onFocus={() => setOpen(true)}
          className={`${field} pr-8`}
          placeholder="Search or type a new account…"
        />
        <ChevronDown size={15} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-gilt-faint pointer-events-none" />
      </div>
      {open && (
        <div className="absolute z-10 mt-1 w-full bg-gilt-card border border-gilt-hairline rounded-lg shadow-sm max-h-56 overflow-y-auto">
          {query.trim() && !known && (
            <button
              type="button"
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => setOpen(false)}
              className="w-full text-left px-3 py-2 text-xs text-gilt-brass bg-gilt-sunken/60 hover:bg-gilt-sunken transition-colors duration-150 border-b border-gilt-hairline"
            >
              + Create new account “{query.trim()}”
            </button>
          )}
          {filtered.length === 0 && (!query.trim() || known) ? (
            <p className="px-3 py-2 text-xs text-gilt-faint">No accounts</p>
          ) : (
            filtered.map((a) => (
              <button
                key={a.id}
                type="button"
                onClick={() => pick(a)}
                className="w-full text-left px-3 py-2 text-sm hover:bg-gilt-sunken transition-colors duration-150 flex items-center justify-between gap-2"
              >
                <span className="truncate text-gilt-ink">{a.name}</span>
                {a.account_type && <span className="text-[10px] text-gilt-faint flex-shrink-0">{a.account_type}</span>}
              </button>
            ))
          )}
        </div>
      )}
    </div>
  );
}

function AdjustmentModal({ open, prefill, submitting, accounts, onClose, onSubmit }) {
  const [form, setForm] = useState(prefill);
  useEffect(() => { setForm(prefill); }, [prefill, open]);
  if (!open) return null;
  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));
  const valid = form.date && form.description && form.party_ledger && Number(form.amount) > 0;

  const field = "w-full px-3 py-2 border border-gilt-hairline rounded-lg text-sm text-gilt-ink bg-gilt-paper placeholder:text-gilt-faint focus:outline-none focus:border-gilt-brass transition-colors duration-150";

  return (
    <div className="fixed inset-0 bg-gilt-ink/40 flex items-center justify-center z-50 p-4">
      <div className="bg-gilt-card border border-gilt-hairline rounded-xl w-full max-w-md">
        <div className="px-5 py-4 border-b border-gilt-hairline flex items-center justify-between">
          <h3 className="font-semibold text-gilt-ink">Create adjusting entry</h3>
          <button onClick={onClose} className="p-1 rounded hover:bg-gilt-sunken transition-colors duration-150"><X size={18} className="text-gilt-soft" /></button>
        </div>
        <div className="p-5 space-y-3">
          <p className="text-xs text-gilt-soft">
            Posts a new entry to QuickBooks and adds it as a book row here, so you can
            select it alongside the bank line(s) and reconcile once the difference clears.
          </p>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-gilt-soft mb-1">Date</label>
              <input type="date" value={form.date} onChange={(e) => set("date", e.target.value)} className={field} />
            </div>
            <div>
              <label className="block text-xs font-medium text-gilt-soft mb-1">Type</label>
              <select value={form.voucher_type} onChange={(e) => set("voucher_type", e.target.value)} className={field}>
                {VOUCHER_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
              </select>
            </div>
          </div>
          <div>
            <label className="block text-xs font-medium text-gilt-soft mb-1">Description</label>
            <input type="text" value={form.description} onChange={(e) => set("description", e.target.value)} className={field} placeholder="e.g. Bank charges" />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-gilt-soft mb-1">Amount</label>
              <input type="number" step="0.01" value={form.amount} onChange={(e) => set("amount", e.target.value)} className={field} />
            </div>
            <div>
              <label className="block text-xs font-medium text-gilt-soft mb-1">Reference</label>
              <input type="text" value={form.reference} onChange={(e) => set("reference", e.target.value)} className={field} placeholder="optional" />
            </div>
          </div>
          <div>
            <label className="block text-xs font-medium text-gilt-soft mb-1">Account</label>
            <AccountSelect
              accounts={accounts}
              value={{ party_ledger: form.party_ledger, party_ledger_id: form.party_ledger_id }}
              onChange={({ party_ledger, party_ledger_id }) =>
                setForm((f) => ({ ...f, party_ledger, party_ledger_id }))
              }
              field={field}
            />
            <p className="text-[11px] text-gilt-faint mt-1">The QuickBooks income/expense account to post against.</p>
          </div>
        </div>
        <div className="px-5 py-4 border-t border-gilt-hairline flex justify-end gap-2">
          <button onClick={onClose} className="px-4 py-2 text-sm text-gilt-soft hover:text-gilt-ink transition-colors duration-150">Cancel</button>
          <button
            onClick={() => onSubmit(form)}
            disabled={!valid || submitting}
            className="flex items-center gap-1.5 px-4 py-2 bg-gilt-brass text-gilt-paper text-sm font-medium rounded-md hover:opacity-90 transition-opacity duration-150 disabled:opacity-40"
          >
            {submitting ? <RefreshCw size={13} className="animate-spin" /> : <Plus size={13} />}
            {submitting ? "Posting..." : "Create & post"}
          </button>
        </div>
      </div>
    </div>
  );
}

// Add or edit a bank-statement row the parser missed. Local-only — nothing is
// written to QuickBooks or the bank; the row joins the statement side of the
// recon flagged as manual.
function BankEntryModal({ open, prefill, editing, submitting, onClose, onSubmit }) {
  const [form, setForm] = useState(prefill);
  useEffect(() => { setForm(prefill); }, [prefill, open]);
  if (!open) return null;
  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));
  const valid = form.date && form.description && Number(form.amount) > 0;

  const field = "w-full px-3 py-2 border border-gilt-hairline rounded-lg text-sm text-gilt-ink bg-gilt-paper placeholder:text-gilt-faint focus:outline-none focus:border-gilt-brass transition-colors duration-150";

  return (
    <div className="fixed inset-0 bg-gilt-ink/40 flex items-center justify-center z-50 p-4">
      <div className="bg-gilt-card border border-gilt-hairline rounded-xl w-full max-w-md">
        <div className="px-5 py-4 border-b border-gilt-hairline flex items-center justify-between">
          <h3 className="font-semibold text-gilt-ink">{editing ? "Edit bank entry" : "Add bank entry"}</h3>
          <button onClick={onClose} className="p-1 rounded hover:bg-gilt-sunken transition-colors duration-150"><X size={18} className="text-gilt-soft" /></button>
        </div>
        <div className="p-5 space-y-3">
          <p className="text-xs text-gilt-soft">
            Adds a statement row the parser missed. Nothing is sent to the bank or
            QuickBooks — the entry only joins this reconciliation, marked as manual.
          </p>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-gilt-soft mb-1">Date</label>
              <input type="date" value={form.date} onChange={(e) => set("date", e.target.value)} className={field} />
            </div>
            <div>
              <label className="block text-xs font-medium text-gilt-soft mb-1">Direction</label>
              <select value={form.direction} onChange={(e) => set("direction", e.target.value)} className={field}>
                <option value="debit">Money out (Dr)</option>
                <option value="credit">Money in (Cr)</option>
              </select>
            </div>
          </div>
          <div>
            <label className="block text-xs font-medium text-gilt-soft mb-1">Description</label>
            <input type="text" value={form.description} onChange={(e) => set("description", e.target.value)} className={field} placeholder="e.g. NEFT to vendor" />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-gilt-soft mb-1">Amount</label>
              <input type="number" step="0.01" value={form.amount} onChange={(e) => set("amount", e.target.value)} className={field} />
            </div>
            <div>
              <label className="block text-xs font-medium text-gilt-soft mb-1">Reference</label>
              <input type="text" value={form.reference} onChange={(e) => set("reference", e.target.value)} className={field} placeholder="optional" />
            </div>
          </div>
        </div>
        <div className="px-5 py-4 border-t border-gilt-hairline flex justify-end gap-2">
          <button onClick={onClose} className="px-4 py-2 text-sm text-gilt-soft hover:text-gilt-ink transition-colors duration-150">Cancel</button>
          <button
            onClick={() => onSubmit(form)}
            disabled={!valid || submitting}
            className="flex items-center gap-1.5 px-4 py-2 bg-gilt-brass text-gilt-paper text-sm font-medium rounded-md hover:opacity-90 transition-opacity duration-150 disabled:opacity-40"
          >
            {submitting ? <RefreshCw size={13} className="animate-spin" /> : <Plus size={13} />}
            {submitting ? "Saving..." : editing ? "Save changes" : "Add entry"}
          </button>
        </div>
      </div>
    </div>
  );
}

export default function QBManualMatchTab({ reportId, onViewReport }) {
  const [bankTxns, setBankTxns] = useState([]);
  const [bookTxns, setBookTxns] = useState([]);
  const [pendingTxns, setPendingTxns] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selectedBank, setSelectedBank] = useState(new Set());
  const [selectedBook, setSelectedBook] = useState(new Set());
  const [reconciling, setReconciling] = useState(false);
  const [bankSearch, setBankSearch] = useState("");
  const [bookSearch, setBookSearch] = useState("");
  const [adjOpen, setAdjOpen] = useState(false);
  const [adjStandalone, setAdjStandalone] = useState(false);
  const [adjSubmitting, setAdjSubmitting] = useState(false);
  const [postingAccounts, setPostingAccounts] = useState([]);
  const [bankEntryOpen, setBankEntryOpen] = useState(false);
  const [bankEntrySubmitting, setBankEntrySubmitting] = useState(false);
  const [bankEntryEditing, setBankEntryEditing] = useState(null); // txn being edited, or null

  const fetchUnmatched = async ({ silent = false } = {}) => {
    if (!silent) setLoading(true);
    try {
      const [bankData, bookData, pendingData] = await Promise.all([
        qbBankReconApi.getTransactions(reportId, { source: "bank_statement", recon_status: "unmatched" }),
        qbBankReconApi.getTransactions(reportId, { source: "book", recon_status: "unmatched" }),
        qbBankReconApi.getTransactions(reportId, { recon_status: "pending" }),
      ]);
      setBankTxns(bankData.transactions || []);
      setBookTxns(bookData.transactions || []);
      setPendingTxns(pendingData.transactions || []);
    } catch (e) {
      if (!silent) toast.error(errMsg(e, "Couldn't load unmatched transactions."));
    } finally {
      if (!silent) setLoading(false);
    }
  };

  useEffect(() => {
    fetchUnmatched();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reportId]);

  // Load the income/expense accounts for the adjustment dropdown (once).
  useEffect(() => {
    let alive = true;
    qbBankReconApi
      .getPostingAccounts()
      .then((d) => { if (alive) setPostingAccounts(d.accounts || []); })
      .catch(() => { /* dropdown still allows typing a new account name */ });
    return () => { alive = false; };
  }, []);

  const clearSelection = () => { setSelectedBank(new Set()); setSelectedBook(new Set()); };

  const toggle = (setter) => (id) =>
    setter((prev) => { const n = new Set(prev); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const toggleBank = toggle(setSelectedBank);
  const toggleBook = toggle(setSelectedBook);

  const filteredBank = useMemo(() => {
    if (!bankSearch) return bankTxns;
    const q = bankSearch.toLowerCase();
    return bankTxns.filter((t) => t.description?.toLowerCase().includes(q) || t.reference?.toLowerCase().includes(q));
  }, [bankTxns, bankSearch]);

  const filteredBook = useMemo(() => {
    if (!bookSearch) return bookTxns;
    const q = bookSearch.toLowerCase();
    return bookTxns.filter((t) => t.description?.toLowerCase().includes(q) || t.reference?.toLowerCase().includes(q));
  }, [bookTxns, bookSearch]);

  const selBankTxns = useMemo(() => bankTxns.filter((t) => selectedBank.has(t.txn_id)), [bankTxns, selectedBank]);
  const selBookTxns = useMemo(() => bookTxns.filter((t) => selectedBook.has(t.txn_id)), [bookTxns, selectedBook]);

  // Difference = net bank inflow − net book inflow (bank credit / book debit are inflows).
  const { diff, balanced } = useMemo(() => {
    const sum = (arr, f) => arr.reduce((s, t) => s + (Number(t[f]) || 0), 0);
    const bankNet = sum(selBankTxns, "credit_amount") - sum(selBankTxns, "debit_amount");
    const bookNet = sum(selBookTxns, "debit_amount") - sum(selBookTxns, "credit_amount");
    const d = Math.round((bankNet - bookNet) * 100) / 100;
    return {
      diff: d,
      balanced: Math.abs(d) < 0.005 && selBankTxns.length > 0 && selBookTxns.length > 0,
    };
  }, [selBankTxns, selBookTxns]);

  const hasSelection = selectedBank.size > 0 || selectedBook.size > 0;

  const handleReconcile = async () => {
    if (!balanced) return;
    setReconciling(true);
    const n = selectedBank.size + selectedBook.size;
    try {
      await qbBankReconApi.createManualMatchGroup(reportId, [...selectedBook], [...selectedBank]);
      setBankTxns((prev) => prev.filter((t) => !selectedBank.has(t.txn_id)));
      setBookTxns((prev) => prev.filter((t) => !selectedBook.has(t.txn_id)));
      clearSelection();
      toast.success(`Reconciled ${n} entries`);
    } catch (e) {
      toast.error(errMsg(e, "Failed to reconcile. Please try again."));
    } finally {
      setReconciling(false);
    }
  };

  const adjPrefill = useMemo(() => {
    // Standalone "Add entry" (book panel header): blank form — e.g. record an
    // amount for a vendor with no statement counterpart at all.
    if (adjStandalone) {
      return {
        date: new Date().toISOString().slice(0, 10),
        description: "",
        voucher_type: "Payment",
        amount: "",
        party_ledger: "",
        party_ledger_id: null,
        reference: "",
      };
    }
    const firstBank = selBankTxns[0];
    // diff < 0 => books are missing an outflow (Payment); diff > 0 => missing inflow (Receipt)
    return {
      date: firstBank?.transaction_date || new Date().toISOString().slice(0, 10),
      description: firstBank?.description || "",
      voucher_type: diff < 0 ? "Payment" : "Receipt",
      amount: Math.abs(diff) > 0 ? Math.abs(diff).toFixed(2) : "",
      party_ledger: "",
      party_ledger_id: null,
      reference: firstBank?.reference || "",
    };
  }, [adjStandalone, selBankTxns, diff]);

  // Post the adjustment to QuickBooks, then keep the user IN the reconciliation:
  // the new entry comes back as a fresh unmatched book row, we re-select it
  // alongside the current bank/book selection so the difference updates live, and
  // the user clicks Reconcile when it's zero. No auto-reconcile.
  const handleCreateAdjustment = async (form) => {
    setAdjSubmitting(true);
    try {
      const res = await qbBankReconApi.createAdjustment(reportId, {
        date: form.date,
        description: form.description,
        voucher_type: form.voucher_type,
        amount: Number(form.amount),
        party_ledger: form.party_ledger,
        party_ledger_id: form.party_ledger_id || null,
        reference: form.reference || "",
        auto_create_masters: true,
        auto_match: false,
      });
      setAdjOpen(false);
      toast.success("Posted to QuickBooks");
      (res?.created_masters || []).forEach((m) =>
        toast.info(`Created account “${m.name || m}” in QuickBooks`)
      );
      await fetchUnmatched({ silent: true });
      // Auto-select the new book row so the difference reflects it immediately —
      // but not for a standalone add, where there's nothing being staged.
      if (!adjStandalone && res?.book_txn_id != null) {
        setSelectedBook((prev) => new Set([...prev, res.book_txn_id]));
      }
    } catch (e) {
      toast.error(errMsg(e, "Failed to post adjustment to QuickBooks."));
    } finally {
      setAdjSubmitting(false);
    }
  };

  const openBankEntryModal = (txn = null) => {
    setBankEntryEditing(txn);
    setBankEntryOpen(true);
  };

  const bankEntryPrefill = useMemo(() => {
    if (bankEntryEditing) {
      return {
        date: bankEntryEditing.transaction_date,
        direction: bankEntryEditing.debit_amount != null ? "debit" : "credit",
        amount: bankEntryEditing.debit_amount ?? bankEntryEditing.credit_amount ?? "",
        description: bankEntryEditing.description || "",
        reference: bankEntryEditing.reference || "",
      };
    }
    return {
      date: new Date().toISOString().slice(0, 10),
      direction: "debit",
      amount: "",
      description: "",
      reference: "",
    };
  }, [bankEntryEditing]);

  const handleSubmitBankEntry = async (form) => {
    setBankEntrySubmitting(true);
    const payload = {
      date: form.date,
      description: form.description,
      reference: form.reference || "",
      debit_amount: form.direction === "debit" ? Number(form.amount) : null,
      credit_amount: form.direction === "credit" ? Number(form.amount) : null,
    };
    try {
      if (bankEntryEditing) {
        await qbBankReconApi.updateBankEntry(reportId, bankEntryEditing.txn_id, payload);
        toast.success("Bank entry updated");
      } else {
        await qbBankReconApi.createBankEntry(reportId, payload);
        toast.success("Bank entry added");
      }
      setBankEntryOpen(false);
      setBankEntryEditing(null);
      await fetchUnmatched({ silent: true });
    } catch (e) {
      toast.error(errMsg(e, "Failed to save bank entry."));
    } finally {
      setBankEntrySubmitting(false);
    }
  };

  const handleDeleteBankEntry = async (txn) => {
    if (!window.confirm("Delete this manually added bank entry?")) return;
    try {
      await qbBankReconApi.deleteBankEntry(reportId, txn.txn_id);
      setBankTxns((prev) => prev.filter((t) => t.txn_id !== txn.txn_id));
      setSelectedBank((prev) => { const n = new Set(prev); n.delete(txn.txn_id); return n; });
      toast.success("Bank entry deleted");
    } catch (e) {
      toast.error(errMsg(e, "Failed to delete bank entry."));
    }
  };

  const handleMarkPending = async (txn) => {
    try {
      await qbBankReconApi.markPending(reportId, [txn.txn_id]);
      setBankTxns((prev) => prev.filter((t) => t.txn_id !== txn.txn_id));
      setBookTxns((prev) => prev.filter((t) => t.txn_id !== txn.txn_id));
      setPendingTxns((prev) => [...prev, txn]);
      setSelectedBank((prev) => { const n = new Set(prev); n.delete(txn.txn_id); return n; });
      setSelectedBook((prev) => { const n = new Set(prev); n.delete(txn.txn_id); return n; });
    } catch (e) {
      toast.error(errMsg(e, "Failed to set aside. Please try again."));
    }
  };

  const handleRestorePending = async (txn) => {
    try {
      await qbBankReconApi.unmarkPending(reportId, [txn.txn_id]);
      setPendingTxns((prev) => prev.filter((t) => t.txn_id !== txn.txn_id));
      if (txn.source === "bank_statement") setBankTxns((prev) => [...prev, txn]);
      else setBookTxns((prev) => [...prev, txn]);
    } catch (e) {
      toast.error(errMsg(e, "Failed to restore. Please try again."));
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-16 gap-3">
        <RefreshCw size={20} className="text-gilt-brass animate-spin" />
        <span className="text-gilt-soft text-sm">Loading unmatched transactions...</span>
      </div>
    );
  }

  const bothEmpty = bankTxns.length === 0 && bookTxns.length === 0;

  return (
    <div className="space-y-4">
      {/* Action bar */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 text-sm text-gilt-soft flex-wrap">
          <span className="border border-gilt-brass/40 text-gilt-brass px-2.5 py-1 rounded-full font-mono tabular-nums text-[10px] uppercase tracking-[0.14em]">{bankTxns.length} unmatched bank</span>
          <span className="border border-gilt-brass/40 text-gilt-brass px-2.5 py-1 rounded-full font-mono tabular-nums text-[10px] uppercase tracking-[0.14em]">{bookTxns.length} unmatched book</span>
          {pendingTxns.length > 0 && (
            <span className="border border-gilt-hairline text-gilt-soft px-2.5 py-1 rounded-full font-mono tabular-nums text-[10px] uppercase tracking-[0.14em] inline-flex items-center gap-1">
              <Clock size={11} /> {pendingTxns.length} pending
            </span>
          )}
        </div>
        <button
          onClick={onViewReport}
          className="flex items-center gap-1.5 px-4 py-2 bg-gilt-brass text-gilt-paper text-sm font-medium rounded-md hover:opacity-90 transition-opacity duration-150"
        >
          <BarChart3 size={14} /> View Report
        </button>
      </div>

      {/* Staging area */}
      {hasSelection && (
        <div className="bg-gilt-sunken border border-gilt-brass rounded-xl p-4">
          <div className="flex items-center justify-between mb-2">
            <p className="font-mono tabular-nums text-[10px] uppercase tracking-[0.14em] text-gilt-brass">
              Staging — {selectedBank.size} bank · {selectedBook.size} book
            </p>
            <button onClick={clearSelection} className="text-xs text-gilt-brass hover:underline inline-flex items-center gap-1">
              <X size={12} /> Clear
            </button>
          </div>
          <div className="grid grid-cols-2 gap-3 text-xs">
            <div className="bg-gilt-card rounded-lg border border-gilt-hairline p-2 max-h-32 overflow-y-auto">
              {selBankTxns.length === 0 ? <p className="text-gilt-faint">No bank lines selected</p> :
                selBankTxns.map((t) => (
                  <div key={t.txn_id} className="flex justify-between gap-2 py-0.5">
                    <span className="truncate text-gilt-soft">{t.transaction_date} · {t.description}</span>
                    <span className="font-mono tabular-nums flex-shrink-0">{t.debit_amount != null ? `Dr ${fmt(t.debit_amount)}` : `Cr ${fmt(t.credit_amount)}`}</span>
                  </div>
                ))}
            </div>
            <div className="bg-gilt-card rounded-lg border border-gilt-hairline p-2 max-h-32 overflow-y-auto">
              {selBookTxns.length === 0 ? <p className="text-gilt-faint">No book lines selected</p> :
                selBookTxns.map((t) => (
                  <div key={t.txn_id} className="flex justify-between gap-2 py-0.5">
                    <span className="truncate text-gilt-soft">{t.transaction_date} · {t.description}</span>
                    <span className="font-mono tabular-nums flex-shrink-0">{t.debit_amount != null ? `Dr ${fmt(t.debit_amount)}` : `Cr ${fmt(t.credit_amount)}`}</span>
                  </div>
                ))}
            </div>
          </div>

          {/* Live difference */}
          <div className="mt-3 flex items-center justify-between">
            <div className={`text-sm font-medium ${balanced ? "text-gilt-credit" : "text-gilt-debit"}`}>
              {balanced
                ? "Balanced — ready to reconcile"
                : `Difference: ${fmt(Math.abs(diff))} ${diff < 0 ? "(missing book entry)" : diff > 0 ? "(missing bank/book inflow)" : ""}`}
            </div>
            <div className="flex items-center gap-2">
              {!balanced && Math.abs(diff) > 0 && (
                <button
                  onClick={() => { setAdjStandalone(false); setAdjOpen(true); }}
                  className="flex items-center gap-1.5 px-3 py-2 bg-gilt-card border border-gilt-hairline text-gilt-soft text-sm font-medium rounded-md hover:border-gilt-brass hover:text-gilt-brass transition-colors duration-150"
                >
                  <Plus size={13} /> Create adjusting entry
                </button>
              )}
              <button
                onClick={handleReconcile}
                disabled={!balanced || reconciling}
                className="flex items-center gap-1.5 px-4 py-2 bg-gilt-brass text-gilt-paper text-sm font-medium rounded-md hover:opacity-90 transition-opacity duration-150 disabled:opacity-40 disabled:cursor-not-allowed"
              >
                {reconciling ? <RefreshCw size={13} className="animate-spin" /> : <Link size={13} />}
                {reconciling ? "Reconciling..." : "Reconcile"}
              </button>
            </div>
          </div>
        </div>
      )}

      {bothEmpty ? (
        <div className="flex flex-col items-center justify-center py-16 text-center gap-3">
          <div className="w-14 h-14 rounded-full bg-gilt-sunken border border-gilt-hairline flex items-center justify-center">
            <FileText size={24} className="text-gilt-credit" />
          </div>
          <p className="text-gilt-ink font-medium">All entries reconciled!</p>
          <p className="text-gilt-soft text-sm">Click "View Report" to see the reconciliation summary.</p>
          <button
            onClick={onViewReport}
            className="mt-1 px-5 py-2 bg-gilt-brass text-gilt-paper text-sm font-medium rounded-md hover:opacity-90 transition-opacity duration-150"
          >
            View Reconciliation Report →
          </button>
          <div className="flex items-center gap-4 mt-2">
            <button
              onClick={() => openBankEntryModal()}
              className="flex items-center gap-1 text-xs font-medium text-gilt-brass hover:underline"
            >
              <Plus size={12} /> Add bank entry
            </button>
            <button
              onClick={() => { setAdjStandalone(true); setAdjOpen(true); }}
              className="flex items-center gap-1 text-xs font-medium text-gilt-brass hover:underline"
            >
              <Plus size={12} /> Add book entry
            </button>
          </div>
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-4">
          {/* Bank panel */}
          <div className="bg-gilt-card rounded-xl border border-gilt-hairline overflow-hidden">
            <div className="px-4 py-3 bg-gilt-sunken border-b border-gilt-hairline flex items-center justify-between">
              <p className="font-mono tabular-nums text-[10px] uppercase tracking-[0.14em] font-medium text-gilt-brass">Unmatched Bank Entries ({bankTxns.length})</p>
              <button
                onClick={() => openBankEntryModal()}
                className="flex items-center gap-1 text-xs font-medium text-gilt-brass hover:underline"
                title="Add a statement row the parser missed (local-only)"
              >
                <Plus size={12} /> Add entry
              </button>
            </div>
            <div className="p-3">
              <div className="relative mb-2">
                <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-gilt-faint" />
                <input
                  type="text"
                  placeholder="Search..."
                  value={bankSearch}
                  onChange={(e) => setBankSearch(e.target.value)}
                  className="w-full pl-7 pr-3 py-1.5 border border-gilt-hairline rounded-lg text-xs text-gilt-ink bg-gilt-paper placeholder:text-gilt-faint focus:outline-none focus:border-gilt-brass transition-colors duration-150"
                />
              </div>
              <div className="max-h-[calc(100vh-340px)] min-h-[20rem] overflow-y-auto">
                {filteredBank.length === 0 ? (
                  <p className="text-center text-gilt-soft text-xs py-6">No entries</p>
                ) : (
                  filteredBank.map((t) => (
                    <TxnRow
                      key={t.txn_id}
                      txn={t}
                      source="bank_statement"
                      selected={selectedBank.has(t.txn_id)}
                      onClick={() => toggleBank(t.txn_id)}
                      onMarkPending={handleMarkPending}
                      onEdit={t.is_manual ? openBankEntryModal : undefined}
                      onDelete={t.is_manual ? handleDeleteBankEntry : undefined}
                    />
                  ))
                )}
              </div>
            </div>
          </div>

          {/* Book panel */}
          <div className="bg-gilt-card rounded-xl border border-gilt-hairline overflow-hidden">
            <div className="px-4 py-3 bg-gilt-sunken border-b border-gilt-hairline flex items-center justify-between">
              <p className="font-mono tabular-nums text-[10px] uppercase tracking-[0.14em] font-medium text-gilt-brass">Unmatched Book Entries ({bookTxns.length})</p>
              <button
                onClick={() => { setAdjStandalone(true); setAdjOpen(true); }}
                className="flex items-center gap-1 text-xs font-medium text-gilt-brass hover:underline"
                title="Record a book entry with no statement counterpart — posts to QuickBooks"
              >
                <Plus size={12} /> Add entry
              </button>
            </div>
            <div className="p-3">
              <div className="relative mb-2">
                <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-gilt-faint" />
                <input
                  type="text"
                  placeholder="Search..."
                  value={bookSearch}
                  onChange={(e) => setBookSearch(e.target.value)}
                  className="w-full pl-7 pr-3 py-1.5 border border-gilt-hairline rounded-lg text-xs text-gilt-ink bg-gilt-paper placeholder:text-gilt-faint focus:outline-none focus:border-gilt-brass transition-colors duration-150"
                />
              </div>
              <div className="max-h-[calc(100vh-340px)] min-h-[20rem] overflow-y-auto">
                {filteredBook.length === 0 ? (
                  <p className="text-center text-gilt-soft text-xs py-6">No entries</p>
                ) : (
                  filteredBook.map((t) => (
                    <TxnRow
                      key={t.txn_id}
                      txn={t}
                      source="book"
                      selected={selectedBook.has(t.txn_id)}
                      onClick={() => toggleBook(t.txn_id)}
                      onMarkPending={handleMarkPending}
                    />
                  ))
                )}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Pending (set aside) */}
      {pendingTxns.length > 0 && (
        <div className="bg-gilt-sunken border border-gilt-brass/40 rounded-xl overflow-hidden">
          <div className="px-4 py-3 border-b border-gilt-hairline flex items-center gap-2">
            <Clock size={14} className="text-gilt-brass" />
            <p className="font-mono tabular-nums text-[10px] font-medium text-gilt-brass uppercase tracking-[0.14em]">Pending review ({pendingTxns.length})</p>
            <span className="text-[11px] text-gilt-faint">— set aside, excluded from the difference</span>
          </div>
          <div className="p-3 space-y-1 max-h-60 overflow-y-auto">
            {pendingTxns.map((t) => (
              <div key={t.txn_id} className="flex items-center justify-between gap-2 bg-gilt-card border border-gilt-hairline rounded-lg px-3 py-2 text-xs">
                <span className="inline-flex items-center gap-1 px-1.5 py-0.5 font-mono text-[10px] tracking-[0.14em] rounded-full border border-gilt-brass/40 text-gilt-brass uppercase flex-shrink-0">
                  {t.source === "bank_statement" ? "bank" : "book"}
                </span>
                <span className="truncate flex-1 text-gilt-ink">{t.transaction_date} · {t.description || "(no description)"}</span>
                <span className="font-mono tabular-nums text-gilt-soft flex-shrink-0">
                  {t.debit_amount != null ? `Dr ${fmt(t.debit_amount)}` : `Cr ${fmt(t.credit_amount)}`}
                </span>
                <button
                  onClick={() => handleRestorePending(t)}
                  className="text-[11px] text-gilt-brass hover:underline inline-flex items-center gap-1 flex-shrink-0"
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
        <p className="text-xs text-gilt-soft text-center">
          Select any number of bank and book entries. Reconcile when the difference is zero, create an adjusting entry to plug a gap, or use "Add entry" on either panel to record a missing row.
        </p>
      )}

      <AdjustmentModal
        open={adjOpen}
        prefill={adjPrefill}
        submitting={adjSubmitting}
        accounts={postingAccounts}
        onClose={() => setAdjOpen(false)}
        onSubmit={handleCreateAdjustment}
      />

      <BankEntryModal
        open={bankEntryOpen}
        prefill={bankEntryPrefill}
        editing={!!bankEntryEditing}
        submitting={bankEntrySubmitting}
        onClose={() => { setBankEntryOpen(false); setBankEntryEditing(null); }}
        onSubmit={handleSubmitBankEntry}
      />
    </div>
  );
}
