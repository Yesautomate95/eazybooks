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
import MatchOriginBadge from "../common/MatchOriginBadge";
import AccountCreatePrompt from "../common/AccountCreatePrompt";
import DraftsPanel, { SaveDraftButton } from "../common/DraftsPanel";
import DuplicateBadge from "../common/DuplicateBadge";
import { MappingRulesModal } from "./ZohoSalesView";
import { PageHeader } from "../../../ui/page-header";
import { Select } from "../../../ui/input";
import { Card } from "../../../ui/card";

// Adapt a Zoho backend entity match (vendor/customer) into the AISuggestedDropdown
// `aiSuggestion` shape. Only surfaces a suggestion for an actual backend match.
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
const btnPrimary = "inline-flex items-center gap-1.5 px-4 py-2 text-sm font-semibold text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed";
const btnSecondary = "inline-flex items-center gap-1.5 px-3 py-2 text-sm text-gray-700 bg-white border border-gray-300 rounded-lg hover:bg-gray-50 transition-colors disabled:opacity-50";
// Zoho-specific green for the actual "Push to Zoho Books" action — keeps the
// affordance familiar to Zoho users.
const btnZohoGreen = "inline-flex items-center gap-1.5 px-4 py-2 text-sm font-semibold text-white bg-[#2CA01C] rounded-lg hover:bg-[#268a18] transition-colors disabled:opacity-50";
const btnDanger = "inline-flex items-center gap-1.5 px-3 py-2 text-sm font-medium text-red-700 bg-red-50 border border-red-200 rounded-lg hover:bg-red-100 transition-colors disabled:opacity-50";
const accountTypeKey = (type) => String(type || "").toLowerCase().replace(/[^a-z]/g, "");

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

const netPurchaseLineAmount = (row) => {
    const discountAmount = num(row?.discount_amount);
    const discountPercent = num(row?.discount_percent);
    const gross = Math.max(
        num(row?.gross_amount),
        discountAmount > 0 ? num(row?.amount) + discountAmount : num(row?.amount),
        0,
    );
    const discount = discountAmount > 0 ? discountAmount : gross * (discountPercent / 100);
    if (discount > 0 || discountPercent > 0) return Math.max(gross - discount, 0);
    return num(row?.amount, gross);
};

const buildZohoBillPushPayload = (result) => {
    const payload = structuredClone(result?.zoho_payload || {});
    const rows = result?.line_review || [];
    payload.line_items = (payload.line_items || []).map((line, idx) => {
        const row = rows[idx] || {};
        if (!rows[idx]) return line;
        return {
            ...line,
            description: row.description ?? line.description,
            name: row.description ?? line.name,
            quantity: 1,
            rate: netPurchaseLineAmount(row),
        };
    });
    return payload;
};

/**
 * Zoho Purchase Bill flow — multi-bill bulk upload.
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
export default function ZohoPurchaseView() {
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
    const [createDialog, setCreateDialog] = useState(null);
    const [createModal, setCreateModal] = useState(null);
    const [bulkOpen, setBulkOpen] = useState(false);
    const [rulesOpen, setRulesOpen] = useState(false);

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

    useEffect(() => {
        if (!company) return;
        setLoadingMasters(true);
        setVendors([]);
        setAccounts([]);
        (async () => {
            try {
                const [v, a] = await Promise.all([
                    zohoApi.getVendors(company),
                    zohoApi.getAccounts(company),
                ]);
                setVendors(v?.vendors || []);
                const expenseTypes = new Set(["expense", "costofgoodssold", "otherexpense", "othercurrentliability"]);
                setAccounts((a?.accounts || []).filter((x) => expenseTypes.has(accountTypeKey(x.account_type))));
            } catch (err) {
                setError(formatZohoError(err, "Failed to load master data"));
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
                const res = await zohoApi.extractBill(bill.file, company);
                patchBill(bill.id, {
                    status: res?.ok ? "ready" : "failed",
                    result: res,
                    error: res?.ok ? null : (res?.error || "Extraction returned no result"),
                    // R2 metadata so a reopened draft can preview its source PDF
                    // (Phase 6). base64/blob URLs aren't persisted; r2_key survives.
                    r2_key: res?.r2_key || null,
                    file_url: res?.file_url || null,
                });
            } catch (err) {
                const msg = formatZohoError(err, "Extraction failed");
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

    // In-batch duplicate detection: two files in the same upload sharing
    // (bill number + vendor) flag each other before either hits Zoho Books.
    const batchDuplicates = useMemo(
        () => findInBatchDuplicates(
            bills.map((b) => ({
                id: b.id,
                status: b.result?.extracted ? "done" : b.status,
                duplicateOverridden: overridePushIds.has(b.id),
                data: { header: {
                    invoice_number: b.result?.extracted?.doc_number,
                    party_name: b.result?.extracted?.vendor_or_customer,
                    party_gstin: "",
                } },
            }))
        ),
        [bills, overridePushIds],
    );

    const isBlockedDuplicate = (bill) => {
        if (!bill) return false;
        if (batchDuplicates.has(bill.id) && !overridePushIds.has(bill.id)) return true;
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
        if (!bill?.result?.zoho_payload) return;
        if (bill.pushStatus === "pushing" || bill.pushStatus === "pushed") return;
        if (isBlockedDuplicate(bill)) {
            // Caller should have called overridePush first; bail silently.
            return;
        }
        patchBill(id, { pushStatus: "pushing", pushError: null });
        try {
            const res = await zohoApi.pushBillZoho({
                payload: buildZohoBillPushPayload(bill.result),
                organization_id: company,
                extracted_total: bill.result.extracted_total ?? bill.result.zoho_payload?.TotalAmt,
                skip_duplicate_check: overridePushIds.has(id),
            });
            patchBill(id, { pushStatus: "pushed", pushResult: res, pushError: null });
        } catch (err) {
            const detail = err?.response?.data?.detail;
            const msg = typeof detail === "string"
                ? detail
                : detail?.message || detail?.zoho_error || detail?.zoho_detail || err.message || "Push failed";
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
        // R2 metadata so a reopened draft can re-fetch the source PDF preview.
        r2_key: b.r2_key || null,
        file_url: b.file_url || null,
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
            // Stamp the draft row with the first bill that has R2 metadata.
            // Per-bill r2_keys stay inside the payload for multi-file drafts.
            const firstWithR2 = bills.find((b) => b.r2_key);
            const res = await zohoApi.drafts.save({
                flow: "purchase",
                payload: { bills: serializeBillsForDraft(), activeId },
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
        const restoredBills = (payload.bills || []).map((b) => ({
            ...b,
            // The raw file blob isn't persisted, but the R2-backed source PDF is
            // re-fetched lazily below (Phase 6 — source-doc preview on reopen).
            file: null,
            pdfData: null,
            r2_key: b.r2_key || null,
            file_url: b.file_url || null,
        }));
        setBills(restoredBills);
        setActiveId(payload.activeId && restoredBills.some((b) => b.id === payload.activeId)
            ? payload.activeId
            : null);
        setDraftId(draft.id);
        setDraftsPanelOpen(false);
        setError("");
        // Best-effort: resolve a short-lived presigned URL for each bill that
        // carries an R2 key so the PDF preview pane shows the source document
        // instead of "No preview available". Never blocks or fails the reopen.
        restoredBills.forEach(async (b) => {
            if (!b.r2_key) return;
            try {
                const res = await zohoApi.drafts.getFileUrl(b.r2_key);
                if (res?.url) patchBill(b.id, { pdfData: res.url });
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

    // ── Active-bill mutators (unchanged from single-bill flow) ─────
    const updateVendor = (name) => {
        const v = vendorByName[name];
        if (!v) return;
        setResult((prev) => {
            const next = structuredClone(prev);
            next.zoho_payload.vendor_id = v.list_id;
            next.zoho_payload.vendor_name = v.name;
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
            next.zoho_payload = next.zoho_payload || {};
            next.zoho_payload.line_items = next.zoho_payload.line_items || [];
            next.line_review = next.line_review || [];
            const row = next.line_review[idx] || (next.line_review[idx] = {});
            const line = next.zoho_payload.line_items[idx] || (next.zoho_payload.line_items[idx] = {});

            const recomputeLineNet = () => {
                const gross = Number(row.gross_amount ?? row.amount) || 0;
                const discAmt = Number(row.discount_amount) || 0;
                const discPct = Number(row.discount_percent) || 0;
                const discount = discAmt > 0 ? discAmt : gross * (discPct / 100);
                return Math.max(gross - discount, 0);
            };
            const syncLineNet = (net) => {
                line.rate = Number(net) || 0;
                line.quantity = 1;
            };

            if (patch.description !== undefined) {
                row.description = patch.description;
                line.description = patch.description;
                line.name = patch.description;
            }
            if (patch.gross_amount !== undefined) {
                row.gross_amount = patch.gross_amount === "" ? "" : Number(patch.gross_amount);
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
                if (!row.gross_amount && !Number(row.discount_amount) && !Number(row.discount_percent)) {
                    row.gross_amount = row.amount;
                }
                syncLineNet(row.amount);
            } else if (
                patch.gross_amount !== undefined
                || patch.discount_percent !== undefined
                || patch.discount_amount !== undefined
            ) {
                const net = recomputeLineNet();
                row.amount = net;
                syncLineNet(net);
            }
            if (patch.accountName !== undefined) {
                const acc = patch.accountObj || accountByName[patch.accountName];
                if (acc && line) {
                    line.account_id = acc.list_id;
                    line.account_name = acc.name;
                    row.match = {
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

    // Edit the document header. Mirrors the human-friendly `extracted` view and
    // the Zoho payload that gets pushed (date / bill_number / due_date).
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
                next.zoho_payload.bill_number = patch.doc_number;
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
                gross_amount: 0,
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

    const setVendorOnPayload = (v, method = "manual") => {
        setResult((prev) => {
            const next = structuredClone(prev);
            next.zoho_payload.vendor_id = v.list_id;
            next.zoho_payload.vendor_name = v.name;
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
            const line = next.zoho_payload?.line_items?.[idx];
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
            await zohoApi.saveAlias({
                entity_type: "vendor",
                alias_text: result?.extracted?.vendor_or_customer || result?.vendor_match?.candidate_name || "",
                zoho_entity_id: cand.list_id,
                zoho_entity_name: cand.name,
                organization_id: company,
            });
        } catch (_) { /* non-fatal */ }
    };

    const onPickAccountCandidate = async (idx, cand) => {
        setAccountOnLine(idx, cand, "user_picked");
        try {
            await zohoApi.saveAlias({
                entity_type: "account",
                alias_text: result?.line_review?.[idx]?.description
                    || result?.line_review?.[idx]?.match?.candidate_name
                    || "",
                zoho_entity_id: cand.list_id,
                zoho_entity_name: cand.name,
                organization_id: company,
            });
        } catch (_) { /* non-fatal */ }
    };

    const onCreatedFromModal = (created) => {
        if (!createModal) return;
        if (createModal.entityType === "vendor") {
            setVendors((prev) => [...prev, created]);
            setVendorOnPayload(created, "created");
        } else if (createModal.entityType === "account") {
            setAccounts((prev) => [...prev, { ...created, account_type: created.account_type || "expense" }]);
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
                    account_type: "expense",
                    date,
                    amount: row.amount != null ? row.amount : total,
                });
            }
        });
        return out;
    };

    const onBulkComplete = (results = []) => {
        const okResults = results.filter((r) => r.ok && r.zoho_entity_id);
        okResults.forEach((r) => {
            const entity = { list_id: String(r.zoho_entity_id), name: r.zoho_entity_name };
            if (r.entity_type === "vendor") {
                setVendors((prev) => [...prev, entity]);
                setVendorOnPayload(entity, "bulk_created");
            } else if (r.entity_type === "account") {
                setAccounts((prev) => [...prev, { ...entity, account_type: "expense" }]);
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
            icon={Receipt}
            title="Zoho Books · Purchase Bill"
            description="Upload purchase bill PDFs, review extracted lines, and push to Zoho Books."
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
                            {loadingCompanies ? "Companies…" : "Loading vendors + accounts…"}
                        </span>
                    )}
                    {mastersReady && (
                        <span className="text-xs text-ink-500 tabular-nums">
                            {vendors.length} vendors · {accounts.length} expense accounts
                        </span>
                    )}
                    <button
                        type="button"
                        onClick={() => setRulesOpen(true)}
                        disabled={!company}
                        className="inline-flex items-center gap-1.5 px-2.5 py-1.5 text-xs font-medium text-ink-700 bg-white border border-ink-200 rounded-lg hover:bg-ink-50 transition-colors disabled:opacity-50"
                        title="Manage mapping rules that auto-assign vendors/expense accounts"
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
            documentType="purchase_invoice"
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
    if (bills.length === 0) {
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
                                    <FileText className="h-8 w-8 text-indigo-500" />
                                </div>
                                <div>
                                    <p className="text-sm font-medium text-gray-700">Drop PDF bills here or click to browse</p>
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
                                <li>Upload one or more purchase bill PDFs</li>
                                <li>Each bill is extracted in parallel — review individually</li>
                                <li>Push them one by one, or "Push all" to send everything ready</li>
                            </ol>
                        </div>
                        <DraftsPanel
                            open={draftsPanelOpen}
                            onToggle={() => setDraftsPanelOpen((o) => !o)}
                            loadList={() => zohoApi.drafts.list({ flow: "purchase", organizationId: company })}
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
    // BRANCH 2: grid/list of bills — no active editor
    // ══════════════════════════════════════════════════════════════
    if (!activeBill) {
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
                            className={btnZohoGreen}
                            title={counts.ready ? `Push the ${counts.ready} ready bill${counts.ready === 1 ? "" : "s"} to Zoho Books` : "No ready bills to push"}
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
                        loadList={() => zohoApi.drafts.list({ flow: "purchase", organizationId: company })}
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
    // BRANCH 3: editor for active bill
    // ══════════════════════════════════════════════════════════════
    const extractDisabled = loadingMasters || loadingCompanies || !company;
    return (
        <div className="h-screen flex flex-col bg-gray-50 text-gray-900">
            {TopBar}
            {ErrorBanner}

            <div className="bg-white border-b border-gray-200 px-6 py-2.5 flex items-center justify-between flex-shrink-0 flex-wrap gap-3">
                <div className="flex items-center gap-3 min-w-0">
                    <button
                        onClick={() => setActiveId(null)}
                        className="p-1.5 hover:bg-gray-100 rounded-lg transition-colors"
                        title="Back to all bills"
                    >
                        <ArrowLeft className="h-4 w-4 text-gray-600" />
                    </button>
                    <FileText className="h-4 w-4 text-gray-400 flex-shrink-0" />
                    <h2 className="text-sm font-semibold text-gray-900 truncate">{activeBill.filename}</h2>
                    <BillStatusBadge bill={activeBill} />
                </div>
                <div className="flex items-center gap-2 text-xs text-gray-500">
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
                                <div className="h-full bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden">
                                    <PdfViewer pdfData={activeBill.pdfData} />
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
                            {activeBill.status === "pending" || activeBill.status === "processing" ? (
                                <div className="flex flex-col items-center justify-center h-full bg-white rounded-xl shadow-sm border border-gray-200 text-gray-600 gap-3 p-8">
                                    <Loader2 className="h-8 w-8 animate-spin text-indigo-500" />
                                    <div className="text-sm font-medium text-gray-700">Extracting bill…</div>
                                    <div className="text-xs text-gray-500">Reading PDF and matching to Zoho Books vendors + accounts.</div>
                                </div>
                            ) : activeBill.status === "failed" ? (
                                <div className="flex flex-col items-center justify-center h-full bg-white rounded-xl shadow-sm border border-gray-200 gap-3 p-8 text-center">
                                    <AlertTriangle className="h-10 w-10 text-red-300" />
                                    <div className="text-sm font-medium text-red-700">Extraction failed</div>
                                    <div className="text-xs text-gray-500 max-w-md">{activeBill.error || "Unknown error"}</div>
                                </div>
                            ) : result ? (
                                <EditPanel
                                    result={result}
                                    vendorNames={vendorNames}
                                    accountNames={accountNames}
                                    updateVendor={updateVendor}
                                    updateLine={updateLine}
                                    updateHeader={updateHeader}
                                    addLine={addLine}
                                    removeLine={removeLine}
                                    onPush={onPush}
                                    pushing={pushing}
                                    pushStatus={activeBill.pushStatus}
                                    pushError={activeBill.pushError}
                                    duplicateCheck={activeBill?.result?.duplicate_check}
                                    duplicateBlocked={isBlockedDuplicate(activeBill)}
                                    duplicateInBatch={batchDuplicates.has(activeBill?.id)}
                                    onOverrideDuplicate={() => overridePush(activeBill.id)}
                                    onPickVendorCandidate={onPickVendorCandidate}
                                    onPickAccountCandidate={onPickAccountCandidate}
                                    onCreateVendor={(prefill) => setCreateModal({ entityType: "vendor", prefillName: prefill })}
                                    onCreateAccount={(prefill, idx) =>
                                        setCreateDialog({ lineIdx: idx, accountType: "expense", initialName: prefill || "" })
                                    }
                                    onOpenBulk={() => setBulkOpen(true)}
                                    unmatchedCount={gatherUnmatchedForBulk().length}
                                    organizationId={company}
                                />
                            ) : null}
                            {activeBill.pushResult && (
                                <div className="bg-emerald-50 border border-emerald-200 rounded-xl p-4 mt-3">
                                    <div className="flex items-center gap-2 mb-2">
                                        <CheckCircle2 className="h-4 w-4 text-emerald-600" />
                                        <h2 className="text-sm font-semibold text-emerald-800 uppercase tracking-wider">Pushed successfully</h2>
                                    </div>
                                    <pre className="text-xs overflow-auto max-h-64 bg-white border border-emerald-200 rounded-lg p-3 text-gray-700">{JSON.stringify(activeBill.pushResult, null, 2)}</pre>
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
            <AccountCreatePrompt
                open={!!createDialog}
                initialName={createDialog?.initialName || ""}
                accountType={createDialog?.accountType}
                api={zohoApi}
                companyId={company}
                companyParam="organization_id"
                onClose={() => setCreateDialog(null)}
                onCreated={(acc) => {
                    if (!acc?.name) {
                        setCreateDialog(null);
                        return;
                    }
                    setAccounts((prev) => {
                        if (prev.some((a) => a.list_id === acc.list_id || a.name === acc.name)) return prev;
                        return [...prev, { list_id: acc.list_id, name: acc.name, account_type: acc.account_type }];
                    });
                    if (typeof createDialog?.lineIdx === "number") {
                        updateLine(createDialog.lineIdx, {
                            accountName: acc.name,
                            accountObj: { list_id: acc.list_id, name: acc.name, account_type: acc.account_type },
                        });
                    }
                    setCreateDialog(null);
                }}
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

function BillStatusBadge({ bill }) {
    const { status, pushStatus } = bill;
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

function BillCard({ bill, overridden, onOpen, onRemove, onRetry, onOverride }) {
    const total = bill.result?.extracted?.total;
    const vendor = bill.result?.extracted?.vendor_or_customer || bill.result?.vendor_match?.name;
    const lineCount = bill.result?.line_review?.length || 0;
    const needsReview = bill.result?.needs_review;
    const isOpenable = bill.status !== "pending";
    const dup = bill.result?.duplicate_check;
    const blockedDup = dup?.is_duplicate && !overridden && bill.pushStatus !== "pushed";
    return (
        <div className="group bg-white rounded-xl shadow-sm border border-gray-200 hover:border-indigo-300 hover:shadow-md transition-all overflow-hidden flex flex-col">
            <button
                onClick={isOpenable ? onOpen : undefined}
                disabled={!isOpenable}
                className="px-4 pt-4 pb-3 text-left flex-1 disabled:cursor-default"
            >
                <div className="flex items-start justify-between gap-2 mb-2">
                    <FileText className="h-4 w-4 text-gray-400 flex-shrink-0 mt-0.5" />
                    <span className="text-sm font-medium text-gray-900 truncate flex-1" title={bill.filename}>
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
                            <div className="flex items-center gap-1.5 text-gray-700 truncate">
                                <Building2 className="h-3 w-3 text-gray-400 flex-shrink-0" />
                                <span className="truncate" title={vendor}>{vendor}</span>
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
                {bill.status === "failed" && (
                    <p className="text-xs text-red-600 line-clamp-2" title={bill.error}>
                        {bill.error || "Extraction failed"}
                    </p>
                )}
                {bill.pushStatus === "failed" && (
                    <p className="text-xs text-red-600 mt-2 line-clamp-2" title={bill.pushError}>
                        Push: {bill.pushError || "failed"}
                    </p>
                )}
            </button>
            <div className="px-3 py-2 border-t border-gray-100 bg-gray-50 flex items-center justify-between gap-2">
                <div className="flex items-center gap-1">
                    {blockedDup && (
                        <button
                            onClick={(e) => { e.stopPropagation?.(); onOverride?.(); }}
                            className="text-[11px] font-medium text-amber-700 hover:text-amber-900 hover:bg-amber-100 px-2 py-1 rounded inline-flex items-center gap-1 transition-colors"
                            title="Push this bill even though it looks like a duplicate"
                        >
                            Push anyway
                        </button>
                    )}
                    {bill.pushStatus === "failed" && (
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

function BillList({ bills, overridePushIds = new Set(), onOpen, onRemove, onRetry, onOverride }) {
    return (
        <div className="bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden">
            <div className="overflow-x-auto">
                <table className="w-full text-sm">
                    <thead className="bg-gray-50">
                        <tr>
                            <th className="px-3 py-2.5 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">File</th>
                            <th className="px-3 py-2.5 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Status</th>
                            <th className="px-3 py-2.5 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Vendor</th>
                            <th className="px-3 py-2.5 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">Total</th>
                            <th className="px-3 py-2.5 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Lines</th>
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
                                            <BillStatusBadge bill={b} />
                                            <DuplicateBadge check={b.result?.duplicate_check} />
                                        </div>
                                    </td>
                                    <td className="px-3 py-2 text-sm text-gray-700 truncate max-w-xs" title={vendor || ""}>{vendor || "—"}</td>
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
    result, vendorNames, accountNames,
    updateVendor, updateLine, updateHeader, addLine, removeLine,
    onPush, pushing, pushStatus, pushError,
    duplicateCheck, duplicateBlocked, duplicateInBatch, onOverrideDuplicate,
    onPickVendorCandidate, onPickAccountCandidate,
    onCreateVendor, onCreateAccount, organizationId,
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
            const gross = Number(row.gross_amount ?? row.amount) || 0;
            const discAmt = Number(row.discount_amount) || 0;
            const discPct = Number(row.discount_percent) || 0;
            const lineDisc = discAmt > 0 ? discAmt : gross * (discPct / 100);
            subtotal += Math.max(gross - lineDisc, 0);
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
                        <label className={labelBase}>Vendor</label>
                        <div className="flex items-center gap-2">
                            <div className="flex-1 min-w-0">
                                <AISuggestedDropdown
                                    options={vendorNames}
                                    value={result?.zoho_payload?.vendor_name || ""}
                                    onChange={updateVendor}
                                    onCreateNew={() => onCreateVendor(result?.extracted?.vendor_or_customer || result?.vendor_match?.candidate_name || "")}
                                    placeholder="Pick vendor…"
                                    aiSuggestion={aiSuggestionFromEntityMatch(result?.vendor_match, "vendor")}
                                    erpLabel="Zoho Books"
                                    onOverride={(picked, ai) => zohoApi.recordSuggestionFeedback({
                                        organization_id: organizationId, slot: "party",
                                        context_text: result?.extracted?.vendor_or_customer,
                                        suggested_id: result?.vendor_match?.list_id,
                                        suggested_name: ai?.recommended_ledger,
                                        suggested_confidence: ai?.confidence,
                                        suggested_method: result?.vendor_match?.method,
                                        picked_name: picked,
                                    })}
                                />
                            </div>
                            <MatchOriginBadge match={result?.vendor_match} />
                        </div>
                    </div>
                    <EditableField icon={Calendar} label="Date" type="date" value={result?.extracted?.doc_date || ""} onChange={(v) => updateHeader({ doc_date: v })} />
                    <EditableField icon={Hash} label="Bill #" value={result?.extracted?.doc_number || ""} onChange={(v) => updateHeader({ doc_number: v })} />
                    <Field icon={DollarSign} label="Total" value={fmtAmount(totals.total, currency)} />
                </div>
                {!result?.vendor_match?.matched && (
                    <div className="mt-4 space-y-2">
                        <div className="flex items-start gap-2 bg-amber-50 border border-amber-200 text-amber-800 px-3 py-2 rounded-lg text-xs">
                            <AlertTriangle className="h-3.5 w-3.5 flex-shrink-0 mt-0.5 text-amber-600" />
                            <span>No matching Zoho Books vendor found. Pick one above, choose a suggestion, or create new.</span>
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
                            title="Add a new line"
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
                                <th className="px-2 py-2.5 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">Amount</th>
                                <th className="px-2 py-2.5 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">Disc&nbsp;%</th>
                                <th className="px-2 py-2.5 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">Disc&nbsp;{currency || "₹"}</th>
                                <th className="px-2 py-2.5 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">Net</th>
                                <th className="px-3 py-2.5 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Expense account</th>
                                <th className="px-2 py-2.5 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Conf.</th>
                                <th className="px-2 py-2.5"></th>
                            </tr>
                        </thead>
                        <tbody>
                            {(result.line_review || []).map((row, i) => {
                                const gross = Number(row.gross_amount ?? row.amount) || 0;
                                const discAmt = Number(row.discount_amount) || 0;
                                const discPct = Number(row.discount_percent) || 0;
                                const lineDisc = discAmt > 0 ? discAmt : gross * (discPct / 100);
                                const net = Math.max(gross - lineDisc, 0);
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
                                                className={`${tableNumInput} w-24`}
                                                type="number"
                                                step="0.01"
                                                value={row.gross_amount ?? row.amount ?? 0}
                                                onChange={(e) => updateLine(i, { gross_amount: e.target.value })}
                                            />
                                        </td>
                                        <td className="px-2 py-2">
                                            <input
                                                className={`${tableNumInput} w-20`}
                                                type="number"
                                                step="0.01"
                                                value={row.discount_percent ?? 0}
                                                onChange={(e) => updateLine(i, { discount_percent: e.target.value })}
                                                title="Discount percent on this line"
                                            />
                                        </td>
                                        <td className="px-2 py-2">
                                            <input
                                                className={`${tableNumInput} w-24`}
                                                type="number"
                                                step="0.01"
                                                value={row.discount_amount ?? 0}
                                                onChange={(e) => updateLine(i, { discount_amount: e.target.value })}
                                                title="Absolute discount on this line — overrides Disc % when > 0"
                                            />
                                        </td>
                                        <td className="px-2 py-2">
                                            <input
                                                className={`${tableNumInputComputed} w-28`}
                                                type="number"
                                                step="0.01"
                                                value={Number(net.toFixed(2))}
                                                onChange={(e) => updateLine(i, { amount: e.target.value })}
                                                title="Amount that posts to Zoho Books (gross − discount). Edit to override."
                                            />
                                        </td>
                                        <td className="px-3 py-2">
                                            <SearchableDropdown
                                                options={accountNames}
                                                value={row.match?.name || ""}
                                                onChange={(name) => updateLine(i, { accountName: name })}
                                                placeholder="Pick account…"
                                                createLabel="+ Create new account"
                                                onCreateNew={onCreateAccount ? (typed) => onCreateAccount(typed || row.description || row.match?.candidate_name || "", i) : undefined}
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
                                        <td className="px-2 py-2">
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
                                    <td colSpan={8} className="px-3 py-6 text-center text-xs text-gray-400">
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
                    title={duplicateBlocked ? "This bill looks like a duplicate. Use \"Push anyway\" to override." : undefined}
                >
                    {pushing ? <Loader2 className="h-4 w-4 animate-spin" /> : pushStatus === "pushed" ? <CheckCircle2 className="h-4 w-4" /> : <Send className="h-4 w-4" />}
                    {pushing ? "Pushing to Zoho Books…" : pushStatus === "pushed" ? "Pushed" : "Push to Zoho Books"}
                </button>
                {duplicateBlocked && (
                    <>
                        {duplicateInBatch && !duplicateCheck?.is_duplicate ? (
                            <span className="inline-flex items-center gap-1 px-2 py-0.5 text-[11px] font-medium rounded-full bg-amber-50 border border-amber-200 text-amber-700" title="Another file in this upload has the same bill number and vendor.">
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

// Editable header field — the user can correct the parsed date / bill number
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
