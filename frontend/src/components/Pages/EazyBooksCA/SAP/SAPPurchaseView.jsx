import React, { useEffect, useMemo, useRef, useState } from "react";
import { Panel, Group as PanelGroup, Separator as PanelResizeHandle } from "react-resizable-panels";
import {
    Receipt,
    Building2,
    FileText,
    Sparkles,
    AlertTriangle,
    X,
    Send,
    CheckCircle2,
    Loader2,
    Layers,
    Calendar,
    Hash,
    DollarSign,
    Upload,
    Plus,
    ArrowLeft,
    Grid3x3,
    List as ListIcon,
    RefreshCw,
    Trash2,
    Clock,
} from "lucide-react";
import { sapApi } from "../../../../services/api";
import { useDraftDeepLink } from "../common/useDraftDeepLink";
import PdfViewer from "../PdfViewer";
import SearchableDropdown from "../common/SearchableDropdown";
import ConfidenceBadge from "../common/ConfidenceBadge";
import CreateMasterModal from "./components/CreateMasterModal";
import MatchPicker from "./components/MatchPicker";
import BulkCreateModal from "./components/BulkCreateModal";
import MatchOriginBadge from "../common/MatchOriginBadge";
import DraftsPanel, { SaveDraftButton } from "../common/DraftsPanel";
import DuplicateBadge from "../common/DuplicateBadge";
import { PageHeader } from "../../../ui/page-header";
import { Select } from "../../../ui/input";
import { Card } from "../../../ui/card";

// Shared class recipes so Purchase/Sales/BankEntries stay visually consistent.
const inputBase = "w-full border border-gilt-hairline rounded-lg px-3 py-2 text-sm text-gilt-ink bg-gilt-paper placeholder:text-gilt-faint focus:border-gilt-brass outline-none transition-colors duration-150";
const labelBase = "block text-xs font-medium text-gilt-soft mb-1.5";

// Line-item cell styles — `inputBase` has `w-full` baked in, which loses the
// fight with a sibling `w-20` / `w-28` in the rendered CSS and leaves table
// cells effectively full-width (the documented gotcha in CLAUDE.md). These
// dedicated classes match ZohoPurchaseView.jsx so the SAP and Zoho line-items
// tables read as the same component.
const tableNumInput = "px-2 py-1 text-sm text-right border border-gilt-hairline rounded text-gilt-ink font-mono bg-gilt-paper focus:border-gilt-brass outline-none transition-colors duration-150 tabular-nums";
const tableTextInput = "w-full px-2 py-1 text-sm border border-gilt-hairline rounded text-gilt-ink bg-gilt-paper focus:border-gilt-brass outline-none transition-colors duration-150";
const btnPrimary = "inline-flex items-center gap-1.5 px-4 py-2 text-sm font-semibold text-gilt-paper bg-gilt-brass rounded-md hover:bg-gilt-brass/90 transition-colors duration-150 disabled:opacity-50 disabled:cursor-not-allowed";
const btnSecondary = "inline-flex items-center gap-1.5 px-3 py-2 text-sm text-gilt-soft bg-gilt-card border border-gilt-hairline rounded-md hover:border-gilt-brass hover:text-gilt-brass transition-colors duration-150 disabled:opacity-50";
// Primary push affordance — quiet ink plate per the gilt workroom idiom.
const btnSapBlue = "inline-flex items-center gap-1.5 px-4 py-2 text-sm font-semibold text-gilt-paper bg-gilt-brass rounded-md hover:bg-gilt-brass/90 transition-colors duration-150 disabled:opacity-50";
const btnDanger = "inline-flex items-center gap-1.5 px-3 py-2 text-sm font-medium text-gilt-debit bg-transparent border border-gilt-debit/40 rounded-md hover:bg-gilt-sunken transition-colors duration-150 disabled:opacity-50";

const PUSH_CONCURRENCY = 2; // be polite to SAP; 2 in flight is plenty for the demo company code

/**
 * SAP S/4HANA Purchase Bill flow — multi-bill bulk upload.
 *
 * State model:
 *   bills[]: { id, filename, status, pdfData, result, error,
 *              pushStatus, pushError, pushResult }
 *   activeId: which bill the editor pane is currently looking at
 *
 * Render branches:
 *   no bills           → empty dropzone (drag PDFs or browse)
 *   bills + no active  → grid/list of bills with status badges
 *   activeId set       → existing PDF + EditPanel layout
 *
 * `setResult(updater)` is preserved so every existing handler keeps working —
 * it routes the patch onto the active bill's result.
 */
export default function SAPPurchaseView() {
    const [companies, setCompanies] = useState([]);
    const [company, setCompany] = useState("");

    const [bills, setBills] = useState([]);
    const [activeId, setActiveId] = useState(null);
    const [gridLayout, setGridLayout] = useState("grid"); // grid | list
    const [isDragging, setIsDragging] = useState(false);
    const [isPushingAll, setIsPushingAll] = useState(false);
    const fileInputRef = useRef(null);

    const [vendors, setVendors] = useState([]);
    const [accounts, setAccounts] = useState([]);
    const [loadingCompanies, setLoadingCompanies] = useState(true);
    const [loadingMasters, setLoadingMasters] = useState(false);
    const [error, setError] = useState("");
    const [createModal, setCreateModal] = useState(null);
    const [bulkOpen, setBulkOpen] = useState(false);

    // Drafts (saved sessions).
    const [draftId, setDraftId] = useState(null);
    const [draftsPanelOpen, setDraftsPanelOpen] = useState(false);
    const [draftsRefreshKey, setDraftsRefreshKey] = useState(0);
    const [savingDraft, setSavingDraft] = useState(false);

    // Bills the user has explicitly said "push anyway" for despite the
    // duplicate verdict. Resets on a new upload run.
    const [overridePushIds, setOverridePushIds] = useState(() => new Set());

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
        setVendors([]);
        setAccounts([]);
        (async () => {
            try {
                const [v, a] = await Promise.all([
                    sapApi.getVendors(company),
                    sapApi.getAccounts(company),
                ]);
                setVendors(v?.vendors || []);
                const expenseTypes = new Set(["Expense", "CostOfGoodsSold", "OtherExpense", "OtherCurrentLiability"]);
                setAccounts((a?.accounts || []).filter((x) => expenseTypes.has(x.account_type)));
            } catch (err) {
                setError(err?.response?.data?.detail || err.message || "Failed to load master data");
            } finally {
                setLoadingMasters(false);
            }
        })();
    }, [company]);

    const vendorNames = useMemo(() => vendors.map((v) => v.name).filter(Boolean).sort(), [vendors]);
    const accountNames = useMemo(() => accounts.map((a) => a.name).filter(Boolean).sort(), [accounts]);
    const vendorByName = useMemo(() => Object.fromEntries(vendors.map((v) => [v.name, v])), [vendors]);
    const accountByName = useMemo(() => Object.fromEntries(accounts.map((a) => [a.name, a])), [accounts]);

    const activeBill = bills.find((b) => b.id === activeId) || null;
    const result = activeBill?.result || null;
    const pushing = activeBill?.pushStatus === "pushing";

    // ── Bill state helpers ──────────────────────────────────────────
    const patchBill = (id, patch) => {
        setBills((prev) => prev.map((b) => {
            if (b.id !== id) return b;
            const merge = typeof patch === "function" ? patch(b) : patch;
            return { ...b, ...merge };
        }));
    };

    // Drop-in replacement for the prior `setResult` — patches the active bill.
    const setResult = (updater) => {
        if (!activeId) return;
        setBills((prev) => prev.map((b) => {
            if (b.id !== activeId) return b;
            const nextResult = typeof updater === "function" ? updater(b.result) : updater;
            return { ...b, result: nextResult };
        }));
    };

    // ── Multi-file upload ───────────────────────────────────────────
    const handleMultiFileUpload = async (fileList) => {
        const pdfFiles = Array.from(fileList).filter((f) => f.type === "application/pdf");
        if (pdfFiles.length === 0) return;

        const newBills = pdfFiles.map((f, i) => ({
            id: `${Date.now()}_${i}_${Math.random().toString(36).slice(2, 7)}`,
            filename: f.name,
            file: f,
            pdfData: URL.createObjectURL(f),
            status: "pending",
            result: null,
            error: null,
            pushStatus: null,
            pushError: null,
            pushResult: null,
        }));

        setBills((prev) => [...prev, ...newBills]);

        // Extract in parallel, but limit concurrency to keep the backend happy.
        await runConcurrent(newBills, 2, async (bill) => {
            patchBill(bill.id, { status: "processing" });
            try {
                const res = await sapApi.extractBill(bill.file, company);
                patchBill(bill.id, { status: res?.ok ? "ready" : "failed", result: res, error: res?.ok ? null : (res?.error || "Extraction returned no result") });
            } catch (err) {
                const msg = err?.response?.data?.detail || err.message || "Extraction failed";
                patchBill(bill.id, { status: "failed", error: typeof msg === "string" ? msg : JSON.stringify(msg) });
            }
        });
    };

    const handleDragOver = (e) => { e.preventDefault(); setIsDragging(true); };
    const handleDragLeave = () => setIsDragging(false);
    const handleDrop = (e) => {
        e.preventDefault();
        setIsDragging(false);
        if (e.dataTransfer.files?.length) handleMultiFileUpload(e.dataTransfer.files);
    };

    const isBlockedDuplicate = (bill) => {
        const dup = bill?.result?.duplicate_check;
        if (!dup?.is_duplicate) return false;
        return !overridePushIds.has(bill.id);
    };

    const overridePush = (id) => {
        setOverridePushIds((prev) => {
            const next = new Set(prev);
            next.add(id);
            return next;
        });
    };

    // ── Push handlers ──────────────────────────────────────────────
    const pushBill = async (id) => {
        const bill = bills.find((b) => b.id === id);
        if (!bill?.result?.sap_payload) return;
        if (bill.pushStatus === "pushing" || bill.pushStatus === "pushed") return;
        if (isBlockedDuplicate(bill)) {
            // Caller should have called overridePush first; bail silently.
            return;
        }
        patchBill(id, { pushStatus: "pushing", pushError: null });
        try {
            const res = await sapApi.pushBillSap({
                payload: bill.result.sap_payload,
                company_code: company,
                extracted_total: bill.result.extracted_total ?? bill.result.sap_payload?.TotalAmt,
                skip_duplicate_check: overridePushIds.has(id),
            });
            patchBill(id, { pushStatus: "pushed", pushResult: res, pushError: null });
        } catch (err) {
            const detail = err?.response?.data?.detail;
            const msg = typeof detail === "string"
                ? detail
                : detail?.message || detail?.sap_error || detail?.sap_detail || err.message || "Push failed";
            patchBill(id, { pushStatus: "failed", pushError: msg });
        }
    };

    const onPush = () => activeId && pushBill(activeId);

    const handlePushAll = async () => {
        // Skip duplicates by default; user can override individual rows via
        // "Push anyway" before bulk-pushing, or re-trigger after overriding.
        const ready = bills.filter((b) =>
            b.status === "ready"
            && b.pushStatus !== "pushed"
            && b.pushStatus !== "pushing"
            && !isBlockedDuplicate(b)
        );
        if (ready.length === 0) return;
        setIsPushingAll(true);
        try {
            await runConcurrent(ready, PUSH_CONCURRENCY, (b) => pushBill(b.id));
        } finally {
            setIsPushingAll(false);
        }
    };

    const handleRetryFailed = async () => {
        const failed = bills.filter((b) => b.pushStatus === "failed");
        if (failed.length === 0) return;
        setIsPushingAll(true);
        try {
            await runConcurrent(failed, PUSH_CONCURRENCY, (b) => pushBill(b.id));
        } finally {
            setIsPushingAll(false);
        }
    };

    const removeBill = (id) => {
        if (activeId === id) setActiveId(null);
        setBills((prev) => prev.filter((b) => b.id !== id));
    };

    // ── Drafts ─────────────────────────────────────────────────────
    // What gets persisted: just the data, not the file objects or blob URLs.
    // On reload the PDF preview pane shows "no preview available" but every
    // extracted/edited field survives.
    const serializeBillsForDraft = () => bills.map((b) => ({
        id: b.id,
        filename: b.filename,
        status: b.status,
        result: b.result,
        error: b.error,
        pushStatus: b.pushStatus,
        pushError: b.pushError,
        pushResult: b.pushResult,
    }));

    const deriveDraftTitle = () => {
        if (bills.length === 0) return "Purchase draft";
        if (bills.length === 1) return bills[0].filename;
        return `${bills.length} bills · ${bills[0].filename}`;
    };

    const handleSaveDraft = async () => {
        if (bills.length === 0) return;
        setSavingDraft(true);
        setError("");
        try {
            const res = await sapApi.drafts.save({
                flow: "purchase",
                payload: { bills: serializeBillsForDraft(), activeId },
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
        const restoredBills = (payload.bills || []).map((b) => ({
            ...b,
            // No file / pdfData on reload — PDFs aren't persisted.
            file: null,
            pdfData: null,
        }));
        setBills(restoredBills);
        setActiveId(payload.activeId && restoredBills.some((b) => b.id === payload.activeId)
            ? payload.activeId
            : null);
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

    // ── Active-bill mutators (unchanged from single-bill flow) ─────
    const updateVendor = (name) => {
        const v = vendorByName[name];
        if (!v) return;
        setResult((prev) => {
            const next = structuredClone(prev);
            next.sap_payload.vendor_id = v.list_id;
            next.sap_payload.vendor_name = v.name;
            next.vendor_match = {
                ...prev.vendor_match,
                name: v.name,
                matched: true,
                list_id: v.list_id,
                confidence: 1,
            };
            return next;
        });
    };

    const updateLine = (idx, patch) => {
        setResult((prev) => {
            if (!prev) return prev;
            const next = structuredClone(prev);
            const line = next.sap_payload.line_items[idx];
            if (patch.amount !== undefined) line.rate = Number(patch.amount) || 0;
            if (patch.description !== undefined) line.description = patch.description;
            if (patch.accountName !== undefined) {
                const acc = patch.accountObj || accountByName[patch.accountName];
                if (acc && line) {
                    line.account_id = acc.list_id;
                    line.account_name = acc.name;
                    next.line_review[idx].match = {
                        matched: true,
                        list_id: acc.list_id,
                        name: acc.name,
                        method: "manual",
                        confidence: 1,
                    };
                }
            }
            return next;
        });
    };

    const setVendorOnPayload = (v, method = "manual") => {
        setResult((prev) => {
            const next = structuredClone(prev);
            next.sap_payload.vendor_id = v.list_id;
            next.sap_payload.vendor_name = v.name;
            next.vendor_match = {
                ...prev.vendor_match,
                name: v.name,
                matched: true,
                list_id: v.list_id,
                confidence: 1,
                method,
            };
            return next;
        });
    };

    const setAccountOnLine = (idx, acc, method = "manual") => {
        setResult((prev) => {
            const next = structuredClone(prev);
            const line = next.sap_payload?.line_items?.[idx];
            if (line) {
                line.account_id = acc.list_id;
                line.account_name = acc.name;
            }
            if (next.line_review?.[idx]) {
                next.line_review[idx].match = {
                    ...next.line_review[idx].match,
                    matched: true,
                    list_id: acc.list_id,
                    name: acc.name,
                    confidence: 1,
                    method,
                };
            }
            return next;
        });
    };

    const onPickVendorCandidate = async (cand) => {
        setVendorOnPayload(cand, "user_picked");
        try {
            await sapApi.saveAlias({
                entity_type: "vendor",
                alias_text: result?.extracted?.vendor_or_customer || result?.vendor_match?.candidate_name || "",
                sap_entity_id: cand.list_id,
                sap_entity_name: cand.name,
                company_code: company,
            });
        } catch (_) { /* non-fatal */ }
    };

    const onPickAccountCandidate = async (idx, cand) => {
        setAccountOnLine(idx, cand, "user_picked");
        try {
            await sapApi.saveAlias({
                entity_type: "account",
                alias_text: result?.line_review?.[idx]?.description
                    || result?.line_review?.[idx]?.match?.candidate_name
                    || "",
                sap_entity_id: cand.list_id,
                sap_entity_name: cand.name,
                company_code: company,
            });
        } catch (_) { /* non-fatal */ }
    };

    const onCreatedFromModal = (created) => {
        if (!createModal) return;
        if (createModal.entityType === "vendor") {
            setVendors((prev) => [...prev, created]);
            setVendorOnPayload(created, "created");
        } else if (createModal.entityType === "account") {
            // SAP accounts can't actually be created via the API — but if the
            // backend ever returns one, patch it in so we don't drop it.
            setAccounts((prev) => [...prev, { ...created, account_type: created.account_type || "Expense" }]);
            if (createModal.targetLineIdx != null) {
                setAccountOnLine(createModal.targetLineIdx, created, "created");
            }
        }
    };

    const gatherUnmatchedForBulk = () => {
        const out = [];
        const date = result?.extracted?.doc_date || "";
        const total = result?.extracted?.total;
        if (result?.vendor_match && !result.vendor_match.matched) {
            out.push({
                key: "vendor_header",
                entity_type: "vendor",
                name: result.vendor_match.candidate_name || result?.extracted?.vendor_or_customer || "",
                date,
                amount: total,
            });
        }
        (result?.line_review || []).forEach((row, i) => {
            if (row?.match && !row.match.matched) {
                out.push({
                    key: `account_${i}`,
                    entity_type: "account",
                    name: row.description || row.match.candidate_name || "",
                    account_type: "Expense",
                    date,
                    amount: row.amount != null ? row.amount : total,
                });
            }
        });
        return out;
    };

    const onBulkComplete = (results = []) => {
        const okResults = results.filter((r) => r.ok && r.entity_id);
        okResults.forEach((r) => {
            const entity = { list_id: String(r.entity_id), name: r.entity_name };
            if (r.entity_type === "vendor") {
                setVendors((prev) => [...prev, entity]);
                setVendorOnPayload(entity, "bulk_created");
            } else if (r.entity_type === "account") {
                setAccounts((prev) => [...prev, { ...entity, account_type: "Expense" }]);
                const lineIdx = (result?.line_review || []).findIndex(
                    (li) => (li.description || li.match?.candidate_name) === r.name && !li.match?.matched,
                );
                if (lineIdx >= 0) setAccountOnLine(lineIdx, entity, "bulk_created");
            }
        });
    };

    // ── Derived counts for the bulk header ─────────────────────────
    const counts = useMemo(() => ({
        total: bills.length,
        processing: bills.filter((b) => b.status === "processing" || b.status === "pending").length,
        ready: bills.filter((b) => b.status === "ready" && b.pushStatus !== "pushed" && !isBlockedDuplicate(b)).length,
        failed: bills.filter((b) => b.status === "failed").length,
        pushed: bills.filter((b) => b.pushStatus === "pushed").length,
        pushFailed: bills.filter((b) => b.pushStatus === "failed").length,
        duplicate: bills.filter((b) => b.result?.duplicate_check?.is_duplicate && !overridePushIds.has(b.id) && b.pushStatus !== "pushed").length,
    }), [bills, overridePushIds]); // eslint-disable-line react-hooks/exhaustive-deps

    const mastersReady = !loadingMasters && vendors.length > 0;

    // ══════════════════════════════════════════════════════════════
    // Top bar — same across all branches
    // ══════════════════════════════════════════════════════════════
    const TopBar = (
        <PageHeader
            className="bg-transparent border-gilt-hairline"
            icon={Receipt}
            title={<span className="gl-display text-gilt-ink">SAP S/4HANA · Purchase Bill</span>}
            description="Upload purchase bill PDFs, review extracted lines, and push to SAP S/4HANA."
            actions={
                <div className="flex items-center gap-2 flex-wrap">
                    <div className="flex items-center gap-1.5 min-w-[200px]">
                        <Building2 className="h-3.5 w-3.5 text-gilt-faint shrink-0" />
                        <Select
                            size="sm"
                            className="bg-gilt-card text-gilt-ink border-gilt-hairline rounded-md focus:border-gilt-brass focus:shadow-none disabled:bg-gilt-sunken disabled:text-gilt-faint transition-colors duration-200"
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
                        </Select>
                    </div>
                    {(loadingCompanies || loadingMasters) && (
                        <span className="inline-flex items-center gap-1 text-xs text-gilt-soft">
                            <Loader2 className="h-3 w-3 animate-spin" />
                            {loadingCompanies ? "Companies…" : "Loading vendors + accounts…"}
                        </span>
                    )}
                    {mastersReady && (
                        <span className="text-xs text-gilt-soft font-mono tabular-nums">
                            {vendors.length} vendors · {accounts.length} expense accounts
                        </span>
                    )}
                </div>
            }
        />
    );

    const ErrorBanner = error ? (
        <Card className="mx-6 lg:mx-8 mt-3 bg-gilt-card border-gilt-debit/40 shadow-none">
            <div className="flex items-start gap-2 px-3 py-2 text-sm text-gilt-debit">
                <AlertTriangle className="h-4 w-4 flex-shrink-0 mt-0.5 text-gilt-debit" />
                <span className="flex-1">{error}</span>
                <button onClick={() => setError("")} className="text-gilt-debit/70 hover:text-gilt-debit transition-colors duration-150">
                    <X className="h-4 w-4" />
                </button>
            </div>
        </Card>
    ) : null;

    // ══════════════════════════════════════════════════════════════
    // BRANCH 1: empty state — big dropzone
    // ══════════════════════════════════════════════════════════════
    if (bills.length === 0) {
        return (
            <div className="h-screen flex flex-col text-gilt-ink">
                {TopBar}
                {ErrorBanner}
                <div className="flex-1 min-h-0 p-6 pt-3 flex items-center justify-center">
                    <div className="w-full max-w-2xl">
                        <div
                            className={`border-2 border-dashed rounded-xl p-12 text-center transition-colors duration-150 cursor-pointer ${
                                isDragging ? "border-gilt-brass bg-gilt-sunken" : "border-gilt-hairline bg-gilt-card hover:border-gilt-brass hover:bg-gilt-sunken"
                            }`}
                            onDragOver={handleDragOver}
                            onDragLeave={handleDragLeave}
                            onDrop={handleDrop}
                            onClick={() => fileInputRef.current?.click()}
                        >
                            <input
                                ref={fileInputRef}
                                type="file"
                                accept="application/pdf"
                                multiple
                                className="hidden"
                                onChange={(e) => {
                                    if (e.target.files?.length) handleMultiFileUpload(e.target.files);
                                    e.target.value = "";
                                }}
                                disabled={loadingMasters}
                            />
                            <div className="flex flex-col items-center gap-3">
                                <div className="p-3 bg-gilt-sunken border border-gilt-hairline rounded-full">
                                    <FileText className="h-8 w-8 text-gilt-brass" />
                                </div>
                                <div>
                                    <p className="text-sm font-medium text-gilt-ink">Drop PDF bills here or click to browse</p>
                                    <p className="text-xs text-gilt-faint mt-1">Select multiple PDFs — they'll be extracted in parallel</p>
                                </div>
                            </div>
                        </div>
                        <div className="mt-6 bg-gilt-card rounded-xl border border-gilt-hairline p-5">
                            <div className="flex items-center gap-2 mb-3">
                                <Sparkles className="h-4 w-4 text-gilt-brass" />
                                <h2 className="font-mono text-[10px] font-semibold text-gilt-brass uppercase tracking-[0.14em]">How this works</h2>
                            </div>
                            <ol className="text-xs text-gilt-soft space-y-1.5 list-decimal list-inside">
                                <li>Pick the SAP S/4HANA company code above</li>
                                <li>Upload one or more purchase bill PDFs</li>
                                <li>Each bill is extracted in parallel — review individually</li>
                                <li>Push them one by one, or "Push all" to send everything ready</li>
                            </ol>
                        </div>
                        <DraftsPanel
                            open={draftsPanelOpen}
                            onToggle={() => setDraftsPanelOpen((o) => !o)}
                            loadList={() => sapApi.drafts.list({ flow: "purchase", companyCode: company })}
                            loadDraft={(id) => sapApi.drafts.get(id)}
                            deleteDraft={(id) => sapApi.drafts.delete(id)}
                            refreshKey={draftsRefreshKey}
                            activeDraftId={draftId}
                            onOpen={handleOpenDraft}
                            onDelete={handleDraftDeleted}
                            className="mt-4"
                        />
                    </div>
                </div>
            </div>
        );
    }

    // ══════════════════════════════════════════════════════════════
    // BRANCH 2: grid/list of bills — no active editor
    // ══════════════════════════════════════════════════════════════
    if (!activeBill) {
        return (
            <div className="h-screen flex flex-col text-gilt-ink">
                {TopBar}
                {ErrorBanner}
                <div className="border-b border-gilt-hairline px-6 py-3 flex items-center justify-between flex-wrap gap-3 flex-shrink-0">
                    <div className="flex items-center gap-3">
                        <div>
                            <p className="text-xs text-gilt-soft font-mono tabular-nums">
                                {counts.total} file{counts.total === 1 ? "" : "s"}
                                {counts.processing > 0 && <> · {counts.processing} processing</>}
                                {counts.ready > 0 && <> · {counts.ready} ready</>}
                                {counts.duplicate > 0 && <> · <span className="text-gilt-brass font-medium">{counts.duplicate} duplicate{counts.duplicate === 1 ? "" : "s"} blocked</span></>}
                                {counts.failed > 0 && <> · {counts.failed} failed</>}
                                {counts.pushed > 0 && <> · {counts.pushed} pushed</>}
                                {counts.pushFailed > 0 && <> · {counts.pushFailed} push-failed</>}
                            </p>
                        </div>
                    </div>
                    <div className="flex items-center gap-2 flex-wrap">
                        {/* Layout toggle */}
                        <div className="flex items-center bg-gilt-sunken border border-gilt-hairline rounded-md p-0.5">
                            <button
                                onClick={() => setGridLayout("grid")}
                                className={`flex items-center gap-1 px-2.5 py-1.5 text-xs rounded-md transition-colors duration-150 ${gridLayout === "grid" ? "bg-gilt-card text-gilt-ink border border-gilt-hairline" : "text-gilt-soft hover:text-gilt-ink"}`}
                                title="Grid view"
                            >
                                <Grid3x3 className="h-3.5 w-3.5" /> Grid
                            </button>
                            <button
                                onClick={() => setGridLayout("list")}
                                className={`flex items-center gap-1 px-2.5 py-1.5 text-xs rounded-md transition-colors duration-150 ${gridLayout === "list" ? "bg-gilt-card text-gilt-ink border border-gilt-hairline" : "text-gilt-soft hover:text-gilt-ink"}`}
                                title="List view"
                            >
                                <ListIcon className="h-3.5 w-3.5" /> List
                            </button>
                        </div>

                        <button
                            onClick={() => fileInputRef.current?.click()}
                            className={btnSecondary}
                            disabled={loadingMasters}
                        >
                            <Plus className="h-4 w-4" /> Add more
                        </button>
                        <input
                            ref={fileInputRef}
                            type="file"
                            accept="application/pdf"
                            multiple
                            className="hidden"
                            onChange={(e) => {
                                if (e.target.files?.length) handleMultiFileUpload(e.target.files);
                                e.target.value = "";
                            }}
                        />

                        {counts.pushFailed > 0 && (
                            <button
                                onClick={handleRetryFailed}
                                disabled={isPushingAll}
                                className={btnDanger}
                                title="Retry only the bills that failed to push"
                            >
                                <RefreshCw className={`h-3.5 w-3.5 ${isPushingAll ? "animate-spin" : ""}`} />
                                Retry failed ({counts.pushFailed})
                            </button>
                        )}

                        <SaveDraftButton onClick={handleSaveDraft} busy={savingDraft} label={draftId ? "Update draft" : "Save draft"} />

                        <button
                            onClick={handlePushAll}
                            disabled={isPushingAll || counts.ready === 0}
                            className={btnSapBlue}
                            title={counts.ready ? `Push the ${counts.ready} ready bill${counts.ready === 1 ? "" : "s"} to SAP S/4HANA` : "No ready bills to push"}
                        >
                            {isPushingAll ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
                            {isPushingAll ? "Pushing…" : `Push all${counts.ready ? ` (${counts.ready})` : ""}`}
                        </button>
                    </div>
                </div>

                <div
                    className="flex-1 overflow-y-auto p-6 space-y-5"
                    onDragOver={handleDragOver}
                    onDragLeave={handleDragLeave}
                    onDrop={handleDrop}
                >
                    {gridLayout === "grid" ? (
                        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
                            {bills.map((b) => (
                                <BillCard
                                    key={b.id}
                                    bill={b}
                                    overridden={overridePushIds.has(b.id)}
                                    onOpen={() => setActiveId(b.id)}
                                    onRemove={() => removeBill(b.id)}
                                    onRetry={() => pushBill(b.id)}
                                    onOverride={() => overridePush(b.id)}
                                />
                            ))}
                        </div>
                    ) : (
                        <BillList
                            bills={bills}
                            overridePushIds={overridePushIds}
                            onOpen={(id) => setActiveId(id)}
                            onRemove={removeBill}
                            onRetry={pushBill}
                            onOverride={overridePush}
                        />
                    )}
                    <DraftsPanel
                        open={draftsPanelOpen}
                        onToggle={() => setDraftsPanelOpen((o) => !o)}
                        loadList={() => sapApi.drafts.list({ flow: "purchase", companyCode: company })}
                        loadDraft={(id) => sapApi.drafts.get(id)}
                        deleteDraft={(id) => sapApi.drafts.delete(id)}
                        refreshKey={draftsRefreshKey}
                        activeDraftId={draftId}
                        onOpen={handleOpenDraft}
                        onDelete={handleDraftDeleted}
                    />
                    {isDragging && (
                        <div className="fixed inset-0 bg-gilt-brass/10 border-4 border-dashed border-gilt-brass pointer-events-none z-40" />
                    )}
                </div>
            </div>
        );
    }

    // ══════════════════════════════════════════════════════════════
    // BRANCH 3: editor for active bill
    // ══════════════════════════════════════════════════════════════
    return (
        <div className="h-screen flex flex-col text-gilt-ink">
            {TopBar}
            {ErrorBanner}

            <div className="border-b border-gilt-hairline px-6 py-2.5 flex items-center justify-between flex-shrink-0 flex-wrap gap-3">
                <div className="flex items-center gap-3 min-w-0">
                    <button
                        onClick={() => setActiveId(null)}
                        className="p-1.5 hover:bg-gilt-sunken rounded-lg transition-colors duration-150"
                        title="Back to all bills"
                    >
                        <ArrowLeft className="h-4 w-4 text-gilt-soft" />
                    </button>
                    <FileText className="h-4 w-4 text-gilt-faint flex-shrink-0" />
                    <h2 className="text-sm font-semibold text-gilt-ink truncate">{activeBill.filename}</h2>
                    <BillStatusBadge bill={activeBill} />
                </div>
                <div className="flex items-center gap-2 text-xs text-gilt-soft font-mono tabular-nums">
                    {bills.length > 1 && (
                        <span>
                            Bill {bills.findIndex((b) => b.id === activeId) + 1} of {bills.length}
                        </span>
                    )}
                </div>
            </div>

            <div className="flex-1 min-h-0 p-6 pt-3">
                <PanelGroup direction="horizontal">
                    <Panel defaultSize={45} minSize={25}>
                        <div className="h-full pr-2">
                            {activeBill.pdfData ? (
                                <div className="h-full bg-gilt-card rounded-xl border border-gilt-hairline overflow-hidden">
                                    <PdfViewer pdfData={activeBill.pdfData} />
                                </div>
                            ) : (
                                <div className="flex flex-col items-center justify-center h-full bg-gilt-card rounded-xl border border-gilt-hairline text-gilt-soft text-sm gap-2 p-8">
                                    <FileText className="h-10 w-10 text-gilt-faint" />
                                    <div className="font-medium text-gilt-ink">No preview available</div>
                                </div>
                            )}
                        </div>
                    </Panel>

                    <PanelResizeHandle className="w-1 bg-gilt-hairline hover:bg-gilt-brass transition-colors duration-150 rounded-full mx-1" />

                    <Panel defaultSize={55} minSize={30}>
                        <div className="h-full pl-2 overflow-auto">
                            {activeBill.status === "pending" || activeBill.status === "processing" ? (
                                <div className="flex flex-col items-center justify-center h-full bg-gilt-card rounded-xl border border-gilt-hairline text-gilt-soft gap-3 p-8">
                                    <Loader2 className="h-8 w-8 animate-spin text-gilt-brass" />
                                    <div className="text-sm font-medium text-gilt-ink">Extracting bill…</div>
                                    <div className="text-xs text-gilt-soft">Reading PDF and matching to SAP S/4HANA vendors + accounts.</div>
                                </div>
                            ) : activeBill.status === "failed" ? (
                                <div className="flex flex-col items-center justify-center h-full bg-gilt-card rounded-xl border border-gilt-hairline gap-3 p-8 text-center">
                                    <AlertTriangle className="h-10 w-10 text-gilt-debit/60" />
                                    <div className="text-sm font-medium text-gilt-debit">Extraction failed</div>
                                    <div className="text-xs text-gilt-soft max-w-md">{activeBill.error || "Unknown error"}</div>
                                </div>
                            ) : result ? (
                                <EditPanel
                                    result={result}
                                    vendorNames={vendorNames}
                                    accountNames={accountNames}
                                    updateVendor={updateVendor}
                                    updateLine={updateLine}
                                    onPush={onPush}
                                    pushing={pushing}
                                    pushStatus={activeBill.pushStatus}
                                    pushError={activeBill.pushError}
                                    duplicateCheck={activeBill?.result?.duplicate_check}
                                    duplicateBlocked={isBlockedDuplicate(activeBill)}
                                    onOverrideDuplicate={() => overridePush(activeBill.id)}
                                    onPickVendorCandidate={onPickVendorCandidate}
                                    onPickAccountCandidate={onPickAccountCandidate}
                                    onCreateVendor={(prefill) => setCreateModal({ entityType: "vendor", prefillName: prefill })}
                                    onCreateAccount={(prefill, idx) =>
                                        setCreateModal({ entityType: "account", prefillName: prefill || "", targetLineIdx: idx })
                                    }
                                    onOpenBulk={() => setBulkOpen(true)}
                                    unmatchedCount={gatherUnmatchedForBulk().length}
                                />
                            ) : null}
                            {activeBill.pushResult && (
                                <div className="bg-gilt-card border border-gilt-credit/40 rounded-xl p-4 mt-3">
                                    <div className="flex items-center gap-2 mb-2">
                                        <CheckCircle2 className="h-4 w-4 text-gilt-credit" />
                                        <h2 className="font-mono text-[10px] font-semibold text-gilt-credit uppercase tracking-[0.14em]">Pushed successfully</h2>
                                    </div>
                                    <pre className="text-xs overflow-auto max-h-64 bg-gilt-sunken border border-gilt-hairline rounded-lg p-3 text-gilt-soft">{JSON.stringify(activeBill.pushResult, null, 2)}</pre>
                                </div>
                            )}
                        </div>
                    </Panel>
                </PanelGroup>
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

// ─── Helpers ────────────────────────────────────────────────────────

/** Run an async fn over items with a concurrency cap. */
async function runConcurrent(items, limit, worker) {
    let idx = 0;
    const runners = Array.from({ length: Math.min(limit, items.length) }, async () => {
        while (idx < items.length) {
            const myIdx = idx++;
            await worker(items[myIdx]);
        }
    });
    await Promise.all(runners);
}

function BillStatusBadge({ bill }) {
    const { status, pushStatus } = bill;
    if (pushStatus === "pushed") {
        return (
            <span className="inline-flex items-center gap-1 px-2 py-0.5 font-mono text-[10px] font-medium uppercase tracking-[0.14em] rounded-sm bg-transparent border border-gilt-credit/40 text-gilt-credit">
                <CheckCircle2 className="h-3 w-3" />
                Pushed
            </span>
        );
    }
    if (pushStatus === "pushing") {
        return (
            <span className="inline-flex items-center gap-1 px-2 py-0.5 font-mono text-[10px] font-medium uppercase tracking-[0.14em] rounded-sm bg-transparent border border-gilt-brass/40 text-gilt-brass">
                <Loader2 className="h-3 w-3 animate-spin" />
                Pushing
            </span>
        );
    }
    if (pushStatus === "failed") {
        return (
            <span className="inline-flex items-center gap-1 px-2 py-0.5 font-mono text-[10px] font-medium uppercase tracking-[0.14em] rounded-sm bg-transparent border border-gilt-debit/40 text-gilt-debit">
                <AlertTriangle className="h-3 w-3" />
                Push failed
            </span>
        );
    }
    if (status === "processing" || status === "pending") {
        return (
            <span className="inline-flex items-center gap-1 px-2 py-0.5 font-mono text-[10px] font-medium uppercase tracking-[0.14em] rounded-sm bg-transparent border border-gilt-brass/40 text-gilt-brass">
                <Loader2 className="h-3 w-3 animate-spin" />
                {status === "pending" ? "Queued" : "Extracting"}
            </span>
        );
    }
    if (status === "failed") {
        return (
            <span className="inline-flex items-center gap-1 px-2 py-0.5 font-mono text-[10px] font-medium uppercase tracking-[0.14em] rounded-sm bg-transparent border border-gilt-debit/40 text-gilt-debit">
                <AlertTriangle className="h-3 w-3" />
                Failed
            </span>
        );
    }
    if (status === "ready") {
        return (
            <span className="inline-flex items-center gap-1 px-2 py-0.5 font-mono text-[10px] font-medium uppercase tracking-[0.14em] rounded-sm bg-transparent border border-gilt-hairline text-gilt-soft">
                <Clock className="h-3 w-3" />
                Ready
            </span>
        );
    }
    return null;
}

function BillCard({ bill, overridden, onOpen, onRemove, onRetry, onOverride }) {
    const total = bill.result?.extracted?.total;
    const vendor = bill.result?.extracted?.vendor_or_customer || bill.result?.vendor_match?.name;
    const lineCount = bill.result?.line_review?.length || 0;
    const needsReview = bill.result?.needs_review;
    const isOpenable = bill.status !== "pending";
    const dup = bill.result?.duplicate_check;
    const blockedDup = dup?.is_duplicate && !overridden && bill.pushStatus !== "pushed";
    return (
        <div className="group bg-gilt-card rounded-xl border border-gilt-hairline hover:border-gilt-brass transition-colors duration-150 overflow-hidden flex flex-col">
            <button
                onClick={isOpenable ? onOpen : undefined}
                disabled={!isOpenable}
                className="px-4 pt-4 pb-3 text-left flex-1 disabled:cursor-default"
            >
                <div className="flex items-start justify-between gap-2 mb-2">
                    <FileText className="h-4 w-4 text-gilt-faint flex-shrink-0 mt-0.5" />
                    <span className="text-sm font-medium text-gilt-ink truncate flex-1" title={bill.filename}>
                        {bill.filename}
                    </span>
                </div>
                <div className="mb-3 flex items-center gap-1.5 flex-wrap">
                    <BillStatusBadge bill={bill} />
                    <DuplicateBadge check={dup} />
                </div>
                {bill.status === "ready" && (
                    <div className="space-y-1.5 text-xs">
                        {vendor && (
                            <div className="flex items-center gap-1.5 text-gilt-soft truncate">
                                <Building2 className="h-3 w-3 text-gilt-faint flex-shrink-0" />
                                <span className="truncate" title={vendor}>{vendor}</span>
                            </div>
                        )}
                        {total != null && (
                            <div className="flex items-center gap-1.5 text-gilt-soft">
                                <DollarSign className="h-3 w-3 text-gilt-faint" />
                                <span className="font-mono tabular-nums">{Number(total).toFixed(2)}</span>
                            </div>
                        )}
                        <div className="flex items-center gap-1.5 text-gilt-soft">
                            <Layers className="h-3 w-3 text-gilt-faint" />
                            <span className="font-mono tabular-nums">{lineCount} line{lineCount === 1 ? "" : "s"}</span>
                            {needsReview && <span className="text-gilt-brass">· needs review</span>}
                        </div>
                    </div>
                )}
                {bill.status === "failed" && (
                    <p className="text-xs text-gilt-debit line-clamp-2" title={bill.error}>
                        {bill.error || "Extraction failed"}
                    </p>
                )}
                {bill.pushStatus === "failed" && (
                    <p className="text-xs text-gilt-debit mt-2 line-clamp-2" title={bill.pushError}>
                        Push: {bill.pushError || "failed"}
                    </p>
                )}
            </button>
            <div className="px-3 py-2 border-t border-gilt-hairline bg-gilt-sunken flex items-center justify-between gap-2">
                <div className="flex items-center gap-1">
                    {blockedDup && (
                        <button
                            onClick={(e) => { e.stopPropagation?.(); onOverride?.(); }}
                            className="text-[11px] font-medium text-gilt-brass hover:text-gilt-ink hover:bg-gilt-card px-2 py-1 rounded inline-flex items-center gap-1 transition-colors duration-150"
                            title="Push this bill even though it looks like a duplicate"
                        >
                            Push anyway
                        </button>
                    )}
                    {bill.pushStatus === "failed" && (
                        <button
                            onClick={onRetry}
                            className="text-[11px] font-medium text-gilt-debit hover:bg-gilt-card px-2 py-1 rounded inline-flex items-center gap-1 transition-colors duration-150"
                        >
                            <RefreshCw className="h-3 w-3" />
                            Retry
                        </button>
                    )}
                </div>
                <button
                    onClick={onRemove}
                    className="text-[11px] text-gilt-faint hover:text-gilt-debit hover:bg-gilt-card px-2 py-1 rounded inline-flex items-center gap-1 transition-colors duration-150 opacity-0 group-hover:opacity-100"
                    title="Remove from queue"
                >
                    <Trash2 className="h-3 w-3" />
                </button>
            </div>
        </div>
    );
}

function BillList({ bills, overridePushIds = new Set(), onOpen, onRemove, onRetry, onOverride }) {
    return (
        <div className="bg-gilt-card rounded-xl border border-gilt-hairline overflow-hidden">
            <div className="overflow-x-auto">
                <table className="w-full text-sm">
                    <thead className="border-b border-gilt-hairline">
                        <tr>
                            <th className="px-3 py-2.5 text-left font-mono text-[10px] font-medium text-gilt-soft uppercase tracking-[0.14em]">File</th>
                            <th className="px-3 py-2.5 text-left font-mono text-[10px] font-medium text-gilt-soft uppercase tracking-[0.14em]">Status</th>
                            <th className="px-3 py-2.5 text-left font-mono text-[10px] font-medium text-gilt-soft uppercase tracking-[0.14em]">Vendor</th>
                            <th className="px-3 py-2.5 text-right font-mono text-[10px] font-medium text-gilt-soft uppercase tracking-[0.14em]">Total</th>
                            <th className="px-3 py-2.5 text-left font-mono text-[10px] font-medium text-gilt-soft uppercase tracking-[0.14em]">Lines</th>
                            <th className="px-3 py-2.5"></th>
                        </tr>
                    </thead>
                    <tbody>
                        {bills.map((b) => {
                            const vendor = b.result?.extracted?.vendor_or_customer || b.result?.vendor_match?.name;
                            const total = b.result?.extracted?.total;
                            const isOpenable = b.status !== "pending";
                            return (
                                <tr
                                    key={b.id}
                                    onClick={isOpenable ? () => onOpen(b.id) : undefined}
                                    className={`border-t border-gilt-hairline hover:bg-gilt-sunken transition-colors duration-150 ${isOpenable ? "cursor-pointer" : ""}`}
                                >
                                    <td className="px-3 py-2 text-sm text-gilt-ink max-w-xs truncate" title={b.filename}>
                                        <span className="inline-flex items-center gap-1.5">
                                            <FileText className="h-3.5 w-3.5 text-gilt-faint flex-shrink-0" />
                                            {b.filename}
                                        </span>
                                    </td>
                                    <td className="px-3 py-2">
                                        <div className="flex items-center gap-1.5 flex-wrap">
                                            <BillStatusBadge bill={b} />
                                            <DuplicateBadge check={b.result?.duplicate_check} />
                                        </div>
                                    </td>
                                    <td className="px-3 py-2 text-sm text-gilt-soft truncate max-w-xs" title={vendor || ""}>{vendor || "—"}</td>
                                    <td className="px-3 py-2 text-sm text-right font-mono tabular-nums text-gilt-ink">
                                        {total != null ? Number(total).toFixed(2) : "—"}
                                    </td>
                                    <td className="px-3 py-2 text-sm font-mono tabular-nums text-gilt-soft">{b.result?.line_review?.length ?? "—"}</td>
                                    <td className="px-3 py-2 text-right">
                                        {b.result?.duplicate_check?.is_duplicate && !overridePushIds.has(b.id) && b.pushStatus !== "pushed" && (
                                            <button
                                                onClick={(e) => { e.stopPropagation(); onOverride?.(b.id); }}
                                                className="text-[11px] font-medium text-gilt-brass hover:text-gilt-ink hover:underline mr-3 transition-colors duration-150"
                                                title="Push anyway"
                                            >
                                                Push anyway
                                            </button>
                                        )}
                                        {b.pushStatus === "failed" && (
                                            <button
                                                onClick={(e) => { e.stopPropagation(); onRetry(b.id); }}
                                                className="text-[11px] font-medium text-gilt-debit hover:underline mr-3 inline-flex items-center gap-1 transition-colors duration-150"
                                            >
                                                <RefreshCw className="h-3 w-3" /> Retry
                                            </button>
                                        )}
                                        <button
                                            onClick={(e) => { e.stopPropagation(); onRemove(b.id); }}
                                            className="text-[11px] text-gilt-faint hover:text-gilt-debit hover:underline inline-flex items-center gap-1 transition-colors duration-150"
                                            title="Remove from queue"
                                        >
                                            <Trash2 className="h-3 w-3" />
                                        </button>
                                    </td>
                                </tr>
                            );
                        })}
                    </tbody>
                </table>
            </div>
        </div>
    );
}

function EditPanel({
    result, vendorNames, accountNames,
    updateVendor, updateLine, onPush, pushing, pushStatus, pushError,
    duplicateCheck, duplicateBlocked, onOverrideDuplicate,
    onPickVendorCandidate, onPickAccountCandidate,
    onCreateVendor, onCreateAccount,
    onOpenBulk, unmatchedCount,
}) {
    return (
        <div className="space-y-5">
            {/* Header card */}
            <div className="bg-gilt-card rounded-xl border border-gilt-hairline p-5">
                <div className="flex items-center gap-2 mb-4">
                    <FileText className="h-4 w-4 text-gilt-brass" />
                    <h2 className="font-mono text-[10px] font-semibold text-gilt-brass uppercase tracking-[0.14em]">Header</h2>
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div>
                        <label className={labelBase}>Vendor</label>
                        <div className="flex items-center gap-2">
                            <SearchableDropdown
                                options={vendorNames}
                                value={result?.sap_payload?.vendor_name || ""}
                                onChange={updateVendor}
                                placeholder="Pick vendor…"
                            />
                            {result?.vendor_match?.confidence != null && (
                                <ConfidenceBadge confidence={result.vendor_match.confidence} size="sm" />
                            )}
                            <MatchOriginBadge match={result?.vendor_match} />
                        </div>
                    </div>
                    <Field icon={Calendar} label="Date" value={result?.extracted?.doc_date} />
                    <Field icon={Hash} label="Bill #" value={result?.extracted?.doc_number} />
                    <Field icon={DollarSign} label="Total" value={result?.extracted?.total} />
                </div>
                {!result?.vendor_match?.matched && (
                    <div className="mt-4 space-y-2">
                        <div className="flex items-start gap-2 bg-gilt-sunken border border-gilt-brass/40 text-gilt-brass px-3 py-2 rounded-lg text-xs">
                            <AlertTriangle className="h-3.5 w-3.5 flex-shrink-0 mt-0.5 text-gilt-brass" />
                            <span>No matching SAP S/4HANA vendor found. Pick one above, choose a suggestion, or create new.</span>
                        </div>
                        <MatchPicker
                            topCandidates={result?.vendor_match?.top_candidates || []}
                            extractedName={result?.extracted?.vendor_or_customer || result?.vendor_match?.candidate_name || ""}
                            onPickExisting={onPickVendorCandidate}
                            onCreateNew={() => onCreateVendor(result?.extracted?.vendor_or_customer || result?.vendor_match?.candidate_name || "")}
                            label="vendor"
                        />
                    </div>
                )}
            </div>

            {/* Line items card */}
            <div className="bg-gilt-card rounded-xl border border-gilt-hairline overflow-hidden">
                <div className="px-5 py-3 border-b border-gilt-hairline flex items-center justify-between">
                    <div className="flex items-center gap-2">
                        <Layers className="h-4 w-4 text-gilt-brass" />
                        <h2 className="font-mono text-[10px] font-semibold text-gilt-brass uppercase tracking-[0.14em]">
                            Line items {result.line_review?.length ? <span className="text-gilt-faint normal-case font-medium tabular-nums">({result.line_review.length})</span> : null}
                        </h2>
                    </div>
                    {unmatchedCount > 0 && (
                        <button
                            onClick={onOpenBulk}
                            className="inline-flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-md border border-gilt-credit/40 text-gilt-credit hover:bg-gilt-sunken transition-colors duration-150"
                            title="Create all missing master data in one batch"
                        >
                            <Layers className="h-3 w-3" />
                            Create all missing ({unmatchedCount})
                        </button>
                    )}
                </div>
                <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                        <thead className="border-b border-gilt-hairline">
                            <tr>
                                <th className="px-3 py-2.5 text-left font-mono text-[10px] font-medium text-gilt-soft uppercase tracking-[0.14em]">Description</th>
                                <th className="px-3 py-2.5 text-right font-mono text-[10px] font-medium text-gilt-soft uppercase tracking-[0.14em]">Amount</th>
                                <th className="px-3 py-2.5 text-left font-mono text-[10px] font-medium text-gilt-soft uppercase tracking-[0.14em]">Expense account</th>
                                <th className="px-3 py-2.5 text-left font-mono text-[10px] font-medium text-gilt-soft uppercase tracking-[0.14em]">Conf.</th>
                            </tr>
                        </thead>
                        <tbody>
                            {(result.line_review || []).map((row, i) => (
                                <tr key={i} className="border-t border-gilt-hairline align-top hover:bg-gilt-sunken transition-colors duration-150">
                                    <td className="px-3 py-2">
                                        <input
                                            className={tableTextInput}
                                            defaultValue={row.description || ""}
                                            onBlur={(e) => updateLine(i, { description: e.target.value })}
                                        />
                                    </td>
                                    <td className="px-3 py-2">
                                        <input
                                            className={`${tableNumInput} w-28`}
                                            type="number"
                                            step="0.01"
                                            defaultValue={row.amount ?? 0}
                                            onBlur={(e) => updateLine(i, { amount: e.target.value })}
                                        />
                                    </td>
                                    <td className="px-3 py-2">
                                        <SearchableDropdown
                                            options={accountNames}
                                            value={row.match?.name || ""}
                                            onChange={(name) => updateLine(i, { accountName: name })}
                                            placeholder="Pick account…"
                                            createLabel="Create New Account"
                                            onCreateNew={onCreateAccount ? () => onCreateAccount("", i) : undefined}
                                        />
                                        {!row.match?.matched && (
                                            <div className="mt-1.5">
                                                <MatchPicker
                                                    topCandidates={row.match?.top_candidates || []}
                                                    extractedName={row.description || row.match?.candidate_name || ""}
                                                    onPickExisting={(cand) => onPickAccountCandidate(i, cand)}
                                                    onCreateNew={() => onCreateAccount(row.description || row.match?.candidate_name || "", i)}
                                                    label="account"
                                                />
                                            </div>
                                        )}
                                    </td>
                                    <td className="px-3 py-2">
                                        <div className="flex items-center gap-1">
                                            {row.match?.confidence != null && (
                                                <ConfidenceBadge
                                                    confidence={row.match.confidence}
                                                    size="sm"
                                                    showPercentage={false}
                                                />
                                            )}
                                            <MatchOriginBadge match={row.match} />
                                        </div>
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            </div>

            {/* Sticky push bar */}
            <div className="bg-gilt-card rounded-xl border border-gilt-hairline p-4 flex items-center gap-3 flex-wrap">
                <button
                    onClick={onPush}
                    disabled={pushing || !result?.sap_payload || pushStatus === "pushed" || duplicateBlocked}
                    className={btnSapBlue}
                    title={duplicateBlocked ? "This bill looks like a duplicate. Use \"Push anyway\" to override." : undefined}
                >
                    {pushing ? <Loader2 className="h-4 w-4 animate-spin" /> : pushStatus === "pushed" ? <CheckCircle2 className="h-4 w-4" /> : <Send className="h-4 w-4" />}
                    {pushing ? "Pushing to SAP S/4HANA…" : pushStatus === "pushed" ? "Pushed" : "Push to SAP S/4HANA"}
                </button>
                {duplicateBlocked && (
                    <>
                        <DuplicateBadge check={duplicateCheck} size="md" />
                        <button
                            onClick={onOverrideDuplicate}
                            className="inline-flex items-center gap-1.5 px-3 py-2 text-sm font-medium text-gilt-brass bg-gilt-sunken border border-gilt-brass/40 rounded-md hover:border-gilt-brass transition-colors duration-150"
                            title="Push anyway despite duplicate verdict"
                        >
                            Push anyway
                        </button>
                    </>
                )}
                {!duplicateBlocked && duplicateCheck?.is_duplicate && (
                    <DuplicateBadge check={duplicateCheck} size="md" />
                )}
                {result?.needs_review && !duplicateCheck?.is_duplicate && (
                    <span className="inline-flex items-center gap-1 px-2 py-0.5 text-[11px] font-medium rounded-full bg-gilt-sunken border border-gilt-brass/40 text-gilt-brass">
                        <AlertTriangle className="h-3 w-3" />
                        Some matches were uncertain — review before pushing.
                    </span>
                )}
                {pushStatus === "failed" && pushError && (
                    <span className="inline-flex items-center gap-1 text-xs text-gilt-debit">
                        <AlertTriangle className="h-3 w-3" />
                        {String(pushError).slice(0, 80)}
                    </span>
                )}
            </div>
        </div>
    );
}

function Field({ icon: Icon, label, value }) {
    return (
        <div>
            <label className={labelBase}>
                {Icon && <Icon className="inline h-3.5 w-3.5 mr-1 text-gilt-faint" />}
                {label}
            </label>
            <div className="text-sm text-gilt-ink px-3 py-2 bg-gilt-sunken border border-gilt-hairline rounded-lg min-h-[36px] font-mono tabular-nums">
                {value ?? <span className="text-gilt-faint">—</span>}
            </div>
        </div>
    );
}
