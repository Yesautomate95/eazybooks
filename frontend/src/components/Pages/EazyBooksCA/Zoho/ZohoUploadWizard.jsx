import React, { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
    X,
    ArrowLeft,
    ArrowRight,
    Upload,
    FileText,
    Loader2,
    Building2,
    FileSignature,
    Receipt,
    CreditCard,
    Landmark,
    Wallet,
} from "lucide-react";
import { zohoApi } from "../../../../services/api";

/**
 * Zoho Upload Wizard — a faithful clone of Tally's upload wizard (Tally.jsx),
 * adapted for Zoho Books. One entry point for every flow instead of separate
 * dashboard tiles:
 *
 *   Step 1  Company   — which Zoho Books company to import into
 *   Step 2  Type      — Sales / Purchase / Bank Entries / Bank Reconciliation
 *   Step 3  File      — upload the document (single file, like Tally)
 *   Step 4  Bank a/c  — bank-entries only: pick the bank account
 *
 * Sales / Purchase / Bank Reconciliation open their existing pages directly
 * (Tally's "direct open"). Bank Entries carries the file + bank account into
 * ZohoBankEntriesView via router state so it auto-extracts on arrival.
 */

// Mirror of Tally's UPLOAD_FLOWS. `direct` flows navigate straight to a page;
// bank_entries walks the file + bank-account steps first. Gilt book plates carry
// numbered brass captions instead of per-flow rainbow colors.
const UPLOAD_FLOWS = [
    {
        id: "sales",
        title: "Sales Invoice",
        description: "Bulk multi-file upload with AI extraction — opens editor directly",
        icon: FileSignature,
        route: "/dashboard/zoho/sales",
        direct: true,
    },
    {
        id: "purchase",
        title: "Purchase Bill",
        description: "Bulk multi-file upload with AI extraction — opens editor directly",
        icon: Receipt,
        route: "/dashboard/zoho/purchase",
        direct: true,
    },
    {
        id: "bank_entries",
        title: "Bank Statement",
        description: "Upload a statement, review extracted vouchers before pushing",
        icon: CreditCard,
        route: "/dashboard/zoho/bank-entries",
        direct: false,
        acceptedFormats: ".pdf,.xls,.xlsx",
        formatHint: "PDF or Excel",
    },
    {
        id: "bank_recon",
        title: "Bank Reconciliation",
        description: "Reconcile a Zoho Books bank account against a statement",
        icon: Landmark,
        route: "/dashboard/zoho/bank-recon",
        direct: true,
    },
];

export default function ZohoUploadWizard({ open, onClose, companies = [], initialCompany = "" }) {
    const navigate = useNavigate();
    const [step, setStep] = useState(1);
    const [company, setCompany] = useState(initialCompany);
    const [flow, setFlow] = useState("");
    const [file, setFile] = useState(null);
    const [accounts, setAccounts] = useState([]);
    const [accountId, setAccountId] = useState("");
    const [loadingAccounts, setLoadingAccounts] = useState(false);
    const [error, setError] = useState("");

    const flowConfig = useMemo(() => UPLOAD_FLOWS.find((f) => f.id === flow), [flow]);

    // Reset to a clean state whenever the wizard is (re)opened.
    useEffect(() => {
        if (open) {
            setStep(1);
            setCompany(initialCompany || companies[0]?.organization_id || companies[0]?.name || "");
            setFlow("");
            setFile(null);
            setAccounts([]);
            setAccountId("");
            setLoadingAccounts(false);
            setError("");
        }
    }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

    const fetchBankAccounts = async (companyKey) => {
        setLoadingAccounts(true);
        try {
            const res = await zohoApi.bank.listAccounts(companyKey);
            const banks = res?.accounts || [];
            setAccounts(banks);
            if (banks[0]) setAccountId(banks[0].id);
        } catch (err) {
            setError(err?.response?.data?.detail || err.message || "Failed to load bank accounts");
        } finally {
            setLoadingAccounts(false);
        }
    };

    const handleSelectCompany = (key) => {
        setCompany(key);
        setStep(2);
    };

    const handleSelectFlow = (f) => {
        setFlow(f.id);
        if (f.direct) {
            // Tally parity: Sales / Purchase / Recon open their page directly.
            onClose?.();
            navigate(f.route, { state: { organizationId: company } });
            return;
        }
        setStep(3);
    };

    const handleContinueFromFile = () => {
        if (!file) { setError("Please select a file"); return; }
        setError("");
        fetchBankAccounts(company);
        setStep(4);
    };

    const handleFinishBankEntries = () => {
        if (!accountId) { setError("Please select a bank account"); return; }
        const acct = accounts.find((a) => a.id === accountId);
        onClose?.();
        // Carry the file + target into the editor; it auto-extracts on arrival.
        navigate("/dashboard/zoho/bank-entries", {
            state: { autoExtract: { file, accountId, accountName: acct?.name, organizationId: company } },
        });
    };

    if (!open) return null;

    const stepLabels = flow === "bank_entries"
        ? ["Company", "Type", "File", "Bank"]
        : ["Company", "Type"];

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-gilt-ink/40 px-4">
            <div className="bg-gilt-card rounded-xl border border-gilt-hairline w-full max-w-2xl max-h-[90vh] overflow-auto">
                {/* Header + step rail */}
                <div className="flex items-center justify-between px-5 py-4 border-b border-gilt-hairline">
                    <h2 className="text-base font-semibold text-gilt-ink">Upload to Zoho Books</h2>
                    <button onClick={onClose} className="text-gilt-faint hover:text-gilt-ink transition-colors duration-150"><X className="h-5 w-5" /></button>
                </div>
                <div className="flex items-center gap-2 px-5 pt-4">
                    {stepLabels.map((label, i) => {
                        const n = i + 1;
                        const active = step === n;
                        const done = step > n;
                        return (
                            <React.Fragment key={label}>
                                <div className={`inline-flex items-center gap-1.5 text-xs font-medium ${active ? "text-gilt-brass" : done ? "text-gilt-soft" : "text-gilt-faint"}`}>
                                    <span className={`inline-flex items-center justify-center h-5 w-5 rounded-full font-mono text-[11px] transition-colors duration-150 ${active ? "border border-gilt-brass text-gilt-brass" : done ? "bg-gilt-ink text-gilt-paper" : "bg-gilt-sunken text-gilt-faint"}`}>{n}</span>
                                    {label}
                                </div>
                                {i < stepLabels.length - 1 && <div className={`flex-1 h-px ${done ? "bg-gilt-brass" : "bg-gilt-hairline"}`} />}
                            </React.Fragment>
                        );
                    })}
                </div>

                <div className="px-5 py-5 space-y-4">
                    {error && (
                        <div className="p-3 bg-gilt-sunken border border-gilt-debit/40 text-gilt-debit rounded-lg text-sm">{error}</div>
                    )}

                    {/* Step 1: Company */}
                    {step === 1 && (
                        <div className="space-y-3">
                            <p className="text-sm font-medium text-gilt-soft">Which company are you importing into?</p>
                            {!companies.length && <p className="text-sm text-gilt-soft">No companies found. Connect Zoho Books first.</p>}
                            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                                {companies.map((c) => {
                                    const key = c.organization_id || c.name;
                                    return (
                                        <button key={key} onClick={() => handleSelectCompany(key)}
                                            className={`flex items-center gap-2.5 px-3.5 py-3 rounded-lg border text-left transition-colors duration-150 ${company === key ? "border-gilt-brass bg-gilt-sunken" : "border-gilt-hairline bg-gilt-card hover:border-gilt-brass"}`}>
                                            <Building2 className="h-4 w-4 text-gilt-faint flex-shrink-0" />
                                            <span className="text-sm font-medium text-gilt-ink truncate">{c.name}{c.zoho_env ? ` (${c.zoho_env})` : ""}</span>
                                        </button>
                                    );
                                })}
                            </div>
                        </div>
                    )}

                    {/* Step 2: Type */}
                    {step === 2 && (
                        <div className="space-y-3">
                            <p className="text-sm font-medium text-gilt-soft">What are you uploading?</p>
                            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                                {UPLOAD_FLOWS.map((f, i) => (
                                    <button key={f.id} onClick={() => handleSelectFlow(f)}
                                        className={`flex flex-col items-start gap-1.5 px-4 py-3.5 rounded-lg border text-left transition-colors duration-150 ${flow === f.id ? "border-gilt-brass bg-gilt-sunken" : "border-gilt-hairline bg-gilt-card hover:border-gilt-brass"}`}>
                                        <div className="p-2 rounded-md border border-gilt-hairline bg-gilt-sunken text-gilt-brass" aria-hidden="true">
                                            <f.icon className="h-5 w-5" />
                                        </div>
                                        <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-gilt-brass" aria-hidden="true">№ {String(i + 1).padStart(2, "0")}</span>
                                        <span className="text-sm font-semibold text-gilt-ink">{f.title}</span>
                                        <span className="text-xs text-gilt-faint leading-snug">{f.description}</span>
                                    </button>
                                ))}
                            </div>
                            <div className="flex justify-start pt-1">
                                <button onClick={() => setStep(1)} className="inline-flex items-center gap-1.5 text-sm text-gilt-soft hover:text-gilt-ink transition-colors duration-150"><ArrowLeft className="h-4 w-4" />Back</button>
                            </div>
                        </div>
                    )}

                    {/* Step 3: File (bank_entries) */}
                    {step === 3 && (
                        <div className="space-y-4">
                            <p className="text-sm font-medium text-gilt-soft">{flowConfig?.title} · upload your statement</p>
                            <label
                                onDrop={(e) => { e.preventDefault(); const d = e.dataTransfer.files?.[0]; if (d) setFile(d); }}
                                onDragOver={(e) => e.preventDefault()}
                                htmlFor="zoho-wizard-file"
                                className={`block border border-dashed rounded-lg p-8 text-center cursor-pointer transition-colors duration-150 ${file ? "border-gilt-credit/50 bg-gilt-sunken" : "border-gilt-hairline hover:border-gilt-brass"}`}>
                                <input id="zoho-wizard-file" type="file" accept={flowConfig?.acceptedFormats} className="hidden"
                                    onChange={(e) => setFile(e.target.files?.[0] || null)} />
                                {file ? (
                                    <div>
                                        <div className="w-10 h-10 rounded-full bg-gilt-card border border-gilt-hairline flex items-center justify-center mx-auto mb-3"><FileText className="w-5 h-5 text-gilt-credit" /></div>
                                        <p className="font-medium text-gilt-credit text-sm">{file.name}</p>
                                        <p className="font-mono tabular-nums text-xs text-gilt-soft mt-1">{(file.size / 1024).toFixed(1)} KB · Click to change</p>
                                    </div>
                                ) : (
                                    <div>
                                        <Upload className="w-10 h-10 text-gilt-faint mx-auto mb-3" />
                                        <p className="text-sm font-medium text-gilt-soft">Drag &amp; drop or click to select</p>
                                        <p className="text-xs text-gilt-faint mt-1">{flowConfig?.formatHint}</p>
                                    </div>
                                )}
                            </label>
                            <div className="flex items-center justify-between">
                                <button onClick={() => { setStep(2); setFile(null); }} className="inline-flex items-center gap-1.5 text-sm text-gilt-soft hover:text-gilt-ink transition-colors duration-150"><ArrowLeft className="h-4 w-4" />Back</button>
                                <button onClick={handleContinueFromFile} disabled={!file}
                                    className="inline-flex items-center gap-1.5 px-5 py-2 bg-gilt-brass text-gilt-paper rounded-md text-sm font-medium hover:bg-gilt-brass/90 disabled:opacity-50 transition-colors duration-150">
                                    Continue<ArrowRight className="h-4 w-4" />
                                </button>
                            </div>
                        </div>
                    )}

                    {/* Step 4: Bank account (bank_entries) */}
                    {step === 4 && (
                        <div className="space-y-4">
                            <p className="text-sm font-medium text-gilt-soft">Which bank account is this statement for?</p>
                            <p className="text-xs text-gilt-faint">File: {file?.name}</p>
                            <div>
                                <label className="block text-xs font-medium text-gilt-soft mb-1.5"><Wallet className="inline h-3.5 w-3.5 mr-1 text-gilt-faint" />Bank account *</label>
                                <select value={accountId} onChange={(e) => setAccountId(e.target.value)} disabled={loadingAccounts || !accounts.length}
                                    className="w-full border border-gilt-hairline rounded-lg px-3 py-2 text-sm text-gilt-ink bg-gilt-paper outline-none focus:border-gilt-brass transition-colors duration-150">
                                    {loadingAccounts && <option value="">Loading bank accounts…</option>}
                                    {!loadingAccounts && !accounts.length && <option value="">No bank accounts found</option>}
                                    {accounts.map((a) => <option key={a.id} value={a.id}>{a.name} ({a.account_type}{a.currency ? `, ${a.currency}` : ""})</option>)}
                                </select>
                            </div>
                            <div className="flex items-center justify-between">
                                <button onClick={() => setStep(3)} className="inline-flex items-center gap-1.5 text-sm text-gilt-soft hover:text-gilt-ink transition-colors duration-150"><ArrowLeft className="h-4 w-4" />Back</button>
                                <button onClick={handleFinishBankEntries} disabled={!accountId || loadingAccounts}
                                    className="inline-flex items-center gap-1.5 px-5 py-2 bg-gilt-brass text-gilt-paper rounded-md text-sm font-medium hover:bg-gilt-brass/90 disabled:opacity-50 transition-colors duration-150">
                                    Extract<ArrowRight className="h-4 w-4" />
                                </button>
                            </div>
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}
