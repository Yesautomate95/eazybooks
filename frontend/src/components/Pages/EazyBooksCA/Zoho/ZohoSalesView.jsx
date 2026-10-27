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
import { zohoApi } from "../../../../services/api";
import { formatZohoError } from "./components/zohoError";
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

// Adapt a Zoho backend entity match (customer/vendor) into the AISuggestedDropdown
// `aiSuggestion` shape. Only surfaces a suggestion for an actual backend match,
// so the auto-fill + reasoning chip reflects real AI matching.
const aiSuggestionFromEntityMatch = (m, noun = "record") => {
    if (!m || !m.matched || !m.name || m.confidence == null) return null;
    return {
        recommended_ledger: m.name,
        confidence: m.confidence,
        match_type: "fuzzy",
        reasoning: `Matched to an existing Zoho Books ${noun}`,
    };
};

// Shared class recipes so Purchase/Sales/BankEntries stay visually consistent.
const inputBase = "w-full border border-gray-300 rounded-lg px-3 py-2 text-sm text-gray-900 bg-white focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 outline-none transition-shadow";
const labelBase = "block text-xs font-medium text-gray-600 mb-1.5";
const tableNumInput = "px-2 py-1 text-sm text-right border border-gray-300 rounded text-gray-900 font-mono bg-white focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 outline-none transition-shadow tabular-nums";
const tableNumInputComputed = "px-2 py-1 text-sm text-right border border-emerald-300 rounded text-emerald-700 font-mono font-semibold bg-emerald-50 focus:ring-2 focus:ring-emerald-500 focus:border-emerald-500 outline-none transition-shadow tabular-nums";
const tableTextInput = "w-full px-2 py-1 text-sm border border-gray-300 rounded text-gray-900 bg-white focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 outline-none transition-shadow";
const btnSecondary = "inline-flex items-center gap-1.5 px-3 py-2 text-sm text-gray-700 bg-white border border-gray-300 rounded-lg hover:bg-gray-50 transition-colors disabled:opacity-50";
// Zoho-specific green for the actual "Push to Zoho Books" action — keeps the
// affordance familiar to Zoho users.
const btnZohoGreen = "inline-flex items-center gap-1.5 px-4 py-2 text-sm font-semibold text-white bg-[#2CA01C] rounded-lg hover:bg-[#268a18] transition-colors disabled:opacity-50";
const btnDanger = "inline-flex items-center gap-1.5 px-3 py-2 text-sm font-medium text-red-700 bg-red-50 border border-red-200 rounded-lg hover:bg-red-100 transition-colors disabled:opacity-50";

const PUSH_CONCURRENCY = 2; // be polite to Zoho; 2 in flight is plenty for the demo org

// Format a money amount for the review UI. Zoho serves IN/global orgs, so use the
// extracted currency (INR/USD/…) when present rather than a fixed symbol.
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

const num = (value, fallback = 0) => {
    const n = Number(value);
    return Number.isFinite(n) ? n : fallback;
};

const netSalesLineAmount = (row) => {
    const qty = num(row?.qty);
    const rate = num(row?.rate);
    const grossFromRate = qty * rate;
    const discountAmount = num(row?.discount_amount);
    const discountPercent = num(row?.discount_percent);
    const gross = Math.max(
        num(row?.gross_amount),
        grossFromRate,
        discountAmount > 0 ? num(row?.amount) + discountAmount : num(row?.amount),
        0,
    );
    const discount = discountAmount > 0 ? discountAmount : gross * (discountPercent / 100);
    if (discount > 0 || discountPercent > 0) return Math.max(gross - discount, 0);
    return num(row?.amount, gross);
};

const buildZohoInvoicePushPayload = (result) => {
    const payload = structuredClone(result?.zoho_payload || {});
    const rows = result?.line_review || [];
    payload.line_items = (payload.line_items || []).map((line, idx) => {
        const row = rows[idx] || {};
        if (!rows[idx]) return line;
        const qty = num(row.qty, num(line.quantity, 1)) || 1;
        const amount = netSalesLineAmount(row);
        return {
            ...line,
            description: row.description ?? line.description,
            quantity: qty,
            rate: qty ? amount / qty : amount,
        };
    });
    return payload;
};

/**
 * Zoho Books Sales Invoice flow — multi-invoice bulk upload.
 *
 * State model:
 *   invoices[]: { id, filename, file, pdfData, status, result, error,
 *                 pushStatus, pushError, pushResult }
 *   activeId: which invoice the editor pane is currently looking at
 *
 * Render branches:
 *   no invoices          → empty dropzone (drag PDFs or browse)
 *   invoices + no active  → grid/list of invoices with status badges
 *   activeId set          → existing PDF + EditPanel layout
 *
 * `setResult(updater)` is preserved so every existing handler keeps working —
 * it routes the patch onto the active invoice's result.
 */
export default function ZohoSalesView() {
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
                const res = await zohoApi.getCompanies();
                const list = res?.organizations || [];
                setCompanies(list);
                if (list[0]) setCompany(list[0].organization_id || list[0].name);
            } catch (err) {
                setError(formatZohoError(err, "Failed to load companies"));
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
                    zohoApi.getCustomers(company),
                    zohoApi.getItems(company),
                ]);
                setCustomers(c?.customers || []);
                setItems(i?.items || []);
            } catch (err) {
                setError(formatZohoError(err, "Failed to load master data"));
            } finally {
                setLoadingMasters(false);
            }
        })();
    }, [company]);

    const customerNames = useMemo(() => customers.map((c) => c.name).filter(Boolean).sort(), [customers]);
    const itemNames = useMemo(() => items.map((i) => i.name).filter(Boolean).sort(), [items]);
    const customerByName = useMemo(() => Object.fromEntries(customers.map((c) => [c.name, c])), [customers]);
    const itemByName = useMemo(() => Object.fromEntries(items.map((i) => [i.name, i])), [items]);

    const activeInvoice = invoices.find((b) => b.id === activeId) || null;
    const result = activeInvoice?.result || null;
    const pushing = activeInvoice?.pushStatus === "pushing";

    // ── Invoice state helpers ───────────────────────────────────────
    const patchInvoice = (id, patch) => {
        setInvoices((prev) => prev.map((b) => {
            if (b.id !== id) return b;
            const merge = typeof patch === "function" ? patch(b) : patch;
            return { ...b, ...merge };
        }));
    };

    // Drop-in replacement for the prior `setResult` — patches the active invoice.
    const setResult = (updater) => {
        if (!activeId) return;
        setInvoices((prev) => prev.map((b) => {
            if (b.id !== activeId) return b;
            const nextResult = typeof updater === "function" ? updater(b.result) : updater;
            return { ...b, result: nextResult };
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
        await runConcurrent(newInvoices, 2, async (invoice) => {
            patchInvoice(invoice.id, { status: "processing" });
            try {
                const res = await zohoApi.extractInvoice(invoice.file, company);
                // Sales extraction responses carry no `ok` flag — treat the
                // presence of a usable payload (what EditPanel reads) as ready.
                const ready = !!(res && (res.zoho_payload || res.line_review || res.extracted));
                patchInvoice(invoice.id, {
                    status: ready ? "ready" : "failed",
                    result: res,
                    error: ready ? null : (res?.error || "Extraction returned no result"),
                    // R2 metadata so a reopened draft can preview its source PDF
                    // (Phase 6). base64/blob URLs aren't persisted; r2_key survives.
                    r2_key: res?.r2_key || null,
                    file_url: res?.file_url || null,
                });
            } catch (err) {
                const msg = formatZohoError(err, "Extraction failed");
                patchInvoice(invoice.id, { status: "failed", error: typeof msg === "string" ? msg : JSON.stringify(msg) });
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
    // (doc number + customer) flag each other before either hits Zoho Books.
    // Mirrors Tally/QB findInBatchDuplicates; adapts the Zoho invoice shape.
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

    const isBlockedDuplicate = (invoice) => {
        if (!invoice) return false;
        if (batchDuplicates.has(invoice.id) && !overridePushIds.has(invoice.id)) return true;
        const dup = invoice?.result?.duplicate_check;
        if (!dup?.is_duplicate) return false;
        return !overridePushIds.has(invoice.id);
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
        const invoice = invoices.find((b) => b.id === id);
        if (!invoice?.result?.zoho_payload) return;
        if (invoice.pushStatus === "pushing" || invoice.pushStatus === "pushed") return;
        if (isBlockedDuplicate(invoice)) {
            // Caller should have called overridePush first; bail silently.
            return;
        }
        patchInvoice(id, { pushStatus: "pushing", pushError: null });
        try {
            const res = await zohoApi.pushInvoiceZoho({
                payload: buildZohoInvoicePushPayload(invoice.result),
                organization_id: company,
                extracted_total: invoice.result.extracted_total ?? invoice.result.extracted?.total,
                skip_duplicate_check: overridePushIds.has(id),
            });
            patchInvoice(id, { pushStatus: "pushed", pushResult: res, pushError: null });
        } catch (err) {
            const detail = err?.response?.data?.detail;
            const msg = typeof detail === "string"
                ? detail
                : detail?.message || detail?.zoho_error || detail?.zoho_detail || err.message || "Push failed";
            patchInvoice(id, { pushStatus: "failed", pushError: msg });
        }
    };

    const onPush = () => activeId && pushInvoice(activeId);

    const handlePushAll = async () => {
        // Skip duplicates by default; user can override individual rows via
        // "Push anyway" before bulk-pushing, or re-trigger after overriding.
        const ready = invoices.filter((b) =>
            b.status === "ready"
            && b.pushStatus !== "pushed"
            && b.pushStatus !== "pushing"
            && !isBlockedDuplicate(b)
        );
        if (ready.length === 0) return;
        setIsPushingAll(true);
        try {
            await runConcurrent(ready, PUSH_CONCURRENCY, (b) => pushInvoice(b.id));
        } finally {
            setIsPushingAll(false);
        }
    };

    const handleRetryFailed = async () => {
        const failed = invoices.filter((b) => b.pushStatus === "failed");
        if (failed.length === 0) return;
        setIsPushingAll(true);
        try {
            await runConcurrent(failed, PUSH_CONCURRENCY, (b) => pushInvoice(b.id));
        } finally {
            setIsPushingAll(false);
        }
    };

    const removeInvoice = (id) => {
        if (activeId === id) setActiveId(null);
        setInvoices((prev) => prev.filter((b) => b.id !== id));
    };

    // ── Drafts ─────────────────────────────────────────────────────
    // What gets persisted: just the data, not the file objects or blob URLs.
    // On reload the PDF preview pane shows "no preview available" but every
    // extracted/edited field survives.
    const serializeInvoicesForDraft = () => invoices.map((b) => ({
        id: b.id,
        filename: b.filename,
        status: b.status,
        result: b.result,
        error: b.error,
        pushStatus: b.pushStatus,
        pushError: b.pushError,
        pushResult: b.pushResult,
        // R2 metadata so a reopened draft can re-fetch the source PDF preview.
        r2_key: b.r2_key || null,
        file_url: b.file_url || null,
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
            // Stamp the draft row with the first invoice that has R2 metadata.
            // Per-invoice r2_keys stay inside the payload for multi-file drafts.
            const firstWithR2 = invoices.find((inv) => inv.r2_key);
            const res = await zohoApi.drafts.save({
                flow: "sales",
                payload: { invoices: serializeInvoicesForDraft(), activeId },
                title: deriveDraftTitle(),
                draftId,
                organizationId: company,
                fileUrl: firstWithR2?.file_url,
                fileName: firstWithR2?.file_name || firstWithR2?.filename,
                r2Key: firstWithR2?.r2_key,
            });
            if (res?.draft_id) setDraftId(res.draft_id);
            setDraftsRefreshKey((k) => k + 1);
        } catch (e) {
            setError(formatZohoError(e, "Save draft failed"));
        } finally {
            setSavingDraft(false);
        }
    };

    const handleOpenDraft = (draft) => {
        const payload = draft?.payload || {};
        const restoredInvoices = (payload.invoices || []).map((b) => ({
            ...b,
            // The raw file blob isn't persisted, but the R2-backed source PDF is
            // re-fetched lazily below (Phase 6 — source-doc preview on reopen).
            file: null,
            pdfData: null,
            r2_key: b.r2_key || null,
            file_url: b.file_url || null,
        }));
        setInvoices(restoredInvoices);
        setActiveId(payload.activeId && restoredInvoices.some((b) => b.id === payload.activeId)
            ? payload.activeId
            : null);
        setDraftId(draft.id);
        setDraftsPanelOpen(false);
        setError("");
        // Best-effort: resolve a short-lived presigned URL for each invoice that
        // carries an R2 key so the PDF preview pane shows the source document
        // instead of "No preview available". Never blocks or fails the reopen.
        restoredInvoices.forEach(async (b) => {
            if (!b.r2_key) return;
            try {
                const res = await zohoApi.drafts.getFileUrl(b.r2_key);
                if (res?.url) patchInvoice(b.id, { pdfData: res.url });
            } catch (_) {
                /* non-fatal — preview stays empty */
            }
        });
    };

    useDraftDeepLink({
        getDraft: zohoApi.drafts.get,
        companyField: "organization_id",
        setCompany,
        onOpen: handleOpenDraft,
    });

    const handleDraftDeleted = (id) => {
        if (draftId === id) setDraftId(null);
    };

    // ── Active-invoice mutators (unchanged from single-invoice flow) ─
    const updateCustomer = (name) => {
        const c = customerByName[name];
        if (!c) return;
        setResult((prev) => {
            const next = structuredClone(prev);
            next.zoho_payload.customer_id = c.list_id;
            next.zoho_payload.customer_name = c.name;
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
            next.zoho_payload = next.zoho_payload || {};
            next.zoho_payload.line_items = next.zoho_payload.line_items || [];
            next.line_review = next.line_review || [];
            const row = next.line_review[idx] || (next.line_review[idx] = {});
            const line = next.zoho_payload.line_items[idx] || (next.zoho_payload.line_items[idx] = {});

            const recomputeLineAmount = () => {
                const qty = Number(row.qty) || 0;
                const rate = Number(row.rate) || 0;
                const gross = qty * rate;
                const discAmt = Number(row.discount_amount) || 0;
                const discPct = Number(row.discount_percent) || 0;
                const discount = discAmt > 0 ? discAmt : gross * (discPct / 100);
                return Math.max(gross - discount, 0);
            };
            const syncLineRate = (amount) => {
                const qty = Number(row.qty) || Number(line.quantity) || 1;
                line.quantity = qty;
                line.rate = qty ? Number(amount || 0) / qty : Number(amount || 0);
            };

            if (patch.description !== undefined) {
                row.description = patch.description;
                line.description = patch.description;
                if (!line.name) line.name = patch.description;
            }
            if (patch.qty !== undefined) {
                row.qty = patch.qty === "" ? "" : Number(patch.qty);
                line.quantity = Number(patch.qty) || 0;
            }
            if (patch.rate !== undefined) {
                row.rate = patch.rate === "" ? "" : Number(patch.rate);
            }
            if (patch.discount_percent !== undefined) {
                row.discount_percent = patch.discount_percent === "" ? "" : Number(patch.discount_percent);
                if (row.discount_percent !== "" && Number(row.discount_amount) > 0) row.discount_amount = 0;
            }
            if (patch.discount_amount !== undefined) {
                row.discount_amount = patch.discount_amount === "" ? "" : Number(patch.discount_amount);
                if (row.discount_amount !== "" && Number(row.discount_amount) > 0) row.discount_percent = 0;
            }
            if (patch.amount !== undefined) {
                row.amount = patch.amount === "" ? "" : Number(patch.amount);
                syncLineRate(row.amount);
            } else if (
                patch.qty !== undefined
                || patch.rate !== undefined
                || patch.discount_percent !== undefined
                || patch.discount_amount !== undefined
            ) {
                const computed = recomputeLineAmount();
                row.amount = computed;
                syncLineRate(computed);
            }
            if (patch.itemName !== undefined) {
                const it = itemByName[patch.itemName];
                if (it) {
                    line.item_id = it.list_id;
                    line.name = it.name;
                    row.match = {
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

    // Edit the document header. Mirrors the human-friendly `extracted` view and
    // the Zoho payload that gets pushed (date / invoice_number / due_date).
    const updateHeader = (patch) => {
        setResult((prev) => {
            if (!prev) return prev;
            const next = structuredClone(prev);
            next.extracted = next.extracted || {};
            next.zoho_payload = next.zoho_payload || {};
            if (patch.doc_date !== undefined) {
                next.extracted.doc_date = patch.doc_date;
                next.zoho_payload.date = patch.doc_date;
            }
            if (patch.doc_number !== undefined) {
                next.extracted.doc_number = patch.doc_number;
                next.zoho_payload.invoice_number = patch.doc_number;
            }
            if (patch.due_date !== undefined) {
                next.extracted.due_date = patch.due_date;
                next.zoho_payload.due_date = patch.due_date;
            }
            return next;
        });
    };

    const addLine = () => {
        setResult((prev) => {
            if (!prev) return prev;
            const next = structuredClone(prev);
            next.zoho_payload = next.zoho_payload || {};
            next.zoho_payload.line_items = next.zoho_payload.line_items || [];
            next.line_review = next.line_review || [];
            next.zoho_payload.line_items.push({
                name: "",
                description: "",
                rate: 0,
                quantity: 1,
            });
            next.line_review.push({
                description: "",
                qty: 1,
                rate: 0,
                amount: 0,
                discount_percent: 0,
                discount_amount: 0,
                match: { matched: false, name: "" },
            });
            return next;
        });
    };

    const removeLine = (idx) => {
        setResult((prev) => {
            if (!prev) return prev;
            const next = structuredClone(prev);
            (next.zoho_payload?.line_items || []).splice(idx, 1);
            (next.line_review || []).splice(idx, 1);
            return next;
        });
    };

    // --- Tier 1/2/4 helpers ---------------------------------------------
    // setCustomerOnPayload patches both the Zoho payload and the local match
    // state without needing the entity to be in the dropdown by name first.
    // Used after inline-create and after Tier-2 candidate picks.
    const setCustomerOnPayload = (c, method = "manual") => {
        setResult((prev) => {
            const next = structuredClone(prev);
            next.zoho_payload.customer_id = c.list_id;
            next.zoho_payload.customer_name = c.name;
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
            const line = next.zoho_payload?.line_items?.[idx];
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
            await zohoApi.saveAlias({
                entity_type: "customer",
                alias_text: result?.extracted?.vendor_or_customer || result?.customer_match?.candidate_name || "",
                zoho_entity_id: cand.list_id,
                zoho_entity_name: cand.name,
                organization_id: company,
            });
        } catch (_) { /* non-fatal — alias is an optimization */ }
    };

    const onPickItemCandidate = async (idx, cand) => {
        setItemOnLine(idx, cand, "user_picked");
        try {
            await zohoApi.saveAlias({
                entity_type: "item",
                alias_text: result?.line_review?.[idx]?.description
                    || result?.line_review?.[idx]?.match?.candidate_name
                    || "",
                zoho_entity_id: cand.list_id,
                zoho_entity_name: cand.name,
                organization_id: company,
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
        const okResults = results.filter((r) => r.ok && r.zoho_entity_id);
        okResults.forEach((r) => {
            const entity = { list_id: String(r.zoho_entity_id), name: r.zoho_entity_name };
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
        processing: invoices.filter((b) => b.status === "processing" || b.status === "pending").length,
        ready: invoices.filter((b) => b.status === "ready" && b.pushStatus !== "pushed" && !isBlockedDuplicate(b)).length,
        failed: invoices.filter((b) => b.status === "failed").length,
        pushed: invoices.filter((b) => b.pushStatus === "pushed").length,
        pushFailed: invoices.filter((b) => b.pushStatus === "failed").length,
        duplicate: invoices.filter((b) => b.result?.duplicate_check?.is_duplicate && !overridePushIds.has(b.id) && b.pushStatus !== "pushed").length,
    }), [invoices, overridePushIds]); // eslint-disable-line react-hooks/exhaustive-deps

    const mastersReady = !loadingMasters && customers.length > 0;

    // ══════════════════════════════════════════════════════════════
    // Top bar — same across all branches
    // ══════════════════════════════════════════════════════════════
    const TopBar = (
        <PageHeader
            icon={FileSignature}
            title="Zoho Books · Sales Invoice"
            description="Upload sales invoice PDFs, review extracted lines, and push to Zoho Books."
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
                                const v = c.organization_id || c.name;
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
            organizationId={company}
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
                                    <Upload className="h-8 w-8 text-indigo-500" />
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
                                <li>Pick the Zoho Books organization above</li>
                                <li>Upload one or more sales invoice PDFs</li>
                                <li>Each invoice is extracted in parallel — review individually</li>
                                <li>Push them one by one, or "Push all" to send everything ready</li>
                            </ol>
                        </div>
                        <DraftsPanel
                            open={draftsPanelOpen}
                            onToggle={() => setDraftsPanelOpen((o) => !o)}
                            loadList={() => zohoApi.drafts.list({ flow: "sales", organizationId: company })}
                            loadDraft={(id) => zohoApi.drafts.get(id)}
                            deleteDraft={(id) => zohoApi.drafts.delete(id)}
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
                            className={btnZohoGreen}
                            title={counts.ready ? `Push the ${counts.ready} ready invoice${counts.ready === 1 ? "" : "s"} to Zoho Books` : "No ready invoices to push"}
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
                            {invoices.map((b) => (
                                <InvoiceCard
                                    key={b.id}
                                    invoice={b}
                                    overridden={overridePushIds.has(b.id)}
                                    onOpen={() => setActiveId(b.id)}
                                    onRemove={() => removeInvoice(b.id)}
                                    onRetry={() => pushInvoice(b.id)}
                                    onOverride={() => overridePush(b.id)}
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
                        loadList={() => zohoApi.drafts.list({ flow: "sales", organizationId: company })}
                        loadDraft={(id) => zohoApi.drafts.get(id)}
                        deleteDraft={(id) => zohoApi.drafts.delete(id)}
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
                            Invoice {invoices.findIndex((b) => b.id === activeId) + 1} of {invoices.length}
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
                                    <div className="text-xs text-gray-500">Reading PDF and matching to Zoho Books customers + items.</div>
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
                                    updateHeader={updateHeader}
                                    addLine={addLine}
                                    removeLine={removeLine}
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
                                    organizationId={company}
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
                organizationId={company}
                onClose={() => setCreateModal(null)}
                onCreated={onCreatedFromModal}
            />
            <BulkCreateModal
                open={bulkOpen}
                items={gatherUnmatchedForBulk()}
                organizationId={company}
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
                        {invoices.map((b) => {
                            const customer = b.result?.extracted?.vendor_or_customer || b.result?.customer_match?.name;
                            const total = b.result?.extracted?.total;
                            const isOpenable = b.status !== "pending";
                            return (
                                <tr
                                    key={b.id}
                                    onClick={isOpenable ? () => onOpen(b.id) : undefined}
                                    className={`border-t border-gray-100 hover:bg-gray-50 ${isOpenable ? "cursor-pointer" : ""}`}
                                >
                                    <td className="px-3 py-2 text-sm text-gray-900 max-w-xs truncate" title={b.filename}>
                                        <span className="inline-flex items-center gap-1.5">
                                            <FileText className="h-3.5 w-3.5 text-gray-400 flex-shrink-0" />
                                            {b.filename}
                                        </span>
                                    </td>
                                    <td className="px-3 py-2">
                                        <div className="flex items-center gap-1.5 flex-wrap">
                                            <InvoiceStatusBadge invoice={b} />
                                            <DuplicateBadge check={b.result?.duplicate_check} />
                                        </div>
                                    </td>
                                    <td className="px-3 py-2 text-sm text-gray-700 truncate max-w-xs" title={customer || ""}>{customer || "—"}</td>
                                    <td className="px-3 py-2 text-sm text-right font-mono text-gray-900">
                                        {total != null ? Number(total).toFixed(2) : "—"}
                                    </td>
                                    <td className="px-3 py-2 text-sm text-gray-600">{b.result?.line_review?.length ?? "—"}</td>
                                    <td className="px-3 py-2 text-right">
                                        {b.result?.duplicate_check?.is_duplicate && !overridePushIds.has(b.id) && b.pushStatus !== "pushed" && (
                                            <button
                                                onClick={(e) => { e.stopPropagation(); onOverride?.(b.id); }}
                                                className="text-[11px] font-medium text-amber-700 hover:text-amber-900 hover:underline mr-3"
                                                title="Push anyway"
                                            >
                                                Push anyway
                                            </button>
                                        )}
                                        {b.pushStatus === "failed" && (
                                            <button
                                                onClick={(e) => { e.stopPropagation(); onRetry(b.id); }}
                                                className="text-[11px] font-medium text-red-700 hover:text-red-900 hover:underline mr-3 inline-flex items-center gap-1"
                                            >
                                                <RefreshCw className="h-3 w-3" /> Retry
                                            </button>
                                        )}
                                        <button
                                            onClick={(e) => { e.stopPropagation(); onRemove(b.id); }}
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
    updateCustomer, updateLine, updateHeader, addLine, removeLine,
    onPush, pushing, pushStatus, pushError,
    duplicateCheck, duplicateBlocked, duplicateInBatch, onOverrideDuplicate,
    onPickCustomerCandidate, onPickItemCandidate,
    onCreateCustomer, onCreateItem, organizationId,
    onOpenBulk, unmatchedCount,
}) {
    const currency = result?.extracted?.currency;
    const origSubtotal = Number(result?.extracted?.subtotal) || 0;
    const origTax = Number(result?.extracted?.tax_total) || 0;
    const taxRate = origSubtotal > 0 ? origTax / origSubtotal : 0;
    const totals = useMemo(() => {
        const rows = result?.line_review || [];
        let subtotal = 0;
        let discount = 0;
        rows.forEach((row) => {
            const qty = Number(row.qty) || 0;
            const rate = Number(row.rate) || 0;
            const gross = qty * rate;
            const discAmt = Number(row.discount_amount) || 0;
            const discPct = Number(row.discount_percent) || 0;
            const lineDisc = discAmt > 0 ? discAmt : gross * (discPct / 100);
            const derived = Math.max(gross - lineDisc, 0);
            const amount = (gross === 0 && row.amount) ? Number(row.amount) || 0 : derived;
            subtotal += amount;
            discount += lineDisc;
        });
        const tax = subtotal * taxRate;
        return { subtotal, discount, tax, total: subtotal + tax };
    }, [result?.line_review, taxRate]);

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
                            value={result?.zoho_payload?.customer_name || ""}
                            onChange={updateCustomer}
                            onCreateNew={() => onCreateCustomer(result?.extracted?.vendor_or_customer || result?.customer_match?.candidate_name || "")}
                            placeholder="Pick customer…"
                            aiSuggestion={aiSuggestionFromEntityMatch(result?.customer_match, "customer")}
                            erpLabel="Zoho Books"
                            onOverride={(picked, ai) => zohoApi.recordSuggestionFeedback({
                                organization_id: organizationId, slot: "party",
                                context_text: result?.extracted?.vendor_or_customer,
                                suggested_id: result?.customer_match?.list_id,
                                suggested_name: ai?.recommended_ledger,
                                suggested_confidence: ai?.confidence,
                                suggested_method: result?.customer_match?.method,
                                picked_name: picked,
                            })}
                        />
                    </div>
                    <EditableField icon={Calendar} label="Date" type="date" value={result?.extracted?.doc_date || ""} onChange={(v) => updateHeader({ doc_date: v })} />
                    <EditableField icon={Hash} label="Doc #" value={result?.extracted?.doc_number || ""} onChange={(v) => updateHeader({ doc_number: v })} />
                    <Field icon={DollarSign} label="Total" value={fmtAmount(totals.total, currency)} />
                </div>
                {!result?.customer_match?.matched && (
                    <div className="mt-4 space-y-2">
                        <div className="flex items-start gap-2 bg-amber-50 border border-amber-200 text-amber-800 px-3 py-2 rounded-lg text-xs">
                            <AlertTriangle className="h-3.5 w-3.5 flex-shrink-0 mt-0.5 text-amber-600" />
                            <span>No matching Zoho Books customer found. Pick one above, choose a suggestion, or create new.</span>
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
                    <div className="flex items-center gap-2">
                        <button
                            onClick={addLine}
                            className="inline-flex items-center gap-1 text-xs px-2.5 py-1 rounded-lg border border-gray-300 text-gray-700 hover:bg-gray-50 transition-colors"
                            title="Add a new line item"
                        >
                            <Plus className="h-3 w-3" />
                            Add line
                        </button>
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
                </div>
                <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                        <thead className="bg-gray-50">
                            <tr>
                                <th className="px-3 py-2.5 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Description</th>
                                <th className="px-2 py-2.5 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">Qty</th>
                                <th className="px-2 py-2.5 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">Rate</th>
                                <th className="px-2 py-2.5 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">Disc&nbsp;%</th>
                                <th className="px-2 py-2.5 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">Disc&nbsp;{currency || "₹"}</th>
                                <th className="px-2 py-2.5 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">Amount</th>
                                <th className="px-3 py-2.5 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Item</th>
                                <th className="px-2 py-2.5 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Conf.</th>
                                <th className="px-2 py-2.5"></th>
                            </tr>
                        </thead>
                        <tbody>
                            {(result.line_review || []).map((row, i) => {
                                const qty = Number(row.qty) || 0;
                                const rate = Number(row.rate) || 0;
                                const gross = qty * rate;
                                const discAmt = Number(row.discount_amount) || 0;
                                const discPct = Number(row.discount_percent) || 0;
                                const lineDisc = discAmt > 0 ? discAmt : gross * (discPct / 100);
                                const computedAmount = (gross === 0 && row.amount)
                                    ? Number(row.amount) || 0
                                    : Math.max(gross - lineDisc, 0);
                                return (
                                    <tr key={i} className="border-t border-gray-100 align-top hover:bg-gray-50">
                                        <td className="px-3 py-2">
                                            <input
                                                className={tableTextInput}
                                                defaultValue={row.description || ""}
                                                onBlur={(e) => updateLine(i, { description: e.target.value })}
                                            />
                                        </td>
                                        <td className="px-2 py-2">
                                            <input
                                                className={`${tableNumInput} w-20`}
                                                type="number"
                                                step="0.01"
                                                value={row.qty ?? 1}
                                                onChange={(e) => updateLine(i, { qty: e.target.value })}
                                            />
                                        </td>
                                        <td className="px-2 py-2">
                                            <input
                                                className={`${tableNumInput} w-24`}
                                                type="number"
                                                step="0.01"
                                                value={row.rate ?? 0}
                                                onChange={(e) => updateLine(i, { rate: e.target.value })}
                                            />
                                        </td>
                                        <td className="px-2 py-2">
                                            <input
                                                className={`${tableNumInput} w-20`}
                                                type="number"
                                                step="0.01"
                                                value={row.discount_percent ?? 0}
                                                onChange={(e) => updateLine(i, { discount_percent: e.target.value })}
                                                title="Discount percent — leaves the absolute discount untouched when 0"
                                            />
                                        </td>
                                        <td className="px-2 py-2">
                                            <input
                                                className={`${tableNumInput} w-24`}
                                                type="number"
                                                step="0.01"
                                                value={row.discount_amount ?? 0}
                                                onChange={(e) => updateLine(i, { discount_amount: e.target.value })}
                                                title="Discount amount in invoice currency — overrides Disc % when > 0"
                                            />
                                        </td>
                                        <td className="px-2 py-2">
                                            <input
                                                className={`${tableNumInputComputed} w-28`}
                                                type="number"
                                                step="0.01"
                                                value={Number(computedAmount.toFixed(2))}
                                                onChange={(e) => updateLine(i, { amount: e.target.value })}
                                                title="Auto-computed from Qty × Rate − Discount. Edit to override."
                                            />
                                        </td>
                                        <td className="px-3 py-2">
                                            <SearchableDropdown
                                                options={itemNames}
                                                value={row.match?.name || ""}
                                                onChange={(name) => updateLine(i, { itemName: name })}
                                                placeholder="Pick item…"
                                                createLabel="+ Create new item"
                                                onCreateNew={(typed) => onCreateItem(typed || row.description || row.match?.candidate_name || "", i)}
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
                                        <td className="px-2 py-2">
                                            {row.match?.confidence != null && (
                                                <ConfidenceBadge
                                                    confidence={row.match.confidence}
                                                    size="sm"
                                                    showPercentage={false}
                                                />
                                            )}
                                        </td>
                                        <td className="px-2 py-2 text-right">
                                            <button
                                                onClick={() => removeLine(i)}
                                                className="text-gray-400 hover:text-red-600 transition-colors p-1 rounded hover:bg-red-50"
                                                title="Remove this line"
                                            >
                                                <X className="h-3.5 w-3.5" />
                                            </button>
                                        </td>
                                    </tr>
                                );
                            })}
                            {(!result.line_review || result.line_review.length === 0) && (
                                <tr>
                                    <td colSpan={9} className="px-3 py-6 text-center text-xs text-gray-400">
                                        No line items extracted. Click “Add line” above to add one manually.
                                    </td>
                                </tr>
                            )}
                        </tbody>
                    </table>
                </div>

                <div className="px-3 py-3 border-t border-gray-100 flex justify-end">
                    <div className="w-72 text-sm space-y-1">
                        <div className="flex justify-between text-gray-600">
                            <span>Subtotal</span>
                            <span className="font-mono tabular-nums">{fmtAmount(totals.subtotal, currency)}</span>
                        </div>
                        {totals.discount > 0 && (
                            <div className="flex justify-between text-gray-600">
                                <span>Discount</span>
                                <span className="font-mono tabular-nums">−{fmtAmount(totals.discount, currency)}</span>
                            </div>
                        )}
                        <div className="flex justify-between text-gray-600">
                            <span>Tax {taxRate > 0 ? <span className="text-[10px] text-gray-400 font-mono tabular-nums">({(taxRate * 100).toFixed(2)}%)</span> : "(none detected)"}</span>
                            <span className="font-mono tabular-nums">{fmtAmount(totals.tax, currency)}</span>
                        </div>
                        <div className="flex justify-between font-semibold text-gray-900 border-t border-gray-200 pt-1">
                            <span>Total</span>
                            <span className="font-mono tabular-nums">{fmtAmount(totals.total, currency)}</span>
                        </div>
                        {taxRate > 0 && (
                            <p className="text-[11px] text-amber-600 pt-1 leading-snug">
                                Tax is estimated from the extracted document rate; Zoho Books applies the final tax settings when posted.
                            </p>
                        )}
                    </div>
                </div>
            </div>

            {/* Sticky push bar */}
            <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-4 flex items-center gap-3 flex-wrap">
                <button
                    onClick={onPush}
                    disabled={pushing || !result?.zoho_payload || pushStatus === "pushed" || duplicateBlocked}
                    className={btnZohoGreen}
                    title={duplicateBlocked ? "This invoice looks like a duplicate. Use \"Push anyway\" to override." : undefined}
                >
                    {pushing ? <Loader2 className="h-4 w-4 animate-spin" /> : pushStatus === "pushed" ? <CheckCircle2 className="h-4 w-4" /> : <Send className="h-4 w-4" />}
                    {pushing ? "Pushing to Zoho Books…" : pushStatus === "pushed" ? "Pushed" : "Push to Zoho Books"}
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

/**
 * MappingRulesModal — self-contained rules manager wired to zohoApi.mappingRules.
 *
 * The shared common/MappingRulesPanel is hard-coupled to tallyApi internally, so
 * we keep a lightweight Tailwind/portal version here (matching this file's look)
 * that talks to the Zoho Books rules endpoints. Rules auto-map a line/party to a
 * Zoho Books account/customer by keyword/pattern/vendor/amount.
 */
export function MappingRulesModal({ organizationId, documentType, onClose }) {
    const [rules, setRules] = useState([]);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState("");
    const [saving, setSaving] = useState(false);
    const [draft, setDraft] = useState({ rule_type: "keyword", pattern: "", account_slot: "expense", target_name: "" });

    const load = async () => {
        if (!organizationId) return;
        setLoading(true);
        setError("");
        try {
            const res = await zohoApi.mappingRules.list({ organization_id: organizationId, document_type: documentType });
            setRules(res?.rules || res || []);
        } catch (e) {
            setError(formatZohoError(e, "Failed to load rules"));
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => { load(); }, [organizationId, documentType]); // eslint-disable-line react-hooks/exhaustive-deps

    const addRule = async () => {
        if (!draft.pattern.trim() || !draft.target_name.trim()) return;
        setSaving(true);
        setError("");
        try {
            await zohoApi.mappingRules.create({
                organization_id: organizationId,
                document_type: documentType,
                rule_type: draft.rule_type,
                pattern: draft.pattern.trim(),
                account_slot: draft.account_slot,
                target_name: draft.target_name.trim(),
            });
            setDraft({ rule_type: "keyword", pattern: "", account_slot: "expense", target_name: "" });
            await load();
        } catch (e) {
            setError(formatZohoError(e, "Failed to create rule"));
        } finally {
            setSaving(false);
        }
    };

    const removeRule = async (id) => {
        try {
            await zohoApi.mappingRules.remove(id);
            await load();
        } catch (e) {
            setError(formatZohoError(e, "Failed to delete rule"));
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
                            <label className={labelBase}>Maps to (Zoho Books name)</label>
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

// Editable header field — the user can correct the parsed date / document number
// before pushing; the change flows into the Zoho payload via updateHeader.
function EditableField({ icon: Icon, label, value, onChange, type = "text", placeholder }) {
    return (
        <div>
            <label className={labelBase}>
                {Icon && <Icon className="inline h-3.5 w-3.5 mr-1 text-gray-400" />}
                {label}
            </label>
            <input
                type={type}
                value={value ?? ""}
                onChange={(e) => onChange(e.target.value)}
                placeholder={placeholder}
                className={inputBase}
            />
        </div>
    );
}
