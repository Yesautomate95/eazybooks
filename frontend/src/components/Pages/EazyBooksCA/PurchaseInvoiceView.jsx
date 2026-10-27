import React, { useState, useEffect, useRef, useCallback, useMemo } from "react";
import { createPortal } from "react-dom";
import {
  ArrowLeft,
  Send,
  CheckCircle,
  AlertCircle,
  Plus,
  Trash2,
  Upload,
  FileText,
  RefreshCw,
  Eye,
  EyeOff,
  X,
  Search,
  ChevronDown,
  Wifi,
  WifiOff,
  Package,
  BookOpen,
  Warehouse,
  Info,
  Grid3x3,
  List as ListIcon,
  Clock,
  Percent,
  Settings,
} from "lucide-react";
import { Panel, Group as PanelGroup, Separator as PanelResizeHandle } from "react-resizable-panels";
import { Document, Page } from "react-pdf";
import { tallyApi } from "../../../services/api";
import PdfViewer from "./PdfViewer";
import AISuggestedDropdown from "./common/AISuggestedDropdown";
import MappingRulesPanel from "./common/MappingRulesPanel";
import { SaveDraftButton } from "./common/DraftsPanel";
import LedgerCreateModal from "./common/LedgerCreateModal";
import { findInBatchDuplicates, isPushBlockedByBatchDuplicate } from "./common/duplicateDetection";

// ── Indian States for Place of Supply dropdown ──
const INDIAN_STATES = [
  "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh",
  "Goa", "Gujarat", "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka",
  "Kerala", "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya", "Mizoram",
  "Nagaland", "Odisha", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu",
  "Telangana", "Tripura", "Uttar Pradesh", "Uttarakhand", "West Bengal",
  "Andaman and Nicobar Islands", "Chandigarh", "Dadra and Nagar Haveli and Daman and Diu",
  "Delhi", "Jammu and Kashmir", "Ladakh", "Lakshadweep", "Puducherry",
];

// ── Accepted invoice file types (PDF + images) ──
const ACCEPTED_FILE_EXT = ".pdf,.jpg,.jpeg,.png,.gif,.bmp,.webp,.tif,.tiff";
const IMAGE_MIME_PREFIX = "image/";
const isAcceptedInvoiceFile = (file) =>
  !!file && (file.type === "application/pdf" || (file.type || "").startsWith(IMAGE_MIME_PREFIX));
const isImageMime = (mime) => !!mime && mime.startsWith(IMAGE_MIME_PREFIX);

const extractErrorMessage = (err, fallback = "An error occurred") => {
  const detail = err?.response?.data?.detail;
  if (!detail) return err?.response?.data?.error || err?.message || fallback;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((d) => (typeof d === "string" ? d : d.msg || JSON.stringify(d))).join("; ");
  if (typeof detail === "object") return detail.msg || JSON.stringify(detail);
  return fallback;
};

// ── Searchable Dropdown ──
// Renders the popup via a React portal pinned to document.body so it can't be
// clipped by ancestors with `overflow: auto/hidden` — the editor uses
// resizable panels and `overflow-x-auto` table containers, both of which would
// otherwise crop the list to the cell's width / panel's height.
const InvoiceDropdown = ({ options, value, onChange, onCreateNew, placeholder = "Select...", label }) => {
  const [isOpen, setIsOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [dropdownStyle, setDropdownStyle] = useState({});
  const triggerRef = useRef(null);
  const dropdownRef = useRef(null);
  const filtered = (options || []).filter((o) => o.toLowerCase().includes(search.toLowerCase()));

  useEffect(() => {
    const handler = (e) => {
      if (triggerRef.current?.contains(e.target)) return;
      if (dropdownRef.current?.contains(e.target)) return;
      setIsOpen(false);
      setSearch("");
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  useEffect(() => {
    if (!isOpen || !triggerRef.current) return;
    const updatePosition = () => {
      const rect = triggerRef.current.getBoundingClientRect();
      const spaceBelow = window.innerHeight - rect.bottom;
      const spaceAbove = rect.top;
      const desired = 384;
      const style = {
        position: "fixed",
        left: rect.left,
        width: Math.max(rect.width, 240),
        zIndex: 9999,
      };
      if (spaceBelow < desired && spaceAbove > spaceBelow) {
        style.bottom = window.innerHeight - rect.top + 4;
        style.maxHeight = Math.max(160, spaceAbove - 16);
      } else {
        style.top = rect.bottom + 4;
        style.maxHeight = Math.max(160, spaceBelow - 16);
      }
      if (style.left + style.width > window.innerWidth - 8) {
        style.left = Math.max(8, window.innerWidth - style.width - 8);
      }
      setDropdownStyle(style);
    };
    updatePosition();
    window.addEventListener("scroll", updatePosition, true);
    window.addEventListener("resize", updatePosition);
    return () => {
      window.removeEventListener("scroll", updatePosition, true);
      window.removeEventListener("resize", updatePosition);
    };
  }, [isOpen]);

  return (
    <div className="relative w-full">
      <div
        ref={triggerRef}
        className={`flex items-center justify-between px-3 py-2 text-sm border rounded-lg cursor-pointer transition-colors focus-within:border-gilt-brass ${
          value ? "border-gilt-hairline bg-gilt-card text-gilt-ink" : "border-gilt-hairline bg-gilt-card text-gilt-faint"
        } hover:border-gilt-brass`}
        onClick={() => setIsOpen(!isOpen)}
      >
        <span className={`truncate ${value ? "text-gilt-ink" : "text-gilt-faint"}`}>{value || placeholder}</span>
        <ChevronDown className={`h-4 w-4 text-gilt-faint flex-shrink-0 transition-transform ${isOpen ? "rotate-180" : ""}`} />
      </div>
      {isOpen && createPortal(
        <div
          ref={dropdownRef}
          style={dropdownStyle}
          className="bg-gilt-card border border-gilt-hairline rounded-lg shadow-sm overflow-hidden flex flex-col"
        >
          <div className="p-2 border-b border-gilt-hairline bg-gilt-sunken shrink-0">
            <div className="flex items-center gap-2 px-2 py-1.5 bg-gilt-card rounded-md border border-gilt-hairline">
              <Search className="h-3.5 w-3.5 text-gilt-faint" />
              <input
                type="text"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="flex-1 text-sm bg-transparent outline-none text-gilt-ink placeholder:text-gilt-faint"
                placeholder="Search..."
                autoFocus
                onClick={(e) => e.stopPropagation()}
              />
              <span className="text-xs text-gilt-soft font-mono tabular-nums">{filtered.length}</span>
            </div>
          </div>
          <div className="overflow-y-auto flex-1">
            {onCreateNew && (
              <div
                className="flex items-center gap-2 px-3 py-2 text-sm text-gilt-brass hover:bg-gilt-sunken cursor-pointer border-b border-gilt-hairline transition-colors"
                onClick={() => { onCreateNew(); setIsOpen(false); setSearch(""); }}
              >
                <Plus className="h-3.5 w-3.5" />
                Create New
              </div>
            )}
            {filtered.length === 0 ? (
              <div className="px-3 py-4 text-sm text-gilt-faint text-center">No results found</div>
            ) : (
              filtered.map((opt, i) => (
                <div
                  key={i}
                  className={`px-3 py-2.5 text-sm cursor-pointer transition-colors border-b border-gilt-hairline/50 last:border-b-0 ${
                    opt === value ? "bg-gilt-sunken font-medium text-gilt-brass" : "text-gilt-ink hover:bg-gilt-sunken"
                  }`}
                  onClick={() => { onChange(opt); setIsOpen(false); setSearch(""); }}
                >
                  {opt}
                </div>
              ))
            )}
          </div>
        </div>,
        document.body
      )}
    </div>
  );
};

// ── Status Toast ──
const StatusToast = ({ type, message, onDismiss }) => {
  if (!message) return null;
  const config = {
    success: { bg: "bg-gilt-sunken border-gilt-credit/40", text: "text-gilt-credit", icon: <CheckCircle className="h-5 w-5 text-gilt-credit flex-shrink-0" /> },
    error:   { bg: "bg-gilt-sunken border-gilt-debit/40",  text: "text-gilt-debit",  icon: <AlertCircle className="h-5 w-5 text-gilt-debit flex-shrink-0" /> },
    warning: { bg: "bg-gilt-sunken border-gilt-brass/40",  text: "text-gilt-brass",  icon: <AlertCircle className="h-5 w-5 text-gilt-brass flex-shrink-0" /> },
    info:    { bg: "bg-gilt-sunken border-gilt-hairline",  text: "text-gilt-soft",   icon: <Info className="h-5 w-5 text-gilt-brass flex-shrink-0" /> },
  };
  const c = config[type] || config.info;
  return (
    <div className={`flex items-start gap-3 px-4 py-3 rounded-xl border ${c.bg} ${c.text} animate-in fade-in`}>
      {c.icon}
      <p className="text-sm flex-1 leading-relaxed">{typeof message === "string" ? message : JSON.stringify(message)}</p>
      {onDismiss && (
        <button onClick={onDismiss} className="p-0.5 hover:opacity-70 flex-shrink-0">
          <X className="h-4 w-4" />
        </button>
      )}
    </div>
  );
};

const isValidGstin = (gstin) => /^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$/.test(gstin);

const toTallyDate = (dateStr) => {
  if (!dateStr) return "";
  if (/^\d{8}$/.test(dateStr)) return dateStr;
  if (/^\d{4}-\d{2}-\d{2}$/.test(dateStr)) return dateStr.replace(/-/g, "");
  const match = dateStr.match(/^(\d{2})[\/\-](\d{2})[\/\-](\d{4})$/);
  if (match) return `${match[3]}${match[2]}${match[1]}`;
  return dateStr;
};

const toInputDate = (dateStr) => {
  if (!dateStr) return "";
  if (/^\d{4}-\d{2}-\d{2}$/.test(dateStr)) return dateStr;
  if (/^\d{8}$/.test(dateStr)) return `${dateStr.slice(0, 4)}-${dateStr.slice(4, 6)}-${dateStr.slice(6, 8)}`;
  const match = dateStr.match(/^(\d{2})[\/\-](\d{2})[\/\-](\d{4})$/);
  if (match) return `${match[3]}-${match[2]}-${match[1]}`;
  return dateStr;
};

// ── Transform extracted invoice payload to editor shape ──
const buildEditorInvoice = (inv) => {
  const hasSalesFormat = !!inv.header;
  let header, rawItems, rawTaxes, summaryData, confidenceData;

  if (hasSalesFormat) {
    header = inv.header || {};
    rawItems = inv.items || [];
    rawTaxes = inv.taxes || inv.tax_entries || [];
    summaryData = inv.summary || {};
    confidenceData = header.confidence || inv.confidence_scores || {};
  } else {
    header = {
      voucher_date: inv.invoice_date || inv.date || "",
      invoice_number: inv.invoice_number || inv.reference_number || "",
      party_name: inv.vendor_name || inv.credit_ledger || "",
      party_gstin: inv.vendor_gstin || inv.gstin || "",
      place_of_supply: inv.place_of_supply || "",
      narration: inv.description || inv.narration || "",
    };
    rawItems = inv.items || [];
    rawTaxes = inv.tax_entries || inv.taxes || [];
    summaryData = {
      subtotal: inv.subtotal || inv.amount || 0,
      tax_amount: inv.gst_amount || inv.tax_amount || 0,
      total_amount: inv.total_amount || 0,
      narration: inv.description || "",
    };
    confidenceData = inv.confidence_scores || {};
  }

  const items = rawItems.map((it) => ({
    item_name: it.item_name || it.description || it.name || "",
    quantity: parseFloat(it.quantity) || 0,
    rate: parseFloat(it.rate || it.price) || 0,
    amount: parseFloat(it.amount) || (parseFloat(it.quantity || 0) * parseFloat(it.rate || 0)),
    unit: it.unit || "Nos",
    discount_percent: parseFloat(it.discount_percent || it.discount_percentage) || 0,
    discount_amount: (() => {
      const q = parseFloat(it.quantity) || 0;
      const r = parseFloat(it.rate || it.price) || 0;
      const gross = q * r;
      const explicit = parseFloat(it.discount_amount);
      if (isFinite(explicit) && explicit > 0) {
        return gross > 0 && explicit > gross ? gross : explicit;
      }
      const pct = parseFloat(it.discount_percent || it.discount_percentage) || 0;
      return gross > 0 && pct > 0 ? Math.round((gross * pct / 100) * 100) / 100 : 0;
    })(),
    godown: it.godown || "Main Location",
    batch: it.batch || it.batch_number || "",
  }));

  const taxes = rawTaxes.map((t) => ({
    ledger_name: t.ledger_name || t.tax_name || t.name || "",
    description: t.description || "",
    amount: parseFloat(t.amount) || 0,
    ai_suggestion: t.ai_suggestion || null,
  }));

  return {
    data: {
      header: {
        voucher_date: header.voucher_date || header.date || header.invoice_date || "",
        invoice_number: header.invoice_number || header.number || header.reference_number || "",
        party_name: header.party_name || header.vendor_name || "",
        party_gstin: header.party_gstin || header.vendor_gstin || header.gstin || "",
        place_of_supply: header.place_of_supply || "",
        narration: header.narration || header.description || summaryData.narration || "",
      },
      items,
      taxes,
      summary: { narration: header.narration || summaryData.narration || "" },
    },
    confidence: confidenceData,
    rawHeader: header,
    rawItems,
    rawSummary: summaryData,
  };
};

// ── Card status badge ──
const StatusBadge = ({ status, pushStatus }) => {
  if (pushStatus === "success") return (
    <span className="flex items-center gap-1 px-2 py-0.5 font-mono text-[10px] uppercase tracking-[0.14em] text-gilt-credit bg-gilt-sunken border border-gilt-credit/40 rounded-full">
      <CheckCircle className="h-3 w-3" /> Pushed
    </span>
  );
  if (pushStatus === "error") return (
    <span className="flex items-center gap-1 px-2 py-0.5 font-mono text-[10px] uppercase tracking-[0.14em] text-gilt-debit bg-gilt-sunken border border-gilt-debit/40 rounded-full">
      <AlertCircle className="h-3 w-3" /> Push failed
    </span>
  );
  if (status === "pending") return (
    <span className="flex items-center gap-1 px-2 py-0.5 font-mono text-[10px] uppercase tracking-[0.14em] text-gilt-soft bg-gilt-sunken border border-gilt-hairline rounded-full">
      <Clock className="h-3 w-3" /> Queued
    </span>
  );
  if (status === "processing") return (
    <span className="flex items-center gap-1 px-2 py-0.5 font-mono text-[10px] uppercase tracking-[0.14em] text-gilt-brass bg-gilt-sunken border border-gilt-brass/40 rounded-full">
      <RefreshCw className="h-3 w-3 animate-spin" /> Extracting
    </span>
  );
  if (status === "done") return (
    <span className="flex items-center gap-1 px-2 py-0.5 font-mono text-[10px] uppercase tracking-[0.14em] text-gilt-credit bg-gilt-sunken border border-gilt-credit/40 rounded-full">
      <CheckCircle className="h-3 w-3" /> Ready
    </span>
  );
  if (status === "failed") return (
    <span className="flex items-center gap-1 px-2 py-0.5 font-mono text-[10px] uppercase tracking-[0.14em] text-gilt-debit bg-gilt-sunken border border-gilt-debit/40 rounded-full">
      <AlertCircle className="h-3 w-3" /> Failed
    </span>
  );
  return null;
};

// ══════════════════════════════════════════════════════════════════
// ██  PurchaseInvoiceView Component
// ══════════════════════════════════════════════════════════════════
export default function PurchaseInvoiceView({ onBack, selectedCompany, draftId: propDraftId = null, initialInvoices = null }) {
  // ── Master data state ──
  const [vendorLedgers, setVendorLedgers] = useState([]);
  const [purchaseLedgers, setPurchaseLedgers] = useState([]);
  const [taxLedgers, setTaxLedgers] = useState([]);
  // Discount ledger candidates — sourced from Tally's Indirect Expenses group.
  const [discountLedgers, setDiscountLedgers] = useState([]);
  const [stockItems, setStockItems] = useState([]);
  const [stockItemsWithUnits, setStockItemsWithUnits] = useState([]);
  const [godowns, setGodowns] = useState(["Main Location"]);
  const [parentGroups, setParentGroups] = useState([]);
  const [tallyConnected, setTallyConnected] = useState(null);
  const [masterDataLoaded, setMasterDataLoaded] = useState(false);

  // ── Multi-invoice state ──
  // Each invoice: { id, filename, status, data, confidence, pdfContent, error,
  //                 pushStatus, pushMessage, selectedPurchaseLedger,
  //                 aiVendorSuggestion, aiPurchaseLedgerSuggestion, aiSuggestLoading }
  const [invoices, setInvoices] = useState([]);
  const [activeId, setActiveId] = useState(null);
  const [gridLayout, setGridLayout] = useState("grid"); // grid | list
  const [showFailedOnly, setShowFailedOnly] = useState(false);

  // ── UI state ──
  const [showPdf, setShowPdf] = useState(true);
  // Side-panel toggle for the company-level mapping rules editor.
  // document_type='purchase_invoice' so the same rules feed Layer 1 of the
  // matcher whenever a purchase bill is uploaded.
  const [showMappingRules, setShowMappingRules] = useState(false);
  const [isDragging, setIsDragging] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [isPushingAll, setIsPushingAll] = useState(false);
  const [tallyDupModal, setTallyDupModal] = useState(null);
  const [batchStatus, setBatchStatus] = useState(null); // null | {type, message}

  // ── Modals ──
  const [showNewLedgerModal, setShowNewLedgerModal] = useState(false);
  // newLedgerType drives the shared modal's section visibility + default parent group.
  // Values: "party" | "purchase" | "tax"  (mapped to LedgerCreateModal's ledgerType prop)
  const [newLedgerType, setNewLedgerType] = useState("party");
  // Optional prefill (e.g. AI-suggested vendor name / GSTIN / state from the invoice)
  const [newLedgerPrefill, setNewLedgerPrefill] = useState({});
  const [isCreatingLedger, setIsCreatingLedger] = useState(false);
  const [showNewItemModal, setShowNewItemModal] = useState(false);
  const [newItem, setNewItem] = useState({ name: "", unit: "Nos", hsn_code: "" });
  const [isCreatingItem, setIsCreatingItem] = useState(false);

  // Drafts: the user explicitly saves the current invoices[] state via
  // SaveDraftButton. The library page (Tally.jsx) is the place to resume
  // a previous draft — this view stays focused on the active document.
  const [draftId, setDraftId] = useState(propDraftId);
  const [savingDraft, setSavingDraft] = useState(false);

  const fileInputRef = useRef(null);

  // Pre-populate invoices when opened from the Document Library.
  // Force-clear `aiSuggestLoading` on restore — older drafts saved mid-request
  // can carry `true` here, and we don't re-fire the suggestion on reopen, so
  // a stale `true` would pin the spinner indefinitely. Same for
  // ledgerSource / cacheRefreshedAt — they describe a specific historical
  // suggestion and would mislead the user if surfaced after reopen.
  useEffect(() => {
    if (!initialInvoices || initialInvoices.length === 0) return;
    const restored = initialInvoices.map((inv) => ({
      ...inv,
      aiSuggestLoading: false,
      ledgerSource: "live",
      cacheRefreshedAt: null,
    }));
    setInvoices(restored);
    setActiveId(restored[0]?.id ?? null);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Drafts ─────────────────────────────────────────────────────
  // Per-session draft: the whole invoices[] queue. PDFs aren't persisted —
  // pdfContent (object URL) is stripped on save; the preview pane will show
  // empty on reload, but every extracted/edited field survives.
  // Transient UI fields that must NOT survive a save:
  //   - pdfPreviewUrl     : presigned URL resolved at open time; expires.
  //   - aiSuggestLoading  : true while a /suggest-ledger request is in flight.
  //     If the user hits Save Draft mid-request, persisting `true` here pins
  //     the spinner on the reopened draft forever (no new request fires on
  //     reopen, so it can never be reset).
  //   - ledgerSource / cacheRefreshedAt : describe the current request's
  //     source. Stale once persisted.
  const serializeInvoicesForDraft = () =>
    invoices.map(({ pdfPreviewUrl, aiSuggestLoading, ledgerSource, cacheRefreshedAt, ...inv }) => ({
      ...inv,
      pdfContent: null,
    }));

  const deriveDraftTitle = () => {
    if (invoices.length === 0) return "Purchase draft";
    if (invoices.length === 1) return invoices[0].filename || "Purchase draft";
    return `${invoices.length} bills · ${invoices[0].filename || ""}`;
  };

  const handleSaveDraft = async () => {
    if (invoices.length === 0) return;
    setSavingDraft(true);
    try {
      const res = await tallyApi.drafts.save({
        flow: "purchase",
        payload: { invoices: serializeInvoicesForDraft(), activeId },
        title: deriveDraftTitle(),
        draftId,
        companyName: selectedCompany,
      });
      if (res?.draft_id) setDraftId(res.draft_id);
    } catch (e) {
      console.error("save draft failed", e);
    } finally {
      setSavingDraft(false);
    }
  };

  // ── Active invoice helpers ──
  const activeInvoice = invoices.find((inv) => inv.id === activeId) || null;

  const patchInvoice = useCallback((id, patch) => {
    setInvoices((prev) => prev.map((inv) => (inv.id === id ? (typeof patch === "function" ? { ...inv, ...patch(inv) } : { ...inv, ...patch }) : inv)));
  }, []);

  const patchActiveData = useCallback((updater) => {
    if (!activeId) return;
    setInvoices((prev) => prev.map((inv) => (inv.id === activeId ? { ...inv, data: updater(inv.data) } : inv)));
  }, [activeId]);

  // ── Computed values (active invoice) ──
  const subtotal = useMemo(() => {
    if (!activeInvoice?.data?.items) return 0;
    return activeInvoice.data.items.reduce((sum, item) => {
      const qty = parseFloat(item.quantity) || 0;
      const rate = parseFloat(item.rate) || 0;
      const discAmt = parseFloat(item.discount_amount) || 0;
      if (discAmt > 0) return sum + Math.max(qty * rate - discAmt, 0);
      const disc = parseFloat(item.discount_percent) || 0;
      return sum + qty * rate * (1 - disc / 100);
    }, 0);
  }, [activeInvoice?.data?.items]);

  // Flag in-batch duplicates by (gstin, invoice#) with fallback to
  // (vendor_name, invoice#). Recomputes on every invoice state change.
  const batchDuplicates = useMemo(() => findInBatchDuplicates(invoices), [invoices]);

  const itemsTotals = useMemo(() => {
    const items = activeInvoice?.data?.items || [];
    return items.reduce(
      (acc, item) => {
        const qty = parseFloat(item.quantity) || 0;
        const rate = parseFloat(item.rate) || 0;
        const gross = qty * rate;
        let disc = parseFloat(item.discount_amount) || 0;
        if (disc <= 0) {
          const pct = parseFloat(item.discount_percent) || 0;
          disc = gross > 0 && pct > 0 ? Math.round((gross * pct / 100) * 100) / 100 : 0;
        }
        acc.quantity += qty;
        acc.discount += disc;
        // amount = qty × rate (gross before discount)
        // net    = qty × rate − discount (what posts to Tally)
        acc.amount += gross;
        acc.net += Math.max(gross - disc, 0);
        return acc;
      },
      { quantity: 0, discount: 0, amount: 0, net: 0 }
    );
  }, [activeInvoice?.data?.items]);

  const taxAmount = useMemo(() => {
    if (!activeInvoice?.data?.taxes) return 0;
    return activeInvoice.data.taxes.reduce((sum, t) => sum + (parseFloat(t.amount) || 0), 0);
  }, [activeInvoice?.data?.taxes]);

  // ── TDS ledgers: fuzzy filter from all tax ledgers ──
  const tdsLedgers = useMemo(
    () => taxLedgers.filter((l) => {
      const lower = l.toLowerCase();
      return lower.includes("tds") || lower.includes("tax deducted");
    }),
    [taxLedgers]
  );

  // ── TDS computed amount (% of base = subtotal before taxes) ──
  const tdsAmount = useMemo(() => {
    if (!activeInvoice?.tds_ledger || !(parseFloat(activeInvoice?.tds_percent) > 0)) return 0;
    return Math.round(subtotal * (parseFloat(activeInvoice.tds_percent) || 0) / 100 * 100) / 100;
  }, [activeInvoice?.tds_ledger, activeInvoice?.tds_percent, subtotal]);

  const totalAmount = subtotal + taxAmount - tdsAmount;

  // ══════════════════════════════════════════════════════════════
  // EFFECT: Load all Tally master data on mount
  // ══════════════════════════════════════════════════════════════
  useEffect(() => {
    if (!selectedCompany) return;

    const loadMasterData = async () => {
      try {
        let isConnected = false;
        try {
          const directResult = await tallyApi.checkDirectConnection();
          isConnected = directResult?.connected ?? false;
        } catch {
          try {
            const wsResult = await tallyApi.checkConnection();
            isConnected = wsResult?.connected ?? false;
          } catch {
            isConnected = false;
          }
        }
        setTallyConnected(isConnected);

        const [groupedRes, ledgersRes, inventoryRes, godownsRes] = await Promise.allSettled([
          tallyApi.getGroupedLedgers(selectedCompany),
          tallyApi.getDirectLedgers(selectedCompany),
          tallyApi.getDirectInventory(selectedCompany),
          tallyApi.getGodowns(selectedCompany),
        ]);

        let groupedWorked = false;
        if (groupedRes.status === "fulfilled" && groupedRes.value) {
          const grouped = groupedRes.value;
          const partyData = grouped.party_ledgers || {};
          const vendors = [
            ...(partyData.sundry_debtors || []),
            ...(partyData.sundry_creditors || []),
          ].map((l) => l.name).filter(Boolean);

          const purchaseData = grouped.purchase_ledgers || {};
          const purchases = [
            ...(purchaseData.purchase_accounts || []),
            ...(purchaseData.direct_expenses || []),
            ...(purchaseData.indirect_expenses || []),
          ].map((l) => l.name).filter(Boolean);

          if (vendors.length > 0 || purchases.length > 0) {
            groupedWorked = true;
            setVendorLedgers(vendors.sort());
            setPurchaseLedgers(purchases.sort());
          }

          // Indirect Expenses → discount ledger options.
          const indirectExpenses = (purchaseData.indirect_expenses || [])
            .map((l) => l.name)
            .filter(Boolean);
          if (indirectExpenses.length > 0) {
            setDiscountLedgers([...new Set(indirectExpenses)].sort());
          }
        }

        if (ledgersRes.status === "fulfilled" && ledgersRes.value) {
          const allLedgers = ledgersRes.value.ledgers || ledgersRes.value.data || ledgersRes.value || [];
          const taxes = [];
          const groups = new Set();
          const PARTY_PARENTS = new Set(["Sundry Debtors", "Sundry Creditors"]);
          const PURCHASE_PARENTS = new Set(["Purchase Accounts", "Direct Expenses", "Indirect Expenses"]);
          const TAX_PARENTS = new Set(["Duties & Taxes"]);
          const fallbackVendors = [];
          const fallbackPurchases = [];

          // Tally stores TDS ledgers either directly under "Duties & Taxes" or
          // under sub-groups like "TDS Payable" / "TDS Receivable". Since Tally
          // only returns the immediate parent, we catch sub-groups by matching
          // the parent group name itself against TDS patterns.
          const ledgerArr = Array.isArray(allLedgers) ? allLedgers : [];
          const TDS_PARENT_RE = /tds|tax.?deducted/i;

          ledgerArr.forEach((l) => {
            const name = l.name || l.ledger_name || l;
            const parent = l.parent || l.parent_group || "";
            groups.add(parent);
            const underDutiesTax = TAX_PARENTS.has(parent) || TDS_PARENT_RE.test(parent);
            if (underDutiesTax) taxes.push(typeof name === "string" ? name : String(name));
            if (!groupedWorked) {
              if (PARTY_PARENTS.has(parent)) fallbackVendors.push(typeof name === "string" ? name : String(name));
              else if (PURCHASE_PARENTS.has(parent)) fallbackPurchases.push(typeof name === "string" ? name : String(name));
            }
          });

          setTaxLedgers((prev) => [...new Set([...prev, ...taxes])].sort());
          setParentGroups([...groups].filter(Boolean).sort());

          if (!groupedWorked) {
            if (fallbackVendors.length) setVendorLedgers(fallbackVendors.sort());
            if (fallbackPurchases.length) setPurchaseLedgers(fallbackPurchases.sort());
          }
        }

        if (inventoryRes.status === "fulfilled" && inventoryRes.value) {
          const items = inventoryRes.value.inventory || inventoryRes.value.items || inventoryRes.value.data || inventoryRes.value || [];
          const itemsArr = Array.isArray(items) ? items : [];
          const itemNames = itemsArr.map((it) => it.name || it.item_name || it).filter(Boolean);
          const withUnits = itemsArr.map((it) => ({ name: it.name || it.item_name || it, unit: it.unit || it.base_unit || "Nos" }));
          setStockItems((prev) => [...new Set([...prev, ...itemNames.map(String)])].sort());
          setStockItemsWithUnits((prev) => {
            const existing = new Map(prev.map((s) => [s.name, s]));
            withUnits.forEach((s) => existing.set(String(s.name), s));
            return [...existing.values()];
          });
        }

        if (godownsRes.status === "fulfilled" && godownsRes.value) {
          const gds = godownsRes.value.godowns || godownsRes.value.data || godownsRes.value || [];
          const gdNames = (Array.isArray(gds) ? gds : []).map((g) => g.name || g).filter(Boolean);
          if (gdNames.length > 0) setGodowns((prev) => [...new Set([...prev, ...gdNames.map(String)])]);
        }

        const gotData = groupedRes.status === "fulfilled" || ledgersRes.status === "fulfilled" || inventoryRes.status === "fulfilled";
        if (gotData && !isConnected) setTallyConnected(true);

        setMasterDataLoaded(true);
      } catch (err) {
        console.error("[PurchaseInvoiceView] Failed to load master data:", err);
        setMasterDataLoaded(true);
      }
    };

    loadMasterData();
  }, [selectedCompany]);

  // ══════════════════════════════════════════════════════════════
  // Multi-file upload with SSE
  // ══════════════════════════════════════════════════════════════
  const triggerAiSuggestion = useCallback((invId, rawInv) => {
    const header = rawInv.header || {};
    const rawItems = rawInv.items || [];
    const summaryData = rawInv.summary || {};
    const vendorName = header.party_name || rawInv.vendor_name || "";
    const vendorGstin = header.party_gstin || rawInv.vendor_gstin || "";

    if (!selectedCompany || (!vendorName && rawItems.length === 0)) return;

    patchInvoice(invId, { aiSuggestLoading: true });

    tallyApi.suggestLedger({
      voucher_type: "purchase_bill",
      vendor_name: vendorName,
      vendor_gstin: vendorGstin,
      invoice_number: header.invoice_number || rawInv.invoice_number || "",
      invoice_date: header.voucher_date || rawInv.invoice_date || "",
      company_name: selectedCompany,
      line_items: rawItems.map((it) => ({
        name: it.item_name || it.description || it.name || "",
        qty: parseFloat(it.quantity) || 0,
        rate: parseFloat(it.rate || it.price) || 0,
        amount: parseFloat(it.amount) || 0,
      })),
      total_amount: parseFloat(summaryData.total_amount || rawInv.total_amount) || 0,
      subtotal: parseFloat(summaryData.subtotal || rawInv.subtotal) || 0,
      tax_details: {
        cgst: 0, sgst: 0, igst: 0,
        total_tax: parseFloat(summaryData.tax_amount || rawInv.tax_amount) || 0,
      },
      hsn_codes: [],
    }).then((suggestion) => {
      const updates = {};
      // Track which side of the Tally bridge served this suggestion so the
      // editor can render an "offline / using cached ledgers" chip.
      updates.ledgerSource = suggestion.ledger_source || "live";
      updates.cacheRefreshedAt = suggestion.cache_refreshed_at || null;
      // Stash the original AI suggestion so we can include it in the push
      // payload — the backend uses this to detect overrides and record them
      // into ledger_suggestion_feedback / confirmation_source.
      if (suggestion.party_suggestion) {
        updates.originalAiPartySuggestion = suggestion.party_suggestion;
      }
      if (suggestion.expense_revenue_suggestion) {
        updates.originalAiExpenseSuggestion = suggestion.expense_revenue_suggestion;
      }
      if (suggestion.party_suggestion) {
        updates.aiVendorSuggestion = suggestion.party_suggestion;
        if (suggestion.action === "auto_select" && suggestion.party_suggestion.recommended_ledger) {
          const pickedVendor = suggestion.party_suggestion.recommended_ledger;
          setInvoices((prev) => prev.map((inv) => inv.id === invId && inv.data
            ? { ...inv, data: { ...inv.data, header: { ...inv.data.header, party_name: pickedVendor } } }
            : inv));
        }
      }
      if (suggestion.expense_revenue_suggestion) {
        updates.aiPurchaseLedgerSuggestion = suggestion.expense_revenue_suggestion;
        if (suggestion.expense_revenue_suggestion.confidence >= 0.85 && suggestion.expense_revenue_suggestion.recommended_ledger) {
          updates.selectedPurchaseLedger = suggestion.expense_revenue_suggestion.recommended_ledger;
        }
      }
      if (suggestion.discount_ledger_suggestion) {
        updates.aiDiscountLedgerSuggestion = suggestion.discount_ledger_suggestion;
        if (
          suggestion.discount_ledger_suggestion.confidence >= 0.85 &&
          suggestion.discount_ledger_suggestion.recommended_ledger
        ) {
          updates.selectedDiscountLedger = suggestion.discount_ledger_suggestion.recommended_ledger;
        }
      }
      patchInvoice(invId, { ...updates, aiSuggestLoading: false });
    }).catch((err) => {
      console.warn("[PurchaseInvoiceView] AI ledger suggestion failed:", err);
      patchInvoice(invId, { aiSuggestLoading: false });
    });
  }, [selectedCompany, patchInvoice]);

  const handleMultiFileUpload = useCallback(async (fileList) => {
    const acceptedFiles = Array.from(fileList).filter(isAcceptedInvoiceFile);
    if (acceptedFiles.length === 0) return;

    setBatchStatus(null);

    const newInvoices = acceptedFiles.map((f, i) => ({
      id: `${Date.now()}_${i}_${Math.random().toString(36).slice(2, 7)}`,
      filename: f.name,
      status: "pending",
      data: null,
      confidence: {},
      pdfContent: URL.createObjectURL(f),
      fileMime: f.type || "application/octet-stream",
      error: null,
      pushStatus: null,
      pushMessage: "",
      selectedPurchaseLedger: "",
      tds_ledger: "",
      tds_percent: 0,
      selectedDiscountLedger: "",
      aiVendorSuggestion: null,
      aiPurchaseLedgerSuggestion: null,
      aiDiscountLedgerSuggestion: null,
      aiSuggestLoading: false,
    }));

    setInvoices((prev) => [...prev, ...newInvoices]);
    setIsUploading(true);

    const idByIndex = newInvoices.map((inv) => inv.id);

    try {
      await tallyApi.uploadPurchaseInvoicesStream(acceptedFiles, selectedCompany || "", (event) => {
        if (event.type === "file_start") {
          const id = idByIndex[event.index];
          if (id) patchInvoice(id, { status: "processing" });
        } else if (event.type === "file_done") {
          const id = idByIndex[event.index];
          if (!id) return;
          const built = buildEditorInvoice(event.invoice);
          const backendMime = event.invoice.file_mime || "application/pdf";
          // Prefer the in-memory base64 for the current session (instant render,
          // no network roundtrip), but also stash the R2 URL so the draft can
          // be reopened later without re-uploading. base64 is stripped before
          // the draft is persisted; file_url survives.
          const sessionPdf = event.invoice.pdf_data
            ? `data:${backendMime};base64,${event.invoice.pdf_data}`
            : event.invoice.file_url || undefined;
          patchInvoice(id, {
            status: "done",
            data: built.data,
            confidence: built.confidence,
            pdfContent: sessionPdf,
            fileMime: backendMime,
            file_url: event.invoice.file_url || null,
            r2_key: event.invoice.r2_key || null,
            file_name: event.invoice.original_filename || null,
          });
          triggerAiSuggestion(id, event.invoice);
        } else if (event.type === "file_error") {
          const id = idByIndex[event.index];
          if (id) patchInvoice(id, { status: "failed", error: event.error });
        } else if (event.type === "batch_end") {
          setBatchStatus({
            type: event.failed > 0 ? "warning" : "success",
            message: `Batch complete: ${event.succeeded} succeeded, ${event.failed} failed (${event.total} total).`,
          });
        }
      });
    } catch (err) {
      console.error("[PurchaseInvoiceView] Batch upload failed:", err);
      setBatchStatus({ type: "error", message: extractErrorMessage(err, "Upload stream failed") });
      // Mark any still-pending as failed
      idByIndex.forEach((id) => patchInvoice(id, (inv) =>
        inv.status === "pending" || inv.status === "processing"
          ? { status: "failed", error: "Upload stream interrupted" }
          : {}));
    } finally {
      setIsUploading(false);
    }
  }, [selectedCompany, patchInvoice, triggerAiSuggestion]);

  // ── Drag and drop ──
  const handleDragOver = (e) => { e.preventDefault(); setIsDragging(true); };
  const handleDragLeave = () => setIsDragging(false);
  const handleDrop = (e) => {
    e.preventDefault();
    setIsDragging(false);
    if (e.dataTransfer.files?.length) handleMultiFileUpload(e.dataTransfer.files);
  };

  // ══════════════════════════════════════════════════════════════
  // Active invoice field handlers
  // ══════════════════════════════════════════════════════════════
  const updateHeader = useCallback((field, value) => {
    patchActiveData((data) => ({ ...data, header: { ...data.header, [field]: value } }));
  }, [patchActiveData]);

  const updateItem = useCallback((index, field, value) => {
    patchActiveData((data) => {
      const items = [...data.items];
      items[index] = { ...items[index], [field]: value };
      if (field === "quantity" || field === "rate" || field === "discount_percent" || field === "discount_amount") {
        const qty = field === "quantity" ? parseFloat(value) || 0 : parseFloat(items[index].quantity) || 0;
        const rate = field === "rate" ? parseFloat(value) || 0 : parseFloat(items[index].rate) || 0;
        const gross = qty * rate;
        let discAmt;
        if (field === "discount_amount") {
          discAmt = parseFloat(value) || 0;
        } else if (field === "discount_percent") {
          const pct = parseFloat(value) || 0;
          discAmt = gross > 0 && pct > 0 ? Math.round((gross * pct / 100) * 100) / 100 : 0;
        } else {
          discAmt = parseFloat(items[index].discount_amount);
          if (!isFinite(discAmt) || discAmt <= 0) {
            const pct = parseFloat(items[index].discount_percent) || 0;
            discAmt = gross > 0 && pct > 0 ? Math.round((gross * pct / 100) * 100) / 100 : 0;
          }
        }
        if (discAmt < 0) discAmt = 0;
        if (gross > 0 && discAmt > gross) discAmt = gross;
        items[index].discount_amount = discAmt;
        items[index].amount = Math.round(Math.max(gross - discAmt, 0) * 100) / 100;
      }
      if (field === "item_name" && stockItemsWithUnits.length > 0) {
        const match = stockItemsWithUnits.find((s) => s.name === value);
        if (match) items[index].unit = match.unit;
      }
      return { ...data, items };
    });
  }, [patchActiveData, stockItemsWithUnits]);

  const updateTax = useCallback((index, field, value) => {
    patchActiveData((data) => {
      const taxes = [...data.taxes];
      taxes[index] = { ...taxes[index], [field]: value };
      return { ...data, taxes };
    });
  }, [patchActiveData]);

  const addItem = () => patchActiveData((data) => ({
    ...data,
    items: [...data.items, { item_name: "", quantity: 0, rate: 0, amount: 0, unit: "Nos", discount_amount: 0, discount_percent: 0, godown: "Main Location", batch: "" }],
  }));

  const removeItem = (idx) => patchActiveData((data) => ({ ...data, items: data.items.filter((_, i) => i !== idx) }));

  const addTax = () => patchActiveData((data) => ({ ...data, taxes: [...data.taxes, { ledger_name: "", description: "", amount: 0 }] }));

  const removeTax = (idx) => patchActiveData((data) => ({ ...data, taxes: data.taxes.filter((_, i) => i !== idx) }));

  const updateNarration = (value) => patchActiveData((data) => ({
    ...data,
    header: { ...data.header, narration: value },
    summary: { ...data.summary, narration: value },
  }));

  const setActivePurchaseLedger = (value) => {
    if (!activeId) return;
    patchInvoice(activeId, { selectedPurchaseLedger: value });
  };

  const setActiveDiscountLedger = (value) => {
    if (!activeId) return;
    patchInvoice(activeId, { selectedDiscountLedger: value });
  };

  const setActiveTdsLedger = (value) => {
    if (!activeId) return;
    patchInvoice(activeId, { tds_ledger: value, tds_percent: value ? (activeInvoice?.tds_percent || 0) : 0 });
  };

  const setActiveTdsPct = (value) => {
    if (!activeId) return;
    patchInvoice(activeId, { tds_percent: parseFloat(value) || 0 });
  };

  // ══════════════════════════════════════════════════════════════
  // Push a single invoice to Tally
  // ══════════════════════════════════════════════════════════════
  const pushInvoiceToTally = useCallback(async (invId) => {
    const inv = invoices.find((i) => i.id === invId);
    if (!inv || !inv.data) return { ok: false, message: "Invoice not ready" };

    if (!inv.data.header.party_name) return { ok: false, message: "Vendor Name is required." };
    if (!inv.data.header.voucher_date) return { ok: false, message: "Voucher Date is required." };

    if (isPushBlockedByBatchDuplicate(inv, batchDuplicates)) {
      const msg = "Duplicate of another invoice in this batch — choose Skip or Keep both on the banner before pushing.";
      patchInvoice(invId, { pushStatus: "error", pushMessage: msg });
      return { ok: false, message: msg };
    }

    patchInvoice(invId, { pushStatus: "pending", pushMessage: "Pushing..." });

    try {
      const invSubtotal = inv.data.items.reduce((s, it) => {
        const q = parseFloat(it.quantity) || 0;
        const r = parseFloat(it.rate) || 0;
        const discAmt = parseFloat(it.discount_amount) || 0;
        if (discAmt > 0) return s + Math.max(q * r - discAmt, 0);
        const d = parseFloat(it.discount_percent) || 0;
        return s + q * r * (1 - d / 100);
      }, 0);
      const invTaxAmount = inv.data.taxes.reduce((s, t) => s + (parseFloat(t.amount) || 0), 0);
      const invTotal = invSubtotal + invTaxAmount;
      // TDS calculated on base amount (before taxes)
      const invTdsLedger = inv.tds_ledger || "";
      const invTdsPct = parseFloat(inv.tds_percent) || 0;
      const invTdsAmount = (invTdsLedger && invTdsPct > 0)
        ? Math.round(invSubtotal * invTdsPct / 100 * 100) / 100
        : 0;
      const invDiscountTotal = (inv.data.items || []).reduce((s, it) => {
        const q = parseFloat(it.quantity) || 0;
        const r = parseFloat(it.rate) || 0;
        const gross = q * r;
        let discAmt = parseFloat(it.discount_amount) || 0;
        if (discAmt <= 0) {
          const pct = parseFloat(it.discount_percent) || 0;
          discAmt = gross > 0 && pct > 0 ? Math.round((gross * pct / 100) * 100) / 100 : 0;
        }
        return s + discAmt;
      }, 0);
      const invDiscountLedger = inv.selectedDiscountLedger || "";

      const invoicePayload = {
        invoice_date: toTallyDate(inv.data.header.voucher_date),
        vendor_ledger: inv.data.header.party_name,
        purchase_ledger: inv.selectedPurchaseLedger || "Purchase",
        description: inv.data.header.narration || inv.data.summary?.narration || "",
        invoice_number: inv.data.header.invoice_number || "",
        place_of_supply: inv.data.header.place_of_supply || "",
        subtotal: invSubtotal,
        amount: invSubtotal,
        gst_percentage: 0,
        gst_amount: invTaxAmount,
        tax_amount: invTaxAmount,
        tax_entries: inv.data.taxes.map((t) => ({ ledger_name: t.ledger_name, amount: parseFloat(t.amount) || 0 })),
        total_amount: invTotal,   // gross total (before TDS) for voucher balance
        tds_ledger: invTdsAmount > 0 ? invTdsLedger : "",
        tds_amount: invTdsAmount,
        discount_ledger: invDiscountTotal > 0 ? invDiscountLedger : "",
        discount_amount: Math.round(invDiscountTotal * 100) / 100,
      };

      const xmlResponse = await tallyApi.generatePurchaseXml({ invoices: [invoicePayload], company: selectedCompany });

      if (!xmlResponse?.success && xmlResponse?.success !== undefined) {
        const errors = xmlResponse.validation_errors || [];
        const msg = `XML generation failed: ${errors.join(", ") || xmlResponse.message || "Unknown error"}`;
        patchInvoice(invId, { pushStatus: "error", pushMessage: msg });
        return { ok: false, message: msg };
      }

      // Build the structured transactions payload so the backend's
      // tally_history_service can record learnable mappings and detect
      // overrides against the original AI suggestion.
      const pushTxn = {
        vendor_name: inv.data.header.party_name || "",
        vendor_gstin: inv.data.header.party_gstin || "",
        party_ledger: inv.data.header.party_name || "",
        purchase_ledger: inv.selectedPurchaseLedger || "Purchase",
        sales_ledger: "",
        items: (inv.data.items || []).map((it) => ({
          original_name: it.original_name || it.item_name || it.description || "",
          matched_stock_item: it.matched_stock_item || it.item_name || "",
          qty: parseFloat(it.quantity) || 0,
          rate: parseFloat(it.rate || it.price) || 0,
          hsn_code: it.hsn_code || it.hsn || "",
        })),
        tax_entries: (inv.data.taxes || []).map((t) => ({
          ledger_name: t.ledger_name,
          amount: parseFloat(t.amount) || 0,
        })),
        tds_section: "",
        narration: inv.data.header.narration || "",
        total_amount: invTotal,
        ai_party_suggestion: inv.originalAiPartySuggestion || null,
        ai_expense_suggestion: inv.originalAiExpenseSuggestion || null,
      };

      const pushResponse = await tallyApi.pushToTally({
        xml: xmlResponse.xml || xmlResponse.data?.xml,
        company: selectedCompany,
        voucher_type: "purchase_bill",
        transactions: [pushTxn],
      });

      if (pushResponse?.success === false) {
        const msg = pushResponse?.message || pushResponse?.error || "Push failed";
        patchInvoice(invId, { pushStatus: "error", pushMessage: msg });
        return { ok: false, message: msg };
      }
      if (pushResponse?.data?.error) {
        const tallyErr = pushResponse.data.error;
        const msg = `Tally Error: ${typeof tallyErr === "string" ? tallyErr : JSON.stringify(tallyErr)}`;
        patchInvoice(invId, { pushStatus: "error", pushMessage: msg });
        return { ok: false, message: msg };
      }
      if (pushResponse?.data?.exception > 0 || pushResponse?.exception > 0) {
        const lineError = pushResponse?.data?.line_error || pushResponse?.line_error
          || pushResponse?.data?.LINEERROR || pushResponse?.LINEERROR
          || pushResponse?.data?.description || pushResponse?.description
          || "Tally reported an exception. Check the voucher data.";
        const msg = `Tally Exception: ${lineError}`;
        patchInvoice(invId, { pushStatus: "error", pushMessage: msg });
        return { ok: false, message: msg };
      }

      patchInvoice(invId, { pushStatus: "success", pushMessage: "Pushed to Tally" });
      if (draftId) tallyApi.drafts.markPushed(draftId).catch(() => {});
      return { ok: true };
    } catch (err) {
      const msg = extractErrorMessage(err, "Failed to push to Tally");
      patchInvoice(invId, { pushStatus: "error", pushMessage: msg });
      return { ok: false, message: msg };
    }
  }, [invoices, selectedCompany, patchInvoice, batchDuplicates]);

  const handlePushActive = async () => {
    if (!activeId) return;
    const inv = invoices.find((i) => i.id === activeId);
    if (!inv) return;
    const allowedIds = await confirmTallyDuplicates([inv]);
    if (!allowedIds.has(activeId)) {
      setBatchStatus({ type: "info", message: "Push cancelled — invoice already exists in Tally." });
      return;
    }
    await pushInvoiceToTally(activeId);
  };

  const confirmTallyDuplicates = useCallback(async (invoicesToPush) => {
    const eligible = invoicesToPush.filter((inv) => !inv.tallyDuplicateOverridden);
    if (eligible.length === 0) return new Set(invoicesToPush.map((i) => i.id));
    const transactions = eligible.map((inv) => {
      const items = inv.data?.items || [];
      const taxes = inv.data?.taxes || [];
      const sub = items.reduce((s, it) => {
        const q = parseFloat(it.quantity) || 0;
        const r = parseFloat(it.rate) || 0;
        const d = parseFloat(it.discount_amount) || 0;
        return s + Math.max(q * r - d, 0);
      }, 0);
      const tax = taxes.reduce((s, t) => s + (parseFloat(t.amount) || 0), 0);
      return {
        invoice_id: inv.id,
        invoice_number: inv.data?.header?.invoice_number || "",
        party_name: inv.data?.header?.party_name || "",
        party_gstin: inv.data?.header?.party_gstin || "",
        date: inv.data?.header?.voucher_date || "",
        amount: sub + tax,
      };
    });
    let response;
    try {
      response = await tallyApi.checkTallyDuplicates({ company: selectedCompany, transactions });
    } catch (err) {
      console.warn("[PurchaseInvoiceView] Tally duplicate check failed, proceeding:", err?.message);
      return new Set(invoicesToPush.map((i) => i.id));
    }
    const matches = (response?.duplicates || []).filter((m) => m.confidence !== "low" || m.matched_by !== "date_amount");
    if (matches.length === 0) return new Set(invoicesToPush.map((i) => i.id));
    const matchedIdSet = new Set(matches.map((m) => transactions[m.index]?.invoice_id).filter(Boolean));
    return new Promise((resolve) => {
      setTallyDupModal({
        matches: matches.map((m) => ({
          ...m,
          invoice_id: transactions[m.index]?.invoice_id,
          filename: invoicesToPush.find((i) => i.id === transactions[m.index]?.invoice_id)?.filename,
        })),
        onConfirm: () => {
          matchedIdSet.forEach((id) => patchInvoice(id, { tallyDuplicateOverridden: true }));
          setTallyDupModal(null);
          resolve(new Set(invoicesToPush.map((i) => i.id)));
        },
        onCancel: () => {
          setTallyDupModal(null);
          resolve(new Set(invoicesToPush.filter((i) => !matchedIdSet.has(i.id)).map((i) => i.id)));
        },
      });
    });
  }, [selectedCompany, patchInvoice]);

  const handlePushAll = async () => {
    setIsPushingAll(true);
    setBatchStatus(null);
    setShowFailedOnly(false);

    const eligible = invoices.filter((inv) =>
      inv.status === "done"
      && inv.pushStatus !== "success"
      && !isPushBlockedByBatchDuplicate(inv, batchDuplicates)
    );
    const allowedIds = await confirmTallyDuplicates(eligible);

    let ok = 0, fail = 0, skippedDupes = 0, skippedTallyDupes = 0;
    for (const inv of invoices) {
      if (inv.status !== "done") continue;
      if (inv.pushStatus === "success") continue;
      if (isPushBlockedByBatchDuplicate(inv, batchDuplicates)) {
        skippedDupes += 1;
        continue;
      }
      if (!allowedIds.has(inv.id)) {
        skippedTallyDupes += 1;
        continue;
      }
      const res = await pushInvoiceToTally(inv.id);
      if (res.ok) ok++; else fail++;
    }
    setIsPushingAll(false);
    const parts = [];
    if (skippedDupes > 0) parts.push(`${skippedDupes} in-batch duplicate${skippedDupes === 1 ? "" : "s"} skipped`);
    if (skippedTallyDupes > 0) parts.push(`${skippedTallyDupes} Tally duplicate${skippedTallyDupes === 1 ? "" : "s"} cancelled`);
    const dupeNote = parts.length ? ` (${parts.join("; ")})` : "";
    setBatchStatus({
      type: fail > 0 ? "warning" : "success",
      message: fail > 0
        ? `Push complete: ${ok} succeeded, ${fail} failed${dupeNote}. Failed invoices are highlighted in red.`
        : `Push complete: ${ok} succeeded${dupeNote}.`,
    });
    if (fail > 0) setShowFailedOnly(true);
  };

  const handleRetryFailed = async () => {
    setIsPushingAll(true);
    setBatchStatus(null);
    let ok = 0, fail = 0;
    for (const inv of invoices) {
      if (inv.pushStatus !== "error") continue;
      if (inv.status !== "done") continue;
      const res = await pushInvoiceToTally(inv.id);
      if (res.ok) ok++; else fail++;
    }
    setIsPushingAll(false);
    setBatchStatus({
      type: fail > 0 ? "warning" : "success",
      message: fail > 0
        ? `Retry complete: ${ok} succeeded, ${fail} still failing.`
        : `Retry complete: ${ok} succeeded.`,
    });
    if (fail === 0) setShowFailedOnly(false);
  };

  // ══════════════════════════════════════════════════════════════
  // Create Ledger / Stock Item
  // ══════════════════════════════════════════════════════════════
  const handleCreateLedger = async (payload) => {
    if (!payload?.name?.trim()) return;
    setIsCreatingLedger(true);
    try {
      await tallyApi.createLedger({
        ...payload,
        company: selectedCompany,
        name: payload.name.trim(),
      });
      const name = payload.name.trim();
      if (newLedgerType === "party") {
        setVendorLedgers((prev) => [...new Set([...prev, name])].sort());
        if (activeId) updateHeader("party_name", name);
      } else if (newLedgerType === "tax") {
        setTaxLedgers((prev) => [...new Set([...prev, name])].sort());
      } else if (newLedgerType === "discount") {
        setDiscountLedgers((prev) => [...new Set([...prev, name])].sort());
        if (activeId) setActiveDiscountLedger(name);
      } else if (newLedgerType === "purchase") {
        setPurchaseLedgers((prev) => [...new Set([...prev, name])].sort());
        if (activeId) setActivePurchaseLedger(name);
      }
      setShowNewLedgerModal(false);
      setNewLedgerPrefill({});
      setBatchStatus({ type: "success", message: `Ledger "${name}" created successfully.` });
    } catch (err) {
      setBatchStatus({ type: "error", message: extractErrorMessage(err, "Failed to create ledger") });
    } finally {
      setIsCreatingLedger(false);
    }
  };

  const handleCreateStockItem = async () => {
    if (!newItem.name.trim()) return;
    setIsCreatingItem(true);
    try {
      await tallyApi.createStockItem({
        company: selectedCompany,
        name: newItem.name.trim(),
        unit: newItem.unit || "Nos",
        hsn_code: newItem.hsn_code || undefined,
      });
      const name = newItem.name.trim();
      setStockItems((prev) => [...new Set([...prev, name])].sort());
      setStockItemsWithUnits((prev) => [...prev, { name, unit: newItem.unit || "Nos" }]);
      setShowNewItemModal(false);
      setNewItem({ name: "", unit: "Nos", hsn_code: "" });
      setBatchStatus({ type: "success", message: `Stock item "${name}" created successfully.` });
    } catch (err) {
      setBatchStatus({ type: "error", message: extractErrorMessage(err, "Failed to create stock item") });
    } finally {
      setIsCreatingItem(false);
    }
  };

  // ── Remove/clear helpers ──
  const removeInvoice = (id) => {
    setInvoices((prev) => {
      const target = prev.find((inv) => inv.id === id);
      if (target?.pdfContent?.startsWith("blob:")) {
        try { URL.revokeObjectURL(target.pdfContent); } catch {}
      }
      return prev.filter((inv) => inv.id !== id);
    });
    if (activeId === id) setActiveId(null);
  };

  const clearAll = () => {
    invoices.forEach((inv) => {
      if (inv.pdfContent?.startsWith("blob:")) {
        try { URL.revokeObjectURL(inv.pdfContent); } catch {}
      }
    });
    setInvoices([]);
    setActiveId(null);
    setBatchStatus(null);
  };

  // ── Styles ──
  const styles = {
    label: "block text-xs font-medium text-gilt-soft mb-1.5 flex items-center gap-1.5",
    input: "w-full border border-gilt-hairline rounded-lg px-3 py-2 text-sm text-gilt-ink bg-gilt-card placeholder:text-gilt-faint focus:ring-1 focus:ring-gilt-brass focus:border-gilt-brass outline-none transition-colors",
    sectionTitle: "text-sm font-semibold text-gilt-ink uppercase tracking-wider flex items-center gap-2",
    th: "px-3 py-2.5 text-left font-mono text-[10px] font-medium text-gilt-soft uppercase tracking-wider",
    td: "px-3 py-2 text-sm text-gilt-ink",
  };

  const readyCount = invoices.filter((i) => i.status === "done").length;
  const failedCount = invoices.filter((i) => i.status === "failed").length;
  const processingCount = invoices.filter((i) => i.status === "processing" || i.status === "pending").length;
  const pushedCount = invoices.filter((i) => i.pushStatus === "success").length;
  const pushFailedCount = invoices.filter((i) => i.pushStatus === "error").length;
  const visibleInvoices = showFailedOnly
    ? invoices.filter((i) => i.pushStatus === "error" || i.status === "failed")
    : invoices;

  // ══════════════════════════════════════════════════════════════
  // RENDER: Upload screen
  // ══════════════════════════════════════════════════════════════
  if (invoices.length === 0) {
    return (
      <div className="p-6 max-w-3xl mx-auto">
        <div className="flex items-center gap-3 mb-6">
          <button onClick={onBack} className="p-2 hover:bg-gilt-sunken rounded-lg transition-colors">
            <ArrowLeft className="h-5 w-5 text-gilt-soft" />
          </button>
          <div>
            <h2 className="gl-display text-xl font-semibold text-gilt-ink">Purchase Invoice Import</h2>
            <p className="text-sm text-gilt-soft">Upload one or more purchase invoice PDFs to extract data</p>
          </div>
        </div>

        {batchStatus && (
          <div className="mb-4">
            <StatusToast type={batchStatus.type} message={batchStatus.message} onDismiss={() => setBatchStatus(null)} />
          </div>
        )}

        {selectedCompany && (
          <div className="mb-4 flex items-center gap-4 px-4 py-2.5 bg-gilt-card rounded-xl border border-gilt-hairline text-xs text-gilt-soft">
            <span className="flex items-center gap-1.5">
              {tallyConnected ? <Wifi className="h-3.5 w-3.5 text-gilt-credit" /> : <WifiOff className="h-3.5 w-3.5 text-gilt-faint" />}
              {tallyConnected ? "Tally Connected" : "Tally Offline"}
            </span>
            <span className="w-px h-4 bg-gilt-hairline" />
            <span className="flex items-center gap-1"><BookOpen className="h-3.5 w-3.5" /> <span className="font-mono tabular-nums">{vendorLedgers.length}</span> Vendors</span>
            <span className="flex items-center gap-1"><Package className="h-3.5 w-3.5" /> <span className="font-mono tabular-nums">{stockItems.length}</span> Items</span>
            <span className="flex items-center gap-1"><Warehouse className="h-3.5 w-3.5" /> <span className="font-mono tabular-nums">{godowns.length}</span> Godowns</span>
          </div>
        )}

        <div
          className={`border-2 border-dashed rounded-xl p-12 text-center transition-colors cursor-pointer ${
            isDragging ? "border-gilt-brass bg-gilt-sunken" : "border-gilt-hairline hover:border-gilt-brass hover:bg-gilt-sunken"
          }`}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
          onClick={() => fileInputRef.current?.click()}
        >
          <input
            ref={fileInputRef}
            type="file"
            accept={ACCEPTED_FILE_EXT}
            multiple
            className="hidden"
            onChange={(e) => {
              if (e.target.files?.length) handleMultiFileUpload(e.target.files);
              e.target.value = "";
            }}
          />
          <div className="flex flex-col items-center gap-3">
            <div className="p-3 bg-gilt-sunken border border-gilt-hairline rounded-full">
              <FileText className="h-8 w-8 text-gilt-brass" />
            </div>
            <div>
              <p className="text-sm font-medium text-gilt-ink">Drop PDF or image invoices here or click to browse</p>
              <p className="text-xs text-gilt-faint mt-1">PDF, JPG, PNG, GIF, BMP, WEBP, TIFF — each file is treated as a separate invoice and extracted in parallel</p>
            </div>
          </div>
        </div>
      </div>
    );
  }

  // ══════════════════════════════════════════════════════════════
  // RENDER: Grid / list view
  // ══════════════════════════════════════════════════════════════
  if (!activeInvoice) {
    return (
      <div className="h-full flex flex-col">
        {/* Top bar */}
        <div className="border-b border-gilt-hairline px-4 py-3 flex items-center justify-between flex-shrink-0">
          <div className="flex items-center gap-3">
            <button onClick={onBack} className="p-2 hover:bg-gilt-sunken rounded-lg transition-colors">
              <ArrowLeft className="h-5 w-5 text-gilt-soft" />
            </button>
            <div>
              <h2 className="gl-display text-lg font-semibold text-gilt-ink">Purchase Invoices</h2>
              <p className="text-xs text-gilt-soft font-mono tabular-nums">
                {invoices.length} file{invoices.length === 1 ? "" : "s"} · {readyCount} ready · {processingCount} processing · {failedCount} failed · {pushedCount} pushed
              </p>
            </div>
            {selectedCompany && (
              <span className="ml-2 px-2.5 py-1 text-xs font-medium text-gilt-brass bg-gilt-sunken rounded-full border border-gilt-brass/40">
                {selectedCompany}
              </span>
            )}
          </div>
          <div className="flex items-center gap-2">
            {/* Layout toggle */}
            <div className="flex items-center bg-gilt-sunken rounded-lg p-0.5">
              <button
                onClick={() => setGridLayout("grid")}
                className={`flex items-center gap-1 px-2.5 py-1.5 text-xs rounded-md transition-colors ${gridLayout === "grid" ? "bg-gilt-card text-gilt-ink border border-gilt-hairline" : "text-gilt-soft hover:text-gilt-ink"}`}
                title="Grid view"
              >
                <Grid3x3 className="h-3.5 w-3.5" /> Grid
              </button>
              <button
                onClick={() => setGridLayout("list")}
                className={`flex items-center gap-1 px-2.5 py-1.5 text-xs rounded-md transition-colors ${gridLayout === "list" ? "bg-gilt-card text-gilt-ink border border-gilt-hairline" : "text-gilt-soft hover:text-gilt-ink"}`}
                title="List view"
              >
                <ListIcon className="h-3.5 w-3.5" /> List
              </button>
            </div>

            <button
              onClick={() => fileInputRef.current?.click()}
              className="flex items-center gap-1.5 px-3 py-2 text-sm text-gilt-soft border border-gilt-hairline rounded-lg hover:border-gilt-brass hover:text-gilt-brass transition-colors"
            >
              <Plus className="h-4 w-4" /> Add More
            </button>
            <input
              ref={fileInputRef}
              type="file"
              accept={ACCEPTED_FILE_EXT}
              multiple
              className="hidden"
              onChange={(e) => {
                if (e.target.files?.length) handleMultiFileUpload(e.target.files);
                e.target.value = "";
              }}
            />

            {pushFailedCount > 0 && (
              <button
                onClick={handleRetryFailed}
                disabled={isPushingAll}
                className="flex items-center gap-1.5 px-3 py-2 text-sm font-medium text-gilt-debit bg-gilt-sunken border border-gilt-debit/40 rounded-lg hover:border-gilt-debit disabled:opacity-50 transition-colors"
                title="Retry only the invoices that failed to push"
              >
                <RefreshCw className={`h-3.5 w-3.5 ${isPushingAll ? "animate-spin" : ""}`} />
                <span>Retry Failed (<span className="font-mono tabular-nums">{pushFailedCount}</span>)</span>
              </button>
            )}

            <SaveDraftButton onClick={handleSaveDraft} busy={savingDraft} label={draftId ? "Update draft" : "Save draft"} />

            <button
              onClick={handlePushAll}
              disabled={isPushingAll || readyCount === 0}
              className="flex items-center gap-2 px-4 py-2 text-sm font-semibold text-gilt-paper bg-gilt-brass rounded-lg hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed transition-opacity"
            >
              {isPushingAll ? <RefreshCw className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
              {isPushingAll ? "Pushing..." : `Push All (${readyCount - pushedCount})`}
            </button>

            <button
              onClick={clearAll}
              className="p-2 text-gilt-soft hover:text-gilt-debit hover:bg-gilt-sunken rounded-lg transition-colors"
              title="Clear all"
            >
              <Trash2 className="h-4 w-4" />
            </button>
          </div>
        </div>

        {/* Info strip */}
        <div className="border-b border-gilt-hairline px-4 py-2 flex items-center gap-4 text-xs text-gilt-soft flex-shrink-0">
          <span className="flex items-center gap-1.5">
            {tallyConnected ? <Wifi className="h-3.5 w-3.5 text-gilt-credit" /> : <WifiOff className="h-3.5 w-3.5 text-gilt-faint" />}
            {tallyConnected ? "Tally Connected" : "Tally Offline"}
          </span>
          <span className="w-px h-4 bg-gilt-hairline" />
          <span className="flex items-center gap-1"><BookOpen className="h-3.5 w-3.5" /> <span className="font-mono tabular-nums">{vendorLedgers.length}</span> Vendors</span>
          <span className="flex items-center gap-1"><Package className="h-3.5 w-3.5" /> <span className="font-mono tabular-nums">{stockItems.length}</span> Items</span>
          <span className="flex items-center gap-1"><Warehouse className="h-3.5 w-3.5" /> <span className="font-mono tabular-nums">{godowns.length}</span> Godowns</span>
          {!masterDataLoaded && <span className="flex items-center gap-1"><RefreshCw className="h-3 w-3 animate-spin" /> Loading masters...</span>}
          {isUploading && <span className="flex items-center gap-1 text-gilt-brass"><RefreshCw className="h-3 w-3 animate-spin" /> Extracting...</span>}
        </div>

        {batchStatus && (
          <div className="px-4 pt-3 flex-shrink-0">
            <StatusToast type={batchStatus.type} message={batchStatus.message} onDismiss={() => setBatchStatus(null)} />
          </div>
        )}

        {/* Failed-only filter strip */}
        {(pushFailedCount > 0 || showFailedOnly) && (
          <div className="px-4 pt-3 flex-shrink-0">
            <div className="flex items-center justify-between gap-3 px-3 py-2 rounded-lg bg-gilt-sunken border border-gilt-debit/40">
              <div className="flex items-center gap-2 text-xs text-gilt-debit">
                <AlertCircle className="h-4 w-4 text-gilt-debit flex-shrink-0" />
                <span>
                  {pushFailedCount > 0
                    ? `${pushFailedCount} invoice${pushFailedCount === 1 ? "" : "s"} failed to push.`
                    : "No push failures remaining."}
                </span>
              </div>
              <button
                onClick={() => setShowFailedOnly((v) => !v)}
                className="text-xs font-medium text-gilt-debit hover:text-gilt-ink underline-offset-2 hover:underline transition-colors"
              >
                {showFailedOnly ? "Show all" : "Show failed only"}
              </button>
            </div>
          </div>
        )}

        {/* Invoices list/grid */}
        <div className="flex-1 overflow-y-auto p-4">
          {visibleInvoices.length === 0 && showFailedOnly ? (
            <div className="flex flex-col items-center justify-center py-16 text-gilt-faint">
              <CheckCircle className="h-10 w-10 text-gilt-credit mb-3" />
              <p className="text-sm">No failed invoices.</p>
            </div>
          ) : gridLayout === "grid" ? (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
              {visibleInvoices.map((inv) => {
                const dupInfo = batchDuplicates.get(inv.id) || null;
                const dupOfInvoice = dupInfo ? invoices.find((i) => i.id === dupInfo.ofId) : null;
                return (
                  <InvoiceCard
                    key={inv.id}
                    invoice={inv}
                    onOpen={() => setActiveId(inv.id)}
                    onRemove={() => removeInvoice(inv.id)}
                    onPush={() => pushInvoiceToTally(inv.id)}
                    duplicateInfo={dupInfo && dupOfInvoice ? {
                      ofFilename: dupOfInvoice.filename || "another file",
                      matchedBy: dupInfo.matchedBy,
                    } : null}
                    onKeepBothDuplicates={() => patchInvoice(inv.id, { duplicateOverridden: true })}
                  />
                );
              })}
            </div>
          ) : (
            <div className="bg-gilt-card rounded-xl border border-gilt-hairline overflow-hidden">
              <table className="w-full">
                <thead className="bg-gilt-sunken border-b border-gilt-hairline">
                  <tr>
                    <th className={styles.th}>File</th>
                    <th className={styles.th}>Vendor</th>
                    <th className={styles.th}>Invoice #</th>
                    <th className={styles.th}>Date</th>
                    <th className={`${styles.th} text-right`}>Total</th>
                    <th className={styles.th}>Status</th>
                    <th className={styles.th}></th>
                  </tr>
                </thead>
                <tbody>
                  {visibleInvoices.map((inv) => {
                    const h = inv.data?.header || {};
                    const itemsTotal = (inv.data?.items || []).reduce((s, it) => {
                      const q = parseFloat(it.quantity) || 0; const r = parseFloat(it.rate) || 0; const d = parseFloat(it.discount_percent) || 0;
                      return s + q * r * (1 - d / 100);
                    }, 0);
                    const taxTotal = (inv.data?.taxes || []).reduce((s, t) => s + (parseFloat(t.amount) || 0), 0);
                    return (
                      <tr
                        key={inv.id}
                        className={`border-t cursor-pointer transition-colors ${
                          inv.pushStatus === "error" || inv.status === "failed"
                            ? "bg-gilt-sunken border-gilt-debit/40 hover:bg-gilt-sunken/70"
                            : "border-gilt-hairline hover:bg-gilt-sunken"
                        }`}
                        onClick={() => setActiveId(inv.id)}
                        title={inv.pushStatus === "error" ? `Push error: ${inv.pushMessage}` : undefined}
                      >
                        <td className={`${styles.td} font-medium truncate max-w-[240px]`} title={inv.filename}>{inv.filename}</td>
                        <td className={styles.td}>{h.party_name || "—"}</td>
                        <td className={`${styles.td} font-mono tabular-nums`}>{h.invoice_number || "—"}</td>
                        <td className={`${styles.td} font-mono tabular-nums`}>{h.voucher_date ? toInputDate(h.voucher_date) : "—"}</td>
                        <td className={`${styles.td} text-right font-medium font-mono tabular-nums`}>{inv.data ? (itemsTotal + taxTotal).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : "—"}</td>
                        <td className={styles.td}><StatusBadge status={inv.status} pushStatus={inv.pushStatus} /></td>
                        <td className={styles.td} onClick={(e) => e.stopPropagation()}>
                          <button
                            onClick={() => removeInvoice(inv.id)}
                            className="p-1 text-gilt-faint hover:text-gilt-debit transition-colors"
                            title="Remove"
                          >
                            <Trash2 className="h-4 w-4" />
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}

        </div>

        {/* Modals shared */}
        {showNewLedgerModal && (
          <LedgerCreateModal
            open={showNewLedgerModal}
            ledgerType={newLedgerType}
            parentGroups={parentGroups}
            initialValues={newLedgerPrefill}
            busy={isCreatingLedger}
            onClose={() => { setShowNewLedgerModal(false); setNewLedgerPrefill({}); }}
            onCreate={handleCreateLedger}
          />
        )}
        {showNewItemModal && (
          <NewItemModal
            styles={styles}
            newItem={newItem}
            setNewItem={setNewItem}
            isCreatingItem={isCreatingItem}
            onClose={() => setShowNewItemModal(false)}
            onCreate={handleCreateStockItem}
          />
        )}
      </div>
    );
  }

  // ══════════════════════════════════════════════════════════════
  // RENDER: Detail editor (active invoice)
  // ══════════════════════════════════════════════════════════════
  const invoice = activeInvoice.data;
  const confidence = activeInvoice.confidence || {};
  const pdfContent = activeInvoice.pdfContent;
  const aiVendorSuggestion = activeInvoice.aiVendorSuggestion;
  const aiPurchaseLedgerSuggestion = activeInvoice.aiPurchaseLedgerSuggestion;
  const aiSuggestLoading = !!activeInvoice.aiSuggestLoading;
  const selectedPurchaseLedger = activeInvoice.selectedPurchaseLedger || "";
  // Derived per-invoice so the dropdown can render an "offline / cached"
  // chip when the most recent suggestion came from the cache instead of
  // live Tally.
  const ledgerSource = activeInvoice.ledgerSource || "live";
  const cacheRefreshedAt = activeInvoice.cacheRefreshedAt || null;

  // Fire-and-forget live feedback when the user picks something other than
  // the AI's suggestion. Backend records it into ledger_suggestion_feedback
  // and uses it to power rule mining + Layer 0 confirmation weighting.
  const submitOverrideFeedback = (field, picked, suggestion) => {
    if (!suggestion || !picked) return;
    const header = invoice?.header || {};
    tallyApi.submitSuggestionFeedback({
      company_name: selectedCompany,
      field,
      vendor_name: header.party_name || "",
      vendor_gstin: header.party_gstin || "",
      voucher_type: "purchase_bill",
      amount: parseFloat(activeInvoice?.data?.summary?.total_amount) || null,
      suggested_ledger: suggestion.recommended_ledger,
      suggested_match_type: suggestion.match_type,
      suggested_confidence: suggestion.confidence,
      picked_ledger: picked,
    });
  };

  // If the active invoice is still processing or failed, show a wrapper
  if (!invoice) {
    return (
      <div className="h-full flex flex-col">
        <div className="border-b border-gilt-hairline px-4 py-3 flex items-center gap-3 flex-shrink-0">
          <button onClick={() => setActiveId(null)} className="p-2 hover:bg-gilt-sunken rounded-lg transition-colors">
            <ArrowLeft className="h-5 w-5 text-gilt-soft" />
          </button>
          <h2 className="text-lg font-semibold text-gilt-ink truncate">{activeInvoice.filename}</h2>
          <StatusBadge status={activeInvoice.status} pushStatus={activeInvoice.pushStatus} />
        </div>
        <div className="flex-1 flex items-center justify-center">
          {activeInvoice.status === "failed" ? (
            <div className="text-center max-w-md">
              <AlertCircle className="h-10 w-10 text-gilt-debit mx-auto mb-3" />
              <p className="text-sm font-medium text-gilt-ink mb-1">Extraction failed</p>
              <p className="text-xs text-gilt-soft">{activeInvoice.error || "Unknown error"}</p>
            </div>
          ) : (
            <div className="text-center">
              <RefreshCw className="h-8 w-8 text-gilt-brass animate-spin mx-auto mb-3" />
              <p className="text-sm text-gilt-soft">Extracting invoice data...</p>
            </div>
          )}
        </div>
      </div>
    );
  }

  const isSending = activeInvoice.pushStatus === "pending";

  return (
    <div className="h-full flex flex-col">
      {/* TOP BAR — pr-44 reserves room on the right edge for the global
          TallyStatusChip (rendered fixed at top-3 right-4) so it never
          overlaps the toolbar buttons. Chip's expanded state can be ~180px
          wide on first connect or when offline. */}
      <div className="border-b border-gilt-hairline pl-4 pr-44 py-3 flex items-center justify-between flex-shrink-0">
        <div className="flex items-center gap-3 min-w-0">
          <button onClick={() => setActiveId(null)} className="p-2 hover:bg-gilt-sunken rounded-lg transition-colors" title="Back to list">
            <ArrowLeft className="h-5 w-5 text-gilt-soft" />
          </button>
          <div className="min-w-0">
            <h2 className="text-lg font-semibold text-gilt-ink truncate">{activeInvoice.filename}</h2>
            <p className="text-xs text-gilt-soft font-mono tabular-nums">
              Invoice {invoices.findIndex((i) => i.id === activeId) + 1} of {invoices.length}
            </p>
          </div>
          {selectedCompany && (
            <span className="ml-2 px-2.5 py-1 text-xs font-medium text-gilt-brass bg-gilt-sunken rounded-full border border-gilt-brass/40">
              {selectedCompany}
            </span>
          )}
          <StatusBadge status={activeInvoice.status} pushStatus={activeInvoice.pushStatus} />
        </div>
        <div className="flex items-center gap-2">
          {/* Prev / Next */}
          <button
            onClick={() => {
              const idx = invoices.findIndex((i) => i.id === activeId);
              if (idx > 0) setActiveId(invoices[idx - 1].id);
            }}
            disabled={invoices.findIndex((i) => i.id === activeId) <= 0}
            className="p-2 text-gilt-soft hover:bg-gilt-sunken rounded-lg disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            title="Previous"
          >
            <ArrowLeft className="h-4 w-4" />
          </button>
          <button
            onClick={() => {
              const idx = invoices.findIndex((i) => i.id === activeId);
              if (idx < invoices.length - 1) setActiveId(invoices[idx + 1].id);
            }}
            disabled={invoices.findIndex((i) => i.id === activeId) >= invoices.length - 1}
            className="p-2 text-gilt-soft hover:bg-gilt-sunken rounded-lg disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            title="Next"
          >
            <ArrowLeft className="h-4 w-4 rotate-180" />
          </button>

          <button
            onClick={() => setShowPdf(!showPdf)}
            className="flex items-center gap-1.5 px-3 py-2 text-sm text-gilt-soft border border-gilt-hairline rounded-lg hover:border-gilt-brass hover:text-gilt-brass transition-colors"
          >
            {showPdf ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
            {showPdf ? "Hide Preview" : "Show Preview"}
          </button>
          <button
            onClick={() => setShowMappingRules((v) => !v)}
            className={`flex items-center gap-1.5 px-3 py-2 text-sm border rounded-lg transition-colors ${
              showMappingRules
                ? "bg-gilt-sunken border-gilt-brass text-gilt-brass"
                : "border-gilt-hairline text-gilt-soft hover:border-gilt-brass hover:text-gilt-brass"
            }`}
            title="Manage classification rules — drives Layer 1 of the AI suggestion"
          >
            <Settings className="h-4 w-4" />
            Rules
          </button>
        </div>
      </div>

      {/* Push message for this invoice */}
      {activeInvoice.pushMessage && (
        <div className="px-4 pt-3 flex-shrink-0">
          <StatusToast
            type={activeInvoice.pushStatus === "success" ? "success" : activeInvoice.pushStatus === "error" ? "error" : "info"}
            message={activeInvoice.pushMessage}
            onDismiss={() => patchInvoice(activeId, { pushMessage: "" })}
          />
        </div>
      )}

      {(() => {
        const dup = batchDuplicates.get(activeId);
        if (!dup) return null;
        const ofInv = invoices.find((i) => i.id === dup.ofId);
        return (
          <div className="px-4 pt-3 flex-shrink-0">
            <div className="flex items-start gap-3 px-4 py-3 rounded-xl border bg-gilt-sunken border-gilt-brass/40 text-gilt-brass">
              <AlertCircle className="h-5 w-5 text-gilt-brass flex-shrink-0 mt-0.5" />
              <div className="flex-1">
                <p className="text-sm font-semibold">Possible duplicate invoice</p>
                <p className="text-xs leading-snug mt-0.5">
                  Same {dup.matchedBy === "gstin" ? "GSTIN + invoice number" : "vendor name + invoice number"} as <span className="font-medium">{ofInv?.filename || "another file in this batch"}</span>. Push is currently blocked for this invoice — pick one of the options on the right.
                </p>
              </div>
              <div className="flex items-center gap-2 flex-shrink-0">
                <button
                  onClick={() => removeInvoice(activeId)}
                  className="px-3 py-1.5 bg-gilt-card border border-gilt-hairline text-gilt-soft rounded-lg text-xs font-medium hover:border-gilt-brass hover:text-gilt-brass transition-colors"
                >
                  Skip this one
                </button>
                <button
                  onClick={() => patchInvoice(activeId, { duplicateOverridden: true })}
                  className="px-3 py-1.5 bg-gilt-brass text-gilt-paper rounded-lg text-xs font-medium hover:opacity-90 transition-opacity"
                >
                  Keep both
                </button>
              </div>
            </div>
          </div>
        );
      })()}

      {/* SPLIT PANELS */}
      <PanelGroup direction="horizontal" className="flex-1 min-h-0">
        {showPdf && (
          <>
            <Panel defaultSize={50} minSize={25}>
              <div className="h-full bg-gilt-sunken overflow-hidden">
                {pdfContent ? (
                  isImageMime(activeInvoice.fileMime) ? (
                    <div className="h-full w-full overflow-auto flex items-start justify-center bg-gilt-sunken p-4">
                      <img
                        src={pdfContent}
                        alt={activeInvoice.filename || "Invoice image"}
                        className="max-w-full h-auto border border-gilt-hairline"
                      />
                    </div>
                  ) : (
                    <PdfViewer pdfData={pdfContent} />
                  )
                ) : (
                  <div className="h-full w-full flex flex-col items-center justify-center text-center px-6 text-gilt-soft">
                    <FileText className="h-10 w-10 text-gilt-faint mb-3" strokeWidth={1.5} />
                    <p className="text-sm font-medium text-gilt-ink">No file attached to this draft</p>
                    <p className="text-xs text-gilt-soft mt-1 max-w-xs">
                      The original PDF wasn't saved with this draft. Edit the extracted data on the right or re-upload the document from the library.
                    </p>
                  </div>
                )}
              </div>
            </Panel>
            <PanelResizeHandle className="w-1.5 bg-gilt-hairline hover:bg-gilt-brass transition-colors duration-150 cursor-col-resize" />
          </>
        )}

        <Panel defaultSize={showPdf ? 50 : 100} minSize={35}>
          <div className="h-full overflow-y-auto">
            <div className="p-5 space-y-5 pb-32">

              {/* INVOICE DETAILS */}
              <div className="bg-gilt-card rounded-xl border border-gilt-hairline p-5">
                <h3 className={styles.sectionTitle}>
                  <FileText className="h-4 w-4 text-gilt-brass" />
                  Invoice Details
                </h3>
                <div className="grid grid-cols-2 gap-4 mt-4">
                  <div>
                    <label className={styles.label}>Voucher Date <span className="text-gilt-debit">*</span></label>
                    <input
                      type="date"
                      value={toInputDate(invoice.header.voucher_date)}
                      onChange={(e) => updateHeader("voucher_date", e.target.value)}
                      className={styles.input}
                      required
                    />
                  </div>
                  <div>
                    <label className={styles.label}>Invoice Number</label>
                    <input
                      type="text"
                      value={invoice.header.invoice_number || ""}
                      onChange={(e) => updateHeader("invoice_number", e.target.value)}
                      className={styles.input}
                      placeholder="INV-001"
                    />
                  </div>
                  <div>
                    <label className={styles.label}>Vendor Name <span className="text-gilt-debit">*</span></label>
                    <AISuggestedDropdown
                      options={vendorLedgers}
                      value={invoice.header.party_name}
                      onChange={(v) => updateHeader("party_name", v)}
                      onCreateNew={() => {
                        setNewLedgerType("party");
                        setNewLedgerPrefill({
                          parent_group: "Sundry Creditors",
                          gstin: invoice.header.party_gstin || "",
                          state: invoice.header.place_of_supply || "",
                        });
                        setShowNewLedgerModal(true);
                      }}
                      onCreateSuggested={(name) => {
                        setNewLedgerType("party");
                        setNewLedgerPrefill({
                          name,
                          parent_group: "Sundry Creditors",
                          gstin: invoice.header.party_gstin || "",
                          state: invoice.header.place_of_supply || "",
                        });
                        setShowNewLedgerModal(true);
                      }}
                      placeholder="Select vendor ledger..."
                      aiSuggestion={aiVendorSuggestion}
                      loading={aiSuggestLoading}
                      ledgerSource={ledgerSource}
                      cacheRefreshedAt={cacheRefreshedAt}
                      onOverride={(picked, suggestion) => submitOverrideFeedback("party_ledger", picked, suggestion)}
                    />
                  </div>
                  <div>
                    <label className={styles.label}>Vendor GSTIN</label>
                    <input
                      type="text"
                      value={invoice.header.party_gstin || ""}
                      onChange={(e) => updateHeader("party_gstin", e.target.value.toUpperCase())}
                      maxLength={15}
                      className={`${styles.input} ${invoice.header.party_gstin && !isValidGstin(invoice.header.party_gstin) ? "border-gilt-brass bg-gilt-sunken focus:ring-gilt-brass focus:border-gilt-brass" : ""}`}
                      placeholder="29AAFCG1234A1ZF"
                    />
                    {invoice.header.party_gstin && !isValidGstin(invoice.header.party_gstin) && (
                      <p className="text-xs text-gilt-brass mt-1">GSTIN format may be invalid</p>
                    )}
                  </div>
                  <div>
                    <label className={styles.label}>Place of Supply</label>
                    <InvoiceDropdown
                      options={INDIAN_STATES}
                      value={invoice.header.place_of_supply}
                      onChange={(v) => updateHeader("place_of_supply", v)}
                      placeholder="Select state..."
                    />
                  </div>
                  <div>
                    <label className={styles.label}>
                      Purchase Ledger
                      {aiPurchaseLedgerSuggestion?.category && (
                        <span className="ml-2 text-[10px] text-gilt-faint font-normal">{aiPurchaseLedgerSuggestion.category}</span>
                      )}
                    </label>
                    <AISuggestedDropdown
                      options={purchaseLedgers}
                      value={selectedPurchaseLedger}
                      onChange={setActivePurchaseLedger}
                      onCreateNew={() => {
                        setNewLedgerType("purchase");
                        setNewLedgerPrefill({ parent_group: "Purchase Accounts" });
                        setShowNewLedgerModal(true);
                      }}
                      placeholder="Select purchase ledger..."
                      aiSuggestion={aiPurchaseLedgerSuggestion}
                      loading={aiSuggestLoading}
                      ledgerSource={ledgerSource}
                      cacheRefreshedAt={cacheRefreshedAt}
                      onOverride={(picked, suggestion) => submitOverrideFeedback("expense_ledger", picked, suggestion)}
                    />
                  </div>
                  <div className="col-span-2">
                    <label className={styles.label}>Narration</label>
                    <textarea
                      value={invoice.header.narration || ""}
                      onChange={(e) => updateNarration(e.target.value)}
                      className={`${styles.input} resize-none`}
                      rows={2}
                      placeholder="Optional narration for the voucher..."
                    />
                  </div>
                </div>
              </div>

              {/* ITEMS */}
              <div className="bg-gilt-card rounded-xl border border-gilt-hairline p-5">
                <div className="flex items-center justify-between mb-3">
                  <h3 className={styles.sectionTitle}>
                    <Package className="h-4 w-4 text-gilt-brass" />
                    Items
                  </h3>
                  <button
                    onClick={addItem}
                    className="flex items-center gap-1.5 px-3 py-1.5 text-sm text-gilt-brass border border-gilt-brass/40 rounded-lg hover:bg-gilt-sunken font-medium transition-colors"
                  >
                    <Plus className="h-4 w-4" /> Add Item
                  </button>
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full">
                    <thead>
                      <tr className="bg-gilt-sunken border-b border-gilt-hairline">
                        <th className={styles.th} style={{ width: "35px" }}>#</th>
                        <th className={styles.th} style={{ minWidth: "160px" }}>Item Name</th>
                        <th className={styles.th} style={{ minWidth: "90px" }}>Qty</th>
                        <th className={styles.th} style={{ minWidth: "100px" }}>Rate</th>
                        <th className={styles.th} style={{ minWidth: "110px" }}>Amount</th>
                        <th className={styles.th} style={{ minWidth: "100px" }}>Discount (₹)</th>
                        <th className={styles.th} style={{ minWidth: "120px" }}>Gross Amount</th>
                        <th className={styles.th} style={{ width: "36px" }}></th>
                      </tr>
                    </thead>
                    <tbody>
                      {invoice.items.map((item, idx) => (
                        <tr key={idx} className="hover:bg-gilt-sunken border-t border-gilt-hairline transition-colors">
                          <td className={`${styles.td} text-gilt-faint text-center font-mono tabular-nums`}>{idx + 1}</td>
                          <td className={styles.td}>
                            <InvoiceDropdown
                              options={stockItems}
                              value={item.item_name}
                              onChange={(v) => updateItem(idx, "item_name", v)}
                              onCreateNew={() => setShowNewItemModal(true)}
                              placeholder="Select item..."
                            />
                          </td>
                          <td className={styles.td}>
                            <input
                              type="number"
                              value={item.quantity}
                              onChange={(e) => updateItem(idx, "quantity", e.target.value)}
                              className="w-20 px-2 py-1 text-sm text-right border border-gilt-hairline rounded text-gilt-ink font-medium font-mono tabular-nums bg-gilt-card focus:ring-1 focus:ring-gilt-brass focus:border-gilt-brass transition-colors"
                              placeholder="0"
                              step="any"
                            />
                          </td>
                          <td className={styles.td}>
                            <input
                              type="number"
                              value={item.rate}
                              onChange={(e) => updateItem(idx, "rate", e.target.value)}
                              className="w-24 px-2 py-1 text-sm text-right border border-gilt-hairline rounded text-gilt-ink font-medium font-mono tabular-nums bg-gilt-card focus:ring-1 focus:ring-gilt-brass focus:border-gilt-brass transition-colors"
                              placeholder="0.00"
                              step="any"
                            />
                          </td>
                          {/* Amount = qty × rate (gross before discount) — computed inline so
                              the display is always in sync with the inputs even before the
                              user edits anything (item.amount may carry the extracted value). */}
                          <td className={`${styles.td} text-right`}>
                            <div className="w-24 px-2 py-1 bg-gilt-sunken border border-gilt-hairline rounded text-sm font-medium text-gilt-soft text-right font-mono tabular-nums">
                              {(((parseFloat(item.quantity) || 0) * (parseFloat(item.rate) || 0))).toFixed(2)}
                            </div>
                          </td>
                          <td className={styles.td}>
                            <input
                              type="number"
                              value={item.discount_amount || 0}
                              onChange={(e) => updateItem(idx, "discount_amount", e.target.value)}
                              className="w-24 px-2 py-1 text-sm text-right border border-gilt-hairline rounded text-gilt-ink font-medium font-mono tabular-nums bg-gilt-card focus:ring-1 focus:ring-gilt-brass focus:border-gilt-brass transition-colors"
                              placeholder="0.00"
                              step="any"
                              min="0"
                              title="Discount amount in rupees"
                            />
                          </td>
                          {/* Gross Amount = qty × rate − discount (what actually posts to Tally) */}
                          <td className={`${styles.td} text-right`}>
                            <div className="w-28 px-2 py-1 bg-gilt-sunken border border-gilt-brass/40 rounded text-sm font-semibold text-gilt-brass text-right font-mono tabular-nums">
                              {(() => {
                                const gross = (parseFloat(item.quantity) || 0) * (parseFloat(item.rate) || 0);
                                const disc = parseFloat(item.discount_amount) || 0;
                                return Math.max(gross - disc, 0).toFixed(2);
                              })()}
                            </div>
                          </td>
                          <td className={styles.td}>
                            <button
                              onClick={() => removeItem(idx)}
                              className="p-1 text-gilt-faint hover:text-gilt-debit transition-colors"
                              title="Delete row"
                            >
                              <Trash2 className="h-4 w-4" />
                            </button>
                          </td>
                        </tr>
                      ))}
                      {invoice.items.length > 0 && (
                        <tr className="border-t-2 border-gilt-hairline bg-gilt-sunken font-semibold text-sm text-gilt-ink">
                          <td className={styles.td}></td>
                          <td className={`${styles.td} text-right`}>Total</td>
                          <td className={`${styles.td} text-right font-mono tabular-nums`}>
                            {itemsTotals.quantity % 1 === 0 ? itemsTotals.quantity : itemsTotals.quantity.toFixed(2)}
                          </td>
                          <td className={styles.td}></td>
                          <td className={`${styles.td} text-right font-mono tabular-nums text-gilt-soft`}>
                            ₹{itemsTotals.amount.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                          </td>
                          <td className={`${styles.td} text-right font-mono tabular-nums text-gilt-brass`}>
                            {itemsTotals.discount > 0 ? `₹${itemsTotals.discount.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : "—"}
                          </td>
                          <td className={`${styles.td} text-right font-mono tabular-nums text-gilt-brass`}>
                            ₹{itemsTotals.net.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                          </td>
                          <td className={styles.td}></td>
                        </tr>
                      )}
                      {invoice.items.length === 0 && (
                        <tr>
                          <td colSpan={8} className="px-3 py-8 text-center text-sm text-gilt-faint">
                            No items added. Click "Add Item" to add inventory items.
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>
              </div>

              {/* TAX LEDGERS */}
              <div className="bg-gilt-card rounded-xl border border-gilt-hairline p-5">
                <div className="flex items-center justify-between mb-3">
                  <h3 className={styles.sectionTitle}>
                    <BookOpen className="h-4 w-4 text-gilt-brass" />
                    GST
                  </h3>
                  <button
                    onClick={addTax}
                    className="flex items-center gap-1.5 px-3 py-1.5 text-sm text-gilt-brass border border-gilt-brass/40 rounded-lg hover:bg-gilt-sunken font-medium transition-colors"
                  >
                    <Plus className="h-4 w-4" /> Add Tax
                  </button>
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full">
                    <thead>
                      <tr className="bg-gilt-sunken border-b border-gilt-hairline">
                        <th className={styles.th} style={{ width: "40px" }}>#</th>
                        <th className={styles.th} style={{ minWidth: "250px" }}>Ledger Name</th>
                        <th className={styles.th} style={{ width: "120px" }}>Amount</th>
                        <th className={styles.th} style={{ width: "36px" }}></th>
                      </tr>
                    </thead>
                    <tbody>
                      {invoice.taxes.map((tax, idx) => (
                        <tr key={idx} className="hover:bg-gilt-sunken border-t border-gilt-hairline transition-colors">
                          <td className={`${styles.td} text-gilt-faint text-center font-mono tabular-nums`}>{idx + 1}</td>
                          <td className={styles.td}>
                            <AISuggestedDropdown
                              options={taxLedgers}
                              value={tax.ledger_name}
                              onChange={(v) => updateTax(idx, "ledger_name", v)}
                              onCreateNew={() => {
                                setNewLedgerType("tax");
                                setNewLedgerPrefill({ parent_group: "Duties & Taxes" });
                                setShowNewLedgerModal(true);
                              }}
                              placeholder="Select tax ledger..."
                              aiSuggestion={tax.ai_suggestion || null}
                              loading={false}
                              ledgerSource={ledgerSource}
                              cacheRefreshedAt={cacheRefreshedAt}
                            />
                          </td>
                          <td className={styles.td}>
                            <input
                              type="number"
                              value={tax.amount}
                              onChange={(e) => updateTax(idx, "amount", parseFloat(e.target.value) || 0)}
                              className="w-24 border border-gilt-hairline rounded px-1 py-1 text-sm text-gilt-ink font-mono tabular-nums bg-gilt-card text-right focus:ring-1 focus:ring-gilt-brass focus:border-gilt-brass outline-none transition-colors"
                              step="any"
                            />
                          </td>
                          <td className={styles.td}>
                            <button onClick={() => removeTax(idx)} className="p-1 text-gilt-faint hover:text-gilt-debit transition-colors" title="Delete row">
                              <Trash2 className="h-4 w-4" />
                            </button>
                          </td>
                        </tr>
                      ))}
                      {invoice.taxes.length === 0 && (
                        <tr>
                          <td colSpan={4} className="px-3 py-8 text-center text-sm text-gilt-faint">
                            No tax entries. Click "Add Tax" to add tax ledgers.
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>
              </div>

              {/* DISCOUNT LEDGER (auto-totals the line discounts) */}
              <div className="bg-gilt-card rounded-xl border border-gilt-hairline p-5">
                <div className="flex items-center gap-2 mb-4">
                  <h3 className={styles.sectionTitle}>
                    <Percent className="h-4 w-4 text-gilt-brass" />
                    Discount Ledger
                  </h3>
                  <span className="text-xs font-normal text-gilt-soft bg-gilt-sunken border border-gilt-hairline px-2 py-0.5 rounded-full">
                    From Indirect Expenses
                  </span>
                  {activeInvoice.selectedDiscountLedger && (
                    <button
                      onClick={() => setActiveDiscountLedger("")}
                      className="ml-auto text-xs text-gilt-faint hover:text-gilt-debit transition-colors"
                    >
                      × Clear
                    </button>
                  )}
                </div>
                <div className="flex items-end gap-3">
                  <div className="flex-1">
                    <label className="block text-xs font-medium text-gilt-soft mb-1">
                      Discount Ledger
                    </label>
                    <AISuggestedDropdown
                      options={discountLedgers}
                      value={activeInvoice.selectedDiscountLedger || ""}
                      onChange={setActiveDiscountLedger}
                      onCreateNew={() => {
                        setNewLedgerType("discount");
                        setNewLedgerPrefill({ parent_group: "Indirect Expenses" });
                        setShowNewLedgerModal(true);
                      }}
                      onCreateSuggested={(name) => {
                        setNewLedgerType("discount");
                        setNewLedgerPrefill({ name, parent_group: "Indirect Expenses" });
                        setShowNewLedgerModal(true);
                      }}
                      placeholder="Select discount ledger…"
                      aiSuggestion={activeInvoice.aiDiscountLedgerSuggestion || null}
                      loading={aiSuggestLoading}
                      ledgerSource={ledgerSource}
                      cacheRefreshedAt={cacheRefreshedAt}
                    />
                  </div>
                  <div className="w-40 shrink-0">
                    <label className="block text-xs font-medium text-gilt-soft mb-1">
                      Total Discount
                    </label>
                    <div className="w-full px-3 py-2 bg-gilt-sunken border border-gilt-brass/40 rounded-lg text-sm font-semibold text-gilt-brass text-right font-mono tabular-nums">
                      ₹{itemsTotals.discount.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                    </div>
                  </div>
                </div>
                {itemsTotals.discount === 0 && (
                  <p className="text-[11px] text-gilt-faint mt-2">
                    No line discounts on this invoice — add a discount amount on any item to populate this entry.
                  </p>
                )}
              </div>

              {/* TDS DEDUCTION (OPTIONAL) */}
              <div className="bg-gilt-card rounded-xl border border-gilt-hairline p-5">
                <div className="flex items-center gap-2 mb-4">
                  <h3 className={styles.sectionTitle}>
                    <Percent className="h-4 w-4 text-gilt-brass" />
                    TDS Deduction
                  </h3>
                  <span className="text-xs font-normal text-gilt-soft bg-gilt-sunken border border-gilt-hairline px-2 py-0.5 rounded-full">Optional</span>
                  {activeInvoice.tds_ledger && (
                    <button
                      onClick={() => setActiveTdsLedger("")}
                      className="ml-auto text-xs text-gilt-faint hover:text-gilt-debit transition-colors"
                    >
                      × Clear TDS
                    </button>
                  )}
                </div>
                <div className="flex items-end gap-3">
                  {/* TDS Ledger dropdown */}
                  <div className="flex-1">
                    <label className="block text-xs font-medium text-gilt-soft mb-1">TDS Ledger</label>
                    <InvoiceDropdown
                      options={tdsLedgers}
                      value={activeInvoice.tds_ledger || ""}
                      onChange={setActiveTdsLedger}
                      placeholder="Select TDS ledger (optional)"
                    />
                  </div>
                  {/* TDS % input */}
                  <div className="w-28 shrink-0">
                    <label className="block text-xs font-medium text-gilt-soft mb-1">TDS %</label>
                    <input
                      type="number"
                      value={activeInvoice.tds_percent || ""}
                      onChange={(e) => setActiveTdsPct(e.target.value)}
                      disabled={!activeInvoice.tds_ledger}
                      className={`w-full border border-gilt-hairline rounded px-2 py-1.5 text-sm text-gilt-ink font-mono tabular-nums bg-gilt-card text-right focus:ring-1 focus:ring-gilt-brass focus:border-gilt-brass outline-none transition-colors ${!activeInvoice.tds_ledger ? "bg-gilt-sunken text-gilt-faint cursor-not-allowed" : ""}`}
                      placeholder="0.00"
                      min="0"
                      max="100"
                      step="0.01"
                    />
                  </div>
                  {/* Computed TDS amount display */}
                  {tdsAmount > 0 && (
                    <div className="flex flex-col items-end gap-0.5 shrink-0">
                      <span className="text-xs text-gilt-faint font-mono tabular-nums">
                        {activeInvoice.tds_percent}% × ₹{subtotal.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} (base)
                      </span>
                      <span className="text-base font-semibold text-gilt-debit font-mono tabular-nums">
                        −₹{tdsAmount.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                      </span>
                    </div>
                  )}
                </div>
                {!activeInvoice.tds_ledger && tdsLedgers.length === 0 && (
                  <p className="text-xs text-gilt-faint mt-2">
                    No TDS ledgers found in Tally. Add a ledger under "Duties &amp; Taxes" with "TDS" in the name.
                  </p>
                )}
              </div>

              {/* SUMMARY */}
              <div className="bg-gilt-card rounded-xl border border-gilt-hairline p-5">
                <h3 className={styles.sectionTitle}>Summary</h3>
                <div className="space-y-2 mt-4 border-t border-gilt-hairline pt-4">
                  <div className="flex justify-between text-sm">
                    <span className="text-gilt-soft">Subtotal (Items)</span>
                    <span className="font-medium text-gilt-ink font-mono tabular-nums">{subtotal.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span>
                  </div>
                  <div className="flex justify-between text-sm">
                    <span className="text-gilt-soft">Tax Amount</span>
                    <span className="font-medium text-gilt-ink font-mono tabular-nums">{taxAmount.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span>
                  </div>
                  {tdsAmount > 0 && (
                    <div className="flex justify-between text-sm">
                      <span className="text-gilt-brass font-medium">TDS Deduction</span>
                      <span className="font-medium text-gilt-debit font-mono tabular-nums">−{tdsAmount.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span>
                    </div>
                  )}
                  <div className="flex justify-between text-base font-semibold border-t border-gilt-hairline pt-3 mt-3">
                    <span className="text-gilt-ink">{tdsAmount > 0 ? "Net Amount" : "Total Amount"}</span>
                    <span className="text-gilt-ink font-mono tabular-nums gl-double-rule">{totalAmount.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span>
                  </div>
                </div>
              </div>
            </div>

            {/* STICKY BOTTOM */}
            <div className="fixed bottom-0 right-0 left-0 bg-gilt-card border-t border-gilt-hairline gl-gilt-edge px-6 py-3 flex items-center justify-between z-40">
              <div className="flex items-center gap-6 text-sm">
                <div className="text-gilt-soft">Subtotal: <span className="font-semibold text-gilt-ink font-mono tabular-nums">{subtotal.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span></div>
                <div className="text-gilt-soft">Tax: <span className="font-semibold text-gilt-ink font-mono tabular-nums">{taxAmount.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span></div>
                {tdsAmount > 0 && (
                  <div className="text-gilt-soft">TDS: <span className="font-semibold text-gilt-debit font-mono tabular-nums">−{tdsAmount.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span></div>
                )}
                <div className="text-gilt-ink font-bold text-base">
                  {tdsAmount > 0 ? "Net" : "Total"}: <span className="text-gilt-brass font-mono tabular-nums">{totalAmount.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span>
                </div>
              </div>
              <button
                onClick={handlePushActive}
                disabled={isSending || activeInvoice.pushStatus === "success"}
                className="flex items-center gap-2 px-6 py-2.5 text-sm font-semibold text-gilt-paper bg-gilt-brass rounded-lg hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed transition-opacity"
              >
                {isSending ? <RefreshCw className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
                {isSending ? "Pushing..." : activeInvoice.pushStatus === "success" ? "Pushed" : "Push to Tally"}
              </button>
            </div>
          </div>
        </Panel>

      </PanelGroup>

      {/* Mapping Rules modal — popup instead of a side panel so PDF + form
          fields keep their full width when rules is open.
          document_type='purchase_invoice'. Available ledgers union vendor +
          purchase + tax + discount so the rule editor can target any of
          them. */}
      {showMappingRules && createPortal(
        <div
          className="fixed inset-0 z-[100] bg-gilt-ink/40 flex items-start justify-center p-4 sm:p-8 overflow-y-auto"
          onClick={() => setShowMappingRules(false)}
        >
          <div
            className="bg-gilt-card border border-gilt-hairline rounded-xl w-full max-w-4xl my-4 max-h-[92vh] flex flex-col overflow-hidden"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between px-5 py-3 border-b border-gilt-hairline bg-gilt-sunken flex-shrink-0">
              <div className="flex items-center gap-2">
                <Settings className="h-4 w-4 text-gilt-brass" />
                <h3 className="text-sm font-semibold text-gilt-ink">
                  Mapping Rules — Purchase
                </h3>
                <span className="text-xs text-gilt-soft">
                  · {selectedCompany || "no company"}
                </span>
              </div>
              <button
                onClick={() => setShowMappingRules(false)}
                className="p-1 text-gilt-faint hover:text-gilt-ink hover:bg-gilt-sunken rounded transition-colors"
                aria-label="Close mapping rules"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            <div className="flex-1 overflow-y-auto">
              <MappingRulesPanel
                documentType="purchase_invoice"
                company={selectedCompany}
                availableLedgers={Array.from(new Set([
                  ...vendorLedgers,
                  ...purchaseLedgers,
                  ...taxLedgers,
                  ...discountLedgers,
                ]))}
                partyLedgers={vendorLedgers}
                expenseLedgers={Array.from(new Set([
                  ...purchaseLedgers,
                  ...taxLedgers,
                  ...discountLedgers,
                ]))}
                onRulesChange={() => {
                  const inv = invoices.find((i) => i.id === activeId);
                  if (inv?.data) triggerAiSuggestion(inv.id, inv.data);
                }}
              />
            </div>
          </div>
        </div>,
        document.body
      )}

      {tallyDupModal && (
        <div className="fixed inset-0 z-50 bg-gilt-ink/40 flex items-center justify-center p-4">
          <div className="bg-gilt-card border border-gilt-hairline rounded-2xl w-full max-w-lg max-h-[85vh] overflow-y-auto">
            <div className="px-6 py-4 border-b border-gilt-hairline flex items-start gap-3">
              <AlertCircle className="h-6 w-6 text-gilt-brass flex-shrink-0 mt-0.5" />
              <div className="flex-1 min-w-0">
                <h3 className="text-base font-semibold text-gilt-ink">
                  {tallyDupModal.matches.length === 1
                    ? "1 invoice already exists in Tally"
                    : `${tallyDupModal.matches.length} invoices already exist in Tally`}
                </h3>
                <p className="text-xs text-gilt-soft mt-1">
                  Review the matches and decide whether to push duplicates or cancel.
                </p>
              </div>
            </div>
            <div className="px-6 py-4 space-y-3">
              {tallyDupModal.matches.map((m, i) => (
                <div key={i} className="rounded-lg border border-gilt-brass/40 bg-gilt-sunken p-3">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-medium text-gilt-ink truncate" title={m.filename}>
                        {m.filename || "—"}
                      </p>
                      <p className="text-[11px] text-gilt-soft mt-0.5">
                        Inv #{m.input?.invoice_number || "—"} · {m.input?.party_name || "Unknown party"}
                      </p>
                    </div>
                    <span className={`font-mono text-[10px] uppercase tracking-[0.14em] font-semibold px-2 py-0.5 rounded-full border ${
                      m.confidence === "high" ? "text-gilt-debit border-gilt-debit/40"
                      : m.confidence === "medium" ? "text-gilt-brass border-gilt-brass/40"
                      : "text-gilt-soft border-gilt-hairline"
                    }`}>
                      {m.confidence} match
                    </span>
                  </div>
                  <p className="text-[11px] text-gilt-soft mt-2 leading-snug">
                    Already in Tally as <span className="font-medium text-gilt-ink">{m.existing_voucher?.voucher_type || "voucher"} #{m.existing_voucher?.voucher_number || m.existing_voucher?.reference || "—"}</span>
                    {m.existing_voucher?.date && ` dated ${m.existing_voucher.date}`}
                    {m.existing_voucher?.party_ledger && ` for ${m.existing_voucher.party_ledger}`}.
                  </p>
                </div>
              ))}
            </div>
            <div className="px-6 py-4 border-t border-gilt-hairline flex items-center justify-end gap-2 bg-gilt-sunken">
              <button
                onClick={() => tallyDupModal.onCancel()}
                className="px-4 py-2 text-sm font-medium text-gilt-soft bg-gilt-card border border-gilt-hairline rounded-lg hover:border-gilt-brass hover:text-gilt-brass transition-colors"
              >
                Cancel push for these
              </button>
              <button
                onClick={() => tallyDupModal.onConfirm()}
                className="px-4 py-2 text-sm font-semibold text-gilt-paper bg-gilt-brass rounded-lg hover:opacity-90 transition-opacity"
              >
                Push anyway
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Modals */}
      {showNewLedgerModal && (
        <LedgerCreateModal
          open={showNewLedgerModal}
          ledgerType={newLedgerType}
          parentGroups={parentGroups}
          initialValues={newLedgerPrefill}
          busy={isCreatingLedger}
          onClose={() => { setShowNewLedgerModal(false); setNewLedgerPrefill({}); }}
          onCreate={handleCreateLedger}
        />
      )}
      {showNewItemModal && (
        <NewItemModal
          styles={styles}
          newItem={newItem}
          setNewItem={setNewItem}
          isCreatingItem={isCreatingItem}
          onClose={() => setShowNewItemModal(false)}
          onCreate={handleCreateStockItem}
        />
      )}
    </div>
  );
}

// ══════════════════════════════════════════════════════════════════
// ██  InvoiceCard — thumbnail + summary + actions
// ══════════════════════════════════════════════════════════════════
function InvoiceCard({ invoice, onOpen, onRemove, onPush, duplicateInfo = null, onKeepBothDuplicates }) {
  const h = invoice.data?.header || {};
  const itemsTotal = (invoice.data?.items || []).reduce((s, it) => {
    const q = parseFloat(it.quantity) || 0; const r = parseFloat(it.rate) || 0; const d = parseFloat(it.discount_percent) || 0;
    return s + q * r * (1 - d / 100);
  }, 0);
  const taxTotal = (invoice.data?.taxes || []).reduce((s, t) => s + (parseFloat(t.amount) || 0), 0);
  const tdsTotal = (invoice.tds_ledger && parseFloat(invoice.tds_percent) > 0)
    ? Math.round(itemsTotal * (parseFloat(invoice.tds_percent) || 0) / 100 * 100) / 100
    : 0;
  const total = itemsTotal + taxTotal - tdsTotal;

  const hasError = invoice.pushStatus === "error" || invoice.status === "failed";
  const isDuplicate = !!duplicateInfo;

  return (
    <div className={`bg-gilt-card rounded-xl border transition-colors overflow-hidden flex flex-col ${
      hasError
        ? "border-gilt-debit/40 hover:border-gilt-debit"
        : isDuplicate
        ? "border-gilt-brass/40 hover:border-gilt-brass"
        : "border-gilt-hairline hover:border-gilt-brass"
    }`}>
      {isDuplicate && (
        <div className="px-3 py-2 bg-gilt-sunken border-b border-gilt-brass/40 text-[11px] text-gilt-brass flex items-start gap-2">
          <AlertCircle className="h-3.5 w-3.5 text-gilt-brass mt-0.5 flex-shrink-0" />
          <div className="flex-1 min-w-0">
            <p className="font-medium leading-tight">Possible duplicate</p>
            <p className="leading-snug truncate" title={duplicateInfo.ofFilename}>
              Same {duplicateInfo.matchedBy === "gstin" ? "GSTIN + invoice #" : "vendor + invoice #"} as <span className="font-medium">{duplicateInfo.ofFilename}</span>
            </p>
            <div className="flex items-center gap-2 mt-1.5">
              <button
                onClick={(e) => { e.stopPropagation(); onRemove(); }}
                className="px-2 py-0.5 bg-gilt-card border border-gilt-hairline text-gilt-soft rounded text-[10px] font-medium hover:border-gilt-brass hover:text-gilt-brass transition-colors"
              >
                Skip this one
              </button>
              <button
                onClick={(e) => { e.stopPropagation(); onKeepBothDuplicates && onKeepBothDuplicates(); }}
                className="px-2 py-0.5 bg-gilt-brass text-gilt-paper rounded text-[10px] font-medium hover:opacity-90 transition-opacity"
              >
                Keep both
              </button>
            </div>
          </div>
        </div>
      )}
      {/* Thumbnail */}
      <div
        className="relative bg-gilt-sunken aspect-[4/5] overflow-hidden cursor-pointer flex items-center justify-center"
        onClick={onOpen}
      >
        {invoice.pdfContent ? (
          isImageMime(invoice.fileMime) ? (
            <img
              src={invoice.pdfContent}
              alt={invoice.filename || "Invoice"}
              className="w-full h-full object-contain bg-gilt-card"
              loading="lazy"
            />
          ) : (
            <Document
              file={invoice.pdfContent}
              loading={<RefreshCw className="h-6 w-6 text-gilt-faint animate-spin" />}
              error={<FileText className="h-10 w-10 text-gilt-faint" />}
              className="w-full h-full flex items-center justify-center"
            >
              <Page
                pageNumber={1}
                width={260}
                renderAnnotationLayer={false}
                renderTextLayer={false}
                className="border border-gilt-hairline"
              />
            </Document>
          )
        ) : (
          <FileText className="h-10 w-10 text-gilt-faint" />
        )}
        {invoice.status === "processing" && (
          <div className="absolute inset-0 bg-gilt-paper/60 flex items-center justify-center">
            <RefreshCw className="h-8 w-8 text-gilt-brass animate-spin" />
          </div>
        )}
        <div className="absolute top-2 left-2">
          <StatusBadge status={invoice.status} pushStatus={invoice.pushStatus} />
        </div>
        <button
          onClick={(e) => { e.stopPropagation(); onRemove(); }}
          className="absolute top-2 right-2 p-1 text-gilt-faint bg-gilt-card/80 border border-gilt-hairline hover:text-gilt-debit hover:bg-gilt-card rounded-lg transition-colors"
          title="Remove"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>

      {/* Body */}
      <div className={`p-3 flex-1 flex flex-col gap-2 min-h-0 ${hasError ? "bg-gilt-sunken" : ""}`}>
        <div className="min-w-0">
          <p className="text-xs font-medium text-gilt-ink truncate" title={invoice.filename}>{invoice.filename}</p>
          {invoice.data ? (
            <p className="text-[11px] text-gilt-soft truncate">{h.party_name || "Unknown vendor"}</p>
          ) : invoice.status === "failed" ? (
            <p className="text-[11px] text-gilt-debit truncate" title={invoice.error}>{invoice.error || "Extraction failed"}</p>
          ) : (
            <p className="text-[11px] text-gilt-faint">—</p>
          )}
        </div>

        {invoice.data && (
          <div className="flex items-center justify-between text-[11px] text-gilt-soft">
            <span className="font-mono tabular-nums">{h.invoice_number || "No invoice #"}</span>
            <span className="font-semibold text-gilt-ink font-mono tabular-nums">{total.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span>
          </div>
        )}

        {invoice.pushStatus === "error" && invoice.pushMessage && (
          <div
            className="text-[11px] text-gilt-debit bg-gilt-sunken border border-gilt-debit/40 rounded px-2 py-1 line-clamp-2"
            title={invoice.pushMessage}
          >
            <span className="font-medium">Push error:</span> {invoice.pushMessage}
          </div>
        )}

        <div className="flex items-center gap-1.5 mt-auto pt-2 border-t border-gilt-hairline">
          <button
            onClick={onOpen}
            className="flex-1 px-2 py-1.5 text-xs font-medium text-gilt-brass border border-gilt-brass/40 rounded-lg hover:bg-gilt-sunken transition-colors"
          >
            {invoice.status === "done" ? "Review & Edit" : invoice.status === "failed" ? "Details" : "Open"}
          </button>
          {invoice.status === "done" && invoice.pushStatus !== "success" && (
            <button
              onClick={onPush}
              disabled={invoice.pushStatus === "pending"}
              className="flex items-center gap-1 px-2 py-1.5 text-xs font-medium text-gilt-paper bg-gilt-brass rounded-lg hover:opacity-90 disabled:opacity-50 transition-opacity"
              title="Push to Tally"
            >
              {invoice.pushStatus === "pending" ? <RefreshCw className="h-3 w-3 animate-spin" /> : <Send className="h-3 w-3" />}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

// ══════════════════════════════════════════════════════════════════
// ██  Shared Modals
// ══════════════════════════════════════════════════════════════════
function NewItemModal({ styles, newItem, setNewItem, isCreatingItem, onClose, onCreate }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-gilt-ink/40">
      <div className="bg-gilt-card border border-gilt-hairline rounded-xl w-full max-w-md p-6">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-semibold text-gilt-ink">Create New Stock Item</h3>
          <button onClick={onClose} className="p-1 hover:bg-gilt-sunken rounded-lg transition-colors">
            <X className="h-5 w-5 text-gilt-soft" />
          </button>
        </div>
        <div className="space-y-3">
          <div>
            <label className={styles.label}>Item Name <span className="text-gilt-debit">*</span></label>
            <input
              type="text"
              value={newItem.name}
              onChange={(e) => setNewItem((prev) => ({ ...prev, name: e.target.value }))}
              className={styles.input}
              placeholder="Enter item name"
              autoFocus
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className={styles.label}>Unit</label>
              <select
                value={newItem.unit}
                onChange={(e) => setNewItem((prev) => ({ ...prev, unit: e.target.value }))}
                className={styles.input}
              >
                <option value="Nos">Nos</option>
                <option value="Pcs">Pcs</option>
                <option value="Kg">Kg</option>
                <option value="Ltr">Ltr</option>
                <option value="Mtr">Mtr</option>
                <option value="Box">Box</option>
                <option value="Set">Set</option>
              </select>
            </div>
            <div>
              <label className={styles.label}>HSN Code</label>
              <input
                type="text"
                value={newItem.hsn_code}
                onChange={(e) => setNewItem((prev) => ({ ...prev, hsn_code: e.target.value }))}
                className={styles.input}
                placeholder="e.g. 8471"
              />
            </div>
          </div>
        </div>
        <div className="flex justify-end gap-2 mt-5">
          <button onClick={onClose} className="px-4 py-2 text-sm text-gilt-soft border border-gilt-hairline rounded-lg hover:border-gilt-brass hover:text-gilt-brass transition-colors">
            Cancel
          </button>
          <button
            onClick={onCreate}
            disabled={isCreatingItem || !newItem.name.trim()}
            className="px-4 py-2 text-sm font-medium text-gilt-paper bg-gilt-brass rounded-lg hover:opacity-90 disabled:opacity-50 transition-opacity"
          >
            {isCreatingItem ? "Creating..." : "Create Item"}
          </button>
        </div>
      </div>
    </div>
  );
}
