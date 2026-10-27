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
import { xeroApi } from "../../../../services/api";

/**
 * Xero Upload Wizard — a faithful clone of Tally's upload wizard (Tally.jsx),
 * adapted for Xero. One entry point for every flow instead of separate
 * dashboard tiles:
 *
 *   Step 1  Company   — which Xero company to import into
 *   Step 2  Type      — Sales / Purchase / Bank Entries / Bank Reconciliation
 *   Step 3  File      — upload the document (single file, like Tally)
 *   Step 4  Bank a/c  — bank-entries only: pick the bank account
 *
 * Sales / Purchase / Bank Reconciliation open their existing pages directly
 * (Tally's "direct open"). Bank Entries carries the file + bank account into
 * XeroBankEntriesView via router state so it auto-extracts on arrival.
 */

// Mirror of Tally's UPLOAD_FLOWS. `direct` flows navigate straight to a page;
// bank_entries walks the file + bank-account steps first.
const UPLOAD_FLOWS = [
    {
        id: "sales",
        title: "Sales Invoice",
        description: "Bulk multi-file upload with AI extraction — opens editor directly",
        icon: FileSignature,
        route: "/dashboard/xero/sales",
        direct: true,
        color: { sel: "border-purple-500 bg-purple-50", icon: "text-purple-600" },
    },
    {
        id: "purchase",
        title: "Purchase Bill",
        description: "Bulk multi-file upload with AI extraction — opens editor directly",
        icon: Receipt,
        route: "/dashboard/xero/purchase",
        direct: true,
        color: { sel: "border-green-500 bg-green-50", icon: "text-green-600" },
    },
    {
        id: "bank_entries",
        title: "Bank Statement",
        description: "Upload a statement, review extracted vouchers before pushing",
        icon: CreditCard,
        route: "/dashboard/xero/bank-entries",
        direct: false,
        acceptedFormats: ".pdf,.xls,.xlsx",
        formatHint: "PDF or Excel",
        color: { sel: "border-blue-500 bg-blue-50", icon: "text-blue-600" },
    },
    {
        id: "bank_recon",
        title: "Bank Reconciliation",
        description: "Reconcile a Xero bank account against a statement",
        icon: Landmark,
        route: "/dashboard/xero/bank-recon",
        direct: true,
        color: { sel: "border-orange-500 bg-orange-50", icon: "text-orange-600" },
    },
];

export default function XeroUploadWizard({ open, onClose, companies = [], initialCompany = "" }) {
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
            setCompany(initialCompany || companies[0]?.tenant_id || companies[0]?.name || "");
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
            const res = await xeroApi.bank.listAccounts(companyKey);
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
            navigate(f.route, { state: { tenantId: company } });
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
        navigate("/dashboard/xero/bank-entries", {
            state: { autoExtract: { file, accountId, accountName: acct?.name, tenantId: company } },
        });
    };

    if (!open) return null;

    const stepLabels = flow === "bank_entries"
        ? ["Company", "Type", "File", "Bank"]
        : ["Company", "Type"];

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4">
            <div className="bg-white rounded-2xl shadow-xl border border-gray-200 w-full max-w-2xl max-h-[90vh] overflow-auto">
                {/* Header + step rail */}
                <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100">
                    <h2 className="text-base font-semibold text-gray-900">Upload to Xero</h2>
                    <button onClick={onClose} className="text-gray-400 hover:text-gray-600"><X className="h-5 w-5" /></button>
                </div>
                <div className="flex items-center gap-2 px-5 pt-4">
                    {stepLabels.map((label, i) => {
                        const n = i + 1;
                        const active = step === n;
                        const done = step > n;
                        return (
                            <React.Fragment key={label}>
                                <div className={`inline-flex items-center gap-1.5 text-xs font-medium ${active ? "text-indigo-600" : done ? "text-gray-500" : "text-gray-400"}`}>
                                    <span className={`inline-flex items-center justify-center h-5 w-5 rounded-full text-[11px] ${active ? "bg-indigo-600 text-white" : done ? "bg-gray-200 text-gray-600" : "bg-gray-100 text-gray-400"}`}>{n}</span>
                                    {label}
                                </div>
                                {i < stepLabels.length - 1 && <div className="flex-1 h-px bg-gray-200" />}
                            </React.Fragment>
                        );
                    })}
                </div>

                <div className="px-5 py-5 space-y-4">
                    {error && (
                        <div className="p-3 bg-red-50 text-red-700 rounded-lg text-sm">{error}</div>
                    )}

                    {/* Step 1: Company */}
                    {step === 1 && (
                        <div className="space-y-3">
                            <p className="text-sm font-medium text-gray-700">Which company are you importing into?</p>
                            {!companies.length && <p className="text-sm text-gray-500">No companies found. Connect Xero first.</p>}
                            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                                {companies.map((c) => {
                                    const key = c.tenant_id || c.name;
                                    return (
                                        <button key={key} onClick={() => handleSelectCompany(key)}
                                            className={`flex items-center gap-2.5 px-3.5 py-3 rounded-lg border text-left transition-colors ${company === key ? "border-indigo-500 bg-indigo-50" : "border-gray-200 hover:border-indigo-300 hover:bg-indigo-50/30"}`}>
                                            <Building2 className="h-4 w-4 text-indigo-500 flex-shrink-0" />
                                            <span className="text-sm font-medium text-gray-800 truncate">{c.name}</span>
                                        </button>
                                    );
                                })}
                            </div>
                        </div>
                    )}

                    {/* Step 2: Type */}
                    {step === 2 && (
                        <div className="space-y-3">
                            <p className="text-sm font-medium text-gray-700">What are you uploading?</p>
                            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                                {UPLOAD_FLOWS.map((f) => (
                                    <button key={f.id} onClick={() => handleSelectFlow(f)}
                                        className={`flex flex-col items-start gap-1.5 px-4 py-3.5 rounded-xl border text-left transition-colors ${flow === f.id ? f.color.sel : "border-gray-200 hover:border-gray-300 hover:bg-gray-50"}`}>
                                        <f.icon className={`h-5 w-5 ${f.color.icon}`} />
                                        <span className="text-sm font-semibold text-gray-900">{f.title}</span>
                                        <span className="text-xs text-gray-500 leading-snug">{f.description}</span>
                                    </button>
                                ))}
                            </div>
                            <div className="flex justify-start pt-1">
                                <button onClick={() => setStep(1)} className="inline-flex items-center gap-1.5 text-sm text-gray-500 hover:text-gray-800"><ArrowLeft className="h-4 w-4" />Back</button>
                            </div>
                        </div>
                    )}

                    {/* Step 3: File (bank_entries) */}
                    {step === 3 && (
                        <div className="space-y-4">
                            <p className="text-sm font-medium text-gray-700">{flowConfig?.title} · upload your statement</p>
                            <label
                                onDrop={(e) => { e.preventDefault(); const d = e.dataTransfer.files?.[0]; if (d) setFile(d); }}
                                onDragOver={(e) => e.preventDefault()}
                                htmlFor="xero-wizard-file"
                                className={`block border-2 border-dashed rounded-xl p-8 text-center cursor-pointer transition-all ${file ? "border-green-400 bg-green-50" : "border-gray-300 hover:border-indigo-400 hover:bg-indigo-50/30"}`}>
                                <input id="xero-wizard-file" type="file" accept={flowConfig?.acceptedFormats} className="hidden"
                                    onChange={(e) => setFile(e.target.files?.[0] || null)} />
                                {file ? (
                                    <div>
                                        <div className="w-10 h-10 rounded-full bg-green-100 flex items-center justify-center mx-auto mb-3"><FileText className="w-5 h-5 text-green-600" /></div>
                                        <p className="font-medium text-green-700 text-sm">{file.name}</p>
                                        <p className="text-xs text-green-600 mt-1">{(file.size / 1024).toFixed(1)} KB · Click to change</p>
                                    </div>
                                ) : (
                                    <div>
                                        <Upload className="w-10 h-10 text-gray-300 mx-auto mb-3" />
                                        <p className="text-sm font-medium text-gray-600">Drag &amp; drop or click to select</p>
                                        <p className="text-xs text-gray-400 mt-1">{flowConfig?.formatHint}</p>
                                    </div>
                                )}
                            </label>
                            <div className="flex items-center justify-between">
                                <button onClick={() => { setStep(2); setFile(null); }} className="inline-flex items-center gap-1.5 text-sm text-gray-500 hover:text-gray-800"><ArrowLeft className="h-4 w-4" />Back</button>
                                <button onClick={handleContinueFromFile} disabled={!file}
                                    className="inline-flex items-center gap-1.5 px-5 py-2 bg-indigo-600 text-white rounded-lg text-sm font-medium hover:bg-indigo-700 disabled:opacity-50">
                                    Continue<ArrowRight className="h-4 w-4" />
                                </button>
                            </div>
                        </div>
                    )}

                    {/* Step 4: Bank account (bank_entries) */}
                    {step === 4 && (
                        <div className="space-y-4">
                            <p className="text-sm font-medium text-gray-700">Which bank account is this statement for?</p>
                            <p className="text-xs text-gray-500">File: {file?.name}</p>
                            <div>
                                <label className="block text-xs font-medium text-gray-600 mb-1.5"><Wallet className="inline h-3.5 w-3.5 mr-1 text-gray-400" />Bank account *</label>
                                <select value={accountId} onChange={(e) => setAccountId(e.target.value)} disabled={loadingAccounts || !accounts.length}
                                    className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm bg-white outline-none focus:ring-2 focus:ring-indigo-500">
                                    {loadingAccounts && <option value="">Loading bank accounts…</option>}
                                    {!loadingAccounts && !accounts.length && <option value="">No bank accounts found</option>}
                                    {accounts.map((a) => <option key={a.id} value={a.id}>{a.name} ({a.account_type}{a.currency ? `, ${a.currency}` : ""})</option>)}
                                </select>
                            </div>
                            <div className="flex items-center justify-between">
                                <button onClick={() => setStep(3)} className="inline-flex items-center gap-1.5 text-sm text-gray-500 hover:text-gray-800"><ArrowLeft className="h-4 w-4" />Back</button>
                                <button onClick={handleFinishBankEntries} disabled={!accountId || loadingAccounts}
                                    className="inline-flex items-center gap-1.5 px-5 py-2 bg-[#2CA01C] text-white rounded-lg text-sm font-medium hover:bg-[#268a18] disabled:opacity-50">
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
