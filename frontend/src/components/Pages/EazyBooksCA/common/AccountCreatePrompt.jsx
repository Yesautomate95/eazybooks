/**
 * AccountCreatePrompt — duplicate-check dialog shown before creating a new
 * accounting-platform account.
 *
 * Flow:
 *   1. User clicks "Create new account" somewhere in the UI.
 *   2. Dialog opens with an input pre-filled to whatever name they typed.
 *   3. As they type, we debounce-call the platform preview-create-account API.
 *   4. If close matches exist (similarity ≥ 0.85), we show them — user can
 *      pick one to reuse, or override and create the new name anyway.
 *   5. If no close matches, the "Create" button just creates directly.
 *
 * Props:
 *   open: boolean
 *   initialName: string                 — prefill (e.g. dropdown search term)
 *   accountType: string                 — platform account type
 *   qbCompany?: string                  — legacy QuickBooks realm id
 *   companyId?: string                  — platform company/org/tenant id
 *   companyParam?: string               — payload key for companyId
 *   api?: object                        — platform API namespace, defaults qbApi
 *   onClose: () => void
 *   onCreated: (account) => void        — called with the created (or picked
 *                                         existing) account: { list_id, name }
 */

import React, { useState, useEffect, useMemo } from "react";
import { X, AlertTriangle, Plus, Check, Loader2 } from "lucide-react";
import { qbApi } from "../../../../services/api";

const inputBase = "w-full border border-gilt-hairline rounded-lg px-3 py-2 text-sm text-gilt-ink bg-gilt-paper focus:border-gilt-brass placeholder:text-gilt-faint outline-none transition-colors duration-150";
const labelBase = "block font-mono text-[10px] uppercase tracking-[0.14em] text-gilt-soft mb-1.5";
const btnSecondary = "inline-flex items-center gap-1.5 px-3 py-2 text-sm text-gilt-soft bg-gilt-card border border-gilt-hairline rounded-md hover:border-gilt-brass hover:text-gilt-brass transition-colors disabled:opacity-50";
const btnPrimary = "inline-flex items-center gap-1.5 px-4 py-2 text-sm font-semibold text-gilt-paper bg-gilt-brass rounded-md hover:bg-gilt-brass/90 transition-colors disabled:opacity-50";

const STRONG_MATCH_THRESHOLD = 0.85;

const AccountCreatePrompt = ({
    open,
    initialName = "",
    accountType,
    qbCompany,
    companyId,
    companyParam = "qb_company",
    api = qbApi,
    onClose,
    onCreated,
}) => {
    const [name, setName] = useState(initialName);
    const [matches, setMatches] = useState([]);
    const [loadingPreview, setLoadingPreview] = useState(false);
    const [submitting, setSubmitting] = useState(false);
    const [error, setError] = useState(null);
    const activeCompany = companyId ?? qbCompany;

    // Reset state whenever the dialog opens.
    useEffect(() => {
        if (open) {
            setName(initialName);
            setMatches([]);
            setError(null);
            setSubmitting(false);
        }
    }, [open, initialName]);

    // Debounced preview lookup.
    useEffect(() => {
        if (!open) return;
        const trimmed = name.trim();
        if (trimmed.length < 2) {
            setMatches([]);
            return;
        }
        let cancelled = false;
        setLoadingPreview(true);
        const handle = setTimeout(async () => {
            try {
                const res = await api.previewCreateAccount({
                    proposed_name: trimmed,
                    account_type: accountType,
                    [companyParam]: activeCompany,
                });
                if (!cancelled) setMatches(res?.closest_matches || []);
            } catch (e) {
                if (!cancelled) setMatches([]);
            } finally {
                if (!cancelled) setLoadingPreview(false);
            }
        }, 300);
        return () => {
            cancelled = true;
            clearTimeout(handle);
        };
    }, [name, accountType, activeCompany, companyParam, api, open]);

    const strongMatches = useMemo(
        () => matches.filter((m) => m.similarity >= STRONG_MATCH_THRESHOLD),
        [matches]
    );

    const handleCreate = async () => {
        const trimmed = name.trim();
        if (!trimmed) return;
        setSubmitting(true);
        setError(null);
        try {
            const res = await api.createAccount({
                name: trimmed,
                account_type: accountType,
                [companyParam]: activeCompany,
            });
            const created = res?.account || {};
            onCreated({
                list_id: created.Id || created.list_id || created.account_id || created.AccountID,
                name: created.Name || created.name || created.account_name || trimmed,
                account_type: created.AccountType || created.account_type || accountType,
            });
        } catch (e) {
            setError(e?.response?.data?.detail || e?.message || "Failed to create account");
        } finally {
            setSubmitting(false);
        }
    };

    const handlePickExisting = (m) => {
        onCreated({ list_id: m.list_id, name: m.name, account_type: m.account_type });
    };

    if (!open) return null;

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-gilt-ink/40 px-4">
            <div className="bg-gilt-card border border-gilt-hairline rounded-xl w-full max-w-md">
                <div className="flex items-center justify-between px-5 py-3.5 border-b border-gilt-hairline">
                    <div className="flex items-center gap-2">
                        <Plus className="h-4 w-4 text-gilt-brass" />
                        <h3 className="text-base font-semibold text-gilt-ink">Create new account</h3>
                    </div>
                    <button onClick={onClose} className="text-gilt-faint hover:text-gilt-ink transition-colors">
                        <X className="h-4 w-4" />
                    </button>
                </div>

                <div className="px-5 py-4 space-y-4">
                    <div>
                        <label className={labelBase}>Account name</label>
                        <input
                            autoFocus
                            type="text"
                            value={name}
                            onChange={(e) => setName(e.target.value)}
                            className={inputBase}
                            placeholder="e.g. Stationery"
                        />
                        {accountType && (
                            <p className="text-[11px] text-gilt-soft mt-1.5">
                                Account type: <span className="font-medium text-gilt-ink">{accountType}</span>
                            </p>
                        )}
                    </div>

                    {strongMatches.length > 0 && (
                        <div className="rounded-lg border border-gilt-brass/40 bg-gilt-sunken p-3">
                            <div className="flex items-start gap-2">
                                <AlertTriangle className="h-4 w-4 text-gilt-brass flex-shrink-0 mt-0.5" />
                                <div className="flex-1 min-w-0">
                                    <p className="text-xs font-medium text-gilt-ink mb-2 leading-relaxed">
                                        {strongMatches.length === 1
                                            ? "A similar account already exists."
                                            : `${strongMatches.length} similar accounts already exist.`}{" "}
                                        Use one of these instead to keep names consistent.
                                    </p>
                                    <ul className="space-y-1.5">
                                        {strongMatches.map((m) => (
                                            <li
                                                key={m.list_id || m.name}
                                                className="flex items-center justify-between gap-2 bg-gilt-card border border-gilt-hairline rounded-lg px-2.5 py-1.5"
                                            >
                                                <div className="min-w-0 flex-1">
                                                    <div className="text-sm text-gilt-ink truncate font-medium">{m.name}</div>
                                                    {m.account_type && (
                                                        <div className="text-[10px] text-gilt-faint">{m.account_type}</div>
                                                    )}
                                                </div>
                                                <span className="font-mono tabular-nums text-[10px] text-gilt-brass font-medium whitespace-nowrap">
                                                    {Math.round(m.similarity * 100)}% match
                                                </span>
                                                <button
                                                    onClick={() => handlePickExisting(m)}
                                                    className="inline-flex items-center gap-1 text-[11px] font-medium px-2 py-1 border border-gilt-brass/40 text-gilt-brass rounded-md hover:bg-gilt-sunken transition-colors"
                                                >
                                                    <Check className="h-3 w-3" />
                                                    Use this
                                                </button>
                                            </li>
                                        ))}
                                    </ul>
                                </div>
                            </div>
                        </div>
                    )}

                    {loadingPreview && strongMatches.length === 0 && name.trim().length >= 2 && (
                        <p className="inline-flex items-center gap-1 text-xs text-gilt-soft">
                            <Loader2 className="h-3 w-3 animate-spin" />
                            Checking for duplicates…
                        </p>
                    )}

                    {error && (
                        <div className="flex items-start gap-2 bg-gilt-sunken border border-gilt-debit/40 text-gilt-debit px-3 py-2 rounded-lg text-xs">
                            <AlertTriangle className="h-3.5 w-3.5 flex-shrink-0 mt-0.5 text-gilt-debit" />
                            <span>{error}</span>
                        </div>
                    )}
                </div>

                <div className="flex items-center justify-end gap-2 px-5 py-3.5 border-t border-gilt-hairline bg-gilt-sunken rounded-b-xl">
                    <button
                        onClick={onClose}
                        className={btnSecondary}
                        disabled={submitting}
                    >
                        Cancel
                    </button>
                    <button
                        onClick={handleCreate}
                        disabled={submitting || !name.trim()}
                        className={btnPrimary}
                    >
                        {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
                        {submitting
                            ? "Creating…"
                            : strongMatches.length > 0
                                ? "Create anyway"
                                : "Create"}
                    </button>
                </div>
            </div>
        </div>
    );
};

export default AccountCreatePrompt;
