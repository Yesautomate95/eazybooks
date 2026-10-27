import React, { useEffect, useMemo, useState } from "react";
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
} from "lucide-react";
import { sapApi } from "../../../../services/api";
import { useDraftDeepLink } from "../common/useDraftDeepLink";
import PdfViewer from "../PdfViewer";
import SearchableDropdown from "../common/SearchableDropdown";
import ConfidenceBadge from "../common/ConfidenceBadge";
import CreateMasterModal from "./components/CreateMasterModal";
import MatchPicker from "./components/MatchPicker";
import BulkCreateModal from "./components/BulkCreateModal";
import DraftsPanel, { SaveDraftButton } from "../common/DraftsPanel";
import DuplicateBadge from "../common/DuplicateBadge";
import { PageHeader } from "../../../ui/page-header";
import { Button } from "../../../ui/button";
import { Select } from "../../../ui/input";
import { Card } from "../../../ui/card";

const inputBase = "w-full border border-gilt-hairline rounded-lg px-3 py-2 text-sm text-gilt-ink bg-gilt-paper placeholder:text-gilt-faint focus:border-gilt-brass outline-none transition-colors duration-150";
const labelBase = "block text-xs font-medium text-gilt-soft mb-1.5";

// Line-item cell styles — `inputBase` has `w-full` baked in, which loses the
// fight with a sibling `w-20` / `w-24` in the rendered CSS and leaves table
// cells effectively full-width (the documented gotcha in CLAUDE.md). These
// dedicated classes match ZohoSalesView.jsx so the SAP and Zoho line-items
// tables read as the same component.
const tableNumInput = "px-2 py-1 text-sm text-right border border-gilt-hairline rounded text-gilt-ink font-mono bg-gilt-paper focus:border-gilt-brass outline-none transition-colors duration-150 tabular-nums";
const tableTextInput = "w-full px-2 py-1 text-sm border border-gilt-hairline rounded text-gilt-ink bg-gilt-paper focus:border-gilt-brass outline-none transition-colors duration-150";
const btnPrimary = "inline-flex items-center gap-1.5 px-4 py-2 text-sm font-semibold text-gilt-paper bg-gilt-brass rounded-md hover:bg-gilt-brass/90 transition-colors duration-150 disabled:opacity-50 disabled:cursor-not-allowed";
// Primary push affordance — quiet ink plate per the gilt workroom idiom.
const btnSapBlue = "inline-flex items-center gap-1.5 px-4 py-2 text-sm font-semibold text-gilt-paper bg-gilt-brass rounded-md hover:bg-gilt-brass/90 transition-colors duration-150 disabled:opacity-50";

/**
 * SAP S/4HANA Sales Invoice flow (upgraded):
 *  - Left pane: PdfViewer of the uploaded source.
 *  - Right pane: editable header + line table with searchable dropdowns
 *    backed by the company code's full customer + item master lists.
 *  - Edits sync back into sap_payload so /push-invoice gets the latest.
 */
export default function SAPSalesView() {
    const [companies, setCompanies] = useState([]);
    const [company, setCompany] = useState("");
    const [file, setFile] = useState(null);
    const [pdfData, setPdfData] = useState(null);
    const [extracting, setExtracting] = useState(false);
    const [pushing, setPushing] = useState(false);
    const [result, setResult] = useState(null);
    const [pushResult, setPushResult] = useState(null);
    const [error, setError] = useState("");
    const [loadingCompanies, setLoadingCompanies] = useState(true);
    const [loadingMasters, setLoadingMasters] = useState(false);

    // Drafts
    const [draftId, setDraftId] = useState(null);
    const [draftsPanelOpen, setDraftsPanelOpen] = useState(false);
    const [draftsRefreshKey, setDraftsRefreshKey] = useState(0);
    const [savingDraft, setSavingDraft] = useState(false);

    // User explicitly overrode the duplicate verdict for this invoice.
    const [overrideDup, setOverrideDup] = useState(false);

    // Master data for dropdowns
    const [customers, setCustomers] = useState([]);
    const [items, setItems] = useState([]);

    // Inline-create modal state: which entity is being created and for which row.
    //   { entityType: 'customer'|'item', prefillName, targetLineIdx?: number }
    // targetLineIdx is set only for line-item creates so we know which line to patch.
    const [createModal, setCreateModal] = useState(null);
    const [bulkOpen, setBulkOpen] = useState(false);

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

    // Fetch customer + item master lists when company changes.
    // Note: SAP FI has no item master, so the items list is typically empty —
    // the UI handles that gracefully (empty dropdown, no crash).
    useEffect(() => {
        if (!company) return;
        setLoadingMasters(true);
        setCustomers([]);
        setItems([]);
        (async () => {
            try {
                const [c, i] = await Promise.all([
                    sapApi.getCustomers(company),
                    sapApi.getItems(company),
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

    const onFileChange = (e) => {
        const f = e.target.files?.[0] || null;
        setFile(f);
        setPdfData(null);
        if (f) {
            const reader = new FileReader();
            reader.onload = () => setPdfData(reader.result);
            reader.readAsDataURL(f);
        }
    };

    const onExtract = async () => {
        if (!file) {
            setError("Pick a PDF first.");
            return;
        }
        setExtracting(true);
        setError("");
        setResult(null);
        setPushResult(null);
        setOverrideDup(false);
        try {
            const res = await sapApi.extractInvoice(file, company);
            setResult(res);
        } catch (err) {
            setError(err?.response?.data?.detail || err.message || "Extraction failed");
        } finally {
            setExtracting(false);
        }
    };

    const duplicateBlocked = !!result?.duplicate_check?.is_duplicate && !overrideDup && !pushResult;

    const onPush = async () => {
        if (!result?.sap_payload || pushing || pushResult) return;
        setPushing(true);
        setError("");
        try {
            const res = await sapApi.pushInvoiceSap({
                payload: result.sap_payload,
                company_code: company,
                extracted_total: result.extracted_total ?? result.extracted?.total,
                skip_duplicate_check: overrideDup,
            });
            setPushResult(res);
        } catch (err) {
            const detail = err?.response?.data?.detail;
            setError(typeof detail === "string" ? detail : detail?.message || detail?.sap_error || detail?.sap_detail || err.message || "Push failed");
        } finally {
            setPushing(false);
        }
    };

    const updateCustomer = (name) => {
        const c = customerByName[name];
        if (!c) return;
        setResult((prev) => {
            const next = structuredClone(prev);
            next.sap_payload.customer_id = c.list_id;
            next.sap_payload.customer_name = c.name;
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
            const line = next.sap_payload.line_items[idx];
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
    // setCustomerOnPayload patches both the SAP payload and the local match
    // state without needing the entity to be in the dropdown by name first.
    // Used after inline-create and after Tier-2 candidate picks.
    const setCustomerOnPayload = (c, method = "manual") => {
        setResult((prev) => {
            const next = structuredClone(prev);
            next.sap_payload.customer_id = c.list_id;
            next.sap_payload.customer_name = c.name;
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
            const line = next.sap_payload?.line_items?.[idx];
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
            await sapApi.saveAlias({
                entity_type: "customer",
                alias_text: result?.extracted?.vendor_or_customer || result?.customer_match?.candidate_name || "",
                sap_entity_id: cand.list_id,
                sap_entity_name: cand.name,
                company_code: company,
            });
        } catch (_) { /* non-fatal — alias is an optimization */ }
    };

    const onPickItemCandidate = async (idx, cand) => {
        setItemOnLine(idx, cand, "user_picked");
        try {
            await sapApi.saveAlias({
                entity_type: "item",
                alias_text: result?.line_review?.[idx]?.description
                    || result?.line_review?.[idx]?.match?.candidate_name
                    || "",
                sap_entity_id: cand.list_id,
                sap_entity_name: cand.name,
                company_code: company,
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
        const okResults = results.filter((r) => r.ok && r.entity_id);
        okResults.forEach((r) => {
            const entity = { list_id: String(r.entity_id), name: r.entity_name };
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
    const deriveDraftTitle = () => {
        const fname = file?.name;
        const customer = result?.sap_payload?.customer_name || result?.customer_match?.name;
        if (customer) return customer;
        if (fname) return fname;
        return "Sales draft";
    };

    const handleSaveDraft = async () => {
        if (!result) return;
        setSavingDraft(true);
        setError("");
        try {
            const res = await sapApi.drafts.save({
                flow: "sales",
                payload: { result, filename: file?.name || null, pushResult },
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
        // PDFs aren't persisted; the preview pane will show "no preview".
        setFile(null);
        setPdfData(null);
        setResult(payload.result || null);
        setPushResult(payload.pushResult || null);
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

    const mastersReady = !loadingMasters && customers.length > 0;
    const extractDisabled = extracting || !file || loadingMasters || loadingCompanies || !company;

    return (
        <div className="h-screen flex flex-col text-gilt-ink">
            <PageHeader
                className="bg-transparent border-gilt-hairline"
                icon={FileSignature}
                title={<span className="gl-display text-gilt-ink">SAP S/4HANA · Sales Invoice</span>}
                description="Upload a sales invoice PDF, review extracted lines, and push to SAP S/4HANA."
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
                                {loadingCompanies ? "Companies…" : "Loading customers + items…"}
                            </span>
                        )}
                        {mastersReady && (
                            <span className="text-xs text-gilt-soft font-mono tabular-nums">
                                {customers.length} customers · {items.length} items
                            </span>
                        )}
                        <label className="inline-flex items-center gap-2 h-9 px-3.5 text-sm font-medium bg-gilt-card text-gilt-soft border border-gilt-hairline hover:border-gilt-brass hover:text-gilt-brass rounded-md cursor-pointer transition-colors duration-150">
                            <FileText className="h-4 w-4 text-gilt-faint" />
                            <span className="truncate max-w-[160px]">{file ? file.name.slice(0, 24) : "Choose PDF"}</span>
                            <input type="file" accept="application/pdf" onChange={onFileChange} disabled={loadingMasters} className="hidden" />
                        </label>
                        <Button
                            variant="primary"
                            size="md"
                            leftIcon={!extracting ? <Sparkles /> : undefined}
                            loading={extracting}
                            disabled={extractDisabled}
                            onClick={onExtract}
                            title={loadingMasters ? "Wait for company data to finish loading" : ""}
                        >
                            {extracting ? "Extracting…" : "Extract"}
                        </Button>
                    </div>
                }
            />

            {error && (
                <Card className="mx-6 lg:mx-8 mt-3 bg-gilt-card border-gilt-debit/40 shadow-none">
                    <div className="flex items-start gap-2 px-3 py-2 text-sm text-gilt-debit">
                        <AlertTriangle className="h-4 w-4 flex-shrink-0 mt-0.5 text-gilt-debit" />
                        <span className="flex-1">{error}</span>
                        <button onClick={() => setError("")} className="text-gilt-debit/70 hover:text-gilt-debit transition-colors duration-150">
                            <X className="h-4 w-4" />
                        </button>
                    </div>
                </Card>
            )}

            <div className="flex-1 min-h-0 p-6 pt-3">
                <PanelGroup direction="horizontal">
                    <Panel defaultSize={45} minSize={25}>
                        <div className="h-full pr-2">
                            {pdfData ? (
                                <div className="h-full bg-gilt-card rounded-xl border border-gilt-hairline overflow-hidden">
                                    <PdfViewer pdfData={pdfData} />
                                </div>
                            ) : (
                                <div className="flex flex-col items-center justify-center h-full bg-gilt-card rounded-xl border border-gilt-hairline text-gilt-soft text-sm gap-2 p-8">
                                    <FileText className="h-10 w-10 text-gilt-faint" />
                                    <div className="font-medium text-gilt-ink">Pick a PDF to preview</div>
                                    <div className="text-xs text-gilt-faint">Step 1 of 3 · Upload sales invoice</div>
                                </div>
                            )}
                        </div>
                    </Panel>

                    <PanelResizeHandle className="w-1 bg-gilt-hairline hover:bg-gilt-brass transition-colors duration-150 rounded-full mx-1" />

                    <Panel defaultSize={55} minSize={30}>
                        <div className="h-full pl-2 overflow-auto">
                            {extracting ? (
                                <div className="flex flex-col items-center justify-center h-full bg-gilt-card rounded-xl border border-gilt-hairline text-gilt-soft gap-3 p-8">
                                    <Loader2 className="h-8 w-8 animate-spin text-gilt-brass" />
                                    <div className="text-sm font-medium text-gilt-ink">Extracting invoice…</div>
                                    <div className="text-xs text-gilt-soft">Reading PDF and matching to SAP S/4HANA customers + items.</div>
                                </div>
                            ) : !result ? (
                                <div className="flex flex-col items-center justify-center h-full bg-gilt-card rounded-xl border border-gilt-hairline text-gilt-soft text-sm gap-3 px-6 text-center p-8">
                                    <Sparkles className="h-10 w-10 text-gilt-brass" />
                                    <div className="font-medium text-gilt-ink">Ready to extract</div>
                                    <ol className="text-xs text-gilt-soft space-y-1 list-decimal list-inside text-left">
                                        <li>Pick the SAP S/4HANA company code above</li>
                                        <li>Upload a sales invoice PDF</li>
                                        <li>Hit Extract — review matches — push to SAP S/4HANA</li>
                                    </ol>
                                </div>
                            ) : (
                                <EditPanel
                                    result={result}
                                    customerNames={customerNames}
                                    itemNames={itemNames}
                                    updateCustomer={updateCustomer}
                                    updateLine={updateLine}
                                    onPush={onPush}
                                    pushing={pushing}
                                    duplicateCheck={result?.duplicate_check}
                                    duplicateBlocked={duplicateBlocked}
                                    onOverrideDuplicate={() => setOverrideDup(true)}
                                    onPickCustomerCandidate={onPickCustomerCandidate}
                                    onPickItemCandidate={onPickItemCandidate}
                                    onCreateCustomer={(prefill) => setCreateModal({ entityType: "customer", prefillName: prefill })}
                                    onCreateItem={(prefill, idx) => setCreateModal({ entityType: "item", prefillName: prefill, targetLineIdx: idx })}
                                    onOpenBulk={() => setBulkOpen(true)}
                                    unmatchedCount={gatherUnmatchedForBulk().length}
                                />
                            )}
                            {pushResult && (
                                <div className="bg-gilt-card border border-gilt-credit/40 rounded-xl p-4 mt-3">
                                    <div className="flex items-center gap-2 mb-2">
                                        <CheckCircle2 className="h-4 w-4 text-gilt-credit" />
                                        <h2 className="font-mono text-[10px] font-semibold text-gilt-credit uppercase tracking-[0.14em]">Pushed successfully</h2>
                                    </div>
                                    <pre className="text-xs overflow-auto max-h-64 bg-gilt-sunken border border-gilt-hairline rounded-lg p-3 text-gilt-soft">{JSON.stringify(pushResult, null, 2)}</pre>
                                </div>
                            )}
                            {/* Save-draft action — only when we have something
                                worth saving. The drafts list below is always
                                visible so the user can browse + open. */}
                            {result && (
                                <div className="mt-3 flex items-center justify-end">
                                    <SaveDraftButton
                                        onClick={handleSaveDraft}
                                        busy={savingDraft}
                                        label={draftId ? "Update draft" : "Save draft"}
                                    />
                                </div>
                            )}
                            <DraftsPanel
                                open={draftsPanelOpen}
                                onToggle={() => setDraftsPanelOpen((o) => !o)}
                                loadList={() => sapApi.drafts.list({ flow: "sales", companyCode: company })}
                                loadDraft={(id) => sapApi.drafts.get(id)}
                                deleteDraft={(id) => sapApi.drafts.delete(id)}
                                refreshKey={draftsRefreshKey}
                                activeDraftId={draftId}
                                onOpen={handleOpenDraft}
                                onDelete={handleDraftDeleted}
                                className="mt-3"
                            />
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

function EditPanel({
    result, customerNames, itemNames,
    updateCustomer, updateLine, onPush, pushing,
    duplicateCheck, duplicateBlocked, onOverrideDuplicate,
    onPickCustomerCandidate, onPickItemCandidate,
    onCreateCustomer, onCreateItem,
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
                        <label className={labelBase}>
                            <User className="inline h-3.5 w-3.5 mr-1 text-gilt-faint" />
                            Customer
                        </label>
                        <div className="flex items-center gap-2">
                            <SearchableDropdown
                                options={customerNames}
                                value={result?.sap_payload?.customer_name || ""}
                                onChange={updateCustomer}
                                placeholder="Pick customer…"
                            />
                            {result?.customer_match?.confidence != null && (
                                <ConfidenceBadge confidence={result.customer_match.confidence} size="sm" />
                            )}
                        </div>
                    </div>
                    <Field icon={Calendar} label="Date" value={result?.extracted?.doc_date} />
                    <Field icon={Hash} label="Doc #" value={result?.extracted?.doc_number} />
                    <Field icon={DollarSign} label="Total" value={result?.extracted?.total} />
                </div>
                {!result?.customer_match?.matched && (
                    <div className="mt-4 space-y-2">
                        <div className="flex items-start gap-2 bg-gilt-sunken border border-gilt-brass/40 text-gilt-brass px-3 py-2 rounded-lg text-xs">
                            <AlertTriangle className="h-3.5 w-3.5 flex-shrink-0 mt-0.5 text-gilt-brass" />
                            <span>No matching SAP S/4HANA customer found. Pick one above, choose a suggestion, or create new.</span>
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
                                <th className="px-3 py-2.5 text-right font-mono text-[10px] font-medium text-gilt-soft uppercase tracking-[0.14em]">Qty</th>
                                <th className="px-3 py-2.5 text-right font-mono text-[10px] font-medium text-gilt-soft uppercase tracking-[0.14em]">Rate</th>
                                <th className="px-3 py-2.5 text-right font-mono text-[10px] font-medium text-gilt-soft uppercase tracking-[0.14em]">Amount</th>
                                <th className="px-3 py-2.5 text-left font-mono text-[10px] font-medium text-gilt-soft uppercase tracking-[0.14em]">Item</th>
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
                                            className={`${tableNumInput} w-20`}
                                            type="number"
                                            step="0.01"
                                            defaultValue={row.qty ?? 1}
                                            onBlur={(e) => updateLine(i, { qty: e.target.value })}
                                        />
                                    </td>
                                    <td className="px-3 py-2">
                                        <input
                                            className={`${tableNumInput} w-24`}
                                            type="number"
                                            step="0.01"
                                            defaultValue={row.rate ?? 0}
                                            onBlur={(e) => updateLine(i, { rate: e.target.value })}
                                        />
                                    </td>
                                    <td className="px-3 py-2">
                                        <input
                                            className={`${tableNumInput} w-24`}
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
            </div>

            {/* Sticky push bar */}
            <div className="bg-gilt-card rounded-xl border border-gilt-hairline p-4 flex items-center gap-3 flex-wrap">
                <button
                    onClick={onPush}
                    disabled={pushing || !!pushResult || !result?.sap_payload || duplicateBlocked}
                    className={btnSapBlue}
                    title={duplicateBlocked ? "This invoice looks like a duplicate. Use \"Push anyway\" to override." : undefined}
                >
                    {pushing ? <Loader2 className="h-4 w-4 animate-spin" /> : pushResult ? <CheckCircle2 className="h-4 w-4" /> : <Send className="h-4 w-4" />}
                    {pushing ? "Pushing to SAP S/4HANA…" : pushResult ? "Pushed" : "Push to SAP S/4HANA"}
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
