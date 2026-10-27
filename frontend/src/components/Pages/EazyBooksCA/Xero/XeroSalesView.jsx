import React, { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Panel, Group as PanelGroup, Separator as PanelResizeHandle } from "react-resizable-panels";
import {
    FileSignature,
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
    User,
    Upload,
    Plus,
    ArrowLeft,
    Grid3x3,
    List as ListIcon,
    RefreshCw,
    Trash2,
    Clock,
    Shapes,
} from "lucide-react";
import { xeroApi } from "../../../../services/api";
import { useDraftDeepLink } from "../common/useDraftDeepLink";
import PdfViewer from "../PdfViewer";
import SearchableDropdown from "../common/SearchableDropdown";
import AISuggestedDropdown from "../common/AISuggestedDropdown";
import { findInBatchDuplicates } from "../common/duplicateDetection";
import ConfidenceBadge from "../common/ConfidenceBadge";
import CreateMasterModal from "./components/CreateMasterModal";
import MatchPicker from "./components/MatchPicker";
import BulkCreateModal from "./components/BulkCreateModal";
import DraftsPanel, { SaveDraftButton } from "../common/DraftsPanel";
import DuplicateBadge from "../common/DuplicateBadge";
import { PageHeader } from "../../../ui/page-header";
import { Select } from "../../../ui/input";
import { Card } from "../../../ui/card";

// Adapt a Xero backend entity match (customer/vendor) into the AISuggestedDropdown
// `aiSuggestion` shape. Only surfaces a suggestion for an actual backend match,
// so the auto-fill + reasoning chip reflects real AI matching.
const aiSuggestionFromEntityMatch = (m, noun = "record") => {
    if (!m || !m.matched || !m.name || m.confidence == null) return null;
    return {
        recommended_ledger: m.name,
        confidence: m.confidence,
        match_type: "fuzzy",
        reasoning: `Matched to an existing Xero ${noun}`,
    };
};

// Shared class recipes so Purchase/Sales/BankEntries stay visually consistent.
const inputBase = "w-full border border-gray-300 rounded-lg px-3 py-2 text-sm text-gray-900 bg-white focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 outline-none transition-shadow";
const labelBase = "block text-xs font-medium text-gray-600 mb-1.5";
const btnPrimary = "inline-flex items-center gap-1.5 px-4 py-2 text-sm font-semibold text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed";
const btnSecondary = "inline-flex items-center gap-1.5 px-3 py-2 text-sm text-gray-700 bg-white border border-gray-300 rounded-lg hover:bg-gray-50 transition-colors disabled:opacity-50";
// Xero-specific green for the actual "Push to Xero" action — keeps the
// affordance familiar to Xero users.
const btnXeroGreen = "inline-flex items-center gap-1.5 px-4 py-2 text-sm font-semibold text-white bg-[#2CA01C] rounded-lg hover:bg-[#268a18] transition-colors disabled:opacity-50";
const btnDanger = "inline-flex items-center gap-1.5 px-3 py-2 text-sm font-medium text-red-700 bg-red-50 border border-red-200 rounded-lg hover:bg-red-100 transition-colors disabled:opacity-50";

const PUSH_CONCURRENCY = 2; // be polite to Xero; 2 in flight is plenty for the demo org

// Format a money amount for the review UI. Xero serves multi-currency orgs, so
// use the extracted currency (USD/GBP/EUR/AUD/…) when present rather than a
// fixed symbol.
const fmtAmount = (n, currency) => {
    const v = Number(n);
    if (n == null || Number.isNaN(v)) return "—";
    try {
        return new Intl.NumberFormat(undefined, currency
            ? { style: "currency", currency }
            : { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(v);
    } catch {
        return v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }
};

/**
 * Xero Sales Invoice flow — multi-invoice bulk upload.
 *
 * State model:
 *   invoices[]: { id, filename, file, pdfData, status, result, error,
 *                 pushStatus, pushError, pushResult }
 *   activeId: which invoice the editor pane is currently looking at
 *
 * Render branches:
 *   no invoices           → empty dropzone (drag PDFs or browse)
 *   invoices + no active  → grid/list of invoices with status badges
 *   activeId set          → PDF + EditPanel layout
 *
 * `setResult(updater)` is preserved so every existing Sales mutator keeps
 * working — it routes the patch onto the active invoice's result.
 */
export default function XeroSalesView() {
    const [companies, setCompanies] = useState([]);
    const [company, setCompany] = useState("");

    const [invoices, setInvoices] = useState([]);
    const [activeId, setActiveId] = useState(null);
    const [gridLayout, setGridLayout] = useState("grid"); // grid | list
    const [isDragging, setIsDragging] = useState(false);
    const [isPushingAll, setIsPushingAll] = useState(false);
    const fileInputRef = useRef(null);

    const [loadingCompanies, setLoadingCompanies] = useState(true);
    const [loadingMasters, setLoadingMasters] = useState(false);
    const [error, setError] = useState("");

    // Master data for dropdowns
    const [customers, setCustomers] = useState([]);
    const [items, setItems] = useState([]);

    // Inline-create modal state: which entity is being created and for which row.
    //   { entityType: 'customer'|'item', prefillName, targetLineIdx?: number }
    // targetLineIdx is set only for line-item creates so we know which line to patch.
    const [createModal, setCreateModal] = useState(null);
    const [bulkOpen, setBulkOpen] = useState(false);
    // User-authorable mapping-rules modal (Phase 3 parity).
    const [rulesOpen, setRulesOpen] = useState(false);

    // Drafts (saved sessions).
    const [draftId, setDraftId] = useState(null);
    const [draftsPanelOpen, setDraftsPanelOpen] = useState(false);
    const [draftsRefreshKey, setDraftsRefreshKey] = useState(0);
    const [savingDraft, setSavingDraft] = useState(false);

    // Invoices the user has explicitly said "push anyway" for despite the
    // duplicate verdict. Resets on a new upload run.
    const [overridePushIds, setOverridePushIds] = useState(() => new Set());

    useEffect(() => {
        (async () => {
            try {
                const res = await xeroApi.getCompanies();
                const list = res?.tenants || [];
                setCompanies(list);
                if (list[0]) setCompany(list[0].tenant_id || list[0].name);
            } catch (err) {
                setError(err?.response?.data?.detail || err.message || "Failed to load companies");
            } finally {
                setLoadingCompanies(false);
            }
        })();
    }, []);

    // Fetch customer + item master lists when company changes.
    useEffect(() => {
        if (!company) return;
        setLoadingMasters(true);
        setCustomers([]);
        setItems([]);
        (async () => {
            try {
                const [c, i] = await Promise.all([
                    xeroApi.getCustomers(company),
                    xeroApi.getItems(company),
                ]);
                setCustomers(c?.customers || []);
                setItems(i?.items || []);
            } catch (err) {
                setError(err?.response?.data?.detail || err.message || "Failed to load master data");
            } finally {
                setLoadingMasters(false);
            }
        })();
    }, [company]);

    const customerNames = useMemo(() => customers.map((c) => c.name).filter(Boolean).sort(), [customers]);
    const itemNames = useMemo(() => items.map((i) => i.name).filter(Boolean).sort(), [items]);
    const customerByName = useMemo(() => Object.fromEntries(customers.map((c) => [c.name, c])), [customers]);
    const itemByName = useMemo(() => Object.fromEntries(items.map((i) => [i.name, i])), [items]);

    const activeInvoice = invoices.find((inv) => inv.id === activeId) || null;
    const result = activeInvoice?.result || null;
    const pushing = activeInvoice?.pushStatus === "pushing";

    // ── Invoice state helpers ───────────────────────────────────────
    const patchInvoice = (id, patch) => {
        setInvoices((prev) => prev.map((inv) => {
            if (inv.id !== id) return inv;
            const merge = typeof patch === "function" ? patch(inv) : patch;
            return { ...inv, ...merge };
        }));
    };

    // Drop-in replacement for the prior `setResult` — patches the active invoice.
    const setResult = (updater) => {
        if (!activeId) return;
        setInvoices((prev) => prev.map((inv) => {
            if (inv.id !== activeId) return inv;
            const nextResult = typeof updater === "function" ? updater(inv.result) : updater;
            return { ...inv, result: nextResult };
        }));
    };

    // ── Multi-file upload ───────────────────────────────────────────
    const handleMultiFileUpload = async (fileList) => {
        const pdfFiles = Array.from(fileList).filter((f) => f.type === "application/pdf");
        if (pdfFiles.length === 0) return;

        const newInvoices = pdfFiles.map((f, i) => ({
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

        setInvoices((prev) => [...prev, ...newInvoices]);

        // Extract in parallel, but limit concurrency to keep the backend happy.
        await runConcurrent(newInvoices, 2, async (inv) => {
            patchInvoice(inv.id, { status: "processing" });
            try {
                const res = await xeroApi.extractInvoice(inv.file, company);
                const ready = res?.ok ?? !!res?.xero_payload;
                patchInvoice(inv.id, { status: ready ? "ready" : "failed", result: res, error: ready ? null : (res?.error || "Extraction returned no result") });
            } catch (err) {
                const msg = err?.response?.data?.detail || err.message || "Extraction failed";
                patchInvoice(inv.id, { status: "failed", error: typeof msg === "string" ? msg : JSON.stringify(msg) });
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

    // In-batch duplicate detection: two files in the same upload that share
    // (doc number + customer) flag each other before either hits Xero. Mirrors
    // Tally's findInBatchDuplicates; adapts the Xero invoice shape to its input.
    const batchDuplicates = useMemo(
        () => findInBatchDuplicates(
            invoices.map((inv) => ({
                id: inv.id,
                status: inv.result?.extracted ? "done" : inv.status,
                duplicateOverridden: overridePushIds.has(inv.id),
                data: { header: {
                    invoice_number: inv.result?.extracted?.doc_number,
                    party_name: inv.result?.extracted?.vendor_or_customer,
                    party_gstin: "",
                } },
            }))
        ),
        [invoices, overridePushIds],
    );

    const isBlockedDuplicate = (inv) => {
        if (!inv) return false;
        if (batchDuplicates.has(inv.id) && !overridePushIds.has(inv.id)) return true;
        const dup = inv?.result?.duplicate_check;
        if (!dup?.is_duplicate) return false;
        return !overridePushIds.has(inv.id);
    };

    const overridePush = (id) => {
        setOverridePushIds((prev) => {
            const next = new Set(prev);
            next.add(id);
            return next;
        });
    };

    // ── Push handlers ──────────────────────────────────────────────
    const pushInvoice = async (id) => {
        const inv = invoices.find((x) => x.id === id);
        if (!inv?.result?.xero_payload) return;
        if (inv.pushStatus === "pushing" || inv.pushStatus === "pushed") return;
        if (isBlockedDuplicate(inv)) {
            // Caller should have called overridePush first; bail silently.
            return;
        }
        patchInvoice(id, { pushStatus: "pushing", pushError: null });
        try {
            const res = await xeroApi.pushInvoiceXero({
                payload: inv.result.xero_payload,
                tenant_id: company,
                extracted_total: inv.result.extracted_total ?? inv.result.extracted?.total,
                skip_duplicate_check: overridePushIds.has(id),
            });
            patchInvoice(id, { pushStatus: "pushed", pushResult: res, pushError: null });
        } catch (err) {
            const detail = err?.response?.data?.detail;
            const msg = typeof detail === "string"
                ? detail
                : detail?.message || detail?.xero_error || detail?.xero_detail || err.message || "Push failed";
            patchInvoice(id, { pushStatus: "failed", pushError: msg });
        }
    };

    const onPush = () => activeId && pushInvoice(activeId);

    const handlePushAll = async () => {
        // Skip duplicates by default; user can override individual rows via
        // "Push anyway" before bulk-pushing, or re-trigger after overriding.
        const ready = invoices.filter((inv) =>
            inv.status === "ready"
            && inv.pushStatus !== "pushed"
            && inv.pushStatus !== "pushing"
            && !isBlockedDuplicate(inv)
        );
        if (ready.length === 0) return;
        setIsPushingAll(true);
        try {
            await runConcurrent(ready, PUSH_CONCURRENCY, (inv) => pushInvoice(inv.id));
        } finally {
            setIsPushingAll(false);
        }
    };

    const handleRetryFailed = async () => {
        const failed = invoices.filter((inv) => inv.pushStatus === "failed");
        if (failed.length === 0) return;
        setIsPushingAll(true);
        try {
            await runConcurrent(failed, PUSH_CONCURRENCY, (inv) => pushInvoice(inv.id));
        } finally {
            setIsPushingAll(false);
        }
    };

    const removeInvoice = (id) => {
        if (activeId === id) setActiveId(null);
        setInvoices((prev) => prev.filter((inv) => inv.id !== id));
    };

    // ── Drafts ─────────────────────────────────────────────────────
    // What gets persisted: just the data, not the file objects or blob URLs.
    // On reload the PDF preview pane shows "no preview available" but every
    // extracted/edited field survives.
    const serializeInvoicesForDraft = () => invoices.map((inv) => ({
        id: inv.id,
        filename: inv.filename,
        status: inv.status,
        result: inv.result,
        error: inv.error,
        pushStatus: inv.pushStatus,
        pushError: inv.pushError,
        pushResult: inv.pushResult,
    }));

    const deriveDraftTitle = () => {
        if (invoices.length === 0) return "Sales draft";
        if (invoices.length === 1) return invoices[0].filename;
        return `${invoices.length} invoices · ${invoices[0].filename}`;
    };

    const handleSaveDraft = async () => {
        if (invoices.length === 0) return;
        setSavingDraft(true);
        setError("");
        try {
            const res = await xeroApi.drafts.save({
                flow: "sales",
                payload: { invoices: serializeInvoicesForDraft(), activeId },
                title: deriveDraftTitle(),
                draftId,
                tenantId: company,
            });
            if (res?.draft_id) setDraftId(res.draft_id);
            setDraftsRefreshKey((k) => k + 1);
        } catch (e) {
            setError(e?.response?.data?.detail || e.message || "Save draft failed");
        } finally {
            setSavingDraft(false);
        }
    };

    const handleOpenDraft = async (draft) => {
        const payload = draft?.payload || {};
        // Phase 6 parity: resolve the source PDF for each invoice when the draft
        // stored an R2 key (or a draft-level file_url). Blob URLs from the upload
        // session aren't persisted, so we re-presign here. Failures are
        // swallowed by xeroApi.draftFileUrl (returns null) → "no preview".
        const draftFileUrl = draft?.file_url || draft?.fileUrl || payload?.file_url || null;
        const restoredInvoices = await Promise.all(
            (payload.invoices || []).map(async (inv) => {
                let pdfData = null;
                const r2Key = inv.r2_key || inv.result?.r2_key;
                if (r2Key) {
                    try { pdfData = await xeroApi.draftFileUrl(r2Key); } catch (_) { /* no preview */ }
                }
                if (!pdfData) pdfData = inv.file_url || draftFileUrl || null;
                return { ...inv, file: null, pdfData };
            })
        );
        setInvoices(restoredInvoices);
        setActiveId(payload.activeId && restoredInvoices.some((inv) => inv.id === payload.activeId)
            ? payload.activeId
            : null);
        setDraftId(draft.id);
        setDraftsPanelOpen(false);
        setError("");
    };

    useDraftDeepLink({
        getDraft: xeroApi.drafts.get,
        companyField: "tenant_id",
        setCompany,
        onOpen: handleOpenDraft,
    });

    const handleDraftDeleted = (id) => {
        if (draftId === id) setDraftId(null);
    };

    // ── Active-invoice mutators (unchanged from single-invoice flow) ──
    const updateCustomer = (name) => {
        const c = customerByName[name];
        if (!c) return;
        setResult((prev) => {
            const next = structuredClone(prev);
            next.xero_payload.customer_id = c.list_id;
            next.xero_payload.customer_name = c.name;
            next.customer_match = {
                ...prev.customer_match,
                name: c.name,
                matched: true,
                list_id: c.list_id,
                confidence: 1,
            };
            return next;
        });
    };

    const updateLine = (idx, patch) => {
        setResult((prev) => {
            if (!prev) return prev;
            const next = structuredClone(prev);
            const line = next.xero_payload.line_items[idx];
            if (patch.amount !== undefined) line.amount = Number(patch.amount) || 0;
            if (patch.description !== undefined) line.description = patch.description;
            if (patch.qty !== undefined) {
                line.quantity = Number(patch.qty) || 0;
            }
            if (patch.rate !== undefined) {
                line.rate = Number(patch.rate) || 0;
            }
            if (patch.itemName !== undefined) {
                const it = itemByName[patch.itemName];
                if (it) {
                    line.item_id = it.list_id;
                    line.name = it.name;
                    next.line_review[idx].match = {
                        matched: true,
                        list_id: it.list_id,
                        name: it.name,
                        method: "manual",
                        confidence: 1,
                    };
                }
            }
            return next;
        });
    };

    // --- Tier 1/2/4 helpers ---------------------------------------------
    // setCustomerOnPayload patches both the Xero payload and the local match
    // state without needing the entity to be in the dropdown by name first.
    // Used after inline-create and after Tier-2 candidate picks.
    const setCustomerOnPayload = (c, method = "manual") => {
        setResult((prev) => {
            const next = structuredClone(prev);
            next.xero_payload.customer_id = c.list_id;
            next.xero_payload.customer_name = c.name;
            next.customer_match = {
                ...prev.customer_match,
                name: c.name,
                matched: true,
                list_id: c.list_id,
                confidence: 1,
                method,
            };
            return next;
        });
    };

    const setItemOnLine = (idx, it, method = "manual") => {
        setResult((prev) => {
            const next = structuredClone(prev);
            const line = next.xero_payload?.line_items?.[idx];
            if (line) {
                line.item_id = it.list_id;
                line.name = it.name;
            }
            if (next.line_review?.[idx]) {
                next.line_review[idx].match = {
                    ...next.line_review[idx].match,
                    matched: true,
                    list_id: it.list_id,
                    name: it.name,
                    confidence: 1,
                    method,
                };
            }
            return next;
        });
    };

    // Tier-2 pick: user accepts an existing candidate. Save alias so future
    // extractions of the same variant resolve without prompting.
    const onPickCustomerCandidate = async (cand) => {
        setCustomerOnPayload(cand, "user_picked");
        try {
            await xeroApi.saveAlias({
                entity_type: "customer",
                alias_text: result?.extracted?.vendor_or_customer || result?.customer_match?.candidate_name || "",
                xero_entity_id: cand.list_id,
                xero_entity_name: cand.name,
                tenant_id: company,
            });
        } catch (_) { /* non-fatal — alias is an optimization */ }
    };

    const onPickItemCandidate = async (idx, cand) => {
        setItemOnLine(idx, cand, "user_picked");
        try {
            await xeroApi.saveAlias({
                entity_type: "item",
                alias_text: result?.line_review?.[idx]?.description
                    || result?.line_review?.[idx]?.match?.candidate_name
                    || "",
                xero_entity_id: cand.list_id,
                xero_entity_name: cand.name,
                tenant_id: company,
            });
        } catch (_) { /* non-fatal */ }
    };

    // Tier-1 inline create: modal returns the freshly-created entity. Append
    // to the local master list (so the dropdown reflects it) and patch payload.
    const onCreatedFromModal = (created) => {
        if (!createModal) return;
        if (createModal.entityType === "customer") {
            setCustomers((prev) => [...prev, created]);
            setCustomerOnPayload(created, "created");
        } else if (createModal.entityType === "item") {
            setItems((prev) => [...prev, created]);
            if (createModal.targetLineIdx != null) {
                setItemOnLine(createModal.targetLineIdx, created, "created");
            }
        }
    };

    // Tier-5: build the bulk-create item list from current pending_actions
    // and unmatched line items. We don't trust pending_actions alone because
    // the user may have already created some via inline modal — recompute from
    // current match state instead.
    const gatherUnmatchedForBulk = () => {
        const out = [];
        const date = result?.extracted?.doc_date || "";
        const total = result?.extracted?.total;
        if (result?.customer_match && !result.customer_match.matched) {
            out.push({
                key: "customer_header",
                entity_type: "customer",
                name: result.customer_match.candidate_name
                    || result?.extracted?.vendor_or_customer
                    || "",
                date,
                amount: total,
            });
        }
        (result?.line_review || []).forEach((row, i) => {
            if (row?.match && !row.match.matched) {
                out.push({
                    key: `item_${i}`,
                    entity_type: "item",
                    name: row.description || row.match.candidate_name || "",
                    date,
                    amount: row.amount != null ? row.amount : total,
                });
            }
        });
        return out;
    };

    const onBulkComplete = (results = []) => {
        // Apply each successful create back to the right slot.
        const okResults = results.filter((r) => r.ok && r.xero_entity_id);
        okResults.forEach((r) => {
            const entity = { list_id: String(r.xero_entity_id), name: r.xero_entity_name };
            if (r.entity_type === "customer") {
                setCustomers((prev) => [...prev, entity]);
                setCustomerOnPayload(entity, "bulk_created");
            } else if (r.entity_type === "item") {
                setItems((prev) => [...prev, entity]);
                // Find the line whose description matches the created item name.
                const lineIdx = (result?.line_review || []).findIndex(
                    (li) => (li.description || li.match?.candidate_name) === r.name && !li.match?.matched,
                );
                if (lineIdx >= 0) setItemOnLine(lineIdx, entity, "bulk_created");
            }
        });
    };

    // ── Derived counts for the bulk header ─────────────────────────
    const counts = useMemo(() => ({
        total: invoices.length,
        processing: invoices.filter((inv) => inv.status === "processing" || inv.status === "pending").length,
        ready: invoices.filter((inv) => inv.status === "ready" && inv.pushStatus !== "pushed" && !isBlockedDuplicate(inv)).length,
        failed: invoices.filter((inv) => inv.status === "failed").length,
        pushed: invoices.filter((inv) => inv.pushStatus === "pushed").length,
        pushFailed: invoices.filter((inv) => inv.pushStatus === "failed").length,
        duplicate: invoices.filter((inv) => inv.result?.duplicate_check?.is_duplicate && !overridePushIds.has(inv.id) && inv.pushStatus !== "pushed").length,
    }), [invoices, overridePushIds]); // eslint-disable-line react-hooks/exhaustive-deps

    const mastersReady = !loadingMasters && customers.length > 0;

    // ══════════════════════════════════════════════════════════════
    // Top bar — same across all branches
    // ══════════════════════════════════════════════════════════════
    const TopBar = (
        <PageHeader
            icon={FileSignature}
            title="Xero · Sales Invoice"
            description="Upload sales invoice PDFs, review extracted lines, and push to Xero."
            actions={
                <div className="flex items-center gap-2 flex-wrap">
                    <div className="flex items-center gap-1.5 min-w-[200px]">
                        <Building2 className="h-3.5 w-3.5 text-ink-400 shrink-0" />
                        <Select
                            size="sm"
                            value={company}
                            onChange={(e) => setCompany(e.target.value)}
                            disabled={loadingCompanies || !companies.length}
                        >
                            {loadingCompanies && <option value="">Loading companies…</option>}
                            {!loadingCompanies && !companies.length && <option value="">No companies</option>}
                            {companies.map((c) => {
                                const v = c.tenant_id || c.name;
                                return <option key={v} value={v}>{c.name}</option>;
                            })}
                        </Select>
                    </div>
                    {(loadingCompanies || loadingMasters) && (
                        <span className="inline-flex items-center gap-1 text-xs text-ink-500">
                            <Loader2 className="h-3 w-3 animate-spin" />
                            {loadingCompanies ? "Companies…" : "Loading customers + items…"}
                        </span>
                    )}
                    {mastersReady && (
                        <span className="text-xs text-ink-500 tabular-nums">
                            {customers.length} customers · {items.length} items
                        </span>
                    )}
                    <button
                        type="button"
                        onClick={() => setRulesOpen(true)}
                        disabled={!company}
                        className="inline-flex items-center gap-1.5 px-2.5 py-1.5 text-xs font-medium text-ink-700 bg-white border border-ink-200 rounded-lg hover:bg-ink-50 transition-colors disabled:opacity-50"
                        title="Manage mapping rules that auto-assign customers/items"
                    >
                        <Shapes className="h-3.5 w-3.5" />
                        Rules
                    </button>
                </div>
            }
        />
    );

    const RulesModal = rulesOpen ? (
        <MappingRulesModal
            tenantId={company}
            documentType="sales_invoice"
            onClose={() => setRulesOpen(false)}
        />
    ) : null;

    const ErrorBanner = error ? (
        <Card className="mx-6 lg:mx-8 mt-3 bg-danger-50 border-danger-200">
            <div className="flex items-start gap-2 px-3 py-2 text-sm text-danger-700">
                <AlertTriangle className="h-4 w-4 flex-shrink-0 mt-0.5 text-danger-600" />
                <span className="flex-1">{error}</span>
                <button onClick={() => setError("")} className="text-danger-500 hover:text-danger-700 transition-colors">
                    <X className="h-4 w-4" />
                </button>
            </div>
        </Card>
    ) : null;

    // ══════════════════════════════════════════════════════════════
    // BRANCH 1: empty state — big dropzone
    // ══════════════════════════════════════════════════════════════
    if (invoices.length === 0) {
        return (
            <div className="h-screen flex flex-col bg-gray-50 text-gray-900">
                {TopBar}
                {ErrorBanner}
                <div className="flex-1 min-h-0 p-6 pt-3 flex items-center justify-center">
                    <div className="w-full max-w-2xl">
                        <div
                            className={`border-2 border-dashed rounded-xl p-12 text-center transition-colors cursor-pointer ${
                                isDragging ? "border-indigo-400 bg-indigo-50" : "border-gray-300 bg-white hover:border-indigo-400 hover:bg-gray-50"
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
                                <div className="p-3 bg-indigo-50 rounded-full">
                                    <FileSignature className="h-8 w-8 text-indigo-500" />
                                </div>
                                <div>
                                    <p className="text-sm font-medium text-gray-700">Drop sales invoice PDFs here or click to browse</p>
                                    <p className="text-xs text-gray-400 mt-1">Select multiple PDFs — they'll be extracted in parallel</p>
                                </div>
                            </div>
                        </div>
                        <div className="mt-6 bg-white rounded-xl shadow-sm border border-gray-200 p-5">
                            <div className="flex items-center gap-2 mb-3">
                                <Sparkles className="h-4 w-4 text-indigo-500" />
                                <h2 className="text-sm font-semibold text-gray-700 uppercase tracking-wider">How this works</h2>
                            </div>
                            <ol className="text-xs text-gray-600 space-y-1.5 list-decimal list-inside">
                                <li>Pick the Xero organization above</li>
                                <li>Upload one or more sales invoice PDFs</li>
                                <li>Each invoice is extracted in parallel — review individually</li>
                                <li>Push them one by one, or "Push all" to send everything ready</li>
                            </ol>
                        </div>
                        <DraftsPanel
                            open={draftsPanelOpen}
                            onToggle={() => setDraftsPanelOpen((o) => !o)}
                            loadList={() => xeroApi.drafts.list({ flow: "sales", tenantId: company })}
                            loadDraft={(id) => xeroApi.drafts.get(id)}
                            deleteDraft={(id) => xeroApi.drafts.delete(id)}
                            refreshKey={draftsRefreshKey}
                            activeDraftId={draftId}
                            onOpen={handleOpenDraft}
                            onDelete={handleDraftDeleted}
                            className="mt-4"
                        />
                    </div>
                </div>
                {RulesModal}
            </div>
        );
    }

    // ══════════════════════════════════════════════════════════════
    // BRANCH 2: grid/list of invoices — no active editor
    // ══════════════════════════════════════════════════════════════
    if (!activeInvoice) {
        return (
            <div className="h-screen flex flex-col bg-gray-50 text-gray-900">
                {TopBar}
                {ErrorBanner}
                <div className="bg-white border-b border-gray-200 px-6 py-3 flex items-center justify-between flex-wrap gap-3 flex-shrink-0">
                    <div className="flex items-center gap-3">
                        <div>
                            <p className="text-xs text-gray-500">
                                {counts.total} file{counts.total === 1 ? "" : "s"}
                                {counts.processing > 0 && <> · {counts.processing} processing</>}
                                {counts.ready > 0 && <> · {counts.ready} ready</>}
                                {counts.duplicate > 0 && <> · <span className="text-amber-700 font-medium">{counts.duplicate} duplicate{counts.duplicate === 1 ? "" : "s"} blocked</span></>}
                                {counts.failed > 0 && <> · {counts.failed} failed</>}
                                {counts.pushed > 0 && <> · {counts.pushed} pushed</>}
                                {counts.pushFailed > 0 && <> · {counts.pushFailed} push-failed</>}
                            </p>
                        </div>
                    </div>
                    <div className="flex items-center gap-2 flex-wrap">
                        {/* Layout toggle */}
                        <div className="flex items-center bg-gray-100 rounded-lg p-0.5">
                            <button
                                onClick={() => setGridLayout("grid")}
                                className={`flex items-center gap-1 px-2.5 py-1.5 text-xs rounded-md transition-colors ${gridLayout === "grid" ? "bg-white text-gray-900 shadow-sm" : "text-gray-500 hover:text-gray-700"}`}
                                title="Grid view"
                            >
                                <Grid3x3 className="h-3.5 w-3.5" /> Grid
                            </button>
                            <button
                                onClick={() => setGridLayout("list")}
                                className={`flex items-center gap-1 px-2.5 py-1.5 text-xs rounded-md transition-colors ${gridLayout === "list" ? "bg-white text-gray-900 shadow-sm" : "text-gray-500 hover:text-gray-700"}`}
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
                                title="Retry only the invoices that failed to push"
                            >
                                <RefreshCw className={`h-3.5 w-3.5 ${isPushingAll ? "animate-spin" : ""}`} />
                                Retry failed ({counts.pushFailed})
                            </button>
                        )}

                        <SaveDraftButton onClick={handleSaveDraft} busy={savingDraft} label={draftId ? "Update draft" : "Save draft"} />

                        <button
                            onClick={handlePushAll}
                            disabled={isPushingAll || counts.ready === 0}
                            className={btnXeroGreen}
                            title={counts.ready ? `Push the ${counts.ready} ready invoice${counts.ready === 1 ? "" : "s"} to Xero` : "No ready invoices to push"}
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
                            {invoices.map((inv) => (
                                <InvoiceCard
                                    key={inv.id}
                                    invoice={inv}
                                    overridden={overridePushIds.has(inv.id)}
                                    onOpen={() => setActiveId(inv.id)}
                                    onRemove={() => removeInvoice(inv.id)}
                                    onRetry={() => pushInvoice(inv.id)}
                                    onOverride={() => overridePush(inv.id)}
                                />
                            ))}
                        </div>
                    ) : (
                        <InvoiceList
                            invoices={invoices}
                            overridePushIds={overridePushIds}
                            onOpen={(id) => setActiveId(id)}
                            onRemove={removeInvoice}
                            onRetry={pushInvoice}
                            onOverride={overridePush}
                        />
                    )}
                    <DraftsPanel
                        open={draftsPanelOpen}
                        onToggle={() => setDraftsPanelOpen((o) => !o)}
                        loadList={() => xeroApi.drafts.list({ flow: "sales", tenantId: company })}
                        loadDraft={(id) => xeroApi.drafts.get(id)}
                        deleteDraft={(id) => xeroApi.drafts.delete(id)}
                        refreshKey={draftsRefreshKey}
                        activeDraftId={draftId}
                        onOpen={handleOpenDraft}
                        onDelete={handleDraftDeleted}
                    />
                    {isDragging && (
                        <div className="fixed inset-0 bg-indigo-500/10 border-4 border-dashed border-indigo-400 pointer-events-none z-40" />
                    )}
                </div>
                {RulesModal}
            </div>
        );
    }

    // ══════════════════════════════════════════════════════════════
    // BRANCH 3: editor for active invoice
    // ══════════════════════════════════════════════════════════════
    return (
        <div className="h-screen flex flex-col bg-gray-50 text-gray-900">
            {TopBar}
            {ErrorBanner}

            <div className="bg-white border-b border-gray-200 px-6 py-2.5 flex items-center justify-between flex-shrink-0 flex-wrap gap-3">
                <div className="flex items-center gap-3 min-w-0">
                    <button
                        onClick={() => setActiveId(null)}
                        className="p-1.5 hover:bg-gray-100 rounded-lg transition-colors"
                        title="Back to all invoices"
                    >
                        <ArrowLeft className="h-4 w-4 text-gray-600" />
                    </button>
                    <FileText className="h-4 w-4 text-gray-400 flex-shrink-0" />
                    <h2 className="text-sm font-semibold text-gray-900 truncate">{activeInvoice.filename}</h2>
                    <InvoiceStatusBadge invoice={activeInvoice} />
                </div>
                <div className="flex items-center gap-2 text-xs text-gray-500">
                    {invoices.length > 1 && (
                        <span>
                            Invoice {invoices.findIndex((inv) => inv.id === activeId) + 1} of {invoices.length}
                        </span>
                    )}
                </div>
            </div>

            <div className="flex-1 min-h-0 p-6 pt-3">
                <PanelGroup direction="horizontal">
                    <Panel defaultSize={45} minSize={25}>
                        <div className="h-full pr-2">
                            {activeInvoice.pdfData ? (
                                <div className="h-full bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden">
                                    <PdfViewer pdfData={activeInvoice.pdfData} />
                                </div>
                            ) : (
                                <div className="flex flex-col items-center justify-center h-full bg-white rounded-xl shadow-sm border border-gray-200 text-gray-500 text-sm gap-2 p-8">
                                    <FileText className="h-10 w-10 text-gray-300" />
                                    <div className="font-medium text-gray-700">No preview available</div>
                                </div>
                            )}
                        </div>
                    </Panel>

                    <PanelResizeHandle className="w-1 bg-gray-200 hover:bg-indigo-400 transition-colors rounded-full mx-1" />

                    <Panel defaultSize={55} minSize={30}>
                        <div className="h-full pl-2 overflow-auto">
                            {activeInvoice.status === "pending" || activeInvoice.status === "processing" ? (
                                <div className="flex flex-col items-center justify-center h-full bg-white rounded-xl shadow-sm border border-gray-200 text-gray-600 gap-3 p-8">
                                    <Loader2 className="h-8 w-8 animate-spin text-indigo-500" />
                                    <div className="text-sm font-medium text-gray-700">Extracting invoice…</div>
                                    <div className="text-xs text-gray-500">Reading PDF and matching to Xero customers + items.</div>
                                </div>
                            ) : activeInvoice.status === "failed" ? (
                                <div className="flex flex-col items-center justify-center h-full bg-white rounded-xl shadow-sm border border-gray-200 gap-3 p-8 text-center">
                                    <AlertTriangle className="h-10 w-10 text-red-300" />
                                    <div className="text-sm font-medium text-red-700">Extraction failed</div>
                                    <div className="text-xs text-gray-500 max-w-md">{activeInvoice.error || "Unknown error"}</div>
                                </div>
                            ) : result ? (
                                <EditPanel
                                    result={result}
                                    customerNames={customerNames}
                                    itemNames={itemNames}
                                    updateCustomer={updateCustomer}
                                    updateLine={updateLine}
                                    onPush={onPush}
                                    pushing={pushing}
                                    pushStatus={activeInvoice.pushStatus}
                                    pushError={activeInvoice.pushError}
                                    duplicateCheck={activeInvoice?.result?.duplicate_check}
                                    duplicateBlocked={isBlockedDuplicate(activeInvoice)}
                                    duplicateInBatch={batchDuplicates.has(activeInvoice?.id)}
                                    onOverrideDuplicate={() => overridePush(activeInvoice.id)}
                                    onPickCustomerCandidate={onPickCustomerCandidate}
                                    onPickItemCandidate={onPickItemCandidate}
                                    onCreateCustomer={(prefill) => setCreateModal({ entityType: "customer", prefillName: prefill })}
                                    onCreateItem={(prefill, idx) => setCreateModal({ entityType: "item", prefillName: prefill, targetLineIdx: idx })}
                                    onOpenBulk={() => setBulkOpen(true)}
                                    unmatchedCount={gatherUnmatchedForBulk().length}
                                    tenantId={company}
                                />
                            ) : null}
                            {activeInvoice.pushResult && (
                                <div className="bg-emerald-50 border border-emerald-200 rounded-xl p-4 mt-3">
                                    <div className="flex items-center gap-2 mb-2">
                                        <CheckCircle2 className="h-4 w-4 text-emerald-600" />
                                        <h2 className="text-sm font-semibold text-emerald-800 uppercase tracking-wider">Pushed successfully</h2>
                                    </div>
                                    <pre className="text-xs overflow-auto max-h-64 bg-white border border-emerald-200 rounded-lg p-3 text-gray-700">{JSON.stringify(activeInvoice.pushResult, null, 2)}</pre>
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
                tenantId={company}
                onClose={() => setCreateModal(null)}
                onCreated={onCreatedFromModal}
            />
            <BulkCreateModal
                open={bulkOpen}
                items={gatherUnmatchedForBulk()}
                tenantId={company}
                onClose={() => setBulkOpen(false)}
                onComplete={onBulkComplete}
            />
            {RulesModal}
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

function InvoiceStatusBadge({ invoice }) {
    const { status, pushStatus } = invoice;
    if (pushStatus === "pushed") {
        return (
            <span className="inline-flex items-center gap-1 px-2 py-0.5 text-[11px] font-medium rounded-full bg-emerald-50 border border-emerald-200 text-emerald-700">
                <CheckCircle2 className="h-3 w-3" />
                Pushed
            </span>
        );
    }
    if (pushStatus === "pushing") {
        return (
            <span className="inline-flex items-center gap-1 px-2 py-0.5 text-[11px] font-medium rounded-full bg-indigo-50 border border-indigo-200 text-indigo-700">
                <Loader2 className="h-3 w-3 animate-spin" />
                Pushing
            </span>
        );
    }
    if (pushStatus === "failed") {
        return (
            <span className="inline-flex items-center gap-1 px-2 py-0.5 text-[11px] font-medium rounded-full bg-red-50 border border-red-200 text-red-700">
                <AlertTriangle className="h-3 w-3" />
                Push failed
            </span>
        );
    }
    if (status === "processing" || status === "pending") {
        return (
            <span className="inline-flex items-center gap-1 px-2 py-0.5 text-[11px] font-medium rounded-full bg-gray-100 border border-gray-200 text-gray-600">
                <Loader2 className="h-3 w-3 animate-spin" />
                {status === "pending" ? "Queued" : "Extracting"}
            </span>
        );
    }
    if (status === "failed") {
        return (
            <span className="inline-flex items-center gap-1 px-2 py-0.5 text-[11px] font-medium rounded-full bg-red-50 border border-red-200 text-red-700">
                <AlertTriangle className="h-3 w-3" />
                Failed
            </span>
        );
    }
    if (status === "ready") {
        return (
            <span className="inline-flex items-center gap-1 px-2 py-0.5 text-[11px] font-medium rounded-full bg-amber-50 border border-amber-200 text-amber-700">
                <Clock className="h-3 w-3" />
                Ready
            </span>
        );
    }
    return null;
}

function InvoiceCard({ invoice, overridden, onOpen, onRemove, onRetry, onOverride }) {
    const total = invoice.result?.extracted?.total;
    const customer = invoice.result?.extracted?.vendor_or_customer || invoice.result?.customer_match?.name;
    const lineCount = invoice.result?.line_review?.length || 0;
    const needsReview = invoice.result?.needs_review;
    const isOpenable = invoice.status !== "pending";
    const dup = invoice.result?.duplicate_check;
    const blockedDup = dup?.is_duplicate && !overridden && invoice.pushStatus !== "pushed";
    return (
        <div className="group bg-white rounded-xl shadow-sm border border-gray-200 hover:border-indigo-300 hover:shadow-md transition-all overflow-hidden flex flex-col">
            <button
                onClick={isOpenable ? onOpen : undefined}
                disabled={!isOpenable}
                className="px-4 pt-4 pb-3 text-left flex-1 disabled:cursor-default"
            >
                <div className="flex items-start justify-between gap-2 mb-2">
                    <FileText className="h-4 w-4 text-gray-400 flex-shrink-0 mt-0.5" />
                    <span className="text-sm font-medium text-gray-900 truncate flex-1" title={invoice.filename}>
                        {invoice.filename}
                    </span>
                </div>
                <div className="mb-3 flex items-center gap-1.5 flex-wrap">
                    <InvoiceStatusBadge invoice={invoice} />
                    <DuplicateBadge check={dup} />
                </div>
                {invoice.status === "ready" && (
                    <div className="space-y-1.5 text-xs">
                        {customer && (
                            <div className="flex items-center gap-1.5 text-gray-700 truncate">
                                <User className="h-3 w-3 text-gray-400 flex-shrink-0" />
                                <span className="truncate" title={customer}>{customer}</span>
                            </div>
                        )}
                        {total != null && (
                            <div className="flex items-center gap-1.5 text-gray-700">
                                <DollarSign className="h-3 w-3 text-gray-400" />
                                <span className="font-mono">{Number(total).toFixed(2)}</span>
                            </div>
                        )}
                        <div className="flex items-center gap-1.5 text-gray-500">
                            <Layers className="h-3 w-3 text-gray-400" />
                            <span>{lineCount} line{lineCount === 1 ? "" : "s"}</span>
                            {needsReview && <span className="text-amber-600">· needs review</span>}
                        </div>
                    </div>
                )}
                {invoice.status === "failed" && (
                    <p className="text-xs text-red-600 line-clamp-2" title={invoice.error}>
                        {invoice.error || "Extraction failed"}
                    </p>
                )}
                {invoice.pushStatus === "failed" && (
                    <p className="text-xs text-red-600 mt-2 line-clamp-2" title={invoice.pushError}>
                        Push: {invoice.pushError || "failed"}
                    </p>
                )}
            </button>
            <div className="px-3 py-2 border-t border-gray-100 bg-gray-50 flex items-center justify-between gap-2">
                <div className="flex items-center gap-1">
                    {blockedDup && (
                        <button
                            onClick={(e) => { e.stopPropagation?.(); onOverride?.(); }}
                            className="text-[11px] font-medium text-amber-700 hover:text-amber-900 hover:bg-amber-100 px-2 py-1 rounded inline-flex items-center gap-1 transition-colors"
                            title="Push this invoice even though it looks like a duplicate"
                        >
                            Push anyway
                        </button>
                    )}
                    {invoice.pushStatus === "failed" && (
                        <button
                            onClick={onRetry}
                            className="text-[11px] font-medium text-red-700 hover:text-red-900 hover:bg-red-50 px-2 py-1 rounded inline-flex items-center gap-1 transition-colors"
                        >
                            <RefreshCw className="h-3 w-3" />
                            Retry
                        </button>
                    )}
                </div>
                <button
                    onClick={onRemove}
                    className="text-[11px] text-gray-500 hover:text-red-600 hover:bg-red-50 px-2 py-1 rounded inline-flex items-center gap-1 transition-colors opacity-0 group-hover:opacity-100"
                    title="Remove from queue"
                >
                    <Trash2 className="h-3 w-3" />
                </button>
            </div>
        </div>
    );
}

function InvoiceList({ invoices, overridePushIds = new Set(), onOpen, onRemove, onRetry, onOverride }) {
    return (
        <div className="bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden">
            <div className="overflow-x-auto">
                <table className="w-full text-sm">
                    <thead className="bg-gray-50">
                        <tr>
                            <th className="px-3 py-2.5 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">File</th>
                            <th className="px-3 py-2.5 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Status</th>
                            <th className="px-3 py-2.5 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Customer</th>
                            <th className="px-3 py-2.5 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">Total</th>
                            <th className="px-3 py-2.5 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Lines</th>
                            <th className="px-3 py-2.5"></th>
                        </tr>
                    </thead>
                    <tbody>
                        {invoices.map((inv) => {
                            const customer = inv.result?.extracted?.vendor_or_customer || inv.result?.customer_match?.name;
                            const total = inv.result?.extracted?.total;
                            const isOpenable = inv.status !== "pending";
                            return (
                                <tr
                                    key={inv.id}
                                    onClick={isOpenable ? () => onOpen(inv.id) : undefined}
                                    className={`border-t border-gray-100 hover:bg-gray-50 ${isOpenable ? "cursor-pointer" : ""}`}
                                >
                                    <td className="px-3 py-2 text-sm text-gray-900 max-w-xs truncate" title={inv.filename}>
                                        <span className="inline-flex items-center gap-1.5">
                                            <FileText className="h-3.5 w-3.5 text-gray-400 flex-shrink-0" />
                                            {inv.filename}
                                        </span>
                                    </td>
                                    <td className="px-3 py-2">
                                        <div className="flex items-center gap-1.5 flex-wrap">
                                            <InvoiceStatusBadge invoice={inv} />
                                            <DuplicateBadge check={inv.result?.duplicate_check} />
                                        </div>
                                    </td>
                                    <td className="px-3 py-2 text-sm text-gray-700 truncate max-w-xs" title={customer || ""}>{customer || "—"}</td>
                                    <td className="px-3 py-2 text-sm text-right font-mono text-gray-900">
                                        {total != null ? Number(total).toFixed(2) : "—"}
                                    </td>
                                    <td className="px-3 py-2 text-sm text-gray-600">{inv.result?.line_review?.length ?? "—"}</td>
                                    <td className="px-3 py-2 text-right">
                                        {inv.result?.duplicate_check?.is_duplicate && !overridePushIds.has(inv.id) && inv.pushStatus !== "pushed" && (
                                            <button
                                                onClick={(e) => { e.stopPropagation(); onOverride?.(inv.id); }}
                                                className="text-[11px] font-medium text-amber-700 hover:text-amber-900 hover:underline mr-3"
                                                title="Push anyway"
                                            >
                                                Push anyway
                                            </button>
                                        )}
                                        {inv.pushStatus === "failed" && (
                                            <button
                                                onClick={(e) => { e.stopPropagation(); onRetry(inv.id); }}
                                                className="text-[11px] font-medium text-red-700 hover:text-red-900 hover:underline mr-3 inline-flex items-center gap-1"
                                            >
                                                <RefreshCw className="h-3 w-3" /> Retry
                                            </button>
                                        )}
                                        <button
                                            onClick={(e) => { e.stopPropagation(); onRemove(inv.id); }}
                                            className="text-[11px] text-gray-500 hover:text-red-600 hover:underline inline-flex items-center gap-1"
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
    result, customerNames, itemNames,
    updateCustomer, updateLine, onPush, pushing, pushStatus, pushError,
    duplicateCheck, duplicateBlocked, duplicateInBatch, onOverrideDuplicate,
    onPickCustomerCandidate, onPickItemCandidate,
    onCreateCustomer, onCreateItem, tenantId,
    onOpenBulk, unmatchedCount,
}) {
    return (
        <div className="space-y-5">
            {/* Header card */}
            <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-5">
                <div className="flex items-center gap-2 mb-4">
                    <FileText className="h-4 w-4 text-gray-500" />
                    <h2 className="text-sm font-semibold text-gray-700 uppercase tracking-wider">Header</h2>
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div>
                        <label className={labelBase}>
                            <User className="inline h-3.5 w-3.5 mr-1 text-gray-400" />
                            Customer
                        </label>
                        <AISuggestedDropdown
                            options={customerNames}
                            value={result?.xero_payload?.customer_name || ""}
                            onChange={updateCustomer}
                            placeholder="Pick customer…"
                            aiSuggestion={aiSuggestionFromEntityMatch(result?.customer_match, "customer")}
                            erpLabel="Xero"
                            onOverride={(picked, ai) => xeroApi.recordSuggestionFeedback({
                                tenant_id: tenantId, slot: "party",
                                context_text: result?.extracted?.vendor_or_customer,
                                suggested_id: result?.customer_match?.list_id,
                                suggested_name: ai?.recommended_ledger,
                                suggested_confidence: ai?.confidence,
                                suggested_method: result?.customer_match?.method,
                                picked_name: picked,
                            })}
                        />
                    </div>
                    <Field icon={Calendar} label="Date" value={result?.extracted?.doc_date} />
                    <Field icon={Hash} label="Doc #" value={result?.extracted?.doc_number} />
                    <Field icon={DollarSign} label="Total" value={result?.extracted?.total} />
                </div>
                {!result?.customer_match?.matched && (
                    <div className="mt-4 space-y-2">
                        <div className="flex items-start gap-2 bg-amber-50 border border-amber-200 text-amber-800 px-3 py-2 rounded-lg text-xs">
                            <AlertTriangle className="h-3.5 w-3.5 flex-shrink-0 mt-0.5 text-amber-600" />
                            <span>No matching Xero customer found. Pick one above, choose a suggestion, or create new.</span>
                        </div>
                        <MatchPicker
                            topCandidates={result?.customer_match?.top_candidates || []}
                            extractedName={result?.extracted?.vendor_or_customer || result?.customer_match?.candidate_name || ""}
                            onPickExisting={onPickCustomerCandidate}
                            onCreateNew={() => onCreateCustomer(result?.extracted?.vendor_or_customer || result?.customer_match?.candidate_name || "")}
                            label="customer"
                        />
                    </div>
                )}
            </div>

            {/* Line items card */}
            <div className="bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden">
                <div className="px-5 py-3 border-b border-gray-200 flex items-center justify-between">
                    <div className="flex items-center gap-2">
                        <Layers className="h-4 w-4 text-gray-500" />
                        <h2 className="text-sm font-semibold text-gray-700 uppercase tracking-wider">
                            Line items {result.line_review?.length ? <span className="text-gray-400 normal-case font-medium">({result.line_review.length})</span> : null}
                        </h2>
                    </div>
                    {unmatchedCount > 0 && (
                        <button
                            onClick={onOpenBulk}
                            className="inline-flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-lg border border-emerald-300 bg-emerald-50 text-emerald-700 hover:bg-emerald-100 transition-colors"
                            title="Create all missing master data in one batch"
                        >
                            <Layers className="h-3 w-3" />
                            Create all missing ({unmatchedCount})
                        </button>
                    )}
                </div>
                <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                        <thead className="bg-gray-50">
                            <tr>
                                <th className="px-3 py-2.5 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Description</th>
                                <th className="px-3 py-2.5 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Qty</th>
                                <th className="px-3 py-2.5 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Rate</th>
                                <th className="px-3 py-2.5 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Amount</th>
                                <th className="px-3 py-2.5 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Item</th>
                                <th className="px-3 py-2.5 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Conf.</th>
                            </tr>
                        </thead>
                        <tbody>
                            {(result.line_review || []).map((row, i) => (
                                <tr key={i} className="border-t border-gray-100 align-top hover:bg-gray-50">
                                    <td className="px-3 py-2">
                                        <input
                                            className={inputBase}
                                            defaultValue={row.description || ""}
                                            onBlur={(e) => updateLine(i, { description: e.target.value })}
                                        />
                                    </td>
                                    <td className="px-3 py-2">
                                        <input
                                            className={`${inputBase} w-20 font-mono text-right`}
                                            type="number"
                                            step="0.01"
                                            defaultValue={row.qty ?? 1}
                                            onBlur={(e) => updateLine(i, { qty: e.target.value })}
                                        />
                                    </td>
                                    <td className="px-3 py-2">
                                        <input
                                            className={`${inputBase} w-24 font-mono text-right`}
                                            type="number"
                                            step="0.01"
                                            defaultValue={row.rate ?? 0}
                                            onBlur={(e) => updateLine(i, { rate: e.target.value })}
                                        />
                                    </td>
                                    <td className="px-3 py-2">
                                        <input
                                            className={`${inputBase} w-24 font-mono text-right`}
                                            type="number"
                                            step="0.01"
                                            defaultValue={row.amount ?? 0}
                                            onBlur={(e) => updateLine(i, { amount: e.target.value })}
                                        />
                                    </td>
                                    <td className="px-3 py-2">
                                        <SearchableDropdown
                                            options={itemNames}
                                            value={row.match?.name || ""}
                                            onChange={(name) => updateLine(i, { itemName: name })}
                                            placeholder="Pick item…"
                                        />
                                        {!row.match?.matched && (
                                            <div className="mt-1.5">
                                                <MatchPicker
                                                    topCandidates={row.match?.top_candidates || []}
                                                    extractedName={row.description || row.match?.candidate_name || ""}
                                                    onPickExisting={(cand) => onPickItemCandidate(i, cand)}
                                                    onCreateNew={() => onCreateItem(row.description || row.match?.candidate_name || "", i)}
                                                    label="item"
                                                />
                                            </div>
                                        )}
                                    </td>
                                    <td className="px-3 py-2">
                                        {row.match?.confidence != null && (
                                            <ConfidenceBadge
                                                confidence={row.match.confidence}
                                                size="sm"
                                                showPercentage={false}
                                            />
                                        )}
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>

                {/* Totals — make tax + discount visible. NOTE: Xero line amounts
                    post pre-tax; these figures come from the extracted document
                    for review. */}
                {result.extracted && (result.extracted.tax_total != null || result.extracted.total != null) && (
                    <div className="px-3 py-3 border-t border-gray-100 flex justify-end">
                        <div className="w-72 text-sm space-y-1">
                            <div className="flex justify-between text-gray-600">
                                <span>Subtotal</span>
                                <span className="font-mono">{fmtAmount(result.extracted.subtotal, result.extracted.currency)}</span>
                            </div>
                            {result.extracted.discount_total ? (
                                <div className="flex justify-between text-gray-600">
                                    <span>Discount</span>
                                    <span className="font-mono">−{fmtAmount(result.extracted.discount_total, result.extracted.currency)}</span>
                                </div>
                            ) : null}
                            <div className="flex justify-between text-gray-600">
                                <span>Tax{result.extracted.tax_total ? "" : " (none detected)"}</span>
                                <span className="font-mono">{fmtAmount(result.extracted.tax_total ?? 0, result.extracted.currency)}</span>
                            </div>
                            <div className="flex justify-between font-semibold text-gray-900 border-t border-gray-200 pt-1">
                                <span>Total</span>
                                <span className="font-mono">{fmtAmount(result.extracted.total, result.extracted.currency)}</span>
                            </div>
                            {result.extracted.tax_total ? (
                                <p className="text-[11px] text-amber-600 pt-1 leading-snug">
                                    Tax is shown for review. Line amounts post to Xero pre-tax; apply the tax rate in Xero if your organization uses GST / VAT.
                                </p>
                            ) : null}
                        </div>
                    </div>
                )}
            </div>

            {/* Sticky push bar */}
            <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-4 flex items-center gap-3 flex-wrap">
                <button
                    onClick={onPush}
                    disabled={pushing || !result?.xero_payload || pushStatus === "pushed" || duplicateBlocked}
                    className={btnXeroGreen}
                    title={duplicateBlocked ? "This invoice looks like a duplicate. Use \"Push anyway\" to override." : undefined}
                >
                    {pushing ? <Loader2 className="h-4 w-4 animate-spin" /> : pushStatus === "pushed" ? <CheckCircle2 className="h-4 w-4" /> : <Send className="h-4 w-4" />}
                    {pushing ? "Pushing to Xero…" : pushStatus === "pushed" ? "Pushed" : "Push to Xero"}
                </button>
                {duplicateBlocked && (
                    <>
                        {duplicateInBatch && !duplicateCheck?.is_duplicate ? (
                            <span className="inline-flex items-center gap-1 px-2 py-0.5 text-[11px] font-medium rounded-full bg-amber-50 border border-amber-200 text-amber-700" title="Another file in this upload has the same document number and customer.">
                                <AlertTriangle className="h-3 w-3" />Duplicate of another file in this batch
                            </span>
                        ) : (
                            <DuplicateBadge check={duplicateCheck} size="md" />
                        )}
                        <button
                            onClick={onOverrideDuplicate}
                            className="inline-flex items-center gap-1.5 px-3 py-2 text-sm font-medium text-amber-800 bg-amber-50 border border-amber-300 rounded-lg hover:bg-amber-100 transition-colors"
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
                    <span className="inline-flex items-center gap-1 px-2 py-0.5 text-[11px] font-medium rounded-full bg-amber-50 border border-amber-200 text-amber-700">
                        <AlertTriangle className="h-3 w-3" />
                        Some matches were uncertain — review before pushing.
                    </span>
                )}
                {pushStatus === "failed" && pushError && (
                    <span className="inline-flex items-center gap-1 text-xs text-red-700">
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
                {Icon && <Icon className="inline h-3.5 w-3.5 mr-1 text-gray-400" />}
                {label}
            </label>
            <div className="text-sm text-gray-900 px-3 py-2 bg-gray-50 border border-gray-200 rounded-lg min-h-[36px]">
                {value ?? <span className="text-gray-400">—</span>}
            </div>
        </div>
    );
}

/**
 * MappingRulesModal — self-contained rules manager wired to xeroApi.mappingRules.
 *
 * The shared common/MappingRulesPanel is hard-coupled to tallyApi internally, so
 * we keep a lightweight Tailwind/portal version here (matching this file's look)
 * that talks to the Xero rules endpoints. Rules auto-map a line/party to a Xero
 * account/customer by keyword/pattern/vendor/amount.
 */
export function MappingRulesModal({ tenantId, documentType, onClose }) {
    const [rules, setRules] = useState([]);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState("");
    const [saving, setSaving] = useState(false);
    const [draft, setDraft] = useState({ rule_type: "keyword", pattern: "", account_slot: "expense", target_name: "" });

    const load = async () => {
        if (!tenantId) return;
        setLoading(true);
        setError("");
        try {
            const res = await xeroApi.mappingRules.list({ tenant_id: tenantId, document_type: documentType });
            setRules(res?.rules || res || []);
        } catch (e) {
            setError(e?.response?.data?.detail || e.message || "Failed to load rules");
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => { load(); }, [tenantId, documentType]); // eslint-disable-line react-hooks/exhaustive-deps

    const addRule = async () => {
        if (!draft.pattern.trim() || !draft.target_name.trim()) return;
        setSaving(true);
        setError("");
        try {
            await xeroApi.mappingRules.create({
                tenant_id: tenantId,
                document_type: documentType,
                rule_type: draft.rule_type,
                pattern: draft.pattern.trim(),
                account_slot: draft.account_slot,
                target_name: draft.target_name.trim(),
            });
            setDraft({ rule_type: "keyword", pattern: "", account_slot: "expense", target_name: "" });
            await load();
        } catch (e) {
            setError(e?.response?.data?.detail || e.message || "Failed to create rule");
        } finally {
            setSaving(false);
        }
    };

    const removeRule = async (id) => {
        try {
            await xeroApi.mappingRules.remove(id);
            await load();
        } catch (e) {
            setError(e?.response?.data?.detail || e.message || "Failed to delete rule");
        }
    };

    return createPortal(
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
            <div className="bg-white rounded-xl shadow-xl border border-gray-200 w-full max-w-2xl max-h-[85vh] overflow-hidden flex flex-col" onClick={(e) => e.stopPropagation()}>
                <div className="px-5 py-3 border-b border-gray-200 flex items-center justify-between">
                    <div className="flex items-center gap-2">
                        <Shapes className="h-4 w-4 text-indigo-500" />
                        <h2 className="text-sm font-semibold text-gray-900">
                            Mapping Rules — {documentType === "purchase_invoice" ? "Purchase" : "Sales"}
                        </h2>
                    </div>
                    <button onClick={onClose} className="p-1.5 hover:bg-gray-100 rounded-lg transition-colors" title="Close">
                        <X className="h-4 w-4 text-gray-600" />
                    </button>
                </div>
                <div className="p-5 overflow-auto space-y-4">
                    {error && (
                        <div className="flex items-start gap-2 bg-red-50 border border-red-200 text-red-700 px-3 py-2 rounded-lg text-xs">
                            <AlertTriangle className="h-3.5 w-3.5 flex-shrink-0 mt-0.5" />
                            <span>{error}</span>
                        </div>
                    )}
                    {/* New rule editor */}
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                        <div>
                            <label className={labelBase}>Rule type</label>
                            <select className={inputBase} value={draft.rule_type} onChange={(e) => setDraft((d) => ({ ...d, rule_type: e.target.value }))}>
                                <option value="keyword">Keyword</option>
                                <option value="pattern">Pattern (regex)</option>
                                <option value="vendor">Vendor</option>
                                <option value="amount">Amount</option>
                            </select>
                        </div>
                        <div>
                            <label className={labelBase}>Slot</label>
                            <select className={inputBase} value={draft.account_slot} onChange={(e) => setDraft((d) => ({ ...d, account_slot: e.target.value }))}>
                                <option value="party">{documentType === "purchase_invoice" ? "Vendor (party)" : "Customer (party)"}</option>
                                <option value="expense">{documentType === "purchase_invoice" ? "Expense account" : "Item / revenue"}</option>
                            </select>
                        </div>
                        <div>
                            <label className={labelBase}>Match text</label>
                            <input className={inputBase} value={draft.pattern} placeholder="e.g. consulting" onChange={(e) => setDraft((d) => ({ ...d, pattern: e.target.value }))} />
                        </div>
                        <div>
                            <label className={labelBase}>Maps to (Xero name)</label>
                            <input className={inputBase} value={draft.target_name} placeholder="e.g. Consulting Revenue" onChange={(e) => setDraft((d) => ({ ...d, target_name: e.target.value }))} />
                        </div>
                    </div>
                    <button
                        onClick={addRule}
                        disabled={saving || !draft.pattern.trim() || !draft.target_name.trim()}
                        className="inline-flex items-center gap-1.5 px-3 py-2 text-sm font-semibold text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 transition-colors disabled:opacity-50"
                    >
                        {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
                        Add rule
                    </button>

                    {/* Existing rules */}
                    <div className="border-t border-gray-100 pt-3">
                        {loading ? (
                            <div className="flex items-center gap-2 text-sm text-gray-500"><Loader2 className="h-4 w-4 animate-spin" /> Loading rules…</div>
                        ) : rules.length === 0 ? (
                            <p className="text-sm text-gray-400">No rules yet. Add one above to auto-map future invoices.</p>
                        ) : (
                            <ul className="space-y-2">
                                {rules.map((r) => (
                                    <li key={r.rule_id || r.id} className="flex items-center justify-between gap-3 px-3 py-2 bg-gray-50 border border-gray-200 rounded-lg text-sm">
                                        <span className="truncate">
                                            <span className="font-medium text-gray-900">{r.rule_type || "keyword"}</span>
                                            <span className="text-gray-500"> · “{r.pattern || r.match_text}” → </span>
                                            <span className="text-indigo-700">{r.target_name || r.target}</span>
                                        </span>
                                        <button
                                            onClick={() => removeRule(r.rule_id || r.id)}
                                            className="text-[11px] text-gray-500 hover:text-red-600 inline-flex items-center gap-1"
                                            title="Delete rule"
                                        >
                                            <Trash2 className="h-3.5 w-3.5" />
                                        </button>
                                    </li>
                                ))}
                            </ul>
                        )}
                    </div>
                </div>
            </div>
        </div>,
        document.body,
    );
}
