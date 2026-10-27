import React, { useEffect, useMemo, useRef, useState } from "react";
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
} from "lucide-react";
import { qbApi } from "../../../../services/api";
import { useDraftDeepLink } from "../common/useDraftDeepLink";
import PdfViewer from "../PdfViewer";
import SearchableDropdown from "../common/SearchableDropdown";
import AISuggestedDropdown from "../common/AISuggestedDropdown";
import { findInBatchDuplicates } from "../common/duplicateDetection";

// Adapt a QB backend entity match (customer/vendor) into the AISuggestedDropdown
// `aiSuggestion` shape. Only surfaces a suggestion for an actual backend match,
// so the auto-fill + reasoning chip reflects real AI matching.
const aiSuggestionFromEntityMatch = (m, noun = "record") => {
    if (!m || !m.matched || !m.name || m.confidence == null) return null;
    return {
        recommended_ledger: m.name,
        confidence: m.confidence,
        match_type: "fuzzy",
        reasoning: `Matched to an existing QuickBooks ${noun}`,
    };
};
import ConfidenceBadge from "../common/ConfidenceBadge";
import CreateMasterModal from "./components/CreateMasterModal";
import BulkCreateModal from "./components/BulkCreateModal";
import ErrorMessage from "./components/ErrorMessage";
import { formatQbError } from "./components/qboError";
import { SaveDraftButton } from "../common/DraftsPanel";
import DuplicateBadge from "../common/DuplicateBadge";
import { PageHeader } from "../../../ui/page-header";
import { Select } from "../../../ui/input";
import { Card } from "../../../ui/card";

const inputBase = "w-full border border-gilt-hairline rounded-lg px-3 py-2 text-sm text-gilt-ink bg-gilt-paper placeholder:text-gilt-faint focus:border-gilt-brass outline-none transition-colors duration-150";
const labelBase = "block text-xs font-medium text-gilt-soft mb-1.5";

// Line-item cell styles — `inputBase` has `w-full` baked in, which loses the
// fight with a sibling `w-20` / `w-24` in the rendered CSS and leaves table
// cells effectively full-width (the documented gotcha in CLAUDE.md). These
// dedicated classes match SalesView.jsx (lines 2024, 2034, 2052, 2061) so the
// QB line-items table reads as the same component as Tally's.
const tableNumInput = "px-2 py-1 text-sm text-right border border-gilt-hairline rounded text-gilt-ink font-mono bg-gilt-paper focus:border-gilt-brass outline-none transition-colors duration-150 tabular-nums";
const tableNumInputComputed = "px-2 py-1 text-sm text-right border border-gilt-brass/40 rounded text-gilt-brass font-mono font-semibold bg-gilt-sunken focus:border-gilt-brass outline-none transition-colors duration-150 tabular-nums";
const tableTextInput = "w-full px-2 py-1 text-sm border border-gilt-hairline rounded text-gilt-ink bg-gilt-paper focus:border-gilt-brass outline-none transition-colors duration-150";
const btnSecondary = "inline-flex items-center gap-1.5 px-3 py-2 text-sm text-gilt-soft bg-gilt-card border border-gilt-hairline rounded-md hover:border-gilt-brass hover:text-gilt-brass transition-colors duration-150 disabled:opacity-50";
// Primary push affordance — quiet ink plate per the gilt workroom idiom.
const btnQbGreen = "inline-flex items-center gap-1.5 px-4 py-2 text-sm font-semibold text-gilt-paper bg-gilt-brass rounded-md hover:bg-gilt-brass/90 transition-colors duration-150 disabled:opacity-50";
const btnDanger = "inline-flex items-center gap-1.5 px-3 py-2 text-sm font-medium text-gilt-debit bg-transparent border border-gilt-debit/40 rounded-md hover:bg-gilt-sunken transition-colors duration-150 disabled:opacity-50";

const PUSH_CONCURRENCY = 2; // be polite to QBO; 2 in flight is plenty for the demo realm

// Format a money amount for the review UI. QB serves US/EU realms, so use the
// extracted currency (USD/EUR/GBP/…) when present rather than a fixed symbol.
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
 * QB Sales Invoice flow — multi-invoice bulk upload.
 *
 * State model:
 *   invoices[]: { id, filename, file, pdfData, status, result, error,
 *                 pushStatus, pushError, pushResult }
 *   activeId: which invoice the editor pane is currently looking at
 *
 * Render branches:
 *   no invoices           → empty dropzone (drag PDFs or browse)
 *   invoices + no active  → grid/list of invoices with status badges
 *   activeId set          → existing PDF + EditPanel layout
 *
 * `setResult(updater)` is preserved so every existing handler keeps working —
 * it routes the patch onto the active invoice's result.
 */
export default function QBSalesView() {
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
    const [createModal, setCreateModal] = useState(null);
    const [bulkOpen, setBulkOpen] = useState(false);

    // Drafts (saved sessions).
    const [draftId, setDraftId] = useState(null);
    const [savingDraft, setSavingDraft] = useState(false);

    // Invoices the user has explicitly said "push anyway" for despite the
    // duplicate verdict. Resets on a new upload run.
    const [overridePushIds, setOverridePushIds] = useState(() => new Set());

    useEffect(() => {
        (async () => {
            try {
                const res = await qbApi.getCompanies();
                const list = res?.companies || [];
                setCompanies(list);
                if (list[0]) setCompany(list[0].realm_id || list[0].name);
            } catch (err) {
                setError(formatQbError(err, "Failed to load companies"));
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
                    qbApi.getCustomers(company),
                    qbApi.getItems(company),
                ]);
                setCustomers(c?.customers || []);
                setItems(i?.items || []);
            } catch (err) {
                setError(formatQbError(err, "Failed to load master data"));
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
        await runConcurrent(newInvoices, 2, async (invoice) => {
            patchInvoice(invoice.id, { status: "processing" });
            try {
                const res = await qbApi.extractInvoice(invoice.file, company);
                // Sales responses don't carry an `ok` flag — treat the presence
                // of the qbo_payload the EditPanel reads as "ready".
                const ready = !!res?.qbo_payload;
                patchInvoice(invoice.id, {
                    status: ready ? "ready" : "failed",
                    result: res,
                    error: ready ? null : (res?.error || "Extraction returned no result"),
                });
            } catch (err) {
                patchInvoice(invoice.id, { status: "failed", error: formatQbError(err, "Extraction failed") });
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
    // (doc number + customer) flag each other before either hits QBO. Mirrors
    // Tally's findInBatchDuplicates; adapts the QB invoice shape to its input.
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
        const invoice = invoices.find((inv) => inv.id === id);
        if (!invoice?.result?.qbo_payload) return;
        if (isBlockedDuplicate(invoice)) {
            // Caller should have called overridePush first; bail silently.
            return;
        }
        patchInvoice(id, { pushStatus: "pushing", pushError: null });
        try {
            const res = await qbApi.pushInvoiceQBO({
                payload: invoice.result.qbo_payload,
                qb_company: company,
                // Gross total from extraction keeps the duplicate-dedupe key
                // consistent with the extract-time check (QBO TotalAmt is net of
                // tax). Without it, the same invoice can re-push as a duplicate.
                extracted_total: invoice.result?.extracted?.total,
                // Honor an explicit "push anyway" so a high-confidence dup isn't
                // re-blocked by the backend 409.
                skip_duplicate_check: overridePushIds.has(id),
            });
            patchInvoice(id, { pushStatus: "pushed", pushResult: res, pushError: null });
        } catch (err) {
            patchInvoice(id, { pushStatus: "failed", pushError: formatQbError(err, "Push failed") });
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

    // ── Active-invoice mutators (unchanged from single-invoice flow) ─
    const updateCustomer = (name) => {
        const c = customerByName[name];
        if (!c) return;
        setResult((prev) => {
            const next = structuredClone(prev);
            next.qbo_payload.CustomerRef = { value: c.list_id, name: c.name };
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

    // Recompute the QBO line Amount whenever any of qty/rate/discount changes.
    // We treat Amount as derived (qty*rate − discount) like Tally does, so what
    // the user sees in the table is always consistent with the totals footer.
    // `discount_amount` wins over `discount_percent` when both are set (matches
    // Tally's `updateItem` precedence in SalesView.jsx). Negative results are
    // clamped to 0 — QBO rejects negative line amounts.
    const recomputeLineAmount = (row) => {
        const qty = Number(row.qty) || 0;
        const rate = Number(row.rate) || 0;
        const gross = qty * rate;
        const discAmt = Number(row.discount_amount) || 0;
        const discPct = Number(row.discount_percent) || 0;
        const discount = discAmt > 0 ? discAmt : gross * (discPct / 100);
        return Math.max(gross - discount, 0);
    };

    const updateLine = (idx, patch) => {
        setResult((prev) => {
            if (!prev) return prev;
            const next = structuredClone(prev);
            const reviewRow = (next.line_review = next.line_review || []);
            const row = reviewRow[idx] || (reviewRow[idx] = {});
            const line = next.qbo_payload.Line[idx];

            // Mirror edits onto the human-friendly review state — totals + table
            // render off line_review, not qbo_payload, so this is the source of truth.
            if (patch.description !== undefined) {
                row.description = patch.description;
                if (line) line.Description = patch.description;
            }
            if (patch.qty !== undefined) {
                row.qty = patch.qty === "" ? "" : Number(patch.qty);
                if (line?.SalesItemLineDetail) line.SalesItemLineDetail.Qty = Number(patch.qty) || 0;
            }
            if (patch.rate !== undefined) {
                row.rate = patch.rate === "" ? "" : Number(patch.rate);
                if (line?.SalesItemLineDetail) line.SalesItemLineDetail.UnitPrice = Number(patch.rate) || 0;
            }
            if (patch.discount_percent !== undefined) {
                row.discount_percent = patch.discount_percent === "" ? "" : Number(patch.discount_percent);
                // A discount % only takes effect if there isn't an absolute amount.
                if (row.discount_percent !== "" && Number(row.discount_amount) > 0) {
                    row.discount_amount = 0;
                }
            }
            if (patch.discount_amount !== undefined) {
                row.discount_amount = patch.discount_amount === "" ? "" : Number(patch.discount_amount);
                if (row.discount_amount !== "" && Number(row.discount_amount) > 0) {
                    row.discount_percent = 0;
                }
            }
            if (patch.amount !== undefined) {
                // Manual override: respect the user-typed amount and back-derive
                // discount from qty*rate so the line stays self-consistent. If
                // the user is typing into Amount and didn't set qty/rate, just
                // store the amount without altering discount.
                row.amount = patch.amount === "" ? "" : Number(patch.amount);
                if (line) line.Amount = Number(patch.amount) || 0;
            } else if (
                patch.qty !== undefined
                || patch.rate !== undefined
                || patch.discount_percent !== undefined
                || patch.discount_amount !== undefined
            ) {
                // Derived path — recompute amount from current row state.
                const computed = recomputeLineAmount(row);
                row.amount = computed;
                if (line) line.Amount = computed;
            }

            if (patch.itemName !== undefined) {
                const it = itemByName[patch.itemName];
                if (it && line?.SalesItemLineDetail) {
                    line.SalesItemLineDetail.ItemRef = { value: it.list_id, name: it.name };
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

    // Header edits — mirror to both qbo_payload (what gets pushed) and
    // extracted (what the review UI / dedupe reads). Keeping them in sync
    // avoids a stale-extracted bug where the user fixes the doc date but the
    // push still goes out with the original.
    const updateHeader = (patch) => {
        setResult((prev) => {
            if (!prev) return prev;
            const next = structuredClone(prev);
            next.extracted = next.extracted || {};
            next.qbo_payload = next.qbo_payload || {};
            if (patch.doc_date !== undefined) {
                next.extracted.doc_date = patch.doc_date;
                next.qbo_payload.TxnDate = patch.doc_date;
            }
            if (patch.doc_number !== undefined) {
                next.extracted.doc_number = patch.doc_number;
                next.qbo_payload.DocNumber = patch.doc_number;
            }
            if (patch.due_date !== undefined) {
                next.extracted.due_date = patch.due_date;
                next.qbo_payload.DueDate = patch.due_date;
            }
            if (patch.currency !== undefined) {
                next.extracted.currency = patch.currency;
                next.qbo_payload.CurrencyRef = { value: patch.currency };
            }
            return next;
        });
    };

    // Adds a blank line. Tally's "+ Add item" affordance — the user sometimes
    // needs to split an extracted line into two or add a miscellaneous charge.
    const addLine = () => {
        setResult((prev) => {
            if (!prev) return prev;
            const next = structuredClone(prev);
            next.qbo_payload = next.qbo_payload || {};
            next.qbo_payload.Line = next.qbo_payload.Line || [];
            next.line_review = next.line_review || [];
            next.qbo_payload.Line.push({
                Amount: 0,
                Description: "",
                DetailType: "SalesItemLineDetail",
                SalesItemLineDetail: { Qty: 1, UnitPrice: 0 },
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
            (next.qbo_payload?.Line || []).splice(idx, 1);
            (next.line_review || []).splice(idx, 1);
            return next;
        });
    };

    // --- Tier 1/2/4 helpers ---------------------------------------------
    // setCustomerOnPayload patches both the QBO payload and the local match
    // state without needing the entity to be in the dropdown by name first.
    // Used after inline-create and after Tier-2 candidate picks.
    const setCustomerOnPayload = (c, method = "manual") => {
        setResult((prev) => {
            const next = structuredClone(prev);
            next.qbo_payload.CustomerRef = { value: c.list_id, name: c.name };
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
            const line = next.qbo_payload?.Line?.[idx];
            if (line?.SalesItemLineDetail) {
                line.SalesItemLineDetail.ItemRef = { value: it.list_id, name: it.name };
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
            await qbApi.saveAlias({
                entity_type: "customer",
                alias_text: result?.extracted?.vendor_or_customer || result?.customer_match?.candidate_name || "",
                qbo_entity_id: cand.list_id,
                qbo_entity_name: cand.name,
                qb_company: company,
            });
        } catch (_) { /* non-fatal — alias is an optimization */ }
    };

    const onPickItemCandidate = async (idx, cand) => {
        setItemOnLine(idx, cand, "user_picked");
        try {
            await qbApi.saveAlias({
                entity_type: "item",
                alias_text: result?.line_review?.[idx]?.description
                    || result?.line_review?.[idx]?.match?.candidate_name
                    || "",
                qbo_entity_id: cand.list_id,
                qbo_entity_name: cand.name,
                qb_company: company,
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
        const okResults = results.filter((r) => r.ok && r.qbo_entity_id);
        okResults.forEach((r) => {
            const entity = { list_id: String(r.qbo_entity_id), name: r.qbo_entity_name };
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

    // ── Drafts ─────────────────────────────────────────────────────
    // What gets persisted: the extracted state + the R2 pointers (file_url,
    // r2_key, file_name) the extract endpoint stamped onto each invoice's
    // result. File objects + blob URLs are NOT persisted — on reopen the
    // restore path mints a fresh signed URL per invoice via getFileUrl(r2_key)
    // so the preview pane re-renders with the source PDF.
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
            // The draft picker needs *something* to identify the source file;
            // use the first invoice with R2 metadata. The per-invoice r2_keys
            // ride inside the JSONB payload so each PDF can still re-render.
            const firstWithR2 = invoices.find((inv) => inv.result?.r2_key);
            const res = await qbApi.drafts.save({
                flow: "sales",
                payload: { invoices: serializeInvoicesForDraft(), activeId },
                title: deriveDraftTitle(),
                draftId,
                qbCompany: company,
                fileUrl: firstWithR2?.result?.file_url,
                fileName: firstWithR2?.result?.file_name || firstWithR2?.filename,
                r2Key: firstWithR2?.result?.r2_key,
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
        const restoredInvoices = (payload.invoices || []).map((inv) => ({
            ...inv,
            // No File object on reload (blob is gone); pdfData gets filled in
            // asynchronously below for invoices whose extract uploaded the
            // source PDF to R2.
            file: null,
            pdfData: null,
        }));
        setInvoices(restoredInvoices);
        setActiveId(payload.activeId && restoredInvoices.some((inv) => inv.id === payload.activeId)
            ? payload.activeId
            : null);
        setDraftId(draft.id);
        setError("");

        // Re-hydrate PDF previews from R2 for invoices that have an r2_key.
        // Fire-and-forget — the editor renders immediately; each preview pops
        // in as the signed URL resolves. A failure (key gone, bucket rotated)
        // just leaves the "no preview" placeholder, which matches the prior
        // behavior so nothing worsens.
        restoredInvoices.forEach(async (inv) => {
            const key = inv.result?.r2_key;
            if (!key) return;
            try {
                const res = await qbApi.drafts.getFileUrl(key);
                if (res?.url) patchInvoice(inv.id, { pdfData: res.url });
            } catch (_) {
                /* leave placeholder */
            }
        });
    };

    useDraftDeepLink({
        getDraft: qbApi.drafts.get,
        companyField: "realm_id",
        setCompany,
        onOpen: handleOpenDraft,
    });

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
            className="bg-transparent border-gilt-hairline"
            icon={FileSignature}
            title={<span className="gl-display text-gilt-ink">QuickBooks · Sales Invoice</span>}
            description="Upload sales invoice PDFs, review extracted lines, and push to QuickBooks."
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
                                const v = c.realm_id || c.name;
                                return <option key={v} value={v}>{c.name}</option>;
                            })}
                        </Select>
                    </div>
                    {(loadingCompanies || loadingMasters) && (
                        <span className="inline-flex items-center gap-1 text-xs text-gilt-soft">
                            <Loader2 className="h-3 w-3 animate-spin" />
                            {loadingCompanies ? "Companies…" : "Loading customers + items…"}
                        </span>
                    )}
                    {mastersReady && (
                        <span className="text-xs text-gilt-soft font-mono tabular-nums">
                            {customers.length} customers · {items.length} items
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
    if (invoices.length === 0) {
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
                                    <p className="text-sm font-medium text-gilt-ink">Drop sales invoice PDFs here or click to browse</p>
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
                                <li>Pick the QuickBooks company above</li>
                                <li>Upload one or more sales invoice PDFs</li>
                                <li>Each invoice is extracted in parallel — review individually</li>
                                <li>Push them one by one, or "Push all" to send everything ready</li>
                            </ol>
                        </div>
                    </div>
                </div>
            </div>
        );
    }

    // ══════════════════════════════════════════════════════════════
    // BRANCH 2: grid/list of invoices — no active editor
    // ══════════════════════════════════════════════════════════════
    if (!activeInvoice) {
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
                            className={btnQbGreen}
                            title={counts.ready ? `Push the ${counts.ready} ready invoice${counts.ready === 1 ? "" : "s"} to QuickBooks` : "No ready invoices to push"}
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
                    {isDragging && (
                        <div className="fixed inset-0 bg-gilt-brass/10 border-4 border-dashed border-gilt-brass pointer-events-none z-40" />
                    )}
                </div>
            </div>
        );
    }

    // ══════════════════════════════════════════════════════════════
    // BRANCH 3: editor for active invoice
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
                        title="Back to all invoices"
                    >
                        <ArrowLeft className="h-4 w-4 text-gilt-soft" />
                    </button>
                    <FileText className="h-4 w-4 text-gilt-faint flex-shrink-0" />
                    <h2 className="text-sm font-semibold text-gilt-ink truncate">{activeInvoice.filename}</h2>
                    <InvoiceStatusBadge invoice={activeInvoice} />
                </div>
                <div className="flex items-center gap-2 text-xs text-gilt-soft font-mono tabular-nums">
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
                                <div className="h-full bg-gilt-card rounded-xl border border-gilt-hairline overflow-hidden">
                                    <PdfViewer pdfData={activeInvoice.pdfData} />
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
                            {activeInvoice.status === "pending" || activeInvoice.status === "processing" ? (
                                <div className="flex flex-col items-center justify-center h-full bg-gilt-card rounded-xl border border-gilt-hairline text-gilt-soft gap-3 p-8">
                                    <Loader2 className="h-8 w-8 animate-spin text-gilt-brass" />
                                    <div className="text-sm font-medium text-gilt-ink">Extracting invoice…</div>
                                    <div className="text-xs text-gilt-soft">Reading PDF and matching to QuickBooks customers + items.</div>
                                </div>
                            ) : activeInvoice.status === "failed" ? (
                                <div className="flex flex-col items-center justify-center h-full bg-gilt-card rounded-xl border border-gilt-hairline gap-3 p-8 text-center">
                                    <AlertTriangle className="h-10 w-10 text-gilt-debit/60" />
                                    <div className="text-sm font-medium text-gilt-debit">Extraction failed</div>
                                    <div className="text-xs text-gilt-soft max-w-md">{activeInvoice.error || "Unknown error"}</div>
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
                                    qbCompany={company}
                                />
                            ) : null}
                            {activeInvoice.pushResult && (
                                <div className="bg-gilt-card border border-gilt-credit/40 rounded-xl p-4 mt-3">
                                    <div className="flex items-center gap-2 mb-2">
                                        <CheckCircle2 className="h-4 w-4 text-gilt-credit" />
                                        <h2 className="font-mono text-[10px] font-semibold text-gilt-credit uppercase tracking-[0.14em]">Pushed successfully</h2>
                                    </div>
                                    <pre className="text-xs overflow-auto max-h-64 bg-gilt-sunken border border-gilt-hairline rounded-lg p-3 text-gilt-soft">{JSON.stringify(activeInvoice.pushResult, null, 2)}</pre>
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
                qbCompany={company}
                onClose={() => setCreateModal(null)}
                onCreated={onCreatedFromModal}
            />
            <BulkCreateModal
                open={bulkOpen}
                items={gatherUnmatchedForBulk()}
                qbCompany={company}
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

function InvoiceStatusBadge({ invoice }) {
    const { status, pushStatus } = invoice;
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

function InvoiceCard({ invoice, overridden, onOpen, onRemove, onRetry, onOverride }) {
    const total = invoice.result?.extracted?.total;
    const customer = invoice.result?.extracted?.vendor_or_customer || invoice.result?.customer_match?.name;
    const lineCount = invoice.result?.line_review?.length || 0;
    const needsReview = invoice.result?.needs_review;
    const isOpenable = invoice.status !== "pending";
    const dup = invoice.result?.duplicate_check;
    const blockedDup = dup?.is_duplicate && !overridden && invoice.pushStatus !== "pushed";
    return (
        <div className="group bg-gilt-card rounded-xl border border-gilt-hairline hover:border-gilt-brass transition-colors duration-150 overflow-hidden flex flex-col">
            <button
                onClick={isOpenable ? onOpen : undefined}
                disabled={!isOpenable}
                className="px-4 pt-4 pb-3 text-left flex-1 disabled:cursor-default"
            >
                <div className="flex items-start justify-between gap-2 mb-2">
                    <FileText className="h-4 w-4 text-gilt-faint flex-shrink-0 mt-0.5" />
                    <span className="text-sm font-medium text-gilt-ink truncate flex-1" title={invoice.filename}>
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
                            <div className="flex items-center gap-1.5 text-gilt-soft truncate">
                                <User className="h-3 w-3 text-gilt-faint flex-shrink-0" />
                                <span className="truncate" title={customer}>{customer}</span>
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
                {invoice.status === "failed" && (
                    <p className="text-xs text-gilt-debit line-clamp-2" title={invoice.error}>
                        {invoice.error || "Extraction failed"}
                    </p>
                )}
                {invoice.pushStatus === "failed" && (
                    <p className="text-xs text-gilt-debit mt-2 line-clamp-2" title={invoice.pushError}>
                        Push: {invoice.pushError || "failed"}
                    </p>
                )}
            </button>
            <div className="px-3 py-2 border-t border-gilt-hairline bg-gilt-sunken flex items-center justify-between gap-2">
                <div className="flex items-center gap-1">
                    {blockedDup && (
                        <button
                            onClick={(e) => { e.stopPropagation?.(); onOverride?.(); }}
                            className="text-[11px] font-medium text-gilt-brass hover:text-gilt-ink hover:bg-gilt-card px-2 py-1 rounded inline-flex items-center gap-1 transition-colors duration-150"
                            title="Push this invoice even though it looks like a duplicate"
                        >
                            Push anyway
                        </button>
                    )}
                    {invoice.pushStatus === "failed" && (
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

function InvoiceList({ invoices, overridePushIds = new Set(), onOpen, onRemove, onRetry, onOverride }) {
    return (
        <div className="bg-gilt-card rounded-xl border border-gilt-hairline overflow-hidden">
            <div className="overflow-x-auto">
                <table className="w-full text-sm">
                    <thead className="border-b border-gilt-hairline">
                        <tr>
                            <th className="px-3 py-2.5 text-left font-mono text-[10px] font-medium text-gilt-soft uppercase tracking-[0.14em]">File</th>
                            <th className="px-3 py-2.5 text-left font-mono text-[10px] font-medium text-gilt-soft uppercase tracking-[0.14em]">Status</th>
                            <th className="px-3 py-2.5 text-left font-mono text-[10px] font-medium text-gilt-soft uppercase tracking-[0.14em]">Customer</th>
                            <th className="px-3 py-2.5 text-right font-mono text-[10px] font-medium text-gilt-soft uppercase tracking-[0.14em]">Total</th>
                            <th className="px-3 py-2.5 text-left font-mono text-[10px] font-medium text-gilt-soft uppercase tracking-[0.14em]">Lines</th>
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
                                    className={`border-t border-gilt-hairline hover:bg-gilt-sunken transition-colors duration-150 ${isOpenable ? "cursor-pointer" : ""}`}
                                >
                                    <td className="px-3 py-2 text-sm text-gilt-ink max-w-xs truncate" title={inv.filename}>
                                        <span className="inline-flex items-center gap-1.5">
                                            <FileText className="h-3.5 w-3.5 text-gilt-faint flex-shrink-0" />
                                            {inv.filename}
                                        </span>
                                    </td>
                                    <td className="px-3 py-2">
                                        <div className="flex items-center gap-1.5 flex-wrap">
                                            <InvoiceStatusBadge invoice={inv} />
                                            <DuplicateBadge check={inv.result?.duplicate_check} />
                                        </div>
                                    </td>
                                    <td className="px-3 py-2 text-sm text-gilt-soft truncate max-w-xs" title={customer || ""}>{customer || "—"}</td>
                                    <td className="px-3 py-2 text-sm text-right font-mono tabular-nums text-gilt-ink">
                                        {total != null ? Number(total).toFixed(2) : "—"}
                                    </td>
                                    <td className="px-3 py-2 text-sm font-mono tabular-nums text-gilt-soft">{inv.result?.line_review?.length ?? "—"}</td>
                                    <td className="px-3 py-2 text-right">
                                        {inv.result?.duplicate_check?.is_duplicate && !overridePushIds.has(inv.id) && inv.pushStatus !== "pushed" && (
                                            <button
                                                onClick={(e) => { e.stopPropagation(); onOverride?.(inv.id); }}
                                                className="text-[11px] font-medium text-gilt-brass hover:text-gilt-ink hover:underline mr-3 transition-colors duration-150"
                                                title="Push anyway"
                                            >
                                                Push anyway
                                            </button>
                                        )}
                                        {inv.pushStatus === "failed" && (
                                            <button
                                                onClick={(e) => { e.stopPropagation(); onRetry(inv.id); }}
                                                className="text-[11px] font-medium text-gilt-debit hover:underline mr-3 inline-flex items-center gap-1 transition-colors duration-150"
                                            >
                                                <RefreshCw className="h-3 w-3" /> Retry
                                            </button>
                                        )}
                                        <button
                                            onClick={(e) => { e.stopPropagation(); onRemove(inv.id); }}
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
    result, customerNames, itemNames,
    updateCustomer, updateLine, updateHeader, addLine, removeLine,
    onPush, pushing, pushStatus, pushError,
    duplicateCheck, duplicateBlocked, duplicateInBatch, onOverrideDuplicate,
    onPickCustomerCandidate, onPickItemCandidate,
    onCreateCustomer, onCreateItem, qbCompany,
    onOpenBulk, unmatchedCount,
}) {
    const currency = result?.extracted?.currency;

    // Reactive totals — derived from line_review so any edit (qty, rate, discount,
    // amount-override) immediately flows into subtotal + tax + grand total. Tax
    // is proportional to the originally-extracted rate (since QBO computes the
    // exact tax server-side via TaxCodeRef, we'd otherwise be guessing).
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
            // When the user has manually set amount and qty*rate is 0, trust
            // amount (free-text Amount only flow). Otherwise derive from
            // qty*rate − discount.
            const derived = Math.max(gross - lineDisc, 0);
            const amount = (gross === 0 && row.amount) ? Number(row.amount) || 0 : derived;
            subtotal += amount;
            discount += lineDisc;
        });
        const tax = subtotal * taxRate;
        return {
            subtotal,
            discount,
            tax,
            total: subtotal + tax,
        };
    }, [result?.line_review, taxRate]);

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
                        <label className={labelBase}>
                            <User className="inline h-3.5 w-3.5 mr-1 text-gilt-faint" />
                            Customer
                        </label>
                        <AISuggestedDropdown
                            options={customerNames}
                            value={result?.qbo_payload?.CustomerRef?.name || ""}
                            onChange={updateCustomer}
                            onCreateNew={() => onCreateCustomer(result?.extracted?.vendor_or_customer || result?.customer_match?.candidate_name || "")}
                            placeholder="Pick customer…"
                            aiSuggestion={aiSuggestionFromEntityMatch(result?.customer_match, "customer")}
                            erpLabel="QuickBooks"
                            onOverride={(picked, ai) => qbApi.recordSuggestionFeedback({
                                qb_company: qbCompany, slot: "party",
                                context_text: result?.extracted?.vendor_or_customer,
                                suggested_id: result?.customer_match?.list_id,
                                suggested_name: ai?.recommended_ledger,
                                suggested_confidence: ai?.confidence,
                                suggested_method: result?.customer_match?.method,
                                picked_name: picked,
                            })}
                        />
                    </div>
                    <EditableField
                        icon={Calendar}
                        label="Date"
                        type="date"
                        value={result?.extracted?.doc_date || ""}
                        onChange={(v) => updateHeader({ doc_date: v })}
                    />
                    <EditableField
                        icon={Hash}
                        label="Doc #"
                        value={result?.extracted?.doc_number || ""}
                        onChange={(v) => updateHeader({ doc_number: v })}
                    />
                    <ComputedField
                        icon={DollarSign}
                        label="Total"
                        value={fmtAmount(totals.total, currency)}
                        title="Computed from line items + tax rate. Edit lines to change."
                    />
                </div>
                {/* Header CTA when no customer matched — kept light, the real
                    "Create new" affordance now lives inside the dropdown above
                    so the user doesn't have to scroll down to act on it. */}
                {!result?.customer_match?.matched && !result?.qbo_payload?.CustomerRef?.value && (
                    <div className="mt-4 flex items-start gap-2 bg-gilt-sunken border border-gilt-brass/40 text-gilt-brass px-3 py-2 rounded-lg text-xs">
                        <AlertTriangle className="h-3.5 w-3.5 flex-shrink-0 mt-0.5 text-gilt-brass" />
                        <span>
                            No matching QuickBooks customer found. Open the dropdown above to pick one
                            {result?.customer_match?.top_candidates?.length ? " from suggested candidates" : ""}, or use “Create New” inside the dropdown.
                        </span>
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
                    <div className="flex items-center gap-2">
                        <button
                            onClick={addLine}
                            className="inline-flex items-center gap-1 text-xs px-2.5 py-1 rounded-md border border-gilt-hairline text-gilt-soft hover:border-gilt-brass hover:text-gilt-brass transition-colors duration-150"
                            title="Add a new line item"
                        >
                            <Plus className="h-3 w-3" />
                            Add line
                        </button>
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
                </div>
                <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                        <thead className="border-b border-gilt-hairline">
                            <tr>
                                <th className="px-3 py-2.5 text-left font-mono text-[10px] font-medium text-gilt-soft uppercase tracking-[0.14em]">Description</th>
                                <th className="px-2 py-2.5 text-right font-mono text-[10px] font-medium text-gilt-soft uppercase tracking-[0.14em]">Qty</th>
                                <th className="px-2 py-2.5 text-right font-mono text-[10px] font-medium text-gilt-soft uppercase tracking-[0.14em]">Rate</th>
                                <th className="px-2 py-2.5 text-right font-mono text-[10px] font-medium text-gilt-soft uppercase tracking-[0.14em]">Disc&nbsp;%</th>
                                <th className="px-2 py-2.5 text-right font-mono text-[10px] font-medium text-gilt-soft uppercase tracking-[0.14em]">Disc&nbsp;{currency || "₹"}</th>
                                <th className="px-2 py-2.5 text-right font-mono text-[10px] font-medium text-gilt-soft uppercase tracking-[0.14em]">Amount</th>
                                <th className="px-3 py-2.5 text-left font-mono text-[10px] font-medium text-gilt-soft uppercase tracking-[0.14em]">Item</th>
                                <th className="px-2 py-2.5 text-left font-mono text-[10px] font-medium text-gilt-soft uppercase tracking-[0.14em]">Conf.</th>
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
                                    <tr key={i} className="border-t border-gilt-hairline align-top hover:bg-gilt-sunken transition-colors duration-150">
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
                                                className="text-gilt-faint hover:text-gilt-debit transition-colors duration-150 p-1 rounded hover:bg-gilt-sunken"
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
                                    <td colSpan={9} className="px-3 py-6 text-center text-xs text-gilt-faint">
                                        No line items extracted. Click “Add line” above to add one manually.
                                    </td>
                                </tr>
                            )}
                        </tbody>
                    </table>
                </div>

                {/* Totals — reactive: derived from line_review so every edit
                    (qty/rate/discount/amount) flows here immediately. */}
                <div className="px-3 py-3 border-t border-gilt-hairline flex justify-end">
                    <div className="w-72 text-sm space-y-1">
                        <div className="flex justify-between text-gilt-soft">
                            <span>Subtotal</span>
                            <span className="font-mono tabular-nums">{fmtAmount(totals.subtotal, currency)}</span>
                        </div>
                        {totals.discount > 0 && (
                            <div className="flex justify-between text-gilt-soft">
                                <span>Discount</span>
                                <span className="font-mono tabular-nums">−{fmtAmount(totals.discount, currency)}</span>
                            </div>
                        )}
                        <div className="flex justify-between text-gilt-soft">
                            <span>Tax {taxRate > 0 ? <span className="text-[10px] text-gilt-faint font-mono tabular-nums">({(taxRate * 100).toFixed(2)}%)</span> : "(none detected)"}</span>
                            <span className="font-mono tabular-nums">{fmtAmount(totals.tax, currency)}</span>
                        </div>
                        <div className="flex justify-between font-semibold text-gilt-ink border-t border-gilt-hairline pt-1">
                            <span>Total</span>
                            <span className="font-mono tabular-nums gl-double-rule">{fmtAmount(totals.total, currency)}</span>
                        </div>
                        {taxRate > 0 && (
                            <p className="text-[11px] text-gilt-brass pt-1 leading-snug">
                                Tax shown uses the extracted rate as an estimate. QuickBooks recomputes the exact tax server-side via the line items’ TaxCodeRef when the invoice is created.
                            </p>
                        )}
                    </div>
                </div>
            </div>

            {/* Sticky push bar */}
            <div className="bg-gilt-card rounded-xl border border-gilt-hairline p-4 flex items-center gap-3 flex-wrap">
                <button
                    onClick={onPush}
                    disabled={pushing || !result?.qbo_payload || pushStatus === "pushed" || duplicateBlocked}
                    className={btnQbGreen}
                    title={duplicateBlocked ? "This invoice looks like a duplicate. Use \"Push anyway\" to override." : undefined}
                >
                    {pushing ? <Loader2 className="h-4 w-4 animate-spin" /> : pushStatus === "pushed" ? <CheckCircle2 className="h-4 w-4" /> : <Send className="h-4 w-4" />}
                    {pushing ? "Pushing to QuickBooks…" : pushStatus === "pushed" ? "Pushed" : "Push to QuickBooks"}
                </button>
                {duplicateBlocked && (
                    <>
                        {duplicateInBatch && !duplicateCheck?.is_duplicate ? (
                            <span className="inline-flex items-center gap-1 px-2 py-0.5 text-[11px] font-medium rounded-full bg-gilt-sunken border border-gilt-brass/40 text-gilt-brass" title="Another file in this upload has the same document number and customer.">
                                <AlertTriangle className="h-3 w-3" />Duplicate of another file in this batch
                            </span>
                        ) : (
                            <DuplicateBadge check={duplicateCheck} size="md" />
                        )}
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
                    <div className="w-full">
                        <ErrorMessage error={pushError} prefix="Push failed" />
                    </div>
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
            <div className="text-sm text-gilt-ink px-3 py-2 bg-gilt-sunken border border-gilt-hairline rounded-lg min-h-[36px]">
                {value ?? <span className="text-gilt-faint">—</span>}
            </div>
        </div>
    );
}

// Editable header field — used for doc date, doc number, etc. Mirrors the
// styling of the read-only `Field` so the form reads as one cohesive grid even
// though some cells are editable and some are computed.
function EditableField({ icon: Icon, label, value, onChange, type = "text", placeholder }) {
    return (
        <div>
            <label className={labelBase}>
                {Icon && <Icon className="inline h-3.5 w-3.5 mr-1 text-gilt-faint" />}
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

// Computed header field — the total — surfaced as derived data, not an input,
// so the user knows changing it requires editing the lines.
function ComputedField({ icon: Icon, label, value, title }) {
    return (
        <div>
            <label className={labelBase}>
                {Icon && <Icon className="inline h-3.5 w-3.5 mr-1 text-gilt-faint" />}
                {label}
                <span className="ml-1 text-[10px] font-normal text-gilt-faint" title={title}>(auto)</span>
            </label>
            <div className="text-sm font-semibold text-gilt-ink px-3 py-2 bg-gilt-sunken border border-gilt-hairline rounded-lg min-h-[36px] font-mono tabular-nums" title={title}>
                {value}
            </div>
        </div>
    );
}
