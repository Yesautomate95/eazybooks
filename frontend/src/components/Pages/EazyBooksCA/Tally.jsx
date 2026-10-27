import React, { useState, useEffect, useCallback } from "react";
import {
  Upload,
  FileText,
  AlertCircle,
  ChevronDown,
  ChevronUp,
  Download,
  CreditCard,
  Receipt,
  ShoppingCart,
  ArrowRight,
  ArrowLeft,
  Building2,
  X,
  RefreshCw,
  Landmark,
  Plus,
  Loader2,
} from "lucide-react";
import { tallyApi } from "../../../services/api";
import BankEntriesView from "./BankEntriesView";
import SalesView from "./SalesView";
import PurchaseInvoiceView from "./PurchaseInvoiceView";
import BankReconciliationView from "./BankReconciliationView";
import LedgerCreateModal from "./common/LedgerCreateModal";
import DraftCard from "./common/DraftCard";
import DraftGroupHeader from "./common/DraftGroupHeader";
import { pushDraft } from "./common/pushers";
import { errorText } from "./common/errorText";
import { usePermissions, PermissionGuard } from "../../rbac";

// ── Helpers ──────────────────────────────────────────────────────────────────

// Converts a raw backend-extracted invoice (from _extract_one_sales_invoice /
// _extract_one_purchase_invoice) to the invoice object shape that SalesView /
// PurchaseInvoiceView expect. Also passes through already-adapted objects.
// `draftMeta` carries the draft-level metadata (file_url / file_mime / file_name)
// the editor uses to rehydrate the PDF preview when a saved draft is reopened —
// the per-invoice `pdf_data` blob is never persisted to the DB, so this is the
// only way to get the PDF back on screen.
function adaptExtractedInvoice(raw, flow = "sales", draftMeta = null) {
  // Already in SalesView/PurchaseInvoiceView format (has id + status + data).
  // Restore the PDF preview from the per-invoice R2 URL when reopening a
  // saved draft (pdfContent was stripped to keep the JSONB payload small).
  // Falls back to the draft-level fileUrl for legacy drafts that only
  // carried one PDF for the whole batch.
  if (raw.id && raw.status && raw.data) {
    const previewUrl = raw.pdfPreviewUrl || raw.file_url || draftMeta?.fileUrl;
    if (!raw.pdfContent && previewUrl) {
      return {
        ...raw,
        pdfContent: previewUrl,
        fileMime: raw.fileMime || draftMeta?.fileMime,
      };
    }
    return raw;
  }

  // Support both nested format {header, items, taxes} and legacy flat purchase format
  // {vendor_name, invoice_date, items, tax_entries} from old DB drafts.
  const header = raw.header || {};
  const summaryData = raw.summary || {};
  const items = (raw.items || []).map((it) => ({
    item_name: it.item_name || it.description || it.name || "",
    quantity: parseFloat(it.quantity) || 0,
    rate: parseFloat(it.rate || it.price) || 0,
    amount: parseFloat(it.amount) || (parseFloat(it.quantity || 0) * parseFloat(it.rate || 0)),
    unit: it.unit || "Nos",
    discount_percent: parseFloat(it.discount_percent || it.discount_percentage) || 0,
    godown: it.godown || "Main Location",
    batch: it.batch || it.batch_number || "",
  }));
  const taxes = (raw.taxes || raw.tax_entries || []).map((t) => ({
    ledger_name: t.ledger_name || t.tax_name || t.name || "",
    description: t.description || "",
    amount: parseFloat(t.amount) || 0,
    ai_suggestion: t.ai_suggestion || null,
  }));
  const pdfMime = raw.file_mime || draftMeta?.fileMime || "application/pdf";
  const isPurchase = flow === "purchase";

  // Priority for the preview source:
  //   1. Fresh extraction: per-invoice pdf_data base64 from the upload SSE.
  //   2. Reopened draft: presigned URL resolved by handleOpenDraft from r2_key.
  //   3. Reopened draft: stored per-invoice file_url (legacy / fallback).
  //   4. Reopened draft: the draft-level file_url stamped by handleOpenDraft.
  //   5. Nothing — the preview pane renders an empty-state placeholder.
  const resolvedPdfContent =
    (raw.pdf_data ? `data:${pdfMime};base64,${raw.pdf_data}` : null)
    || raw.pdfPreviewUrl
    || raw.file_url
    || raw.fileUrl
    || draftMeta?.fileUrl
    || null;

  return {
    id: `draft_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`,
    filename: raw.original_filename || draftMeta?.fileName || "invoice.pdf",
    status: "done",
    data: {
      header: {
        voucher_date: header.voucher_date || header.date || header.invoice_date || raw.invoice_date || raw.date || "",
        invoice_number: header.invoice_number || header.number || header.reference_number || raw.invoice_number || raw.reference_number || "",
        party_name: header.party_name || header.buyer_name || header.vendor_name || raw.vendor_name || raw.party_name || "",
        party_gstin: header.party_gstin || header.vendor_gstin || header.gstin || raw.vendor_gstin || raw.party_gstin || raw.gstin || "",
        place_of_supply: header.place_of_supply || raw.place_of_supply || "",
        narration: header.narration || summaryData.narration || raw.description || raw.narration || "",
      },
      items,
      taxes,
      summary: { narration: header.narration || summaryData.narration || raw.description || "" },
    },
    confidence: header.confidence || raw.confidence_scores || {},
    pdfContent: resolvedPdfContent,
    fileMime: pdfMime,
    pushStatus: null,
    pushMessage: "",
    ...(isPurchase
      ? { selectedPurchaseLedger: "", aiVendorSuggestion: null, aiPurchaseLedgerSuggestion: null }
      : { selectedSalesLedger: "", aiPartySuggestion: null, aiSalesLedgerSuggestion: null }),
    tds_ledger: "",
    tds_percent: 0,
    aiSuggestLoading: false,
  };
}

// ── Constants ─────────────────────────────────────────────────────────────────

// Desktop release distribution lives in Cloudflare R2 (managed r2.dev
// subdomain). LATEST_VERSION is bumped automatically by the release-desktop
// GitHub Actions workflow on tag push — don't edit by hand, or it'll drift
// again like the old 1.0.3 hardcode did.
const LATEST_VERSION = "1.0.12";
const DESKTOP_DOWNLOAD_BASE = "https://eazybooks.ai/downloads";
const DOWNLOAD_URL = `${DESKTOP_DOWNLOAD_BASE}/EazyBooks-Setup-${LATEST_VERSION}.exe`;

// bank_entries is the only flow that uses R2 upload + draft card library.
// sales and purchase open their DataView directly (built-in bulk multi-file upload).
const R2_FLOWS = ["bank_entries"];

const UPLOAD_FLOWS = {
  sales: {
    id: "sales",
    title: "Sales Invoice",
    description: "Bulk multi-file upload with AI extraction — opens editor directly",
    icon: Receipt,
  },
  purchase: {
    id: "purchase",
    title: "Purchase Invoice",
    description: "Bulk multi-file upload with AI extraction — opens editor directly",
    icon: ShoppingCart,
  },
  bank_entries: {
    id: "bank_entries",
    title: "Bank Statement",
    description: "Upload statement to library — review extracted data before pushing",
    icon: CreditCard,
    acceptedFormats: ".pdf,.csv,.xlsx,.xls,.xml",
    formatHint: "PDF, CSV, Excel, or XML",
  },
  bank_reconciliation: {
    id: "bank_reconciliation",
    title: "Bank Reconciliation",
    description: "Reconcile your Tally bank ledger against a bank statement",
    icon: Landmark,
  },
};

// ── Upload Modal ──────────────────────────────────────────────────────────────

function UploadModal({
  companies,
  isLoadingCompanies,
  onClose,
  onUploaded,
  onDirectOpen,
}) {
  const [step, setStep] = useState(1); // 1=company 2=doctype 3=file 4=bankledger
  const [company, setCompany] = useState("");
  const [flow, setFlow] = useState("");
  const [file, setFile] = useState(null);
  const [bankLedger, setBankLedger] = useState("");
  const [bankLedgers, setBankLedgers] = useState([]);
  const [allLedgers, setAllLedgers] = useState([]);
  const [isLoadingLedgers, setIsLoadingLedgers] = useState(false);
  const [showLedgerDrop, setShowLedgerDrop] = useState(false);
  const [showNewLedgerModal, setShowNewLedgerModal] = useState(false);
  const [isCreatingLedger, setIsCreatingLedger] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [error, setError] = useState(null);
  const [manualCompany, setManualCompany] = useState("");
  const [showManualEntry, setShowManualEntry] = useState(false);

  const fetchLedgers = useCallback(async (companyName) => {
    if (!companyName) return;
    setIsLoadingLedgers(true);
    try {
      const res = await tallyApi.getLedgers(companyName);
      if (res.success && res.ledgers) {
        const mapped = res.ledgers.map((l) => ({
          name: l.name || l,
          group: l.group || l.parent || "",
        }));
        setAllLedgers(mapped.map((l) => l.name));
        setBankLedgers(
          mapped
            .filter(
              (l) =>
                l.group === "Bank Accounts" ||
                l.group === "Bank OD A/c" ||
                l.group === "Bank OCC A/c" ||
                l.name.toLowerCase().includes("bank")
            )
            .map((l) => l.name)
        );
      }
    } catch {
      // non-critical
    } finally {
      setIsLoadingLedgers(false);
    }
  }, []);

  const handleSelectCompany = (name) => {
    setCompany(name);
    setStep(2);
  };

  const handleSelectFlow = (f) => {
    // bank_reconciliation and sales/purchase open their DataView directly
    if (!R2_FLOWS.includes(f)) {
      onDirectOpen(company, f);
      onClose();
      return;
    }
    setFlow(f);
    setStep(3);
  };

  const handleFileDrop = (e) => {
    e.preventDefault();
    const dropped = e.dataTransfer.files[0];
    if (dropped) setFile(dropped);
  };

  const handleFileChange = (e) => {
    setFile(e.target.files[0] || null);
  };

  const handleContinueFromFile = () => {
    if (!file) return setError("Please select a file");
    setError(null);
    if (flow === "bank_entries") {
      fetchLedgers(company);
      setStep(4);
    } else {
      handleUpload();
    }
  };

  const handleUpload = async (ledger) => {
    setIsUploading(true);
    setError(null);
    try {
      const result = await tallyApi.uploadFile(file, company, flow, ledger || bankLedger || undefined);
      onUploaded(result);
      onClose();
    } catch (err) {
      setError(errorText(err, "Upload failed. Please try again."));
    } finally {
      setIsUploading(false);
    }
  };

  const handleCreateLedger = async (payload) => {
    if (!payload?.name?.trim()) return;
    setIsCreatingLedger(true);
    try {
      const res = await tallyApi.createLedger({
        ...payload,
        company,
        name: payload.name.trim(),
      });
      if (res.success) {
        const newName = payload.name.trim();
        setBankLedgers((prev) => [...prev, newName]);
        setAllLedgers((prev) => [...prev, newName]);
        setBankLedger(newName);
        setShowNewLedgerModal(false);
      } else {
        setError(res.error || "Failed to create ledger");
      }
    } catch (err) {
      setError(errorText(err, "Failed to create ledger in Tally"));
    } finally {
      setIsCreatingLedger(false);
    }
  };

  const flowConfig = UPLOAD_FLOWS[flow];

  return (
    <>
      <div className="fixed inset-0 z-40 bg-gilt-ink/40 flex items-center justify-center p-4">
        <div className="bg-gilt-card border border-gilt-hairline rounded-xl w-full max-w-lg max-h-[90vh] overflow-y-auto">
          {/* Modal header */}
          <div className="flex items-center justify-between px-6 py-4 border-b border-gilt-hairline">
            <h2 className="text-lg font-semibold text-gilt-ink">New Upload</h2>
            <button
              onClick={onClose}
              className="p-1.5 rounded-md text-gilt-faint hover:text-gilt-ink hover:bg-gilt-sunken transition-colors"
            >
              <X className="w-5 h-5" />
            </button>
          </div>

          {/* Step indicator */}
          <div className="flex gap-1 px-6 pt-4">
            {["Company", "Type", "File", ...(flow === "bank_entries" ? ["Bank"] : [])].map((label, i) => (
              <React.Fragment key={label}>
                <div className="flex items-center gap-1.5">
                  <div
                    className={`w-6 h-6 rounded-full flex items-center justify-center text-xs font-mono transition-colors duration-150 ${
                      step > i + 1
                        ? "bg-gilt-ink text-gilt-paper"
                        : step === i + 1
                        ? "border border-gilt-brass text-gilt-brass"
                        : "bg-gilt-sunken text-gilt-faint"
                    }`}
                  >
                    {i + 1}
                  </div>
                  <span className={`text-xs ${step >= i + 1 ? "text-gilt-soft" : "text-gilt-faint"}`}>{label}</span>
                </div>
                {i < (flow === "bank_entries" ? 3 : 2) && (
                  <div className={`flex-1 h-px mt-3 mx-1 rounded ${step > i + 1 ? "bg-gilt-brass" : "bg-gilt-hairline"}`} />
                )}
              </React.Fragment>
            ))}
          </div>

          <div className="px-6 py-5">
            {/* Step 1: Company */}
            {step === 1 && (
              <div className="space-y-3">
                <p className="text-sm text-gilt-soft mb-4">Which company are you importing into?</p>
                {isLoadingCompanies ? (
                  <div className="flex items-center justify-center py-8 text-gilt-soft">
                    <Loader2 className="w-5 h-5 animate-spin mr-2 text-gilt-brass" />
                    Loading companies…
                  </div>
                ) : (
                  <>
                    {companies.length === 0 && !showManualEntry && (
                      <div className="text-center py-4 rounded-lg bg-gilt-sunken border border-gilt-brass/40">
                        <AlertCircle className="w-8 h-8 text-gilt-brass mx-auto mb-2" />
                        <p className="text-sm text-gilt-soft px-4">
                          Tally isn't reachable right now and we haven't seen your
                          companies before. You can still continue — type the company
                          name and we'll save your work as a draft until Tally is online.
                        </p>
                      </div>
                    )}
                    {companies.map((c, i) => (
                      <button
                        key={i}
                        onClick={() => handleSelectCompany(c.name)}
                        className="w-full p-3.5 rounded-lg border border-gilt-hairline bg-gilt-card hover:border-gilt-brass text-left flex items-center gap-3 transition-colors duration-150"
                      >
                        <Building2 className="w-5 h-5 text-gilt-faint" />
                        <span className="font-medium text-gilt-ink">{c.name}</span>
                        <ArrowRight className="w-4 h-4 text-gilt-faint ml-auto" />
                      </button>
                    ))}

                    {showManualEntry || companies.length === 0 ? (
                      <div className="rounded-lg border border-dashed border-gilt-hairline p-3.5 space-y-2">
                        <label className="text-xs font-medium text-gilt-soft">
                          Type the company name
                        </label>
                        <input
                          type="text"
                          value={manualCompany}
                          onChange={(e) => setManualCompany(e.target.value)}
                          placeholder="e.g. ABC Pvt Ltd"
                          className="w-full px-3 py-2 text-sm bg-gilt-card text-gilt-ink placeholder:text-gilt-faint border border-gilt-hairline rounded-md focus:outline-none focus:border-gilt-brass focus:ring-1 focus:ring-gilt-brass"
                          autoFocus={companies.length === 0}
                        />
                        <div className="flex items-center gap-2">
                          <button
                            onClick={() => {
                              const name = manualCompany.trim();
                              if (name) handleSelectCompany(name);
                            }}
                            disabled={!manualCompany.trim()}
                            className="flex-1 px-3 py-2 text-sm font-semibold text-gilt-paper bg-gilt-brass rounded-md hover:bg-gilt-brass/90 disabled:opacity-50 transition-colors"
                          >
                            Continue
                          </button>
                          {companies.length > 0 && (
                            <button
                              onClick={() => {
                                setShowManualEntry(false);
                                setManualCompany("");
                              }}
                              className="px-3 py-2 text-sm text-gilt-soft hover:text-gilt-ink transition-colors"
                            >
                              Cancel
                            </button>
                          )}
                        </div>
                        <p className="text-[11px] text-gilt-faint">
                          The name must match the company in Tally exactly when you
                          push later.
                        </p>
                      </div>
                    ) : (
                      <button
                        onClick={() => setShowManualEntry(true)}
                        className="w-full text-xs text-gilt-brass hover:text-gilt-ink py-2 text-center transition-colors"
                      >
                        Don't see your company? Enter it manually
                      </button>
                    )}
                  </>
                )}
              </div>
            )}

            {/* Step 2: Document type */}
            {step === 2 && (
              <div className="space-y-3">
                <p className="text-sm text-gilt-soft mb-4">
                  Company: <span className="font-medium text-gilt-ink">{company}</span>
                </p>
                {Object.values(UPLOAD_FLOWS).map((f, i) => {
                  const Icon = f.icon;
                  return (
                    <button
                      key={f.id}
                      onClick={() => handleSelectFlow(f.id)}
                      className="group w-full p-4 rounded-lg border border-gilt-hairline bg-gilt-card text-left flex items-center gap-4 hover:border-gilt-brass transition-colors duration-150"
                    >
                      <div className="p-2 rounded-md border border-gilt-hairline bg-gilt-sunken text-gilt-brass" aria-hidden="true">
                        <Icon className="w-5 h-5" />
                      </div>
                      <div className="flex-1 min-w-0">
                        <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-gilt-brass" aria-hidden="true">
                          № {String(i + 1).padStart(2, "0")}
                        </p>
                        <p className="font-semibold text-gilt-ink">{f.title}</p>
                        <p className="text-xs text-gilt-faint">{f.formatHint}</p>
                      </div>
                      <ArrowRight className="w-4 h-4 text-gilt-faint group-hover:text-gilt-brass transition-colors duration-150" />
                    </button>
                  );
                })}
                <button
                  onClick={() => setStep(1)}
                  className="flex items-center gap-2 text-sm text-gilt-soft hover:text-gilt-ink transition-colors mt-3"
                >
                  <ArrowLeft className="w-4 h-4" />
                  Change company
                </button>
              </div>
            )}

            {/* Step 3: File upload */}
            {step === 3 && flowConfig && (
              <div className="space-y-4">
                <p className="text-sm text-gilt-soft">
                  {flowConfig.title} · <span className="font-medium text-gilt-ink">{company}</span>
                </p>

                <div
                  onDrop={handleFileDrop}
                  onDragOver={(e) => e.preventDefault()}
                  className={`border border-dashed rounded-lg p-8 text-center cursor-pointer transition-colors duration-150 ${
                    file ? "border-gilt-credit/50 bg-gilt-sunken" : "border-gilt-hairline hover:border-gilt-brass"
                  }`}
                >
                  <input
                    type="file"
                    accept={flowConfig.acceptedFormats}
                    onChange={handleFileChange}
                    className="hidden"
                    id="modal-file-upload"
                  />
                  <label htmlFor="modal-file-upload" className="cursor-pointer">
                    {file ? (
                      <div>
                        <div className="w-10 h-10 rounded-full bg-gilt-card border border-gilt-hairline flex items-center justify-center mx-auto mb-3">
                          <FileText className="w-5 h-5 text-gilt-credit" />
                        </div>
                        <p className="font-medium text-gilt-credit text-sm">{file.name}</p>
                        <p className="font-mono tabular-nums text-xs text-gilt-soft mt-1">{(file.size / 1024).toFixed(1)} KB · Click to change</p>
                      </div>
                    ) : (
                      <div>
                        <Upload className="w-10 h-10 text-gilt-faint mx-auto mb-3" />
                        <p className="text-sm font-medium text-gilt-soft">Drag & drop or click to select</p>
                        <p className="text-xs text-gilt-faint mt-1">{flowConfig.formatHint}</p>
                      </div>
                    )}
                  </label>
                </div>

                {error && (
                  <div className="p-3 bg-gilt-sunken border border-gilt-debit/40 text-gilt-debit rounded-md text-sm flex items-center gap-2">
                    <AlertCircle className="w-4 h-4 flex-shrink-0" />
                    {error}
                  </div>
                )}

                <div className="flex items-center justify-between pt-1">
                  <button
                    onClick={() => { setStep(2); setFile(null); setFlow(""); }}
                    className="flex items-center gap-2 text-sm text-gilt-soft hover:text-gilt-ink transition-colors"
                  >
                    <ArrowLeft className="w-4 h-4" />
                    Back
                  </button>
                  <button
                    onClick={handleContinueFromFile}
                    disabled={!file || isUploading}
                    className="flex items-center gap-2 px-5 py-2 bg-gilt-brass text-gilt-paper rounded-md text-sm font-medium hover:bg-gilt-brass/90 disabled:opacity-50 transition-colors"
                  >
                    {isUploading ? (
                      <>
                        <Loader2 className="w-4 h-4 animate-spin" />
                        Uploading…
                      </>
                    ) : (
                      <>
                        {flow === "bank_entries" ? "Continue" : "Upload"}
                        <ArrowRight className="w-4 h-4" />
                      </>
                    )}
                  </button>
                </div>
              </div>
            )}

            {/* Step 4: Bank ledger (bank_entries only) */}
            {step === 4 && flow === "bank_entries" && (
              <div className="space-y-4">
                <p className="text-sm text-gilt-soft">
                  Bank Statement · <span className="font-medium text-gilt-ink">{company}</span>
                </p>
                <p className="text-xs text-gilt-faint">File: <span className="font-mono">{file?.name}</span></p>

                <div>
                  <div className="flex items-center justify-between mb-2">
                    <label className="text-sm font-medium text-gilt-ink">Bank Ledger *</label>
                    <button
                      onClick={() => fetchLedgers(company)}
                      disabled={isLoadingLedgers}
                      className="text-xs text-gilt-brass hover:text-gilt-ink flex items-center gap-1 transition-colors"
                    >
                      <RefreshCw className={`w-3 h-3 ${isLoadingLedgers ? "animate-spin" : ""}`} />
                      Refresh
                    </button>
                  </div>

                  {isLoadingLedgers ? (
                    <div className="flex items-center justify-center py-4 border border-gilt-hairline rounded-lg text-gilt-soft text-sm">
                      <Loader2 className="w-4 h-4 animate-spin mr-2 text-gilt-brass" />
                      Loading ledgers…
                    </div>
                  ) : (
                    <div className="relative">
                      <button
                        onClick={() => setShowLedgerDrop(!showLedgerDrop)}
                        className="w-full p-3 border border-gilt-hairline rounded-lg text-left flex items-center justify-between bg-gilt-card hover:border-gilt-brass transition-colors duration-150"
                      >
                        <div className="flex items-center gap-3">
                          <Landmark className={`w-4 h-4 ${bankLedger ? "text-gilt-brass" : "text-gilt-faint"}`} />
                          <span className={bankLedger ? "text-gilt-ink text-sm" : "text-gilt-faint text-sm"}>
                            {bankLedger || "Select a bank ledger"}
                          </span>
                        </div>
                        {showLedgerDrop ? (
                          <ChevronUp className="w-4 h-4 text-gilt-faint" />
                        ) : (
                          <ChevronDown className="w-4 h-4 text-gilt-faint" />
                        )}
                      </button>

                      {showLedgerDrop && (
                        <div className="absolute z-10 w-full mt-1 bg-gilt-card border border-gilt-hairline rounded-lg shadow-sm max-h-56 overflow-auto">
                          <button
                            onClick={() => { setShowLedgerDrop(false); setShowNewLedgerModal(true); }}
                            className="w-full p-3 text-left hover:bg-gilt-sunken flex items-center gap-2 border-b border-gilt-hairline text-gilt-brass text-sm transition-colors"
                          >
                            <Plus className="w-4 h-4" />
                            <span className="font-medium">Create New Bank Ledger</span>
                          </button>
                          {(bankLedgers.length > 0 ? bankLedgers : allLedgers.slice(0, 20)).map((l, i) => (
                            <button
                              key={i}
                              onClick={() => { setBankLedger(l); setShowLedgerDrop(false); }}
                              className={`w-full p-3 text-left hover:bg-gilt-sunken flex items-center gap-2 text-sm transition-colors ${bankLedger === l ? "bg-gilt-sunken text-gilt-brass" : "text-gilt-ink"}`}
                            >
                              <Landmark className="w-4 h-4 text-gilt-faint flex-shrink-0" />
                              {l}
                            </button>
                          ))}
                          {bankLedgers.length === 0 && allLedgers.length === 0 && (
                            <p className="text-sm text-gilt-faint text-center py-4">No ledgers found in Tally</p>
                          )}
                        </div>
                      )}
                    </div>
                  )}
                </div>

                {error && (
                  <div className="p-3 bg-gilt-sunken border border-gilt-debit/40 text-gilt-debit rounded-md text-sm flex items-center gap-2">
                    <AlertCircle className="w-4 h-4 flex-shrink-0" />
                    {error}
                  </div>
                )}

                <div className="flex items-center justify-between pt-1">
                  <button
                    onClick={() => { setStep(3); setBankLedger(""); }}
                    className="flex items-center gap-2 text-sm text-gilt-soft hover:text-gilt-ink transition-colors"
                  >
                    <ArrowLeft className="w-4 h-4" />
                    Back
                  </button>
                  <button
                    onClick={() => handleUpload(bankLedger)}
                    disabled={!bankLedger || isUploading}
                    className="flex items-center gap-2 px-5 py-2 bg-gilt-brass text-gilt-paper rounded-md text-sm font-medium hover:bg-gilt-brass/90 disabled:opacity-50 transition-colors"
                  >
                    {isUploading ? (
                      <>
                        <Loader2 className="w-4 h-4 animate-spin" />
                        Uploading…
                      </>
                    ) : (
                      <>
                        Upload
                        <ArrowRight className="w-4 h-4" />
                      </>
                    )}
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      {showNewLedgerModal && (
        <LedgerCreateModal
          open
          ledgerType="bank"
          defaultParentGroup="Bank Accounts"
          parentGroups={["Bank Accounts", "Bank OD A/c", "Bank OCC A/c"]}
          busy={isCreatingLedger}
          onClose={() => setShowNewLedgerModal(false)}
          onCreate={handleCreateLedger}
          title="Create Bank Ledger"
        />
      )}
    </>
  );
}

// ── Empty State ───────────────────────────────────────────────────────────────

function EmptyState({ tab, onNewUpload, canUpload = true }) {
  const msgs = {
    all: { title: "No documents yet", sub: "Upload your first invoice or statement to get started." },
    pending: { title: "Nothing pending", sub: "Documents awaiting review or push will appear here." },
    pushed: { title: "Nothing pushed yet", sub: "Documents successfully pushed to Tally will appear here." },
  };
  const m = msgs[tab] || msgs.all;
  return (
    <div className="bg-gilt-card border border-gilt-hairline rounded-xl">
      <div className="flex flex-col items-center justify-center py-16 px-6 text-center">
        <div className="w-12 h-12 rounded-xl bg-gilt-sunken border border-gilt-hairline flex items-center justify-center mb-4 text-gilt-brass">
          <FileText className="w-5 h-5" strokeWidth={1.75} />
        </div>
        <p className="text-base font-semibold text-gilt-ink">{m.title}</p>
        <p className="text-sm text-gilt-soft mt-1 mb-5 max-w-sm">{m.sub}</p>
        {canUpload && (
          <button
            onClick={onNewUpload}
            className="inline-flex items-center gap-2 px-3.5 py-2 bg-gilt-brass text-gilt-paper rounded-md text-sm font-medium hover:bg-gilt-brass/90 transition-colors"
          >
            <Plus className="w-4 h-4" />
            New upload
          </button>
        )}
      </div>
    </div>
  );
}

// ── Extraction loading overlay ────────────────────────────────────────────────

function ExtractionOverlay() {
  return (
    <div className="fixed inset-0 z-50 bg-gilt-ink/30 backdrop-blur-sm flex items-center justify-center p-4">
      <div className="bg-gilt-card border border-gilt-hairline rounded-xl px-10 py-8 flex flex-col items-center gap-4">
        <div className="h-14 w-14 rounded-lg bg-gilt-sunken border border-gilt-hairline flex items-center justify-center">
          <Loader2 className="w-7 h-7 text-gilt-brass animate-spin" />
        </div>
        <div className="text-center">
          <p className="text-base font-semibold text-gilt-ink">Extracting details…</p>
          <p className="text-sm text-gilt-soft mt-1">This usually takes under a minute</p>
        </div>
      </div>
    </div>
  );
}

// ── Main Tally Component ──────────────────────────────────────────────────────

function Tally() {
  // Library state
  const [activeTab, setActiveTab] = useState("all");
  const [drafts, setDrafts] = useState([]);
  const [isLoadingDrafts, setIsLoadingDrafts] = useState(false);
  const [showUploadModal, setShowUploadModal] = useState(false);
  const [toast, setToast] = useState(null); // {msg, type}

  // Permissions — determine whether action affordances render. Backend also
  // enforces this on every endpoint, so failing the client check just keeps
  // the UI honest.
  const { hasPermission } = usePermissions();
  const canPushTally = hasPermission("tally.push");

  // DataView state
  const [showDataView, setShowDataView] = useState(false);
  const [documentType, setDocumentType] = useState("");
  const [selectedCompany, setSelectedCompany] = useState("");
  const [activeDraftId, setActiveDraftId] = useState(null);
  const [activeDraftPayload, setActiveDraftPayload] = useState(null);
  const [isExtracting, setIsExtracting] = useState(false);

  // Shared data (needed by DataViews)
  const [companies, setCompanies] = useState([]);
  const [isLoadingCompanies, setIsLoadingCompanies] = useState(false);
  const [ledgersAvailable, setLedgersAvailable] = useState([]);
  const [inventoryItems, setInventoryItems] = useState([]);

  // Bank reconciliation — direct DataView (no library flow)
  const [showBankRecon, setShowBankRecon] = useState(false);

  // Tally bridge state — informational for the library badge and gating
  // condition for the per-company bulk-push button.
  const [tallyConnected, setTallyConnected] = useState(null);
  const [currentTallyCompany, setCurrentTallyCompany] = useState("");
  const [bulkPushingCompany, setBulkPushingCompany] = useState(null);
  const [bulkPushProgress, setBulkPushProgress] = useState(null);
  const [bulkPushSummary, setBulkPushSummary] = useState(null);

  // ── Companies & Ledgers ─────────────────────────────────────────────────────

  useEffect(() => {
    const load = async () => {
      setIsLoadingCompanies(true);
      try {
        let loaded = false;
        try {
          const res = await tallyApi.getCompanies();
          if (res?.success && res.companies && res.companies.length > 0) {
            setCompanies(res.companies);
            setSelectedCompany(res.companies[0].name);
            loaded = true;
          }
        } catch {
          // Try the direct HTTP bridge below before using cached companies.
        }
        if (!loaded) {
          try {
            const direct = await tallyApi.getDirectCompanies();
            if (direct?.success && direct.companies && direct.companies.length > 0) {
              setCompanies(direct.companies);
              setSelectedCompany(direct.companies[0].name);
              loaded = true;
            }
          } catch {
            // fall through to cached fallback
          }
        }
        if (!loaded) {
          try {
            const cached = await tallyApi.getCachedCompanies();
            if (cached?.success && cached.companies?.length > 0) {
              setCompanies(cached.companies);
              setSelectedCompany(cached.companies[0].name);
            }
          } catch {
            // non-fatal
          }
        }
      } finally {
        setIsLoadingCompanies(false);
      }
    };
    load();
  }, []);

  // Poll Tally connection state for the library badge and bulk-push gate.
  // Same direct → WebSocket fallback the views use, kept passive: it never
  // blocks anything, it only tells the bulk-push button when it's safe to fire.
  //
  // Smoothing: a single failed poll won't flip the button to disabled — the
  // last 'connected' state is held for CONNECTION_GRACE_MS before downgrading.
  // Covers the 30s-interval case where one WS check misses transiently
  // (reconnect, brief network drop) without flickering the UI.
  useEffect(() => {
    let cancelled = false;
    const lastSuccessRef = { current: 0 };
    const CONNECTION_GRACE_MS = 90000;

    const check = async () => {
      let connected = false;
      let company = "";
      try {
        const direct = await tallyApi.checkDirectConnection();
        if (direct?.connected) {
          connected = true;
          company = direct.company || "";
        }
      } catch {
        // fall through
      }
      if (!connected) {
        try {
          const ws = await tallyApi.checkConnection();
          if (ws?.connected) {
            connected = true;
            company = ws.company || company;
          }
        } catch {
          // remain disconnected
        }
      }
      if (cancelled) return;
      if (connected) {
        lastSuccessRef.current = Date.now();
        setTallyConnected(true);
        if (company) setCurrentTallyCompany(company);
      } else {
        // Only downgrade after the grace window — protects against a single
        // transient miss between healthy polls.
        const sinceLastSuccess = Date.now() - lastSuccessRef.current;
        if (lastSuccessRef.current === 0 || sinceLastSuccess > CONNECTION_GRACE_MS) {
          setTallyConnected(false);
        }
      }
    };
    check();
    const interval = setInterval(check, 30000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  useEffect(() => {
    if (!selectedCompany) return;
    const loadLedgers = async () => {
      try {
        const res = await tallyApi.getLedgers(selectedCompany);
        if (res.success && res.ledgers) {
          setLedgersAvailable(res.ledgers.map((l) => l.name || l));
        }
      } catch {
        // non-fatal
      }
    };
    loadLedgers();
  }, [selectedCompany]);

  useEffect(() => {
    if (!selectedCompany) return;
    const loadInventory = async () => {
      try {
        const res = await tallyApi.getInventory(selectedCompany);
        if (res.success && res.inventory) setInventoryItems(res.inventory);
      } catch {
        // non-fatal
      }
    };
    loadInventory();
  }, [selectedCompany]);

  // ── Drafts list ─────────────────────────────────────────────────────────────

  const loadDrafts = useCallback(async () => {
    setIsLoadingDrafts(true);
    try {
      // Always fetch all drafts — filter client-side so tab badge counts are always correct
      const res = await tallyApi.drafts.list({});
      setDrafts(res.drafts || res || []);
    } catch {
      setDrafts([]);
    } finally {
      setIsLoadingDrafts(false);
    }
  }, []);

  useEffect(() => {
    if (!showDataView && !showBankRecon) {
      loadDrafts();
    }
  }, [loadDrafts, showDataView, showBankRecon]);

  // ── Toast ───────────────────────────────────────────────────────────────────

  const showToast = (msg, type = "success") => {
    setToast({ msg, type });
    setTimeout(() => setToast(null), 3500);
  };

  // ── Handlers ────────────────────────────────────────────────────────────────

  const handleUploaded = (result) => {
    showToast(`${result.file_name || "File"} uploaded successfully`);
    loadDrafts();
  };

  const handleExtract = async (draft) => {
    setIsExtracting(true);
    try {
      const res = await tallyApi.extractDraft(draft.id);
      // Reload drafts list then open DataView
      await loadDrafts();
      // Open DataView with extracted payload
      const payload = res.extracted_data || res;
      openDataView(draft.flow, draft.company_name, draft.id, payload);
    } catch (err) {
      showToast(errorText(err, "Extraction failed"), "error");
      loadDrafts();
    } finally {
      setIsExtracting(false);
    }
  };

  const handleOpenDraft = async (draft) => {
    // Fetch full draft payload
    try {
      const res = await tallyApi.drafts.get(draft.id);
      const rawPayload = res.payload || res;

      // Resolve fresh presigned download URLs for each file referenced by the
      // draft. The stored `file_url` is built from R2_PUBLIC_URL at upload
      // time and can 404 when that config drifts (e.g. bucket name ends up
      // duplicated in the path). Presigned URLs sidestep the public URL
      // entirely. Stash them on `pdfPreviewUrl` so file_url stays intact
      // for non-preview consumers (and we don't accidentally persist a
      // presigned URL on the next Save Draft — see serializeInvoicesForDraft).
      let resolvedPayload = { ...(rawPayload || {}) };
      if (Array.isArray(rawPayload?.invoices)) {
        resolvedPayload.invoices = await Promise.all(
          rawPayload.invoices.map(async (inv) => {
            if (!inv?.r2_key) return inv;
            try {
              const { url } = await tallyApi.drafts.getFileUrl(draft.id, inv.r2_key);
              return { ...inv, pdfPreviewUrl: url };
            } catch {
              return inv;
            }
          })
        );
      }

      let draftFileUrl = res.file_url || res.fileUrl || null;
      if (res.r2_key) {
        try {
          const { url } = await tallyApi.drafts.getFileUrl(draft.id);
          draftFileUrl = url;
        } catch {
          // Fall back to the stored URL — preview may 404 but link still renders.
        }
      }

      const payload = {
        ...resolvedPayload,
        _draft_file_url: draftFileUrl,
        _draft_file_mime: res.file_mime || res.fileMime || null,
        _draft_file_name: res.file_name || res.fileName || null,
      };
      openDataView(draft.flow, draft.company_name, draft.id, payload);
    } catch {
      showToast("Failed to load draft", "error");
    }
  };

  const openDataView = (flow, company, draftId, payload) => {
    setDocumentType(flow);
    setSelectedCompany(company);
    setActiveDraftId(draftId);
    setActiveDraftPayload(payload);
    setShowDataView(true);
  };

  const handleDeleteDraft = async (draft) => {
    try {
      await tallyApi.drafts.delete(draft.id);
      showToast("Document deleted");
      setDrafts((prev) => prev.filter((d) => d.id !== draft.id));
    } catch {
      showToast("Delete failed", "error");
    }
  };

  const handlePushFromCard = (draft) => {
    // "Push to Tally" from card → open DataView so user can review then push
    handleOpenDraft(draft);
  };

  // Bulk push every pushable draft in a company group. Validates each draft
  // first (company match + ledger presence), then pushes sequentially so the
  // user sees real-time progress and a failure on one draft doesn't sink the
  // rest.
  const handleBulkPush = async (company, pushable) => {
    if (!pushable.length || bulkPushingCompany) return;
    setBulkPushingCompany(company);
    setBulkPushSummary(null);
    setBulkPushProgress({ company, current: 0, total: pushable.length, currentTitle: "" });

    const results = [];
    for (let i = 0; i < pushable.length; i += 1) {
      const summary = pushable[i];
      setBulkPushProgress({
        company,
        current: i + 1,
        total: pushable.length,
        currentTitle: summary.title || "Untitled draft",
      });

      // Pre-flight: company + ledger presence. Skip the draft on hard fail.
      try {
        const check = await tallyApi.validateDraftPushable(summary.id);
        if (!check?.ok) {
          const reason = !check?.company_ok
            ? `Company '${company}' not found in Tally`
            : `Missing ledgers: ${(check?.missing_ledgers || []).join(", ")}`;
          results.push({ id: summary.id, title: summary.title, ok: false, message: reason });
          continue;
        }
      } catch (err) {
        // Validation endpoint failure shouldn't block the push outright.
      }

      try {
        const full = await tallyApi.drafts.get(summary.id);
        const draft = { ...summary, ...full };
        const pushResult = await pushDraft(draft);
        if (pushResult.ok) {
          await tallyApi.drafts.markPushed(summary.id).catch(() => {});
          results.push({ id: summary.id, title: summary.title, ok: true });
        } else {
          const firstErr = pushResult.errors?.[0]?.message || "Push failed";
          results.push({
            id: summary.id,
            title: summary.title,
            ok: false,
            message:
              pushResult.failed > 1
                ? `${pushResult.failed} of ${pushResult.total} entries failed (${firstErr})`
                : firstErr,
          });
        }
      } catch (err) {
        results.push({
          id: summary.id,
          title: summary.title,
          ok: false,
          message: errorText(err, "Push failed"),
        });
      }
    }

    const ok = results.filter((r) => r.ok).length;
    const failed = results.length - ok;
    setBulkPushSummary({ company, results, ok, failed });
    setBulkPushProgress(null);
    setBulkPushingCompany(null);
    showToast(
      failed === 0
        ? `Pushed ${ok} draft${ok === 1 ? "" : "s"} to ${company}`
        : `${ok} pushed · ${failed} failed in ${company}`,
      failed === 0 ? "success" : "error"
    );
    loadDrafts();
  };

  const handleDirectOpen = (company, flow) => {
    setSelectedCompany(company);
    if (flow === "bank_reconciliation") {
      setShowBankRecon(true);
    } else {
      setDocumentType(flow);
      setActiveDraftId(null);
      setActiveDraftPayload(null);
      setShowDataView(true);
    }
  };

  const handleBackFromDataView = () => {
    setShowDataView(false);
    setActiveDraftId(null);
    setActiveDraftPayload(null);
    setDocumentType("");
    loadDrafts();
  };

  // ── DataView rendering ──────────────────────────────────────────────────────

  if (showBankRecon) {
    return (
      <BankReconciliationView
        onBack={() => setShowBankRecon(false)}
        selectedCompany={selectedCompany}
      />
    );
  }

  if (showDataView) {
    const flow = documentType.toLowerCase();

    const rawInvoices = activeDraftPayload?.invoices || null;
    const draftMeta = {
      fileUrl: activeDraftPayload?._draft_file_url || null,
      fileMime: activeDraftPayload?._draft_file_mime || null,
      fileName: activeDraftPayload?._draft_file_name || null,
    };
    const adaptedInvoices = rawInvoices ? rawInvoices.map((inv) => adaptExtractedInvoice(inv, flow, draftMeta)) : null;

    if (flow === "sales") {
      return (
        <SalesView
          onBack={handleBackFromDataView}
          selectedCompany={selectedCompany}
          ledgersAvailable={ledgersAvailable}
          inventoryItems={inventoryItems}
          draftId={activeDraftId}
          initialInvoices={adaptedInvoices}
        />
      );
    }
    if (flow === "purchase" || flow === "purchase_invoice") {
      return (
        <PurchaseInvoiceView
          onBack={handleBackFromDataView}
          selectedCompany={selectedCompany}
          ledgersAvailable={ledgersAvailable}
          inventoryItems={inventoryItems}
          draftId={activeDraftId}
          initialInvoices={adaptedInvoices}
        />
      );
    }
    const bankXmlData = activeDraftPayload?.transactions
      ? [{
          transactions: activeDraftPayload.transactions,
          bankLedger: activeDraftPayload.bank_ledger || "",
          filename: activeDraftPayload._draft_file_name || "Draft",
          pdfData: activeDraftPayload._draft_file_url || null,
        }]
      : [];
    return (
      <BankEntriesView
        xmlData={bankXmlData}
        ledgersAvailable={ledgersAvailable}
        setLedgersAvailable={setLedgersAvailable}
        onBack={handleBackFromDataView}
        selectedCompany={selectedCompany}
        draftId={activeDraftId}
      />
    );
  }

  // ── Tab counts ──────────────────────────────────────────────────────────────
  // Lifecycle (simplified to a single "Pending" bucket):
  //   uploaded / extracting / extracted / draft / error  → "Pending"
  //   pushed                                              → "Pushed"
  // The 'draft' vs 'extracted' distinction is preserved internally as an audit
  // signal (was the entry human-reviewed?), but is no longer surfaced as a tab.

  const isPendingStatus = (s) =>
    s === "uploaded" ||
    s === "extracting" ||
    s === "extracted" ||
    s === "draft" ||
    s === "error";

  const countAll = drafts.length;
  const countPending = drafts.filter((d) => isPendingStatus(d.status)).length;
  const countPushed = drafts.filter((d) => d.status === "pushed").length;

  const tabDrafts =
    activeTab === "pending"
      ? drafts.filter((d) => isPendingStatus(d.status))
      : activeTab === "pushed"
      ? drafts.filter((d) => d.status === "pushed")
      : drafts;

  // ── Render library ──────────────────────────────────────────────────────────

  return (
    <div className="min-h-screen">
      {isExtracting && <ExtractionOverlay />}

      {/* Page Header */}
      <header className="border-b border-gilt-hairline">
        <div className="max-w-6xl mx-auto px-6 lg:px-8 py-5">
          <div className="flex items-start justify-between gap-4">
            <div className="flex items-start gap-3 min-w-0">
              <div className="shrink-0 mt-0.5 h-10 w-10 rounded-xl bg-gilt-card border border-gilt-hairline text-gilt-brass flex items-center justify-center" aria-hidden="true">
                <Receipt size={20} strokeWidth={1.75} />
              </div>
              <div className="min-w-0">
                <h1 className="gl-display text-2xl text-gilt-ink leading-tight">Tally Import</h1>
                <p className="mt-1 text-sm text-gilt-soft">
                  Upload documents, review AI-extracted entries, and push to Tally.
                </p>
              </div>
            </div>
            <div className="shrink-0 flex items-center gap-2">
              <button
                onClick={() => window.open(DOWNLOAD_URL, "_blank")}
                className="inline-flex items-center gap-2 h-9 px-3 text-sm font-medium text-gilt-soft border border-gilt-hairline hover:border-gilt-brass hover:text-gilt-brass rounded-md transition-colors"
              >
                <Download className="w-4 h-4" />
                Desktop <span className="font-mono text-xs">v{LATEST_VERSION}</span>
              </button>
              <PermissionGuard permission="tally.push">
                <button
                  onClick={() => setShowUploadModal(true)}
                  className="inline-flex items-center gap-2 h-9 px-3.5 text-sm font-medium bg-gilt-brass text-gilt-paper hover:bg-gilt-brass/90 rounded-md transition-colors"
                >
                  <Plus className="w-4 h-4" />
                  New upload
                </button>
              </PermissionGuard>
            </div>
          </div>

          {/* Tabs */}
          <div className="mt-5 flex items-center gap-1 border-b border-gilt-hairline">
            {[
              { key: "all", label: "All", count: countAll },
              { key: "pending", label: "Pending", count: countPending },
              { key: "pushed", label: "Pushed", count: countPushed },
            ].map(({ key, label, count }) => {
              const active = activeTab === key;
              return (
                <button
                  key={key}
                  onClick={() => setActiveTab(key)}
                  className={`relative inline-flex items-center gap-1.5 px-3 py-2.5 text-sm font-medium transition-colors -mb-px ${
                    active ? "text-gilt-ink" : "text-gilt-soft hover:text-gilt-ink"
                  }`}
                >
                  {label}
                  {count > 0 && (
                    <span
                      className={`px-1.5 py-0.5 rounded-full text-2xs font-mono tabular-nums ${
                        active
                          ? "bg-gilt-sunken text-gilt-brass"
                          : "bg-gilt-sunken text-gilt-faint"
                      }`}
                    >
                      {count}
                    </span>
                  )}
                  {active && (
                    <span className="absolute left-2 right-2 -bottom-px h-0.5 rounded-full bg-gilt-brass" aria-hidden="true" />
                  )}
                </button>
              );
            })}
            <button
              onClick={loadDrafts}
              disabled={isLoadingDrafts}
              className="ml-auto mb-1 inline-flex h-7 w-7 items-center justify-center text-gilt-faint hover:text-gilt-ink rounded-md hover:bg-gilt-sunken transition-colors"
              title="Refresh"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${isLoadingDrafts ? "animate-spin" : ""}`} />
            </button>
          </div>
        </div>
      </header>

      <div className="max-w-6xl mx-auto px-6 lg:px-8 py-6">
        {/* Content */}
        {isLoadingDrafts && drafts.length === 0 ? (
          <div className="flex items-center justify-center py-20 text-gilt-soft">
            <Loader2 className="w-5 h-5 animate-spin mr-2 text-gilt-brass" />
            <span className="text-sm">Loading documents…</span>
          </div>
        ) : tabDrafts.length === 0 ? (
          <EmptyState
            tab={activeTab}
            onNewUpload={() => setShowUploadModal(true)}
            canUpload={canPushTally}
          />
        ) : (
          (() => {
            const groups = new Map();
            tabDrafts.forEach((d) => {
              const key = d.company_name || "Unassigned";
              if (!groups.has(key)) groups.set(key, []);
              groups.get(key).push(d);
            });
            const groupEntries = Array.from(groups.entries()).sort((a, b) => {
              // Surface the currently-open Tally company first so its bulk-push
              // button is the obvious one.
              if (a[0] === currentTallyCompany) return -1;
              if (b[0] === currentTallyCompany) return 1;
              return a[0].localeCompare(b[0]);
            });
            return (
              <div className="space-y-6">
                {groupEntries.map(([company, groupDrafts]) => (
                  <section key={company}>
                    <DraftGroupHeader
                      company={company}
                      drafts={groupDrafts}
                      currentCompany={currentTallyCompany}
                      tallyConnected={tallyConnected}
                      isPushing={bulkPushingCompany === company}
                      onBulkPush={handleBulkPush}
                    />
                    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
                      {groupDrafts.map((draft) => (
                        <DraftCard
                          key={draft.id}
                          draft={draft}
                          onExtract={handleExtract}
                          onOpen={handleOpenDraft}
                          onDelete={handleDeleteDraft}
                          onPush={handlePushFromCard}
                        />
                      ))}
                    </div>
                  </section>
                ))}
              </div>
            );
          })()
        )}

        {bulkPushProgress && (
          <div className="fixed bottom-6 right-6 z-50 bg-gilt-card shadow-sm border border-gilt-hairline rounded-lg px-4 py-3.5 w-80">
            <div className="flex items-center gap-2 mb-2">
              <Loader2 className="w-4 h-4 animate-spin text-gilt-brass" />
              <p className="text-sm font-semibold text-gilt-ink">
                Pushing to {bulkPushProgress.company}
              </p>
            </div>
            <p className="text-xs text-gilt-soft mb-2 truncate">
              {bulkPushProgress.currentTitle}
            </p>
            <div className="h-1.5 bg-gilt-sunken rounded-full overflow-hidden">
              <div
                className="h-full bg-gilt-gold transition-all duration-300"
                style={{
                  width: `${(bulkPushProgress.current / bulkPushProgress.total) * 100}%`,
                }}
              />
            </div>
            <p className="font-mono text-2xs text-gilt-soft mt-1.5 tabular-nums">
              {bulkPushProgress.current} of {bulkPushProgress.total}
            </p>
          </div>
        )}

        {bulkPushSummary && bulkPushSummary.failed > 0 && (
          <div className="fixed bottom-6 right-6 z-50 bg-gilt-card shadow-sm border border-gilt-debit/40 rounded-lg p-4 w-96 max-h-80 overflow-y-auto">
            <div className="flex items-start justify-between mb-3">
              <div>
                <p className="text-sm font-semibold text-gilt-ink">
                  Bulk push to {bulkPushSummary.company}
                </p>
                <p className="font-mono text-xs text-gilt-soft tabular-nums">
                  {bulkPushSummary.ok} pushed · {bulkPushSummary.failed} failed
                </p>
              </div>
              <button
                onClick={() => setBulkPushSummary(null)}
                className="h-7 w-7 inline-flex items-center justify-center rounded-md text-gilt-faint hover:bg-gilt-sunken hover:text-gilt-ink transition-colors"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            </div>
            <ul className="space-y-1.5 text-xs">
              {bulkPushSummary.results
                .filter((r) => !r.ok)
                .map((r) => (
                  <li
                    key={r.id}
                    className="bg-gilt-sunken border border-gilt-debit/40 rounded-md px-2.5 py-1.5"
                  >
                    <p className="font-medium text-gilt-debit truncate">{r.title}</p>
                    <p className="text-gilt-debit/80">{r.message}</p>
                  </li>
                ))}
            </ul>
          </div>
        )}
      </div>

      {/* Upload Modal */}
      {showUploadModal && (
        <UploadModal
          companies={companies}
          isLoadingCompanies={isLoadingCompanies}
          onClose={() => setShowUploadModal(false)}
          onUploaded={handleUploaded}
          onDirectOpen={handleDirectOpen}
        />
      )}

      {/* Toast */}
      {toast && (
        <div
          className={`fixed bottom-5 right-5 z-[60] flex items-center gap-2.5 pl-3 pr-2 py-2.5 rounded-lg shadow-sm border text-sm font-medium ${
            toast.type === "error"
              ? "bg-gilt-card border-gilt-debit/40 text-gilt-debit"
              : "bg-gilt-card border-gilt-hairline text-gilt-ink"
          }`}
        >
          {toast.type === "error" ? (
            <AlertCircle className="w-4 h-4 flex-shrink-0 text-gilt-debit" />
          ) : (
            <div className="w-4 h-4 rounded-full bg-gilt-credit flex-shrink-0 flex items-center justify-center" aria-hidden="true">
              <svg width="10" height="10" viewBox="0 0 10 10" fill="none">
                <path d="M2 5L4 7L8 3" stroke="white" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </div>
          )}
          {toast.msg}
          <button
            onClick={() => setToast(null)}
            className="ml-1 h-6 w-6 inline-flex items-center justify-center rounded-md text-gilt-faint hover:bg-gilt-sunken hover:text-gilt-ink transition-colors"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        </div>
      )}
    </div>
  );
}

export default Tally;
