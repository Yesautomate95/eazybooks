import React, { useEffect, useMemo, useState } from "react";
import {
    Scale,
    Upload,
    Calendar,
    Building2,
    Wallet,
    Loader2,
    Link2,
    Clock,
    Send,
    Save,
    CheckCircle2,
    AlertTriangle,
    X,
    Sparkles,
    History as HistoryIcon,
    FileText,
    ArrowRight,
} from "lucide-react";
import { qbApi } from "../../../../services/api";
import { PageHeader } from "../../../ui/page-header";
import { Card } from "../../../ui/card";
import { Badge } from "../../../ui/badge";

/**
 * QB Bank Reconciliation flow.
 *
 *   1. Pick realm + bank/credit-card account
 *   2. Upload PDF/Excel statement (optional period override)
 *   3. POST /ca/qb/bank/extract-and-match  -> matches + unmatched on both sides
 *   4. Manually pair leftover rows (one QBO + one statement at a time)
 *   5. Save the report (draft / completed) — persisted in qb_recon_reports.
 */
export default function QBBankReconView() {
    const [companies, setCompanies] = useState([]);
    const [company, setCompany] = useState("");
    const [accounts, setAccounts] = useState([]);
    const [accountId, setAccountId] = useState("");
    const [file, setFile] = useState(null);
    const [periodStart, setPeriodStart] = useState("");
    const [periodEnd, setPeriodEnd] = useState("");
    const [qboClosingBalance, setQboClosingBalance] = useState("");

    const [loadingAccounts, setLoadingAccounts] = useState(false);
    const [matching, setMatching] = useState(false);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState("");
    const [result, setResult] = useState(null);
    const [savedReportId, setSavedReportId] = useState(null);
    const [reports, setReports] = useState([]);

    // Manual-pairing selection (indexes into unmatched_qbo / unmatched_statement)
    const [pickedQbo, setPickedQbo] = useState(null);
    const [pickedStmt, setPickedStmt] = useState(null);

    // Rows the user explicitly flagged "pending review" — persisted alongside
    // the saved report. Shape: [{side: 'qbo'|'statement', row}].
    const [pendingReview, setPendingReview] = useState([]);

    // Suspense-account dialog state for the bulk "Post to suspense" action.
    const [suspenseDialog, setSuspenseDialog] = useState(null);
    const [resolving, setResolving] = useState(false);
    // Master list of *all* QBO accounts (used to populate the suspense picker).
    const [allAccounts, setAllAccounts] = useState([]);

    // ── Initial company list ──
    useEffect(() => {
        (async () => {
            try {
                const res = await qbApi.getCompanies();
                const list = res?.companies || [];
                setCompanies(list);
                if (list[0]) setCompany(list[0].realm_id || list[0].name);
            } catch (err) {
                setError(err?.response?.data?.detail || err.message || "Failed to load companies");
            }
        })();
    }, []);

    // ── Bank accounts for the chosen realm ──
    useEffect(() => {
        if (!company) return;
        setLoadingAccounts(true);
        setAccounts([]);
        setAccountId("");
        setAllAccounts([]);
        (async () => {
            try {
                const [bankRes, allRes] = await Promise.all([
                    qbApi.bank.listAccounts(company),
                    qbApi.getAccounts(company),
                ]);
                const list = bankRes?.accounts || [];
                setAccounts(list);
                if (list[0]) setAccountId(list[0].id);
                setAllAccounts(allRes?.accounts || []);
            } catch (err) {
                setError(err?.response?.data?.detail || err.message || "Failed to load bank accounts");
            } finally {
                setLoadingAccounts(false);
            }
        })();
    }, [company]);

    const onMatch = async () => {
        if (!file) {
            setError("Pick a statement file first.");
            return;
        }
        if (!accountId) {
            setError("Pick a bank account.");
            return;
        }
        setMatching(true);
        setError("");
        setResult(null);
        setPickedQbo(null);
        setPickedStmt(null);
        setPendingReview([]);
        try {
            const res = await qbApi.bank.extractAndMatch({
                file,
                accountId,
                qbCompany: company,
                periodStart: periodStart || undefined,
                periodEnd: periodEnd || undefined,
                qboClosingBalance: qboClosingBalance || undefined,
            });
            setResult(res);
        } catch (err) {
            setError(err?.response?.data?.detail || err.message || "Matching failed");
        } finally {
            setMatching(false);
        }
    };

    // ── Past reports list ──
    const refreshReports = async (realm) => {
        try {
            const r = await qbApi.bank.listReports(realm || company);
            setReports(r?.reports || []);
        } catch (err) {
            // Non-fatal — surface but don't block the flow
            console.warn("listReports failed:", err);
        }
    };

    useEffect(() => {
        if (company) refreshReports(company);
    }, [company]);

    const onSaveReport = async (status = "draft") => {
        if (!result) return;
        const acct = accounts.find((a) => a.id === accountId);
        setSaving(true);
        setError("");
        try {
            const r = await qbApi.bank.saveReport({
                qb_company: company,
                account_id: accountId,
                account_name: acct?.name,
                period_start: result.period.start,
                period_end: result.period.end,
                statement: result.statement,
                qbo_txns: result.qbo_txns,
                matches: result.matches,
                summary: result.summary,
                qbo_closing_balance: qboClosingBalance ? Number(qboClosingBalance) : null,
                needs_review: !!result.needs_review,
                status,
                pending_review: pendingReview,
            });
            setSavedReportId(r.report_id);
            await refreshReports();
        } catch (err) {
            setError(err?.response?.data?.detail || err.message || "Save failed");
        } finally {
            setSaving(false);
        }
    };

    const onLoadReport = async (id) => {
        setError("");
        try {
            const rep = await qbApi.bank.getReport(id);
            // Reconstruct view-shape from saved snapshot
            const stmt = rep.statement_json;
            const qbo = rep.qbo_txns_json || [];
            const matches = (rep.matches || []).map((m) => ({
                qbo_txn: m.qbo_txn_json,
                statement_row: m.statement_row_json,
                confidence: Number(m.confidence) || 0,
                reason: m.reason,
                manual: m.manual,
            }));
            const matchedTxnIds = new Set(matches.map((m) => m.qbo_txn?.id).filter(Boolean));
            const matchedStmtKeys = new Set(matches.map((m) => `${m.statement_row?.date}|${m.statement_row?.description}`));
            const unmatchedQbo = qbo
                .filter((t) => !matchedTxnIds.has(t.id))
                .map((t) => ({
                    date: t.date, description: [t.payee, t.memo].filter(Boolean).join(" · "),
                    reference: t.doc_number,
                    debit_amount: t.amount > 0 ? t.amount : null,
                    credit_amount: t.amount < 0 ? -t.amount : null,
                    qbo_txn: t,
                }));
            const unmatchedStmt = (stmt.transactions || [])
                .filter((s) => !matchedStmtKeys.has(`${s.date}|${s.description}`));
            setResult({
                ok: true,
                period: { start: rep.period_start, end: rep.period_end },
                statement: stmt,
                qbo_txns: qbo,
                matches,
                unmatched_qbo: unmatchedQbo,
                unmatched_statement: unmatchedStmt,
                summary: rep.summary_json || {},
                needs_review: rep.needs_review,
            });
            setPendingReview(rep.summary_json?.pending_review || []);
            setAccountId(rep.account_id);
            setSavedReportId(rep.id);
        } catch (err) {
            setError(err?.response?.data?.detail || err.message || "Load failed");
        }
    };

    const onDeleteReport = async (id) => {
        if (!window.confirm("Delete this saved report?")) return;
        try {
            await qbApi.bank.deleteReport(id);
            await refreshReports();
            if (savedReportId === id) setSavedReportId(null);
        } catch (err) {
            setError(err?.response?.data?.detail || err.message || "Delete failed");
        }
    };

    const addManualMatch = async () => {
        if (pickedQbo === null || pickedStmt === null) return;
        const qboTxn = result.unmatched_qbo[pickedQbo].qbo_txn;
        const statementRow = result.unmatched_statement[pickedStmt];
        // Compute the post-removal lists *before* the call so the backend can
        // recompute the summary against the same state we're about to set.
        const nextUnmatchedQbo = result.unmatched_qbo.filter((_, i) => i !== pickedQbo);
        const nextUnmatchedStmt = result.unmatched_statement.filter((_, i) => i !== pickedStmt);
        try {
            const m = await qbApi.bank.manualMatch({
                qboTxn,
                statementRow,
                unmatchedQbo: nextUnmatchedQbo,
                unmatchedStatement: nextUnmatchedStmt,
                qboClosingBalance: qboClosingBalance ? Number(qboClosingBalance) : null,
                bankClosingBalance: result?.statement?.closing_balance ?? null,
            });
            setResult((prev) => ({
                ...prev,
                matches: [...prev.matches, { ...(m.match || m), qbo_idx: -1, bank_idx: -1 }],
                unmatched_qbo: nextUnmatchedQbo,
                unmatched_statement: nextUnmatchedStmt,
                summary: m.summary || prev.summary,
            }));
            setPickedQbo(null);
            setPickedStmt(null);
        } catch (err) {
            setError(err?.response?.data?.detail || err.message || "Manual match failed");
        }
    };

    // ── Pending-review handling ─────────────────────────────────────────
    // Mark a single row "pending review": remove from the unmatched list,
    // add to pendingReview, recompute summary on the backend.
    const markPending = async (side, idx) => {
        if (!result) return;
        const arrKey = side === "qbo" ? "unmatched_qbo" : "unmatched_statement";
        const row = result[arrKey][idx];
        if (!row) return;
        const nextArr = result[arrKey].filter((_, i) => i !== idx);
        const nextUnmatchedQbo = side === "qbo" ? nextArr : result.unmatched_qbo;
        const nextUnmatchedStmt = side === "statement" ? nextArr : result.unmatched_statement;
        try {
            const res = await qbApi.bank.resolvePending({
                mode: "mark_pending",
                unmatchedQbo: nextUnmatchedQbo,
                unmatchedStatement: nextUnmatchedStmt,
                qboClosingBalance: qboClosingBalance ? Number(qboClosingBalance) : null,
                bankClosingBalance: result?.statement?.closing_balance ?? null,
            });
            setResult((prev) => ({
                ...prev,
                unmatched_qbo: nextUnmatchedQbo,
                unmatched_statement: nextUnmatchedStmt,
                summary: res.summary || prev.summary,
            }));
            setPendingReview((prev) => [...prev, { side, row }]);
        } catch (err) {
            setError(err?.response?.data?.detail || err.message || "Mark pending failed");
        }
    };

    // Bulk-mark all currently-unmatched statement rows (the most common case
    // since QBO leftovers are typically reclassified rather than ignored).
    const markAllStatementPending = async () => {
        if (!result?.unmatched_statement?.length) return;
        const toMark = result.unmatched_statement.map((row) => ({ side: "statement", row }));
        try {
            const res = await qbApi.bank.resolvePending({
                mode: "mark_pending",
                unmatchedQbo: result.unmatched_qbo,
                unmatchedStatement: [],
                qboClosingBalance: qboClosingBalance ? Number(qboClosingBalance) : null,
                bankClosingBalance: result?.statement?.closing_balance ?? null,
            });
            setResult((prev) => ({ ...prev, unmatched_statement: [], summary: res.summary || prev.summary }));
            setPendingReview((prev) => [...prev, ...toMark]);
        } catch (err) {
            setError(err?.response?.data?.detail || err.message || "Mark pending failed");
        }
    };

    // Open the suspense-account picker. The dialog confirms which account
    // (and which rows) before any QBO writes happen.
    const openPostToSuspense = () => {
        if (!result?.unmatched_statement?.length) return;
        setSuspenseDialog({
            // Default: post every remaining unmatched statement row.
            indices: result.unmatched_statement.map((_, i) => i),
            suspenseAccountId: "",
            suspenseAccountName: "",
        });
    };

    const confirmPostToSuspense = async () => {
        if (!suspenseDialog?.suspenseAccountId) {
            setError("Pick a suspense account first.");
            return;
        }
        const acct = accounts.find((a) => a.id === accountId);
        setResolving(true);
        setError("");
        try {
            const res = await qbApi.bank.resolvePending({
                mode: "post_to_suspense",
                unmatchedQbo: result.unmatched_qbo,
                unmatchedStatement: result.unmatched_statement,
                qboClosingBalance: qboClosingBalance ? Number(qboClosingBalance) : null,
                bankClosingBalance: result?.statement?.closing_balance ?? null,
                qbCompany: company,
                bankAccountId: accountId,
                bankAccountName: acct?.name,
                suspenseAccountId: suspenseDialog.suspenseAccountId,
                suspenseAccountName: suspenseDialog.suspenseAccountName,
                statementRowIndices: suspenseDialog.indices,
            });
            const successIndices = new Set(
                (res.posted || []).filter((p) => p.ok).map((p) => p.statement_index)
            );
            setResult((prev) => ({
                ...prev,
                unmatched_statement: prev.unmatched_statement.filter((_, i) => !successIndices.has(i)),
                summary: res.summary || prev.summary,
            }));
            const failures = (res.posted || []).filter((p) => !p.ok);
            if (failures.length) {
                setError(`Posted ${successIndices.size}, failed ${failures.length}: ${failures[0].error}`);
            }
            setSuspenseDialog(null);
        } catch (err) {
            setError(err?.response?.data?.detail || err.message || "Post to suspense failed");
        } finally {
            setResolving(false);
        }
    };

    const summaryRows = useMemo(() => {
        if (!result?.summary) return [];
        const s = result.summary;
        return [
            ["Statement closing balance", result?.statement?.closing_balance],
            ["QBO ledger credits not on statement", s.ledger_credits_not_in_bank],
            ["QBO ledger debits not on statement", s.ledger_debits_not_in_bank],
            ["Statement credits not in QBO", s.bank_credits_not_in_ledger],
            ["Statement debits not in QBO", s.bank_debits_not_in_ledger],
            ["Reconciled amount", s.reconciled_amount],
            ["Difference (should be 0)", s.reconciliation_difference],
            ["Pending review", pendingReview.length],
        ];
    }, [result, pendingReview]);

    const inputBase = "w-full border border-gilt-hairline rounded-lg px-3 py-2 text-sm text-gilt-ink bg-gilt-paper placeholder:text-gilt-faint focus:border-gilt-brass outline-none transition-colors duration-150";
    const labelBase = "block text-xs font-medium text-gilt-soft mb-1.5";
    // Primary actions — quiet ink plate per the gilt workroom idiom.
    const btnPrimary = "inline-flex items-center gap-1.5 px-4 py-2 text-sm font-semibold text-gilt-paper bg-gilt-brass rounded-md hover:bg-gilt-brass/90 transition-colors duration-150 disabled:opacity-50 disabled:cursor-not-allowed";
    const btnSecondary = "inline-flex items-center gap-1.5 px-3 py-2 text-sm text-gilt-soft bg-gilt-card border border-gilt-hairline rounded-md hover:border-gilt-brass hover:text-gilt-brass transition-colors duration-150 disabled:opacity-50";
    const btnAmber = "inline-flex items-center gap-1.5 px-3 py-2 text-sm font-medium text-gilt-brass bg-gilt-sunken border border-gilt-brass/40 rounded-md hover:border-gilt-brass transition-colors duration-150 disabled:opacity-50";
    const btnPurple = "inline-flex items-center gap-1.5 px-3 py-2 text-sm font-medium text-gilt-brass bg-transparent border border-gilt-brass/40 rounded-md hover:bg-gilt-sunken transition-colors duration-150 disabled:opacity-50";

    return (
        <div className="h-screen flex flex-col text-gilt-ink">
            <PageHeader
                icon={Scale}
                title={<span className="gl-display text-gilt-ink">QuickBooks · Bank Reconciliation</span>}
                description="Match a bank statement against your QBO ledger and save the reconciliation."
                actions={
                    savedReportId && (
                        <Badge variant="success" size="md" dot>
                            Saved {savedReportId.slice(0, 8)}
                        </Badge>
                    )
                }
            />

            <div className="flex-1 overflow-y-auto px-6 lg:px-8 py-5 space-y-5">
                {error && (
                    <Card className="bg-gilt-card border-gilt-debit/40 shadow-none">
                        <div className="flex items-start gap-2 px-3 py-2 text-sm text-gilt-debit">
                            <AlertTriangle className="h-4 w-4 flex-shrink-0 mt-0.5 text-gilt-debit" />
                            <span className="flex-1">{error}</span>
                            <button onClick={() => setError("")} className="text-gilt-debit/70 hover:text-gilt-debit transition-colors duration-150">
                                <X className="h-4 w-4" />
                            </button>
                        </div>
                    </Card>
                )}

                {/* Extract & match card */}
                <div className="bg-gilt-card rounded-xl border border-gilt-hairline p-5">
                    <div className="flex items-center gap-2 mb-4">
                        <Upload className="h-4 w-4 text-gilt-brass" />
                        <h2 className="font-mono text-[10px] font-semibold text-gilt-brass uppercase tracking-[0.14em]">Statement & period</h2>
                    </div>
                    <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                        <div>
                            <label className={labelBase}>
                                <Building2 className="inline h-3.5 w-3.5 mr-1 text-gilt-faint" />
                                Company
                            </label>
                            <select
                                className={inputBase}
                                value={company}
                                onChange={(e) => setCompany(e.target.value)}
                            >
                                {companies.map((c) => {
                                    const v = c.realm_id || c.name;
                                    return <option key={v} value={v}>{c.name}</option>;
                                })}
                            </select>
                        </div>
                        <div>
                            <label className={labelBase}>
                                <Wallet className="inline h-3.5 w-3.5 mr-1 text-gilt-faint" />
                                Bank account
                            </label>
                            <select
                                className={inputBase}
                                value={accountId}
                                onChange={(e) => setAccountId(e.target.value)}
                                disabled={loadingAccounts}
                            >
                                {loadingAccounts && <option>Loading…</option>}
                                {!loadingAccounts && accounts.length === 0 && <option value="">No bank accounts</option>}
                                {accounts.map((a) => (
                                    <option key={a.id} value={a.id}>
                                        {a.name} ({a.account_type}{a.currency ? `, ${a.currency}` : ""})
                                    </option>
                                ))}
                            </select>
                        </div>
                        <div>
                            <label className={labelBase}>
                                <FileText className="inline h-3.5 w-3.5 mr-1 text-gilt-faint" />
                                Statement file
                            </label>
                            <input
                                type="file"
                                accept=".pdf,.xls,.xlsx"
                                onChange={(e) => setFile(e.target.files?.[0] || null)}
                                className="block w-full text-sm text-gilt-soft file:mr-3 file:py-1.5 file:px-3 file:rounded-md file:border file:border-gilt-hairline file:bg-gilt-sunken file:text-sm file:font-medium file:text-gilt-brass hover:file:border-gilt-brass file:transition-colors file:duration-150"
                            />
                        </div>
                        <div>
                            <label className={labelBase}>
                                <Calendar className="inline h-3.5 w-3.5 mr-1 text-gilt-faint" />
                                From
                            </label>
                            <input
                                type="date"
                                className={inputBase}
                                value={periodStart}
                                onChange={(e) => setPeriodStart(e.target.value)}
                            />
                        </div>
                        <div>
                            <label className={labelBase}>
                                <Calendar className="inline h-3.5 w-3.5 mr-1 text-gilt-faint" />
                                To
                            </label>
                            <input
                                type="date"
                                className={inputBase}
                                value={periodEnd}
                                onChange={(e) => setPeriodEnd(e.target.value)}
                            />
                        </div>
                        <div>
                            <label className={labelBase}>QBO closing balance <span className="text-gilt-faint font-normal">(optional)</span></label>
                            <input
                                type="number"
                                step="0.01"
                                className={inputBase}
                                value={qboClosingBalance}
                                onChange={(e) => setQboClosingBalance(e.target.value)}
                                placeholder="0.00"
                            />
                        </div>
                    </div>
                    <div className="mt-4 flex justify-end">
                        <button
                            onClick={onMatch}
                            disabled={matching || !file || !accountId}
                            className={btnPrimary}
                        >
                            {matching ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
                            {matching ? "Matching…" : "Extract & match"}
                        </button>
                    </div>
                </div>

            {result && (
                <div className="space-y-5">
                    {/* Summary card with stat tiles */}
                    <div className="bg-gilt-card rounded-xl border border-gilt-hairline p-5">
                        <div className="flex items-center justify-between mb-4">
                            <div className="flex items-center gap-2">
                                <Calendar className="h-4 w-4 text-gilt-brass" />
                                <h2 className="font-mono text-[10px] font-semibold text-gilt-brass uppercase tracking-[0.14em]">
                                    Period {result?.period?.start} → {result?.period?.end}
                                </h2>
                            </div>
                            {result.needs_review && (
                                <span className="inline-flex items-center gap-1 px-2 py-0.5 font-mono text-[10px] font-medium uppercase tracking-[0.14em] rounded-sm bg-transparent border border-gilt-brass/40 text-gilt-brass">
                                    <AlertTriangle className="h-3 w-3" />
                                    {result.unmatched_qbo.length + result.unmatched_statement.length} unmatched
                                </span>
                            )}
                        </div>
                        <table className="w-full text-sm">
                            <tbody>
                                {summaryRows.map(([label, val]) => {
                                    const isDifference = label.startsWith("Difference");
                                    const isReconciled = label === "Reconciled amount";
                                    const isPending = label === "Pending review";
                                    return (
                                        <tr key={label} className="border-b border-gilt-hairline last:border-0">
                                            <td className="py-2 pr-4 text-gilt-soft">{label}</td>
                                            <td className={`py-2 text-right font-mono tabular-nums ${
                                                isDifference && Math.abs(Number(val) || 0) > 0.01 ? "text-gilt-debit font-semibold" :
                                                isReconciled ? "text-gilt-credit font-semibold" :
                                                isPending && Number(val) > 0 ? "text-gilt-brass font-semibold" :
                                                "text-gilt-ink"
                                            }`}>
                                                <span className={isReconciled ? "gl-double-rule" : undefined}>{val == null ? "—" : Number(val).toFixed(2)}</span>
                                            </td>
                                        </tr>
                                    );
                                })}
                            </tbody>
                        </table>
                    </div>

                    {/* Matches table */}
                    <div className="bg-gilt-card rounded-xl border border-gilt-hairline overflow-hidden">
                        <div className="px-5 py-3 border-b border-gilt-hairline flex items-center gap-2">
                            <Link2 className="h-4 w-4 text-gilt-brass" />
                            <h2 className="font-mono text-[10px] font-semibold text-gilt-brass uppercase tracking-[0.14em]">
                                Matches ({result.matches.length})
                            </h2>
                        </div>
                        <MatchTable matches={result.matches} />
                    </div>

                    {/* Unmatched side-by-side */}
                    <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
                        <UnmatchedList
                            title="Unmatched QBO"
                            count={result.unmatched_qbo.length}
                            rows={result.unmatched_qbo.map((r) => ({
                                date: r.date,
                                description: r.description,
                                ref: r.reference,
                                debit: r.debit_amount,
                                credit: r.credit_amount,
                            }))}
                            picked={pickedQbo}
                            onPick={setPickedQbo}
                            onMarkPending={(i) => markPending("qbo", i)}
                        />
                        <UnmatchedList
                            title="Unmatched statement"
                            count={result.unmatched_statement.length}
                            rows={result.unmatched_statement.map((r) => ({
                                date: r.date,
                                description: r.description,
                                ref: r.reference,
                                debit: r.debit_amount,
                                credit: r.credit_amount,
                            }))}
                            picked={pickedStmt}
                            onPick={setPickedStmt}
                            onMarkPending={(i) => markPending("statement", i)}
                        />
                    </div>

                    {/* Sticky action bar */}
                    <div className="bg-gilt-card rounded-xl border border-gilt-hairline p-4 flex items-center gap-2 flex-wrap">
                        <button
                            onClick={addManualMatch}
                            disabled={pickedQbo === null || pickedStmt === null}
                            className={btnPrimary}
                        >
                            <Link2 className="h-4 w-4" />
                            Pair selected
                        </button>
                        <button
                            onClick={markAllStatementPending}
                            disabled={!result.unmatched_statement.length}
                            className={btnAmber}
                            title="Flag all remaining statement rows for later review"
                        >
                            <Clock className="h-4 w-4" />
                            Mark all pending ({result.unmatched_statement.length})
                        </button>
                        <button
                            onClick={openPostToSuspense}
                            disabled={!result.unmatched_statement.length || resolving}
                            className={btnPurple}
                            title="Create QBO entries for remaining statement rows, booked to a suspense account"
                        >
                            <Send className="h-4 w-4" />
                            Post to suspense…
                        </button>
                        <div className="ml-auto flex items-center gap-2 flex-wrap">
                            {pendingReview.length > 0 && (
                                <span className="inline-flex items-center gap-1 px-2 py-0.5 font-mono text-[10px] font-medium uppercase tracking-[0.14em] rounded-sm bg-transparent border border-gilt-brass/40 text-gilt-brass">
                                    <Clock className="h-3 w-3" />
                                    {pendingReview.length} pending
                                </span>
                            )}
                            <button
                                onClick={() => onSaveReport("draft")}
                                disabled={saving}
                                className={btnSecondary}
                            >
                                {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
                                Save draft
                            </button>
                            <button
                                onClick={() => onSaveReport("completed")}
                                disabled={saving}
                                className={btnPrimary}
                            >
                                {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCircle2 className="h-4 w-4" />}
                                Mark completed
                            </button>
                        </div>
                    </div>

                    {pendingReview.length > 0 && (
                        <div className="bg-gilt-sunken border border-gilt-brass/40 rounded-xl p-5">
                            <div className="flex items-center justify-between mb-3">
                                <div className="flex items-center gap-2">
                                    <Clock className="h-4 w-4 text-gilt-brass" />
                                    <h2 className="font-mono text-[10px] font-semibold text-gilt-brass uppercase tracking-[0.14em]">
                                        Pending review ({pendingReview.length})
                                    </h2>
                                </div>
                                <button
                                    onClick={() => setPendingReview([])}
                                    className="text-xs text-gilt-brass hover:text-gilt-ink underline transition-colors duration-150"
                                >
                                    Clear all
                                </button>
                            </div>
                            <ul className="text-xs space-y-1.5 max-h-48 overflow-y-auto">
                                {pendingReview.map((p, i) => (
                                    <li key={i} className="flex items-center justify-between gap-2 bg-gilt-card border border-gilt-hairline rounded-lg px-3 py-2">
                                        <span className="inline-flex items-center gap-1 px-1.5 py-0.5 font-mono text-[10px] font-medium rounded-sm border border-gilt-brass/40 text-gilt-brass uppercase tracking-[0.14em]">
                                            {p.side}
                                        </span>
                                        <span className="truncate flex-1 text-gilt-ink">
                                            {p.row?.date || ""} · {p.row?.description || ""}
                                        </span>
                                        <span className="text-gilt-brass font-mono tabular-nums font-medium">
                                            {Number(p.row?.debit_amount || p.row?.credit_amount || 0).toFixed(2)}
                                        </span>
                                    </li>
                                ))}
                            </ul>
                        </div>
                    )}
                </div>
            )}

                {reports.length > 0 && (
                    <div className="bg-gilt-card rounded-xl border border-gilt-hairline overflow-hidden">
                        <div className="px-5 py-3 border-b border-gilt-hairline flex items-center gap-2">
                            <HistoryIcon className="h-4 w-4 text-gilt-brass" />
                            <h2 className="font-mono text-[10px] font-semibold text-gilt-brass uppercase tracking-[0.14em]">
                                Past reports ({reports.length})
                            </h2>
                        </div>
                        <table className="w-full text-sm">
                            <thead className="bg-gilt-sunken border-b border-gilt-hairline">
                                <tr>
                                    <th className="px-3 py-2.5 text-left font-mono text-[10px] font-medium text-gilt-soft uppercase tracking-wider">Period</th>
                                    <th className="px-3 py-2.5 text-left font-mono text-[10px] font-medium text-gilt-soft uppercase tracking-wider">Account</th>
                                    <th className="px-3 py-2.5 text-left font-mono text-[10px] font-medium text-gilt-soft uppercase tracking-wider">Status</th>
                                    <th className="px-3 py-2.5 text-right font-mono text-[10px] font-medium text-gilt-soft uppercase tracking-wider">Difference</th>
                                    <th className="px-3 py-2.5 text-left font-mono text-[10px] font-medium text-gilt-soft uppercase tracking-wider">Saved</th>
                                    <th className="px-3 py-2.5"></th>
                                </tr>
                            </thead>
                            <tbody>
                                {reports.map((r) => (
                                    <tr key={r.id} className="border-t border-gilt-hairline hover:bg-gilt-sunken transition-colors duration-150">
                                        <td className="px-3 py-2 text-sm text-gilt-ink font-mono tabular-nums">
                                            {r.period_start} <ArrowRight className="inline h-3 w-3 text-gilt-faint mx-1" /> {r.period_end}
                                        </td>
                                        <td className="px-3 py-2 text-sm text-gilt-ink">{r.account_name || r.account_id}</td>
                                        <td className="px-3 py-2 text-sm">
                                            <span className={`inline-flex items-center gap-1 px-2 py-0.5 font-mono text-[10px] font-medium uppercase tracking-[0.14em] rounded-sm bg-transparent border ${
                                                r.status === "completed"
                                                    ? "border-gilt-credit/40 text-gilt-credit"
                                                    : "border-gilt-hairline text-gilt-soft"
                                            }`}>
                                                {r.status === "completed" && <CheckCircle2 className="h-3 w-3" />}
                                                {r.status}
                                            </span>
                                        </td>
                                        <td className="px-3 py-2 text-sm text-right font-mono tabular-nums text-gilt-ink">
                                            {r.summary_json?.reconciliation_difference != null
                                                ? Number(r.summary_json.reconciliation_difference).toFixed(2)
                                                : "—"}
                                        </td>
                                        <td className="px-3 py-2 text-xs text-gilt-faint font-mono tabular-nums">
                                            {r.created_at ? new Date(r.created_at).toLocaleString() : ""}
                                        </td>
                                        <td className="px-3 py-2 text-right">
                                            <button
                                                onClick={() => onLoadReport(r.id)}
                                                className="text-gilt-brass hover:text-gilt-ink hover:underline mr-3 text-sm font-medium transition-colors duration-150"
                                            >
                                                Open
                                            </button>
                                            <button
                                                onClick={() => onDeleteReport(r.id)}
                                                className="text-gilt-debit hover:underline text-sm font-medium transition-colors duration-150"
                                            >
                                                Delete
                                            </button>
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )}

            </div>

            {suspenseDialog && (
                <div className="fixed inset-0 z-50 flex items-center justify-center bg-gilt-ink/40 px-4">
                    <div className="bg-gilt-card rounded-xl border border-gilt-hairline w-full max-w-md">
                        <div className="flex items-center justify-between px-5 py-3.5 border-b border-gilt-hairline">
                            <div className="flex items-center gap-2">
                                <Send className="h-4 w-4 text-gilt-brass" />
                                <h3 className="text-base font-semibold text-gilt-ink">Post to suspense account</h3>
                            </div>
                            <button onClick={() => setSuspenseDialog(null)} className="text-gilt-faint hover:text-gilt-ink transition-colors duration-150">
                                <X className="h-4 w-4" />
                            </button>
                        </div>
                        <div className="px-5 py-4 space-y-4">
                            <p className="text-xs text-gilt-soft leading-relaxed">
                                One QBO entry per remaining statement row will be created and booked to the chosen account.
                                Credits become Deposits, debits become Cash Purchases. You can reclassify later in QBO.
                            </p>
                            <div>
                                <label className={labelBase}>Suspense account</label>
                                <select
                                    className={inputBase}
                                    value={suspenseDialog.suspenseAccountId}
                                    onChange={(e) => {
                                        const acc = allAccounts.find((a) => a.list_id === e.target.value);
                                        setSuspenseDialog((s) => ({ ...s, suspenseAccountId: e.target.value, suspenseAccountName: acc?.name || "" }));
                                    }}
                                >
                                    <option value="">Select…</option>
                                    {allAccounts.map((a) => (
                                        <option key={a.list_id} value={a.list_id}>
                                            {a.name} ({a.account_type})
                                        </option>
                                    ))}
                                </select>
                            </div>
                            <p className="text-xs text-gilt-faint">
                                Posting <span className="font-mono tabular-nums">{suspenseDialog.indices.length}</span> row{suspenseDialog.indices.length === 1 ? "" : "s"}.
                            </p>
                        </div>
                        <div className="flex items-center justify-end gap-2 px-5 py-3.5 border-t border-gilt-hairline bg-gilt-sunken rounded-b-xl">
                            <button
                                onClick={() => setSuspenseDialog(null)}
                                disabled={resolving}
                                className={btnSecondary}
                            >
                                Cancel
                            </button>
                            <button
                                onClick={confirmPostToSuspense}
                                disabled={resolving || !suspenseDialog.suspenseAccountId}
                                className={btnPurple}
                            >
                                {resolving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
                                {resolving ? "Posting…" : "Post"}
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}

function MatchTable({ matches }) {
    if (matches.length === 0) {
        return (
            <div className="px-5 py-8 text-center text-sm text-gilt-soft">
                <Link2 className="h-6 w-6 mx-auto mb-2 text-gilt-faint" />
                No matches yet — extract a statement to begin.
            </div>
        );
    }
    return (
        <div className="overflow-x-auto">
            <table className="w-full text-sm">
                <thead className="bg-gilt-sunken border-b border-gilt-hairline">
                    <tr>
                        <th className="px-3 py-2.5 text-left font-mono text-[10px] font-medium text-gilt-soft uppercase tracking-wider">QBO date</th>
                        <th className="px-3 py-2.5 text-left font-mono text-[10px] font-medium text-gilt-soft uppercase tracking-wider">QBO payee / memo</th>
                        <th className="px-3 py-2.5 text-right font-mono text-[10px] font-medium text-gilt-soft uppercase tracking-wider">QBO amt</th>
                        <th className="px-3 py-2.5 text-left font-mono text-[10px] font-medium text-gilt-soft uppercase tracking-wider">Stmt date</th>
                        <th className="px-3 py-2.5 text-left font-mono text-[10px] font-medium text-gilt-soft uppercase tracking-wider">Stmt desc</th>
                        <th className="px-3 py-2.5 text-right font-mono text-[10px] font-medium text-gilt-soft uppercase tracking-wider">Stmt amt</th>
                        <th className="px-3 py-2.5 text-left font-mono text-[10px] font-medium text-gilt-soft uppercase tracking-wider">Confidence</th>
                    </tr>
                </thead>
                <tbody>
                    {matches.map((m, i) => {
                        const t = m.qbo_txn || {};
                        const s = m.statement_row || {};
                        const conf = m.confidence ?? 0;
                        const confStyle =
                            conf >= 0.9 ? "border-gilt-credit/40 text-gilt-credit"
                            : conf >= 0.5 ? "border-gilt-brass/40 text-gilt-brass"
                            : "border-gilt-debit/40 text-gilt-debit";
                        return (
                            <tr key={i} className="border-t border-gilt-hairline hover:bg-gilt-sunken transition-colors duration-150">
                                <td className="px-3 py-2 text-sm text-gilt-ink font-mono tabular-nums">{t.date || "—"}</td>
                                <td className="px-3 py-2 text-sm text-gilt-ink truncate max-w-xs">{[t.payee, t.memo].filter(Boolean).join(" · ") || "—"}</td>
                                <td className="px-3 py-2 text-sm text-right font-mono tabular-nums text-gilt-ink">{t.amount?.toFixed?.(2) ?? "—"}</td>
                                <td className="px-3 py-2 text-sm text-gilt-ink font-mono tabular-nums">{s.date || "—"}</td>
                                <td className="px-3 py-2 text-sm text-gilt-ink truncate max-w-xs">{s.description || "—"}</td>
                                <td className="px-3 py-2 text-sm text-right font-mono tabular-nums text-gilt-ink">
                                    {(s.debit_amount ?? s.credit_amount)?.toFixed?.(2) ?? "—"}
                                </td>
                                <td className="px-3 py-2">
                                    <span className={`inline-flex items-center gap-1 px-2 py-0.5 font-mono tabular-nums text-[10px] font-medium uppercase tracking-[0.14em] rounded-sm bg-transparent border ${confStyle}`}>
                                        {(conf * 100).toFixed(0)}%
                                        {m.manual && <span className="text-[10px] opacity-70">· manual</span>}
                                    </span>
                                </td>
                            </tr>
                        );
                    })}
                </tbody>
            </table>
        </div>
    );
}

function UnmatchedList({ title, count, rows, picked, onPick, onMarkPending }) {
    return (
        <div className="bg-gilt-card rounded-xl border border-gilt-hairline overflow-hidden">
            <div className="px-5 py-3 border-b border-gilt-hairline flex items-center justify-between">
                <h3 className="font-mono text-[10px] font-semibold text-gilt-brass uppercase tracking-[0.14em]">
                    {title} <span className="text-gilt-faint normal-case font-medium tabular-nums">({count})</span>
                </h3>
            </div>
            <div className="overflow-x-auto">
                <table className="w-full text-sm">
                    <thead className="bg-gilt-sunken border-b border-gilt-hairline">
                        <tr>
                            <th className="px-3 py-2.5 text-left font-mono text-[10px] font-medium text-gilt-soft uppercase tracking-wider">Date</th>
                            <th className="px-3 py-2.5 text-left font-mono text-[10px] font-medium text-gilt-soft uppercase tracking-wider">Description</th>
                            <th className="px-3 py-2.5 text-right font-mono text-[10px] font-medium text-gilt-debit uppercase tracking-wider">Dr</th>
                            <th className="px-3 py-2.5 text-right font-mono text-[10px] font-medium text-gilt-credit uppercase tracking-wider">Cr</th>
                            <th className="px-3 py-2.5"></th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows.length === 0 && (
                            <tr>
                                <td colSpan={5} className="px-3 py-6 text-center text-sm text-gilt-faint">
                                    None — all reconciled.
                                </td>
                            </tr>
                        )}
                        {rows.map((r, i) => (
                            <tr
                                key={i}
                                onClick={() => onPick(picked === i ? null : i)}
                                className={`border-t border-gilt-hairline cursor-pointer group transition-colors duration-150 ${
                                    picked === i ? "bg-gilt-brass/10" : "hover:bg-gilt-sunken"
                                }`}
                            >
                                <td className="px-3 py-2 text-sm text-gilt-ink font-mono tabular-nums whitespace-nowrap">{r.date || "—"}</td>
                                <td className="px-3 py-2 text-sm text-gilt-ink truncate max-w-xs">{r.description || "—"}</td>
                                <td className="px-3 py-2 text-sm text-right font-mono tabular-nums text-gilt-debit">{r.debit?.toFixed?.(2) ?? ""}</td>
                                <td className="px-3 py-2 text-sm text-right font-mono tabular-nums text-gilt-credit">{r.credit?.toFixed?.(2) ?? ""}</td>
                                <td className="px-3 py-2 text-right">
                                    {onMarkPending && (
                                        <button
                                            onClick={(e) => { e.stopPropagation(); onMarkPending(i); }}
                                            className="opacity-0 group-hover:opacity-100 transition-opacity duration-150 text-[11px] text-gilt-brass hover:text-gilt-ink hover:underline"
                                            title="Move to Pending review"
                                        >
                                            pending
                                        </button>
                                    )}
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </div>
    );
}
