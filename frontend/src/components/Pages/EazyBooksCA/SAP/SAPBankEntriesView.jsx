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
} from "lucide-react";
import { sapApi } from "../../../../services/api";
import { useDraftDeepLink } from "../common/useDraftDeepLink";
import SearchableDropdown from "../common/SearchableDropdown";
import ConfidenceBadge from "../common/ConfidenceBadge";
import CreateMasterModal from "./components/CreateMasterModal";
import MatchPicker from "./components/MatchPicker";
import BulkCreateModal from "./components/BulkCreateModal";
import MatchOriginBadge from "../common/MatchOriginBadge";
import DraftsPanel, { SaveDraftButton } from "../common/DraftsPanel";
import DuplicateBadge from "../common/DuplicateBadge";
import { PageHeader } from "../../../ui/page-header";
import { Card } from "../../../ui/card";

const inputBase = "w-full border border-gilt-hairline rounded-lg px-3 py-2 text-sm text-gilt-ink bg-gilt-card focus:border-gilt-brass placeholder:text-gilt-faint outline-none transition-colors duration-150";
const labelBase = "block font-mono text-[10px] uppercase tracking-[0.14em] text-gilt-soft mb-1.5";
const btnPrimary = "inline-flex items-center gap-1.5 px-4 py-2 text-sm font-semibold text-gilt-paper bg-gilt-brass rounded-md hover:bg-gilt-brass/90 transition-colors disabled:opacity-50 disabled:cursor-not-allowed";
const btnSapBlue = "inline-flex items-center gap-1.5 px-4 py-2 text-sm font-semibold text-gilt-paper bg-gilt-brass rounded-md hover:bg-gilt-brass/90 transition-colors disabled:opacity-50";

/**
 * SAP S/4HANA Bank Entries flow — write-side analog of bank reconciliation.
 *
 *   1. Pick company code + bank account
 *   2. Upload statement (PDF/Excel)
 *   3. Backend classifies each row (deposit / purchase / payment), matches counterparty
 *      and counter-account, returns draft SAP payloads
 *   4. User edits per-row counterparty / account / type
 *   5. Push each row to SAP S/4HANA (creates Deposit, Purchase, or Payment per row)
 */
export default function SAPBankEntriesView() {
    const [companies, setCompanies] = useState([]);
    const [company, setCompany] = useState("");
    const [accounts, setAccounts] = useState([]);
    const [accountId, setAccountId] = useState("");
    const [file, setFile] = useState(null);

    const [extracting, setExtracting] = useState(false);
    const [error, setError] = useState("");
    const [result, setResult] = useState(null);

    // Master data for per-row dropdowns
    const [customers, setCustomers] = useState([]);
    const [vendors, setVendors] = useState([]);
    const [allAccounts, setAllAccounts] = useState([]);
    const [loadingCompanies, setLoadingCompanies] = useState(true);
    const [loadingMasters, setLoadingMasters] = useState(false);

    // Per-row push state: { [idx]: 'pushing' | 'done' | 'error', message }
    const [rowState, setRowState] = useState({});

    // Inline-create modal state for unmatched counterparties / accounts.
    //   { entityType, prefillName, slot: 'counterparty'|'account', rowIdx }
    const [createModal, setCreateModal] = useState(null);
    const [bulkOpen, setBulkOpen] = useState(false);

    // Drafts
    const [draftId, setDraftId] = useState(null);
    const [draftsPanelOpen, setDraftsPanelOpen] = useState(false);
    const [draftsRefreshKey, setDraftsRefreshKey] = useState(0);
    const [savingDraft, setSavingDraft] = useState(false);

    // Per-row indices the user has explicitly said "push anyway" for despite
    // a duplicate verdict.
    const [overrideDupIdx, setOverrideDupIdx] = useState(() => new Set());
    const overrideDup = (i) => setOverrideDupIdx((prev) => {
        const next = new Set(prev);
        next.add(i);
        return next;
    });

    useEffect(() => {
        (async () => {
            try {
                const res = await sapApi.getCompanies();
                const list = res?.organizations || [];
                setCompanies(list);
                if (list[0]) setCompany(list[0].company_code || list[0].name);
            } catch (err) {
                setError(err?.response?.data?.detail || err.message || "Failed to load companies");
            } finally {
                setLoadingCompanies(false);
            }
        })();
    }, []);

    useEffect(() => {
        if (!company) return;
        setLoadingMasters(true);
        setAccounts([]);
        setAccountId("");
        setCustomers([]);
        setVendors([]);
        setAllAccounts([]);
        (async () => {
            try {
                const [bankRes, c, v, a] = await Promise.all([
                    sapApi.bank.listAccounts(company),
                    sapApi.getCustomers(company),
                    sapApi.getVendors(company),
                    sapApi.getAccounts(company),
                ]);
                const banks = bankRes?.accounts || [];
                setAccounts(banks);
                if (banks[0]) setAccountId(banks[0].id);
                setCustomers(c?.customers || []);
                setVendors(v?.vendors || []);
                setAllAccounts(a?.accounts || []);
            } catch (err) {
                setError(err?.response?.data?.detail || err.message || "Failed to load master data");
            } finally {
                setLoadingMasters(false);
            }
        })();
    }, [company]);

    const customerNames = useMemo(() => customers.map((c) => c.name).filter(Boolean).sort(), [customers]);
    const vendorNames = useMemo(() => vendors.map((v) => v.name).filter(Boolean).sort(), [vendors]);
    const customerByName = useMemo(() => Object.fromEntries(customers.map((c) => [c.name, c])), [customers]);
    const vendorByName = useMemo(() => Object.fromEntries(vendors.map((v) => [v.name, v])), [vendors]);

    const incomeAccountNames = useMemo(
        () => allAccounts
            .filter((a) => ["Income", "OtherIncome"].includes(a.account_type))
            .map((a) => a.name).filter(Boolean).sort(),
        [allAccounts],
    );
    const expenseAccountNames = useMemo(
        () => allAccounts
            .filter((a) => ["Expense", "CostOfGoodsSold", "OtherExpense", "OtherCurrentLiability"].includes(a.account_type))
            .map((a) => a.name).filter(Boolean).sort(),
        [allAccounts],
    );
    const accountByName = useMemo(() => Object.fromEntries(allAccounts.map((a) => [a.name, a])), [allAccounts]);

    const onExtract = async () => {
        if (!file) {
            setError("Pick a statement file first.");
            return;
        }
        if (!accountId) {
            setError("Pick a bank account.");
            return;
        }
        setExtracting(true);
        setError("");
        setResult(null);
        setRowState({});
        try {
            const acct = accounts.find((a) => a.id === accountId);
            const res = await sapApi.bankEntries.extract({
                file,
                accountId,
                accountName: acct?.name,
                companyCode: company,
            });
            setResult(res);
        } catch (err) {
            setError(err?.response?.data?.detail || err.message || "Extraction failed");
        } finally {
            setExtracting(false);
        }
    };

    const updateEntry = (idx, patch) => {
        setResult((prev) => {
            const next = structuredClone(prev);
            const e = next.entries[idx];
            if (patch.type !== undefined && patch.type !== e.type) {
                // Switch deposit <-> purchase: rebuild the payload skeleton.
                // SAP payloads use flat ids; the bank account id lives on the
                // top-level account_id and display names are carried inline.
                e.type = patch.type;
                const acct = accounts.find((a) => a.id === next.bank_account_id);
                if (patch.type === "deposit") {
                    e.sap_payload = {
                        account_id: next.bank_account_id,
                        account_name: acct?.name || "",
                        date: e.sap_payload?.date,
                        amount: e.amount,
                        description: e.row?.description || null,
                        // counter-account the deposit is posted against
                        from_account_id: "",
                        from_account_name: "",
                    };
                } else if (patch.type === "purchase") {
                    e.sap_payload = {
                        // expense paid out of the bank account
                        paid_through_account_id: next.bank_account_id,
                        paid_through_account_name: acct?.name || "",
                        date: e.sap_payload?.date,
                        line_items: [{
                            amount: e.amount,
                            description: e.row?.description || null,
                            account_id: "",
                            account_name: "",
                        }],
                    };
                } else if (patch.type === "payment") {
                    e.sap_payload = {
                        payment_mode: "check",
                        amount: e.amount,
                        date: e.sap_payload?.date,
                        account_id: next.bank_account_id,
                        account_name: acct?.name || "",
                    };
                }
                e.counterparty_match = { matched: false };
                e.account_match = { matched: false };
            }
            if (patch.amount !== undefined) {
                e.amount = Number(patch.amount) || 0;
                if (e.type === "purchase") {
                    if (e.sap_payload?.line_items?.[0]) e.sap_payload.line_items[0].amount = e.amount;
                } else if (e.sap_payload) {
                    e.sap_payload.amount = e.amount;
                }
            }
            if (patch.description !== undefined) {
                if (e.type === "purchase") {
                    if (e.sap_payload?.line_items?.[0]) e.sap_payload.line_items[0].description = patch.description;
                } else if (e.sap_payload) {
                    e.sap_payload.description = patch.description;
                }
            }
            if (patch.counterpartyName !== undefined) {
                if (e.type === "deposit") {
                    const c = customerByName[patch.counterpartyName];
                    if (c) {
                        e.sap_payload.customer_id = c.list_id;
                        e.sap_payload.customer_name = c.name;
                        e.counterparty_match = { matched: true, list_id: c.list_id, name: c.name, confidence: 1 };
                    }
                } else if (e.type === "purchase") {
                    const v = vendorByName[patch.counterpartyName];
                    if (v) {
                        e.sap_payload.vendor_id = v.list_id;
                        e.sap_payload.vendor_name = v.name;
                        e.counterparty_match = { matched: true, list_id: v.list_id, name: v.name, confidence: 1 };
                    }
                } else if (e.type === "payment") {
                    const v = vendorByName[patch.counterpartyName];
                    if (v) {
                        e.sap_payload.vendor_id = v.list_id;
                        e.sap_payload.vendor_name = v.name;
                        e.counterparty_match = { matched: true, list_id: v.list_id, name: v.name, confidence: 1 };
                    }
                }
            }
            if (patch.accountName !== undefined) {
                // patch.accountObj wins over name-based lookup so a brand-new
                // account (just created in the same tick) doesn't get lost to
                // a stale accountByName memo.
                const acc = patch.accountObj || accountByName[patch.accountName];
                if (acc) {
                    if (e.type === "deposit" && e.sap_payload) {
                        e.sap_payload.from_account_id = acc.list_id;
                        e.sap_payload.from_account_name = acc.name;
                    } else if (e.type === "purchase" && e.sap_payload?.line_items?.[0]) {
                        e.sap_payload.line_items[0].account_id = acc.list_id;
                        e.sap_payload.line_items[0].account_name = acc.name;
                    }
                    e.account_match = { matched: true, list_id: acc.list_id, name: acc.name, confidence: 1 };
                }
            }
            return next;
        });
    };

    // --- Tier 1/2/4 helpers (per-row counterparty + account) -----------
    const setCounterpartyOnEntry = (idx, entity, method = "manual") => {
        setResult((prev) => {
            const next = structuredClone(prev);
            const e = next.entries[idx];
            if (e.type === "deposit") {
                if (e.sap_payload) {
                    e.sap_payload.customer_id = entity.list_id;
                    e.sap_payload.customer_name = entity.name;
                }
            } else if (e.type === "purchase") {
                e.sap_payload.vendor_id = entity.list_id;
                e.sap_payload.vendor_name = entity.name;
            } else if (e.type === "payment") {
                e.sap_payload.vendor_id = entity.list_id;
                e.sap_payload.vendor_name = entity.name;
            }
            e.counterparty_match = {
                ...(e.counterparty_match || {}),
                matched: true,
                list_id: entity.list_id,
                name: entity.name,
                confidence: 1,
                method,
            };
            return next;
        });
    };

    const setAccountOnEntry = (idx, entity, method = "manual") => {
        setResult((prev) => {
            const next = structuredClone(prev);
            const e = next.entries[idx];
            if (e.type === "deposit" && e.sap_payload) {
                e.sap_payload.from_account_id = entity.list_id;
                e.sap_payload.from_account_name = entity.name;
            } else if (e.type === "purchase" && e.sap_payload?.line_items?.[0]) {
                e.sap_payload.line_items[0].account_id = entity.list_id;
                e.sap_payload.line_items[0].account_name = entity.name;
            }
            e.account_match = {
                ...(e.account_match || {}),
                matched: true,
                list_id: entity.list_id,
                name: entity.name,
                confidence: 1,
                method,
            };
            return next;
        });
    };

    const onPickCounterpartyCandidate = async (idx, cand) => {
        setCounterpartyOnEntry(idx, cand, "user_picked");
        const e = result.entries[idx];
        try {
            await sapApi.saveAlias({
                entity_type: e.type === "deposit" ? "customer" : "vendor",
                alias_text: e.row?.description || e.counterparty_match?.candidate_name || "",
                sap_entity_id: cand.list_id,
                sap_entity_name: cand.name,
                company_code: company,
            });
        } catch (_) { /* non-fatal */ }
    };

    const onPickAccountCandidate = async (idx, cand) => {
        setAccountOnEntry(idx, cand, "user_picked");
        const e = result.entries[idx];
        try {
            await sapApi.saveAlias({
                entity_type: "account",
                alias_text: e.row?.description || e.account_match?.candidate_name || "",
                sap_entity_id: cand.list_id,
                sap_entity_name: cand.name,
                company_code: company,
            });
        } catch (_) { /* non-fatal */ }
    };

    const onCreatedFromModal = (created) => {
        if (!createModal) return;
        const idx = createModal.rowIdx;
        if (createModal.slot === "counterparty") {
            if (createModal.entityType === "customer") {
                setCustomers((prev) => [...prev, created]);
            } else if (createModal.entityType === "vendor") {
                setVendors((prev) => [...prev, created]);
            }
            setCounterpartyOnEntry(idx, created, "created");
        } else if (createModal.slot === "account") {
            // SAP GL accounts can't actually be created via the API; if the
            // backend ever returns one, patch it in so we don't drop it.
            setAllAccounts((prev) => [...prev, { ...created, account_type: created.account_type || "Expense" }]);
            setAccountOnEntry(idx, created, "created");
        }
    };

    const gatherUnmatchedForBulk = () => {
        const out = [];
        (result?.entries || []).forEach((e, i) => {
            const date = e.row?.date || "";
            const amount = e.amount;
            if (e.counterparty_match && !e.counterparty_match.matched) {
                out.push({
                    key: `cp_${i}`,
                    entity_type: e.type === "deposit" ? "customer" : "vendor",
                    name: e.row?.description || e.counterparty_match.candidate_name || "",
                    date,
                    amount,
                });
            }
            if (e.account_match && !e.account_match.matched) {
                out.push({
                    key: `acc_${i}`,
                    entity_type: "account",
                    name: e.row?.description || e.account_match.candidate_name || "",
                    account_type: e.type === "deposit" ? "Income" : "Expense",
                    date,
                    amount,
                });
            }
        });
        // Dedup by (entity_type, normalized name) — bank rows often repeat.
        const seen = new Set();
        return out.filter((r) => {
            const k = `${r.entity_type}::${(r.name || "").toLowerCase().trim()}`;
            if (seen.has(k)) return false;
            seen.add(k);
            return true;
        });
    };

    const onBulkComplete = (results = []) => {
        const okResults = results.filter((r) => r.ok && r.entity_id);
        // For each successful create, walk entries and patch every row whose
        // description matches the created name (bank rows often share a vendor).
        okResults.forEach((r) => {
            const entity = { list_id: String(r.entity_id), name: r.entity_name };
            if (r.entity_type === "customer") setCustomers((prev) => [...prev, entity]);
            else if (r.entity_type === "vendor") setVendors((prev) => [...prev, entity]);
            else if (r.entity_type === "account") setAllAccounts((prev) => [...prev, { ...entity, account_type: "Expense" }]);
            (result?.entries || []).forEach((e, i) => {
                const desc = e.row?.description || "";
                if (desc !== r.name) return;
                if (r.entity_type === "customer" && e.type === "deposit" && !e.counterparty_match?.matched) {
                    setCounterpartyOnEntry(i, entity, "bulk_created");
                } else if (r.entity_type === "vendor" && (e.type === "purchase" || e.type === "payment") && !e.counterparty_match?.matched) {
                    setCounterpartyOnEntry(i, entity, "bulk_created");
                } else if (r.entity_type === "account" && !e.account_match?.matched) {
                    setAccountOnEntry(i, entity, "bulk_created");
                }
            });
        });
    };

    const isRowBlockedDup = (idx) => {
        const e = result?.entries?.[idx];
        return !!e?.duplicate_check?.is_duplicate && !overrideDupIdx.has(idx);
    };

    const pushOne = async (idx) => {
        const e = result.entries[idx];
        if (isRowBlockedDup(idx)) return; // caller must override first
        setRowState((s) => ({ ...s, [idx]: { status: "pushing" } }));
        try {
            const res = await sapApi.bankEntries.pushOne({
                entryType: e.type,
                payload: e.sap_payload,
                companyCode: company,
            });
            const id = res?.result?.Id;
            setRowState((s) => ({ ...s, [idx]: { status: "done", message: `${e.type} #${id || "?"}` } }));
        } catch (err) {
            const detail = err?.response?.data?.detail;
            const msg = typeof detail === "string"
                ? detail
                : (detail?.sap_error || detail?.sap_detail || err.message || "Push failed");
            setRowState((s) => ({ ...s, [idx]: { status: "error", message: msg } }));
        }
    };

    const pushAllUnmatched = async () => {
        if (!result) return;
        // Skip duplicates by default — they need explicit per-row override.
        for (let i = 0; i < result.entries.length; i++) {
            if (rowState[i]?.status === "done") continue;
            if (isRowBlockedDup(i)) continue;
            await pushOne(i);
        }
    };

    // ── Drafts ─────────────────────────────────────────────────────
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
            const res = await sapApi.drafts.save({
                flow: "bank_entries",
                payload: {
                    result,
                    rowState,
                    accountId,
                    accountName: accounts.find((a) => a.id === accountId)?.name,
                },
                title: deriveDraftTitle(),
                draftId,
                companyCode: company,
            });
            if (res?.draft_id) setDraftId(res.draft_id);
            setDraftsRefreshKey((k) => k + 1);
        } catch (e) {
            setError(e?.response?.data?.detail || e.message || "Save draft failed");
        } finally {
            setSavingDraft(false);
        }
    };

    const handleOpenDraft = (draft) => {
        const payload = draft?.payload || {};
        setResult(payload.result || null);
        setRowState(payload.rowState || {});
        if (payload.accountId) setAccountId(payload.accountId);
        setDraftId(draft.id);
        setDraftsPanelOpen(false);
        setError("");
    };

    useDraftDeepLink({
        getDraft: sapApi.drafts.get,
        companyField: "company_code",
        setCompany,
        onOpen: handleOpenDraft,
    });

    const handleDraftDeleted = (id) => {
        if (draftId === id) setDraftId(null);
    };

    return (
        <div className="min-h-screen flex flex-col">
            <PageHeader
                icon={Banknote}
                className="bg-transparent border-gilt-hairline"
                title={<span className="gl-display text-gilt-ink">SAP S/4HANA · Bank Entries</span>}
                description="Upload a bank statement, classify transactions, and post entries to SAP S/4HANA."
            />

            <div className="flex-1 px-6 lg:px-8 py-5 space-y-5">
                {error && (
                    <Card className="bg-gilt-card border-gilt-hairline shadow-none">
                        <div className="flex items-start gap-2 px-3 py-2 text-sm text-gilt-debit">
                            <AlertTriangle className="h-4 w-4 flex-shrink-0 mt-0.5 text-gilt-debit" />
                            <span className="flex-1">{error}</span>
                            <button onClick={() => setError("")} className="text-gilt-faint hover:text-gilt-ink transition-colors duration-150">
                                <X className="h-4 w-4" />
                            </button>
                        </div>
                    </Card>
                )}

                {/* Statement & target card */}
                <div className="bg-gilt-card rounded-xl border border-gilt-hairline p-5">
                    <div className="flex items-center gap-2 mb-4">
                        <Sparkles className="h-4 w-4 text-gilt-brass" />
                        <h2 className="font-mono text-[10px] font-medium uppercase tracking-[0.14em] text-gilt-brass">Statement & target</h2>
                    </div>
                    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                        <div>
                            <label className={labelBase}>
                                <Building2 className="inline h-3.5 w-3.5 mr-1 text-gilt-faint" />
                                Company code
                            </label>
                            <select
                                className={inputBase}
                                value={company}
                                onChange={(e) => setCompany(e.target.value)}
                                disabled={loadingCompanies || !companies.length}
                            >
                                {loadingCompanies && <option value="">Loading companies…</option>}
                                {!loadingCompanies && !companies.length && <option value="">No companies</option>}
                                {companies.map((c) => {
                                    const v = c.company_code || c.name;
                                    return <option key={v} value={v}>{c.name}</option>;
                                })}
                            </select>
                        </div>
                        <div>
                            <label className={labelBase}>
                                <Wallet className="inline h-3.5 w-3.5 mr-1 text-gilt-faint" />
                                Bank account
                            </label>
                            <select
                                className={inputBase}
                                value={accountId}
                                onChange={(e) => setAccountId(e.target.value)}
                                disabled={loadingMasters || !accounts.length}
                            >
                                {loadingMasters && <option value="">Loading bank accounts…</option>}
                                {!loadingMasters && accounts.length === 0 && <option value="">No bank accounts</option>}
                                {accounts.map((a) => (
                                    <option key={a.id} value={a.id}>
                                        {a.name} ({a.account_type}{a.currency ? `, ${a.currency}` : ""})
                                    </option>
                                ))}
                            </select>
                        </div>
                        <div>
                            <label className={labelBase}>
                                <FileText className="inline h-3.5 w-3.5 mr-1 text-gilt-faint" />
                                Statement file
                            </label>
                            <input
                                type="file"
                                accept=".pdf,.xls,.xlsx"
                                onChange={(e) => setFile(e.target.files?.[0] || null)}
                                disabled={loadingMasters}
                                className="block w-full text-sm text-gilt-soft file:mr-3 file:py-1.5 file:px-3 file:rounded-md file:border file:border-solid file:border-gilt-hairline file:text-sm file:font-medium file:bg-gilt-sunken file:text-gilt-brass hover:file:border-gilt-brass file:transition-colors file:duration-150"
                            />
                        </div>
                    </div>
                    <div className="mt-4 flex items-center justify-between gap-3 flex-wrap">
                        <div className="flex items-center gap-3 text-xs text-gilt-soft">
                            {(loadingCompanies || loadingMasters) && (
                                <span className="inline-flex items-center gap-1">
                                    <Loader2 className="h-3 w-3 animate-spin" />
                                    {loadingCompanies ? "Companies…" : "Loading bank accounts + masters…"}
                                </span>
                            )}
                            {!loadingMasters && accounts.length > 0 && (
                                <span><span className="font-mono tabular-nums">{accounts.length}</span> bank · <span className="font-mono tabular-nums">{customers.length}</span> cust · <span className="font-mono tabular-nums">{vendors.length}</span> vend</span>
                            )}
                            <span className="text-gilt-faint">Each row becomes a Deposit, Purchase, or Payment in SAP S/4HANA.</span>
                        </div>
                        <button
                            onClick={onExtract}
                            disabled={extracting || !file || !accountId || loadingMasters}
                            title={loadingMasters ? "Wait for company data to finish loading" : ""}
                            className={btnPrimary}
                        >
                            {extracting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
                            {extracting ? "Extracting…" : "Extract"}
                        </button>
                    </div>
                </div>

                {extracting && (
                    <div className="bg-gilt-card rounded-xl border border-gilt-hairline p-8 flex flex-col items-center justify-center gap-3">
                        <Loader2 className="h-8 w-8 animate-spin text-gilt-brass" />
                        <div className="text-sm font-medium text-gilt-ink">Extracting statement rows…</div>
                        <div className="text-xs text-gilt-soft">Classifying deposits, purchases, and payments, and matching counterparties.</div>
                    </div>
                )}

                {!extracting && !result && !loadingMasters && accounts.length > 0 && (
                    <div className="bg-gilt-card rounded-xl border border-gilt-hairline p-6">
                        <div className="flex items-center gap-2 mb-3">
                            <Sparkles className="h-4 w-4 text-gilt-brass" />
                            <h2 className="font-mono text-[10px] font-medium uppercase tracking-[0.14em] text-gilt-brass">How this works</h2>
                        </div>
                        <ol className="text-xs text-gilt-soft space-y-1.5 list-decimal list-inside">
                            <li>Pick the SAP S/4HANA company code + bank account above</li>
                            <li>Upload a bank statement (PDF / Excel)</li>
                            <li>EazyBooks extracts rows + matches counterparties</li>
                            <li>Review each row, then push individually or "Push all"</li>
                        </ol>
                    </div>
                )}

                {result && (
                <div className="space-y-5">
                    {/* Status / actions bar */}
                    <div className="bg-gilt-card rounded-xl border border-gilt-hairline p-4 flex items-center gap-3 flex-wrap">
                        <span className="inline-flex items-center gap-1.5 text-sm font-medium text-gilt-ink">
                            <Layers className="h-4 w-4 text-gilt-brass" />
                            <span className="font-mono tabular-nums">{result.entries.length}</span> entries extracted
                        </span>
                        {result.needs_review && (
                            <span className="inline-flex items-center gap-1 px-2 py-0.5 font-mono text-[10px] uppercase tracking-[0.14em] rounded-full bg-gilt-sunken border border-gilt-brass/40 text-gilt-brass">
                                <AlertTriangle className="h-3 w-3" />
                                Some rows unmatched
                            </span>
                        )}
                        {(() => {
                            const dupCount = result.entries.filter((e, i) => e.duplicate_check?.is_duplicate && !overrideDupIdx.has(i) && rowState[i]?.status !== "done").length;
                            return dupCount > 0 ? (
                                <span className="inline-flex items-center gap-1 px-2 py-0.5 font-mono text-[10px] uppercase tracking-[0.14em] rounded-full bg-transparent border border-gilt-debit/40 text-gilt-debit">
                                    <AlertTriangle className="h-3 w-3" />
                                    {dupCount} duplicate{dupCount === 1 ? "" : "s"} blocked
                                </span>
                            ) : null;
                        })()}
                        <div className="ml-auto flex items-center gap-2 flex-wrap">
                            {gatherUnmatchedForBulk().length > 0 && (
                                <button
                                    onClick={() => setBulkOpen(true)}
                                    className="inline-flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded-lg border border-gilt-credit/40 bg-transparent text-gilt-credit hover:bg-gilt-sunken transition-colors duration-150"
                                    title="Create all missing customers/vendors/accounts in one batch"
                                >
                                    <Layers className="h-3 w-3" />
                                    Create all missing ({gatherUnmatchedForBulk().length})
                                </button>
                            )}
                            <SaveDraftButton
                                onClick={handleSaveDraft}
                                busy={savingDraft}
                                label={draftId ? "Update draft" : "Save draft"}
                            />
                            <button onClick={pushAllUnmatched} className={btnSapBlue}>
                                <Send className="h-4 w-4" />
                                Push all
                            </button>
                        </div>
                    </div>

                    {/* Entries table */}
                    <div className="bg-gilt-card rounded-xl border border-gilt-hairline overflow-hidden">
                        <div className="overflow-x-auto">
                        <table className="w-full text-sm">
                            <thead className="bg-gilt-sunken">
                                <tr className="border-b border-gilt-hairline">
                                    <th className="px-3 py-2.5 text-left font-mono text-[10px] font-medium text-gilt-soft uppercase tracking-wider">Date</th>
                                    <th className="px-3 py-2.5 text-left font-mono text-[10px] font-medium text-gilt-soft uppercase tracking-wider">Type</th>
                                    <th className="px-3 py-2.5 text-left font-mono text-[10px] font-medium text-gilt-soft uppercase tracking-wider">Description</th>
                                    <th className="px-3 py-2.5 text-right font-mono text-[10px] font-medium text-gilt-soft uppercase tracking-wider">Amount</th>
                                    <th className="px-3 py-2.5 text-left font-mono text-[10px] font-medium text-gilt-soft uppercase tracking-wider">Counterparty</th>
                                    <th className="px-3 py-2.5 text-left font-mono text-[10px] font-medium text-gilt-soft uppercase tracking-wider">Account</th>
                                    <th className="px-3 py-2.5 text-left font-mono text-[10px] font-medium text-gilt-soft uppercase tracking-wider">Status</th>
                                </tr>
                            </thead>
                            <tbody>
                                {result.entries.map((e, i) => {
                                    const status = rowState[i];
                                    return (
                                        <tr key={i} className="border-t border-gilt-hairline align-top hover:bg-gilt-sunken transition-colors duration-150">
                                            <td className="px-3 py-2 text-sm text-gilt-ink font-mono tabular-nums whitespace-nowrap">{e.row?.date || "—"}</td>
                                            <td className="px-3 py-2">
                                                <select
                                                    className="border border-gilt-hairline rounded-lg px-2 py-1 text-xs text-gilt-ink bg-gilt-card focus:border-gilt-brass outline-none transition-colors duration-150"
                                                    value={e.type}
                                                    onChange={(ev) => updateEntry(i, { type: ev.target.value })}
                                                >
                                                    <option value="deposit">Deposit</option>
                                                    <option value="purchase">Purchase</option>
                                                    <option value="payment">Payment</option>
                                                </select>
                                            </td>
                                            <td className="px-3 py-2">
                                                <input
                                                    className={inputBase}
                                                    defaultValue={e.row?.description || ""}
                                                    onBlur={(ev) => updateEntry(i, { description: ev.target.value })}
                                                />
                                            </td>
                                            <td className="px-3 py-2 text-right">
                                                <input
                                                    className="w-28 px-2 py-1 text-sm text-right border border-gilt-hairline rounded text-gilt-ink font-mono tabular-nums bg-gilt-paper focus:border-gilt-brass outline-none transition-colors duration-150"
                                                    type="number"
                                                    step="0.01"
                                                    defaultValue={e.amount ?? 0}
                                                    onBlur={(ev) => updateEntry(i, { amount: ev.target.value })}
                                                />
                                            </td>
                                            <td className="px-3 py-2">
                                                <div className="flex items-center gap-1">
                                                    <SearchableDropdown
                                                        options={e.type === "deposit" ? customerNames : vendorNames}
                                                        value={e.counterparty_match?.name || ""}
                                                        onChange={(name) => updateEntry(i, { counterpartyName: name })}
                                                        placeholder={e.type === "deposit" ? "Pick customer…" : "Pick vendor…"}
                                                    />
                                                    {e.counterparty_match?.confidence != null && (
                                                        <ConfidenceBadge
                                                            confidence={e.counterparty_match.confidence}
                                                            size="sm"
                                                            showPercentage={false}
                                                        />
                                                    )}
                                                    <MatchOriginBadge match={e.counterparty_match} />
                                                </div>
                                                {!e.counterparty_match?.matched && (
                                                    <div className="mt-1.5">
                                                        <MatchPicker
                                                            topCandidates={e.counterparty_match?.top_candidates || []}
                                                            extractedName={e.row?.description || e.counterparty_match?.candidate_name || ""}
                                                            onPickExisting={(cand) => onPickCounterpartyCandidate(i, cand)}
                                                            onCreateNew={() => setCreateModal({
                                                                entityType: e.type === "deposit" ? "customer" : "vendor",
                                                                prefillName: e.row?.description || e.counterparty_match?.candidate_name || "",
                                                                slot: "counterparty",
                                                                rowIdx: i,
                                                            })}
                                                            label={e.type === "deposit" ? "customer" : "vendor"}
                                                        />
                                                    </div>
                                                )}
                                            </td>
                                            <td className="px-3 py-2">
                                                <div className="flex items-center gap-1">
                                                    <SearchableDropdown
                                                        options={e.type === "deposit" ? incomeAccountNames : expenseAccountNames}
                                                        value={e.account_match?.name || ""}
                                                        onChange={(name) => updateEntry(i, { accountName: name })}
                                                        placeholder="Pick account…"
                                                        createLabel="Create New Account"
                                                        onCreateNew={() => setCreateModal({
                                                            entityType: "account",
                                                            prefillName: e.row?.description || e.account_match?.candidate_name || "",
                                                            slot: "account",
                                                            rowIdx: i,
                                                        })}
                                                    />
                                                    {e.account_match?.confidence != null && (
                                                        <ConfidenceBadge
                                                            confidence={e.account_match.confidence}
                                                            size="sm"
                                                            showPercentage={false}
                                                        />
                                                    )}
                                                    <MatchOriginBadge match={e.account_match} />
                                                </div>
                                                {!e.account_match?.matched && (
                                                    <div className="mt-1.5">
                                                        <MatchPicker
                                                            topCandidates={e.account_match?.top_candidates || []}
                                                            extractedName={e.row?.description || e.account_match?.candidate_name || ""}
                                                            onPickExisting={(cand) => onPickAccountCandidate(i, cand)}
                                                            onCreateNew={() => setCreateModal({
                                                                entityType: "account",
                                                                prefillName: e.row?.description || e.account_match?.candidate_name || "",
                                                                slot: "account",
                                                                rowIdx: i,
                                                            })}
                                                            label="account"
                                                        />
                                                    </div>
                                                )}
                                            </td>
                                            <td className="px-3 py-2">
                                                {/* Duplicate badge shows first regardless of push status. */}
                                                {e.duplicate_check?.is_duplicate && (
                                                    <div className="mb-1.5">
                                                        <DuplicateBadge check={e.duplicate_check} />
                                                    </div>
                                                )}
                                                {!status && isRowBlockedDup(i) && (
                                                    <button
                                                        onClick={() => overrideDup(i)}
                                                        className="inline-flex items-center gap-1 px-2.5 py-1 bg-gilt-sunken border border-gilt-brass/40 text-gilt-brass rounded-lg text-xs font-medium hover:border-gilt-brass transition-colors duration-150"
                                                        title="Push anyway despite duplicate verdict"
                                                    >
                                                        Push anyway
                                                    </button>
                                                )}
                                                {!status && !isRowBlockedDup(i) && (
                                                    <button
                                                        onClick={() => pushOne(i)}
                                                        className="inline-flex items-center gap-1 px-2.5 py-1 bg-gilt-brass text-gilt-paper rounded-md text-xs font-medium hover:bg-gilt-brass/90 transition-colors duration-150"
                                                    >
                                                        <ArrowUpRight className="h-3 w-3" />
                                                        Push
                                                    </button>
                                                )}
                                                {status?.status === "pushing" && (
                                                    <span className="inline-flex items-center gap-1 text-xs text-gilt-soft">
                                                        <Loader2 className="h-3 w-3 animate-spin" />
                                                        Pushing…
                                                    </span>
                                                )}
                                                {status?.status === "done" && (
                                                    <span className="inline-flex items-center gap-1 px-2 py-0.5 font-mono text-[10px] uppercase tracking-[0.14em] rounded-full bg-transparent border border-gilt-credit/40 text-gilt-credit">
                                                        <CheckCircle2 className="h-3 w-3" />
                                                        {status.message}
                                                    </span>
                                                )}
                                                {status?.status === "error" && (
                                                    <span className="inline-flex items-center gap-1 px-2 py-0.5 font-mono text-[10px] uppercase tracking-[0.14em] rounded-full bg-transparent border border-gilt-debit/40 text-gilt-debit" title={status.message}>
                                                        <AlertTriangle className="h-3 w-3" />
                                                        {String(status.message).slice(0, 40)}
                                                    </span>
                                                )}
                                            </td>
                                        </tr>
                                    );
                                })}
                            </tbody>
                        </table>
                        </div>
                    </div>
                </div>
                )}

                <DraftsPanel
                    open={draftsPanelOpen}
                    onToggle={() => setDraftsPanelOpen((o) => !o)}
                    loadList={() => sapApi.drafts.list({ flow: "bank_entries", companyCode: company })}
                    loadDraft={(id) => sapApi.drafts.get(id)}
                    deleteDraft={(id) => sapApi.drafts.delete(id)}
                    refreshKey={draftsRefreshKey}
                    activeDraftId={draftId}
                    onOpen={handleOpenDraft}
                    onDelete={handleDraftDeleted}
                />
            </div>

            <CreateMasterModal
                open={!!createModal}
                entityType={createModal?.entityType}
                prefillName={createModal?.prefillName || ""}
                companyCode={company}
                onClose={() => setCreateModal(null)}
                onCreated={onCreatedFromModal}
            />
            <BulkCreateModal
                open={bulkOpen}
                items={gatherUnmatchedForBulk()}
                companyCode={company}
                onClose={() => setBulkOpen(false)}
                onComplete={onBulkComplete}
            />
        </div>
    );
}
