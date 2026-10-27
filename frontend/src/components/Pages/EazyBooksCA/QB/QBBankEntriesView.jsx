import React, { useEffect, useMemo, useState } from "react";
import {
    Banknote,
    Building2,
    Wallet,
    FileText,
    Sparkles,
    AlertTriangle,
    X,
    Send,
    CheckCircle2,
    Loader2,
    Layers,
    ArrowUpRight,
    Search,
    Eye,
    EyeOff,
    ArrowDownUp,
} from "lucide-react";
import { Panel, Group as PanelGroup, Separator as PanelResizeHandle } from "react-resizable-panels";
import { useLocation, useNavigate } from "react-router-dom";
import { qbApi } from "../../../../services/api";
import { useDraftDeepLink } from "../common/useDraftDeepLink";
import SearchableDropdown from "../common/SearchableDropdown";
import AISuggestedDropdown from "../common/AISuggestedDropdown";
import ConfidenceBadge from "../common/ConfidenceBadge";
import AccountCreatePrompt from "../common/AccountCreatePrompt";
import { SaveDraftButton } from "../common/DraftsPanel";
import DuplicateBadge from "../common/DuplicateBadge";
import ErrorMessage from "./components/ErrorMessage";
import { formatQbError } from "./components/qboError";
import { PageHeader } from "../../../ui/page-header";
import { Card } from "../../../ui/card";

const inputBase = "w-full border border-gilt-hairline rounded-lg px-3 py-2 text-sm text-gilt-ink bg-gilt-paper placeholder:text-gilt-faint focus:border-gilt-brass outline-none transition-colors duration-150";
const labelBase = "block text-xs font-medium text-gilt-soft mb-1.5";
const btnPrimary = "inline-flex items-center gap-1.5 px-4 py-2 text-sm font-semibold text-gilt-paper bg-gilt-brass rounded-md hover:bg-gilt-brass/90 transition-colors duration-150 disabled:opacity-50 disabled:cursor-not-allowed";
// Primary push affordance — quiet ink plate per the gilt workroom idiom.
const btnQbGreen = "inline-flex items-center gap-1.5 px-4 py-2 text-sm font-semibold text-gilt-paper bg-gilt-brass rounded-md hover:bg-gilt-brass/90 transition-colors duration-150 disabled:opacity-50";

// Table cell inputs — fixed widths only (never w-full; repo rule), mono figures.
// These mirror Tally's BankEntriesView so the editor reads as the same component.
const tableDateInput = "w-28 px-2 py-1 text-sm font-mono tabular-nums text-gilt-ink bg-gilt-paper border border-gilt-hairline rounded focus:outline-none focus:border-gilt-brass transition-colors";
const tableTextInput = "w-full px-2 py-1 text-sm text-gilt-ink bg-gilt-paper border border-gilt-hairline rounded focus:outline-none focus:border-gilt-brass transition-colors placeholder:text-gilt-faint";
const tableDebitInput = "w-28 px-2 py-1 text-sm text-right font-mono tabular-nums border border-gilt-hairline rounded text-gilt-debit font-medium bg-gilt-paper focus:outline-none focus:border-gilt-brass transition-colors placeholder:text-gilt-faint";
const tableCreditInput = "w-28 px-2 py-1 text-sm text-right font-mono tabular-nums border border-gilt-hairline rounded text-gilt-credit font-medium bg-gilt-paper focus:outline-none focus:border-gilt-brass transition-colors placeholder:text-gilt-faint";

// Tally-parity voucher vocabulary. The backend maps these to QBO objects
// (Receipt->Deposit, Payment->Purchase, Contra->Transfer, Journal->JournalEntry).
const VOUCHER_TYPES = ["Receipt", "Payment", "Contra", "Journal"];

// Adapt a QB backend account/party match into the AISuggestedDropdown
// `aiSuggestion` shape. Skips user/manual picks so the "AI suggested" chip only
// shows for actual backend suggestions.
const aiSuggestionFromMatch = (m) => {
    if (!m || !m.name || m.confidence == null) return null;
    if (m.method === "manual" || m.method === "created") return null;
    return {
        recommended_ledger: m.name,
        confidence: m.confidence,
        match_type: m.method === "history" ? "history" : "fuzzy",
        reasoning: m.method ? `Matched via ${m.method}` : "AI-matched account",
    };
};

// Parse a row date to a sortable timestamp. Handles DD/MM/YYYY and DD-MM-YYYY
// (what the extractor emits) explicitly — Date.parse mis-reads those as US
// MM/DD — and falls back to Date.parse for ISO. Unparseable -> 0.
const dateToTs = (s = "") => {
    const m = String(s).trim().match(/^(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})$/);
    if (m) {
        const yy = m[3].length === 2 ? "20" + m[3] : m[3];
        return new Date(Number(yy), Number(m[2]) - 1, Number(m[1])).getTime();
    }
    const t = Date.parse(s);
    return Number.isNaN(t) ? 0 : t;
};

/**
 * QB Bank Entries — a faithful clone of the Tally bank-entries flow, made
 * ERP-agnostic. The frontend holds and edits the SAME normalized voucher Tally
 * uses; it never builds a QBO payload. All QBO-format translation lives in the
 * backend (build_qbo_bank_payload), exactly like Tally's build_bank_vouchers_xml.
 *
 *   1. Pick company + bank account, upload a statement
 *   2. Backend extracts rows -> normalized vouchers (with silent customer/vendor match)
 *   3. User edits Date / Narration / Debit / Credit / Voucher Type / Party Ledger
 *   4. Validate (accounts exist, duplicates), then push the batch -> QBO
 */
export default function QBBankEntriesView() {
    const [companies, setCompanies] = useState([]);
    const [company, setCompany] = useState("");
    const [accounts, setAccounts] = useState([]);
    const [accountId, setAccountId] = useState("");
    const [file, setFile] = useState(null);

    const [extracting, setExtracting] = useState(false);
    const [pushingAll, setPushingAll] = useState(false);
    const [error, setError] = useState("");
    const [result, setResult] = useState(null);

    // Master data for the Party Ledger (account) dropdowns.
    const [allAccounts, setAllAccounts] = useState([]);
    const [loadingCompanies, setLoadingCompanies] = useState(true);
    const [loadingMasters, setLoadingMasters] = useState(false);

    // Per-row push state: { [idx]: { status: 'pushing'|'done'|'error', message } }
    const [rowState, setRowState] = useState({});

    // Row selection for bulk ops (Tally parity).
    const [selectedRows, setSelectedRows] = useState(() => new Set());

    // "Create New Account" dialog from the Party Ledger dropdown.
    //   { entryIdx, accountType: 'Income'|'Expense', initialName }
    const [createDialog, setCreateDialog] = useState(null);

    // Pre-push validation that blocks the push (missing accounts / hard errors).
    const [blockingValidation, setBlockingValidation] = useState(null);

    // Drafts
    const [draftId, setDraftId] = useState(null);
    const [savingDraft, setSavingDraft] = useState(false);

    // Rows the user explicitly said "push anyway" for despite a duplicate verdict.
    const [overrideDupIdx, setOverrideDupIdx] = useState(() => new Set());
    const overrideDup = (i) => setOverrideDupIdx((prev) => new Set(prev).add(i));

    // ── Tally-parity editor controls: search / filter / sort / skip / PDF panel ──
    const [searchTerm, setSearchTerm] = useState("");
    const [voucherFilter, setVoucherFilter] = useState("");          // "" = all
    const [sortConfig, setSortConfig] = useState({ key: null, dir: "asc" }); // key: 'date'|'amount'
    const [skippedRows, setSkippedRows] = useState(() => new Set());
    const toggleSkip = (i) => {
        setSkippedRows((prev) => {
            const n = new Set(prev);
            n.has(i) ? n.delete(i) : n.add(i);
            return n;
        });
        // Drop it from the selection so the "N selected" count and push set
        // never include a skipped row.
        setSelectedRows((prev) => {
            if (!prev.has(i)) return prev;
            const n = new Set(prev); n.delete(i); return n;
        });
    };
    const [showPdf, setShowPdf] = useState(true);
    const [pdfUrl, setPdfUrl] = useState("");

    // Build/tear down an object URL for the uploaded statement so we can preview
    // it beside the table, mirroring Tally's PDF side panel.
    useEffect(() => {
        if (!file) { setPdfUrl(""); return; }
        const url = URL.createObjectURL(file);
        setPdfUrl(url);
        return () => URL.revokeObjectURL(url);
    }, [file]);
    const isPdf = !!file && /\.pdf$/i.test(file.name || "");

    const location = useLocation();
    const navigate = useNavigate();

    useEffect(() => {
        (async () => {
            try {
                const res = await qbApi.getCompanies();
                const list = res?.companies || [];
                setCompanies(list);
                // Prefer a company handed in by the upload wizard, else the first.
                const fromWizard = location.state?.autoExtract?.qbCompany;
                if (fromWizard) setCompany(fromWizard);
                else if (list[0]) setCompany(list[0].realm_id || list[0].name);
            } catch (err) {
                setError(formatQbError(err, "Failed to load companies"));
            } finally {
                setLoadingCompanies(false);
            }
        })();
    }, []); // eslint-disable-line react-hooks/exhaustive-deps

    // Auto-extract when the upload wizard hands us a file (Tally-style direct
    // open). Runs once; clears router state so a refresh won't re-trigger it.
    useEffect(() => {
        const auto = location.state?.autoExtract;
        if (!auto?.file) return;
        navigate(location.pathname, { replace: true, state: {} });
        setFile(auto.file);
        setAccountId(auto.accountId || "");
        (async () => {
            setExtracting(true);
            setError("");
            setResult(null);
            setRowState({});
            setSelectedRows(new Set());
            try {
                const res = await qbApi.bankEntries.extract({
                    file: auto.file, accountId: auto.accountId, accountName: auto.accountName, qbCompany: auto.qbCompany,
                });
                setResult(withVouchers(res, { id: auto.accountId, name: auto.accountName }));
            } catch (err) {
                setError(formatQbError(err, "Extraction failed"));
            } finally {
                setExtracting(false);
            }
        })();
    }, []); // eslint-disable-line react-hooks/exhaustive-deps

    useEffect(() => {
        if (!company) return;
        setLoadingMasters(true);
        setAccounts([]);
        setAccountId("");
        setAllAccounts([]);
        (async () => {
            try {
                const [bankRes, a] = await Promise.all([
                    qbApi.bank.listAccounts(company),
                    qbApi.getAccounts(company),
                ]);
                const banks = bankRes?.accounts || [];
                setAccounts(banks);
                if (banks[0]) setAccountId(banks[0].id);
                setAllAccounts(a?.accounts || []);
            } catch (err) {
                setError(formatQbError(err, "Failed to load master data"));
            } finally {
                setLoadingMasters(false);
            }
        })();
    }, [company]);

    // Direction-filtered Party Ledger options, mirroring Tally's per-row ledger
    // list: income accounts for Receipts, expense accounts for Payments, and the
    // full account list for Contra / Journal (account-to-account moves).
    const incomeAccountNames = useMemo(
        () => allAccounts.filter((a) => ["Income", "OtherIncome"].includes(a.account_type)).map((a) => a.name).filter(Boolean).sort(),
        [allAccounts],
    );
    const expenseAccountNames = useMemo(
        () => allAccounts.filter((a) => ["Expense", "CostOfGoodsSold", "OtherExpense", "OtherCurrentLiability"].includes(a.account_type)).map((a) => a.name).filter(Boolean).sort(),
        [allAccounts],
    );
    const allAccountNames = useMemo(() => allAccounts.map((a) => a.name).filter(Boolean).sort(), [allAccounts]);
    const accountByName = useMemo(() => Object.fromEntries(allAccounts.map((a) => [a.name, a])), [allAccounts]);

    const partyOptionsFor = (vt) => {
        if (vt === "Receipt") return incomeAccountNames;
        if (vt === "Payment") return expenseAccountNames;
        return allAccountNames;
    };

    // ── Normalize extract / draft payloads so every entry carries a `voucher` ──
    // Old extracts/drafts (pre-parity) only had qbo_payload/type — backfill a
    // normalized voucher so this view works regardless of payload vintage.
    const withVouchers = (res, acct) => {
        if (!res?.entries) return res;
        const bankName = acct?.name;
        const bankId = acct?.id;
        const entries = res.entries.map((e) => {
            if (e.voucher) {
                return { ...e, voucher: { ...e.voucher, bank_ledger: bankName ?? e.voucher.bank_ledger, bank_ledger_id: bankId ?? e.voucher.bank_ledger_id } };
            }
            const cp = e.counterparty_match?.matched
                ? { id: e.counterparty_match.list_id, name: e.counterparty_match.name, type: e.type === "deposit" ? "Customer" : "Vendor" }
                : null;
            return {
                ...e,
                voucher: {
                    date: e.row?.date || "",
                    narration: e.row?.description || "",
                    amount: e.amount,
                    voucher_type: e.type === "deposit" ? "Receipt" : "Payment",
                    party_ledger: e.account_match?.name || null,
                    party_ledger_id: e.account_match?.list_id || null,
                    bank_ledger: bankName,
                    bank_ledger_id: bankId,
                    reference: e.row?.reference || "",
                    voucher_number: "",
                    counterparty: cp,
                },
            };
        });
        return { ...res, entries };
    };

    const onExtract = async () => {
        if (!file) { setError("Pick a statement file first."); return; }
        if (!accountId) { setError("Pick a bank account."); return; }
        setExtracting(true);
        setError("");
        setResult(null);
        setRowState({});
        setSelectedRows(new Set());
        try {
            const acct = accounts.find((a) => a.id === accountId);
            const res = await qbApi.bankEntries.extract({ file, accountId, accountName: acct?.name, qbCompany: company });
            setResult(withVouchers(res, acct));
        } catch (err) {
            setError(formatQbError(err, "Extraction failed"));
        } finally {
            setExtracting(false);
        }
    };

    // ── Row editing — mutate the normalized voucher, never a QBO payload ──
    const updateRow = (idx, patch) => {
        setResult((prev) => {
            const next = structuredClone(prev);
            const e = next.entries[idx];
            const v = e.voucher;
            if (patch.voucher_type !== undefined) v.voucher_type = patch.voucher_type;
            if (patch.narration !== undefined) v.narration = patch.narration;
            if (patch.date !== undefined) v.date = patch.date;
            if (patch.amount !== undefined) v.amount = Number(patch.amount) || 0;
            if (patch.partyName !== undefined) {
                const acc = patch.partyObj || accountByName[patch.partyName];
                v.party_ledger = patch.partyName;
                if (acc) {
                    v.party_ledger_id = acc.list_id;
                    e.account_match = { matched: true, name: acc.name, list_id: acc.list_id, confidence: 1, method: "manual" };
                } else {
                    v.party_ledger_id = null;
                    e.account_match = { matched: false, name: patch.partyName };
                }
            }
            return next;
        });
    };

    // ── Selection / bulk ops (Tally parity) ──
    const pushableIndices = () => (result?.entries || []).map((_, i) => i)
        .filter((i) => rowState[i]?.status !== "done" && !skippedRows.has(i));

    const toggleRow = (i) => setSelectedRows((prev) => {
        const n = new Set(prev);
        n.has(i) ? n.delete(i) : n.add(i);
        return n;
    });
    const bulkSetType = (vt) => selectedRows.forEach((i) => updateRow(i, { voucher_type: vt }));
    const bulkSetParty = (name) => selectedRows.forEach((i) => updateRow(i, { partyName: name }));

    const isRowBlockedDup = (idx) => {
        const e = result?.entries?.[idx];
        return !!e?.duplicate_check?.is_duplicate && !overrideDupIdx.has(idx);
    };

    // ── Validate + push (the normalized contract -> push-batch) ──
    const buildVouchers = (items, acct) => items.map(({ e }) => ({
        ...e.voucher,
        amount: Math.abs(Number(e.voucher.amount) || 0),
        bank_ledger: acct?.name,
        bank_ledger_id: acct?.id,
    }));

    // Merge accounts the backend auto-created into local state + onto the rows
    // that referenced them, so the UI reflects the new ledgers.
    const mergeCreatedMasters = (created = []) => {
        if (!created.length) return;
        setAllAccounts((prev) => {
            const next = [...prev];
            created.forEach((m) => {
                if (!next.some((a) => a.list_id === m.id || a.name === m.name)) {
                    next.push({ list_id: m.id, name: m.name, account_type: m.account_type });
                }
            });
            return next;
        });
        setResult((prev) => {
            if (!prev) return prev;
            const byName = Object.fromEntries(created.map((m) => [m.name.toLowerCase(), m]));
            const next = structuredClone(prev);
            next.entries.forEach((e) => {
                const m = byName[(e.voucher?.party_ledger || "").toLowerCase()];
                if (m && !e.voucher.party_ledger_id) {
                    e.voucher.party_ledger_id = m.id;
                    e.account_match = { matched: true, name: m.name, list_id: m.id, confidence: 1, method: "created" };
                }
            });
            return next;
        });
    };

    const pushRows = async (indices, { autoCreate = false } = {}) => {
        const items = indices
            .filter((i) => rowState[i]?.status !== "done" && !isRowBlockedDup(i) && !skippedRows.has(i))
            .map((i) => ({ i, e: result.entries[i] }));
        if (!items.length) return;

        const bad = items.find(({ e }) => !e.voucher?.date || !e.voucher?.party_ledger || !e.voucher?.voucher_type);
        if (bad) {
            setError("Every row needs a date, party ledger and voucher type before pushing.");
            return;
        }
        setError("");

        // Flip the affected rows to "pushing" *before* the pre-push validation
        // round-trip so the button responds to the click immediately. The
        // validation call routinely takes ~2s (it hits QBO for masters +
        // duplicate-check); without this, the row sat in the idle state for
        // that whole window and the click felt unacknowledged.
        setRowState((s) => {
            const n = { ...s };
            items.forEach(({ i }) => { n[i] = { status: "pushing" }; });
            return n;
        });

        const acct = accounts.find((a) => a.id === accountId);
        const vouchers = buildVouchers(items, acct);

        // Pre-push validation (accounts exist, duplicates). Skipped when the user
        // chose "Create accounts & push" — auto-create resolves the missing ones.
        if (!autoCreate) {
            try {
                const v = await qbApi.bankEntries.validate({
                    qbCompany: company, bankAccountId: accountId, bankAccountName: acct?.name, vouchers,
                });
                if (v?.missing_masters?.length || v?.errors?.length) {
                    // Roll the rows back to idle — validation needs the user's
                    // attention (resolve-before-pushing block) before we can post.
                    setRowState((s) => {
                        const n = { ...s };
                        items.forEach(({ i }) => { delete n[i]; });
                        return n;
                    });
                    setBlockingValidation({ ...v, _indices: indices });
                    return;
                }
            } catch (_) { /* validation unavailable — fall through to push */ }
        }
        try {
            const res = await qbApi.bankEntries.pushBatch({
                qbCompany: company, bankAccountId: accountId, bankAccountName: acct?.name, vouchers,
                autoCreateMasters: autoCreate,
            });
            mergeCreatedMasters(res?.created_masters);
            (res?.results || []).forEach((r) => {
                const target = items[r.index];
                if (!target) return;
                setRowState((s) => ({
                    ...s,
                    [target.i]: r.ok
                        ? { status: "done", message: `${r.type} #${r.id || "?"}` }
                        : { status: "error", message: r.error || "Push failed" },
                }));
            });
        } catch (err) {
            const msg = formatQbError(err, "Push failed");
            setRowState((s) => {
                const n = { ...s };
                items.forEach(({ i }) => { n[i] = { status: "error", message: msg }; });
                return n;
            });
        }
    };

    const pushAll = async () => {
        setPushingAll(true);
        try {
            await pushRows(pushableIndices());
        } finally {
            setPushingAll(false);
        }
    };
    const pushSelected = () => pushRows([...selectedRows]);

    // ── Create-account from the Party Ledger dropdown ──
    const onAccountCreated = (acc) => {
        if (!acc?.name) { setCreateDialog(null); return; }
        setAllAccounts((prev) => (prev.some((a) => a.list_id === acc.list_id || a.name === acc.name)
            ? prev
            : [...prev, { list_id: acc.list_id, name: acc.name, account_type: acc.account_type }]));
        if (typeof createDialog?.entryIdx === "number") {
            updateRow(createDialog.entryIdx, {
                partyName: acc.name,
                partyObj: { list_id: acc.list_id, name: acc.name, account_type: acc.account_type },
            });
        }
        setCreateDialog(null);
    };

    // ── Drafts ──
    const deriveDraftTitle = () => {
        const acct = accounts.find((a) => a.id === accountId);
        const n = result?.entries?.length;
        if (acct && n) return `${acct.name} · ${n} ${n === 1 ? "entry" : "entries"}`;
        if (acct) return acct.name;
        if (n) return `${n} bank ${n === 1 ? "entry" : "entries"}`;
        return "Bank entries draft";
    };
    const handleSaveDraft = async () => {
        if (!result) return;
        setSavingDraft(true);
        setError("");
        try {
            const res = await qbApi.drafts.save({
                flow: "bank_entries",
                payload: { result, rowState, accountId, accountName: accounts.find((a) => a.id === accountId)?.name },
                title: deriveDraftTitle(),
                draftId,
                qbCompany: company,
            });
            if (res?.draft_id) setDraftId(res.draft_id);
        } catch (e) {
            setError(formatQbError(e, "Save draft failed"));
        } finally {
            setSavingDraft(false);
        }
    };
    const handleOpenDraft = (draft) => {
        const payload = draft?.payload || {};
        const acct = accounts.find((a) => a.id === (payload.accountId || accountId));
        setResult(payload.result ? withVouchers(payload.result, acct) : null);
        setRowState(payload.rowState || {});
        if (payload.accountId) setAccountId(payload.accountId);
        setDraftId(draft.id);
        setError("");
    };
    useDraftDeepLink({ getDraft: qbApi.drafts.get, companyField: "realm_id", setCompany, onOpen: handleOpenDraft });
    const handleDraftDeleted = (id) => { if (draftId === id) setDraftId(null); };

    // ── Render ──
    const dupCount = result ? result.entries.filter((e, i) => e.duplicate_check?.is_duplicate && !overrideDupIdx.has(i) && rowState[i]?.status !== "done").length : 0;

    // Rows to display: original index preserved so all state ops (rowState,
    // selectedRows, skippedRows, updateRow) keep working after filter/sort.
    const visibleEntries = useMemo(() => {
        if (!result?.entries) return [];
        let rows = result.entries.map((e, i) => ({ e, i }));
        const q = searchTerm.trim().toLowerCase();
        if (q) {
            rows = rows.filter(({ e }) => {
                const v = e.voucher || {};
                return [v.narration, v.party_ledger, v.reference, v.date, v.counterparty?.name, String(v.amount ?? "")]
                    .some((f) => (f || "").toString().toLowerCase().includes(q));
            });
        }
        if (voucherFilter) rows = rows.filter(({ e }) => (e.voucher?.voucher_type || "Receipt") === voucherFilter);
        if (sortConfig.key) {
            const dir = sortConfig.dir === "asc" ? 1 : -1;
            rows = [...rows].sort((a, b) => {
                const va = a.e.voucher || {}, vb = b.e.voucher || {};
                if (sortConfig.key === "amount") return (Math.abs(va.amount || 0) - Math.abs(vb.amount || 0)) * dir;
                return (dateToTs(va.date) - dateToTs(vb.date)) * dir;
            });
        }
        return rows;
    }, [result, searchTerm, voucherFilter, sortConfig]);

    const toggleSort = (key) => setSortConfig((prev) =>
        prev.key === key ? { key, dir: prev.dir === "asc" ? "desc" : "asc" } : { key, dir: "asc" });

    // Stats bar (Tally parity): counts + credit/debit totals over all entries.
    // Only Receipt/Payment contribute to the bank-side credit/debit totals;
    // Contra/Journal are account-to-account moves and would skew the figures.
    const stats = useMemo(() => {
        const entries = result?.entries || [];
        let mapped = 0, credits = 0, debits = 0;
        entries.forEach((e, i) => {
            const v = e.voucher || {};
            if (v.party_ledger) mapped += 1;
            if (skippedRows.has(i)) return;
            const amt = Math.abs(Number(v.amount) || 0);
            const vt = v.voucher_type || "Receipt";
            if (vt === "Receipt") credits += amt; else if (vt === "Payment") debits += amt;
        });
        return { total: entries.length, mapped, skipped: skippedRows.size, credits, debits };
    }, [result, skippedRows]);
    const fmt = (n) => `₹${n.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

    // Select-all and its checked state operate on the VISIBLE pushable rows only
    // (respect the active search/filter; exclude pushed + skipped rows).
    const visiblePushable = visibleEntries.map(({ i }) => i)
        .filter((i) => rowState[i]?.status !== "done" && !skippedRows.has(i));
    const allSelected = visiblePushable.length > 0 && visiblePushable.every((i) => selectedRows.has(i));
    const toggleAll = () => setSelectedRows(() => (allSelected ? new Set() : new Set(visiblePushable)));

    return (
        <div className="min-h-screen flex flex-col text-gilt-ink">
            <PageHeader
                icon={Banknote}
                title={<span className="gl-display text-gilt-ink">QuickBooks · Bank Entries</span>}
                description="Upload a bank statement, review the vouchers, and post them to QuickBooks."
            />

            <div className="flex-1 px-6 lg:px-8 py-5 space-y-5">
                {error && (
                    <Card className="bg-gilt-card border-gilt-debit/40 shadow-none">
                        <div className="flex items-start gap-2 px-3 py-2 text-sm text-gilt-debit">
                            <AlertTriangle className="h-4 w-4 flex-shrink-0 mt-0.5 text-gilt-debit" />
                            <span className="flex-1">{error}</span>
                            <button onClick={() => setError("")} className="text-gilt-debit/70 hover:text-gilt-debit transition-colors duration-150">
                                <X className="h-4 w-4" />
                            </button>
                        </div>
                    </Card>
                )}

                {/* Statement & target card */}
                <div className="bg-gilt-card rounded-xl border border-gilt-hairline p-5">
                    <div className="flex items-center gap-2 mb-4">
                        <Sparkles className="h-4 w-4 text-gilt-brass" />
                        <h2 className="font-mono text-[10px] font-semibold text-gilt-brass uppercase tracking-[0.14em]">Statement & target</h2>
                    </div>
                    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                        <div>
                            <label className={labelBase}><Building2 className="inline h-3.5 w-3.5 mr-1 text-gilt-faint" />Company</label>
                            <select className={inputBase} value={company} onChange={(e) => setCompany(e.target.value)} disabled={loadingCompanies || !companies.length}>
                                {loadingCompanies && <option value="">Loading companies…</option>}
                                {!loadingCompanies && !companies.length && <option value="">No companies</option>}
                                {companies.map((c) => {
                                    const v = c.realm_id || c.name;
                                    return <option key={v} value={v}>{c.name}</option>;
                                })}
                            </select>
                        </div>
                        <div>
                            <label className={labelBase}><Wallet className="inline h-3.5 w-3.5 mr-1 text-gilt-faint" />Bank account</label>
                            <select className={inputBase} value={accountId} onChange={(e) => setAccountId(e.target.value)} disabled={loadingMasters || !accounts.length}>
                                {loadingMasters && <option value="">Loading bank accounts…</option>}
                                {!loadingMasters && accounts.length === 0 && <option value="">No bank accounts</option>}
                                {accounts.map((a) => (
                                    <option key={a.id} value={a.id}>{a.name} ({a.account_type}{a.currency ? `, ${a.currency}` : ""})</option>
                                ))}
                            </select>
                        </div>
                        <div>
                            <label className={labelBase}><FileText className="inline h-3.5 w-3.5 mr-1 text-gilt-faint" />Statement file</label>
                            <input type="file" accept=".pdf,.xls,.xlsx" onChange={(e) => setFile(e.target.files?.[0] || null)} disabled={loadingMasters}
                                className="block w-full text-sm text-gilt-soft file:mr-3 file:py-1.5 file:px-3 file:rounded-md file:border file:border-gilt-hairline file:bg-gilt-sunken file:text-sm file:font-medium file:text-gilt-brass hover:file:border-gilt-brass file:transition-colors file:duration-150" />
                        </div>
                    </div>
                    <div className="mt-4 flex items-center justify-between gap-3 flex-wrap">
                        <div className="flex items-center gap-3 text-xs text-gilt-soft">
                            {(loadingCompanies || loadingMasters) && (
                                <span className="inline-flex items-center gap-1"><Loader2 className="h-3 w-3 animate-spin" />{loadingCompanies ? "Companies…" : "Loading accounts…"}</span>
                            )}
                            {!loadingMasters && accounts.length > 0 && <span className="font-mono tabular-nums">{accounts.length} bank · {allAccounts.length} accounts</span>}
                            <span className="text-gilt-faint">Each voucher becomes a Deposit / Purchase / Transfer / Journal in QBO.</span>
                        </div>
                        <button onClick={onExtract} disabled={extracting || !file || !accountId || loadingMasters} className={btnPrimary}>
                            {extracting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
                            {extracting ? "Extracting…" : "Extract"}
                        </button>
                    </div>
                </div>

                {extracting && (
                    <div className="bg-gilt-card rounded-xl border border-gilt-hairline p-8 flex flex-col items-center justify-center gap-3">
                        <Loader2 className="h-8 w-8 animate-spin text-gilt-brass" />
                        <div className="text-sm font-medium text-gilt-ink">Extracting statement rows…</div>
                        <div className="text-xs text-gilt-soft">Classifying receipts and payments, and matching ledgers.</div>
                    </div>
                )}

                {result && (
                <div className="space-y-5">
                    {/* Status / actions bar */}
                    <div className="bg-gilt-card rounded-xl border border-gilt-hairline p-4 flex items-center gap-3 flex-wrap">
                        <span className="inline-flex items-center gap-1.5 text-sm font-medium text-gilt-ink font-mono tabular-nums">
                            <Layers className="h-4 w-4 text-gilt-brass" />
                            {result.entries.length} entries
                        </span>
                        {result.needs_review && (
                            <span className="inline-flex items-center gap-1 px-2 py-0.5 font-mono text-[10px] font-medium uppercase tracking-[0.14em] rounded-sm bg-transparent border border-gilt-brass/40 text-gilt-brass">
                                <AlertTriangle className="h-3 w-3" />Some rows unmatched
                            </span>
                        )}
                        {dupCount > 0 && (
                            <span className="inline-flex items-center gap-1 px-2 py-0.5 font-mono text-[10px] font-medium uppercase tracking-[0.14em] rounded-sm bg-transparent border border-gilt-debit/40 text-gilt-debit">
                                <AlertTriangle className="h-3 w-3" />{dupCount} duplicate{dupCount === 1 ? "" : "s"} blocked
                            </span>
                        )}
                        <div className="ml-auto flex items-center gap-2 flex-wrap">
                            {isPdf && (
                                <button onClick={() => setShowPdf((s) => !s)} className="inline-flex items-center gap-1.5 px-3 py-2 text-sm font-medium text-gilt-soft border border-gilt-hairline rounded-md hover:border-gilt-brass hover:text-gilt-brass transition-colors duration-150">
                                    <FileText className="h-4 w-4" />{showPdf ? "Hide PDF" : "Show PDF"}
                                </button>
                            )}
                            <SaveDraftButton onClick={handleSaveDraft} busy={savingDraft} label={draftId ? "Update draft" : "Save draft"} />
                            <button onClick={pushAll} disabled={pushingAll} className={btnQbGreen}>
                                {pushingAll ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
                                Push all
                            </button>
                        </div>
                    </div>

                    {/* Stats bar (Tally parity) */}
                    <div className="bg-gilt-card rounded-xl border border-gilt-hairline px-4 py-2.5 flex items-center gap-5 flex-wrap text-xs">
                        <span className="text-gilt-soft"><span className="font-semibold font-mono tabular-nums text-gilt-ink">{stats.total}</span> total</span>
                        <span className="text-gilt-soft"><span className="font-semibold font-mono tabular-nums text-gilt-ink">{stats.mapped}</span> mapped</span>
                        {stats.skipped > 0 && <span className="text-gilt-brass"><span className="font-semibold font-mono tabular-nums">{stats.skipped}</span> skipped</span>}
                        <span className="ml-auto text-gilt-credit">Credits: <span className="font-semibold font-mono tabular-nums">{fmt(stats.credits)}</span></span>
                        <span className="text-gilt-debit">Debits: <span className="font-semibold font-mono tabular-nums">{fmt(stats.debits)}</span></span>
                    </div>

                    {/* Bulk bar (visible when rows are selected) */}
                    {selectedRows.size > 0 && (
                        <div className="bg-gilt-sunken border border-gilt-hairline rounded-xl p-3 flex items-center gap-3 flex-wrap text-sm">
                            <span className="font-medium text-gilt-ink font-mono tabular-nums">{selectedRows.size} selected</span>
                            <div className="flex items-center gap-1.5">
                                <span className="text-xs text-gilt-soft">Set type:</span>
                                {VOUCHER_TYPES.map((vt) => (
                                    <button key={vt} onClick={() => bulkSetType(vt)} className="text-xs px-2 py-1 rounded-md border border-gilt-hairline bg-gilt-card text-gilt-soft hover:border-gilt-brass hover:text-gilt-brass transition-colors duration-150">{vt}</button>
                                ))}
                            </div>
                            <div className="flex items-center gap-1.5 min-w-[220px]">
                                <span className="text-xs text-gilt-soft">Set party:</span>
                                <div className="flex-1">
                                    <SearchableDropdown options={allAccountNames} value="" onChange={(name) => bulkSetParty(name)} placeholder="Apply ledger to selected…" />
                                </div>
                            </div>
                            <button onClick={pushSelected} className="ml-auto inline-flex items-center gap-1 px-3 py-1.5 bg-gilt-brass text-gilt-paper rounded-md text-xs font-semibold hover:bg-gilt-brass/90 transition-colors duration-150">
                                <Send className="h-3 w-3" />Push selected
                            </button>
                            <button onClick={() => setSelectedRows(new Set())} className="text-xs text-gilt-brass hover:text-gilt-ink transition-colors duration-150">Clear</button>
                        </div>
                    )}

                    {/* Search / filter / sort toolbar (Tally parity) */}
                    <div className="bg-gilt-card rounded-xl border border-gilt-hairline px-3 py-2.5 flex items-center gap-2 flex-wrap">
                        <div className="relative flex-1 min-w-[220px]">
                            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-4 w-4 text-gilt-faint" />
                            <input value={searchTerm} onChange={(e) => setSearchTerm(e.target.value)} placeholder="Search narration, ledger, reference, amount…"
                                className="w-full border border-gilt-hairline bg-gilt-paper text-gilt-ink placeholder:text-gilt-faint rounded-lg pl-8 pr-3 py-1.5 text-sm outline-none focus:border-gilt-brass transition-colors duration-150" />
                        </div>
                        <select value={voucherFilter} onChange={(e) => setVoucherFilter(e.target.value)} className="border border-gilt-hairline rounded-lg px-2 py-1.5 text-sm text-gilt-ink bg-gilt-paper outline-none focus:border-gilt-brass transition-colors duration-150">
                            <option value="">All types</option>
                            {VOUCHER_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
                        </select>
                        <button onClick={() => toggleSort("date")} className={`inline-flex items-center gap-1 px-2.5 py-1.5 text-xs font-medium rounded-lg border transition-colors duration-150 ${sortConfig.key === "date" ? "border-gilt-brass/40 bg-gilt-sunken text-gilt-brass" : "border-gilt-hairline text-gilt-soft hover:border-gilt-brass hover:text-gilt-brass"}`}>
                            <ArrowDownUp className="h-3.5 w-3.5" />Date{sortConfig.key === "date" ? (sortConfig.dir === "asc" ? " ↑" : " ↓") : ""}
                        </button>
                        <button onClick={() => toggleSort("amount")} className={`inline-flex items-center gap-1 px-2.5 py-1.5 text-xs font-medium rounded-lg border transition-colors duration-150 ${sortConfig.key === "amount" ? "border-gilt-brass/40 bg-gilt-sunken text-gilt-brass" : "border-gilt-hairline text-gilt-soft hover:border-gilt-brass hover:text-gilt-brass"}`}>
                            <ArrowDownUp className="h-3.5 w-3.5" />Amount{sortConfig.key === "amount" ? (sortConfig.dir === "asc" ? " ↑" : " ↓") : ""}
                        </button>
                        {(searchTerm || voucherFilter || sortConfig.key) && (
                            <button onClick={() => { setSearchTerm(""); setVoucherFilter(""); setSortConfig({ key: null, dir: "asc" }); }} className="text-xs text-gilt-soft hover:text-gilt-ink transition-colors duration-150">Clear</button>
                        )}
                        <span className="text-xs text-gilt-faint font-mono tabular-nums">{visibleEntries.length} shown</span>
                    </div>

                    {/* Editor: table + optional PDF preview (resizable, Tally parity) */}
                    <PanelGroup direction="horizontal" className="min-h-[60vh]">
                      <Panel defaultSize={showPdf && isPdf ? 58 : 100} minSize={30}>
                        <div className="h-full bg-gilt-card rounded-xl border border-gilt-hairline overflow-hidden flex flex-col">
                        <div className="overflow-auto flex-1">
                        <table className="w-full text-sm">
                            <thead className="bg-gilt-sunken border-b border-gilt-hairline">
                                <tr>
                                    <th className="px-3 py-2.5 w-8"><input type="checkbox" checked={!!allSelected} onChange={toggleAll} className="rounded border-gilt-hairline" /></th>
                                    <th className="px-3 py-2.5 text-left font-mono text-[10px] font-medium text-gilt-soft uppercase tracking-wider">Date</th>
                                    <th className="px-3 py-2.5 text-left font-mono text-[10px] font-medium text-gilt-soft uppercase tracking-wider">Narration</th>
                                    <th className="px-3 py-2.5 text-right font-mono text-[10px] font-medium text-gilt-debit uppercase tracking-wider">Debit</th>
                                    <th className="px-3 py-2.5 text-right font-mono text-[10px] font-medium text-gilt-credit uppercase tracking-wider">Credit</th>
                                    <th className="px-3 py-2.5 text-left font-mono text-[10px] font-medium text-gilt-soft uppercase tracking-wider">Voucher Type</th>
                                    <th className="px-3 py-2.5 text-left font-mono text-[10px] font-medium text-gilt-soft uppercase tracking-wider">Party Ledger</th>
                                    <th className="px-3 py-2.5 text-left font-mono text-[10px] font-medium text-gilt-soft uppercase tracking-wider">Status</th>
                                </tr>
                            </thead>
                            <tbody>
                                {visibleEntries.length === 0 && (
                                    <tr><td colSpan={8} className="px-3 py-8 text-center text-sm text-gilt-faint">No rows match the current search/filter.</td></tr>
                                )}
                                {visibleEntries.map(({ e, i }) => {
                                    const v = e.voucher || {};
                                    const vt = v.voucher_type || "Receipt";
                                    const isReceipt = vt === "Receipt";
                                    const status = rowState[i];
                                    const skipped = skippedRows.has(i);
                                    return (
                                        <tr key={i} className={`border-t border-gilt-hairline align-top hover:bg-gilt-sunken transition-colors duration-150 ${skipped ? "opacity-40" : ""}`}>
                                            <td className="px-3 py-2"><input type="checkbox" checked={selectedRows.has(i)} onChange={() => toggleRow(i)} className="rounded border-gilt-hairline" /></td>
                                            <td className="px-3 py-2">
                                                <input className={tableDateInput} defaultValue={v.date || ""} onBlur={(ev) => updateRow(i, { date: ev.target.value })} />
                                            </td>
                                            <td className="px-3 py-2">
                                                <input className={tableTextInput} defaultValue={v.narration || ""} onBlur={(ev) => updateRow(i, { narration: ev.target.value })} />
                                                {v.reference ? <div className="mt-0.5 text-[11px] font-mono text-gilt-faint">Ref: {v.reference}</div> : null}
                                            </td>
                                            <td className="px-3 py-2 text-right">
                                                <input className={tableDebitInput} type="number" step="0.01"
                                                    value={isReceipt ? "" : (v.amount ?? "")}
                                                    onChange={(ev) => updateRow(i, { amount: ev.target.value, voucher_type: (vt === "Receipt" || vt === "Payment") ? "Payment" : vt })} />
                                            </td>
                                            <td className="px-3 py-2 text-right">
                                                <input className={tableCreditInput} type="number" step="0.01"
                                                    value={isReceipt ? (v.amount ?? "") : ""}
                                                    onChange={(ev) => updateRow(i, { amount: ev.target.value, voucher_type: (vt === "Receipt" || vt === "Payment") ? "Receipt" : vt })} />
                                            </td>
                                            <td className="px-3 py-2">
                                                <select className={`px-2 py-1.5 text-xs font-mono border rounded focus:outline-none focus:border-gilt-brass transition-colors duration-150 ${
                                                        vt === "Receipt" ? "bg-gilt-sunken border-gilt-credit/40 text-gilt-credit"
                                                        : vt === "Payment" ? "bg-gilt-sunken border-gilt-debit/40 text-gilt-debit"
                                                        : vt === "Contra" ? "bg-gilt-sunken border-gilt-brass/40 text-gilt-brass"
                                                        : "bg-gilt-paper border-gilt-hairline text-gilt-ink"
                                                    }`}
                                                    value={vt} onChange={(ev) => updateRow(i, { voucher_type: ev.target.value })}>
                                                    {VOUCHER_TYPES.map((t) => <option key={t} value={t} className="text-gilt-ink bg-gilt-card">{t}</option>)}
                                                </select>
                                            </td>
                                            <td className="px-3 py-2">
                                                <AISuggestedDropdown
                                                    options={partyOptionsFor(vt)}
                                                    value={v.party_ledger || ""}
                                                    onChange={(name) => updateRow(i, { partyName: name })}
                                                    placeholder="Pick ledger…"
                                                    onCreateNew={() => setCreateDialog({ entryIdx: i, accountType: isReceipt ? "Income" : "Expense", initialName: v.party_ledger || "" })}
                                                    aiSuggestion={aiSuggestionFromMatch(e.account_match)}
                                                    erpLabel="QuickBooks"
                                                    onOverride={(picked, ai) => qbApi.recordSuggestionFeedback({
                                                        qb_company: company, slot: "account",
                                                        context_text: v.narration,
                                                        suggested_id: e.account_match?.list_id,
                                                        suggested_name: ai?.recommended_ledger,
                                                        suggested_confidence: ai?.confidence,
                                                        suggested_method: e.account_match?.method,
                                                        picked_id: accountByName[picked]?.list_id,
                                                        picked_name: picked,
                                                    })}
                                                />
                                                {v.counterparty?.name && (
                                                    <div className="mt-1 text-[11px] text-gilt-faint">↳ {v.counterparty.type}: {v.counterparty.name}</div>
                                                )}
                                            </td>
                                            <td className="px-3 py-2">
                                                {e.duplicate_check?.is_duplicate && (
                                                    <div className="mb-1.5"><DuplicateBadge check={e.duplicate_check} /></div>
                                                )}
                                                <div className="flex items-center gap-1.5">
                                                    {status?.status !== "done" && (
                                                        <button onClick={() => toggleSkip(i)} title={skipped ? "Include this row" : "Skip this row"}
                                                            className={`inline-flex items-center justify-center h-6 w-6 rounded-md border transition-colors duration-150 ${skipped ? "border-gilt-brass/40 bg-gilt-sunken text-gilt-brass" : "border-gilt-hairline text-gilt-faint hover:bg-gilt-sunken"}`}>
                                                            {skipped ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
                                                        </button>
                                                    )}
                                                    {!status && !skipped && isRowBlockedDup(i) && (
                                                        <button onClick={() => overrideDup(i)} className="inline-flex items-center gap-1 px-2.5 py-1 bg-gilt-sunken border border-gilt-brass/40 text-gilt-brass rounded-md text-xs font-medium hover:border-gilt-brass transition-colors duration-150" title="Push anyway despite duplicate verdict">Push anyway</button>
                                                    )}
                                                    {!status && !skipped && !isRowBlockedDup(i) && (
                                                        <button onClick={() => pushRows([i])} className="inline-flex items-center gap-1 px-2.5 py-1 bg-gilt-brass text-gilt-paper rounded-md text-xs font-medium hover:bg-gilt-brass/90 transition-colors duration-150">
                                                            <ArrowUpRight className="h-3 w-3" />Push
                                                        </button>
                                                    )}
                                                    {skipped && !status && <span className="text-[11px] text-gilt-brass">Skipped</span>}
                                                </div>
                                                {status?.status === "pushing" && (
                                                    <span className="inline-flex items-center gap-1 text-xs text-gilt-soft"><Loader2 className="h-3 w-3 animate-spin" />Pushing…</span>
                                                )}
                                                {status?.status === "done" && (
                                                    <span className="inline-flex items-center gap-1 px-2 py-0.5 font-mono text-[10px] font-medium uppercase tracking-[0.14em] rounded-sm bg-transparent border border-gilt-credit/40 text-gilt-credit"><CheckCircle2 className="h-3 w-3" />{status.message}</span>
                                                )}
                                                {status?.status === "error" && (
                                                    <div className="max-w-[420px]">
                                                        <ErrorMessage error={status.message} prefix="Push failed" compact />
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
                      </Panel>
                      {showPdf && isPdf && (
                        <>
                          <PanelResizeHandle className="w-1.5 mx-1 rounded bg-gilt-hairline hover:bg-gilt-brass transition-colors duration-150" />
                          <Panel defaultSize={42} minSize={25}>
                            <div className="h-full bg-gilt-card rounded-xl border border-gilt-hairline overflow-hidden">
                              <iframe title="Statement preview" src={pdfUrl} className="w-full h-full" />
                            </div>
                          </Panel>
                        </>
                      )}
                    </PanelGroup>
                </div>
                )}

            </div>

            {/* Create-account dialog from the Party Ledger dropdown */}
            <AccountCreatePrompt
                open={!!createDialog}
                initialName={createDialog?.initialName || ""}
                accountType={createDialog?.accountType}
                qbCompany={company}
                onClose={() => setCreateDialog(null)}
                onCreated={onAccountCreated}
            />

            {/* Pre-push validation block (missing accounts / hard errors) */}
            {blockingValidation && (
                <div className="fixed inset-0 z-50 flex items-center justify-center bg-gilt-ink/40 px-4">
                    <div className="bg-gilt-card rounded-xl border border-gilt-hairline max-w-lg w-full p-5 space-y-4">
                        <div className="flex items-center gap-2">
                            <AlertTriangle className="h-5 w-5 text-gilt-brass" />
                            <h3 className="text-sm font-semibold text-gilt-ink">Resolve before pushing</h3>
                        </div>
                        {blockingValidation.missing_masters?.length > 0 && (
                            <div>
                                <div className="text-xs font-medium text-gilt-soft mb-1">These ledgers don't exist in QuickBooks yet. "Create accounts &amp; push" will add them automatically (Income for Receipts, Expense for Payments) and post the entries:</div>
                                <ul className="text-sm text-gilt-ink list-disc list-inside">
                                    {blockingValidation.missing_masters.map((m, k) => <li key={k}>{m.name}</li>)}
                                </ul>
                            </div>
                        )}
                        {blockingValidation.errors?.length > 0 && (
                            <div>
                                <div className="text-xs font-medium text-gilt-soft mb-1">Errors:</div>
                                <ul className="text-sm text-gilt-debit list-disc list-inside">
                                    {blockingValidation.errors.map((er, k) => <li key={k}>{er.error || String(er)}</li>)}
                                </ul>
                            </div>
                        )}
                        <div className="flex justify-end gap-2">
                            <button onClick={() => setBlockingValidation(null)} className="px-4 py-2 text-sm font-medium text-gilt-soft hover:text-gilt-ink transition-colors duration-150">
                                {blockingValidation.missing_masters?.length > 0 ? "Cancel" : "Got it"}
                            </button>
                            {blockingValidation.missing_masters?.length > 0 && (
                                <button
                                    onClick={() => {
                                        const idx = blockingValidation._indices || [];
                                        setBlockingValidation(null);
                                        pushRows(idx, { autoCreate: true });
                                    }}
                                    className={btnQbGreen}
                                >
                                    Create accounts &amp; push
                                </button>
                            )}
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}
