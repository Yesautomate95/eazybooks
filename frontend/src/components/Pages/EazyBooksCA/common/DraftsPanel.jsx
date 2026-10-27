/**
 * DraftsPanel — collapsible list of saved drafts for a given flow.
 *
 * Used by Purchase / Sales / Bank-Entries views on both Tally and QB.
 * The panel is render-agnostic about which backend it talks to — the caller
 * passes `loadList`, `loadDraft`, `deleteDraft` functions that wrap the right
 * API (qbApi.drafts.* or tallyApi.drafts.*).
 *
 * Props:
 *   open: boolean              — controlled expanded state
 *   onToggle: () => void
 *   loadList: () => Promise<{drafts: [...]}>
 *   loadDraft: (id) => Promise<{payload, ...}>   — caller restores state from this
 *   deleteDraft: (id) => Promise<void>
 *   refreshKey: any            — increment to force a list refresh
 *   activeDraftId: string|null — highlighted as "currently loaded"
 *   onOpen: (draft) => void    — fired with the full draft after loadDraft
 *   onDelete: (id) => void     — fired after successful delete
 *   className: string
 */

import React, { useEffect, useState, useCallback } from "react";
import { Save, Loader2, Trash2, FileClock, ChevronRight, ChevronDown, RefreshCw } from "lucide-react";

const fmtTime = (iso) => {
    if (!iso) return "";
    try {
        const d = new Date(iso);
        return d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
    } catch (_) { return iso; }
};

export default function DraftsPanel({
    open,
    onToggle,
    loadList,
    loadDraft,
    deleteDraft,
    refreshKey,
    activeDraftId,
    onOpen,
    onDelete,
    className = "",
}) {
    const [drafts, setDrafts] = useState([]);
    const [loadingList, setLoadingList] = useState(false);
    const [busyId, setBusyId] = useState(null);
    const [error, setError] = useState("");

    const refresh = useCallback(async () => {
        setLoadingList(true);
        setError("");
        try {
            const res = await loadList();
            setDrafts(res?.drafts || []);
        } catch (e) {
            setError(e?.response?.data?.detail || e.message || "Failed to load drafts");
        } finally {
            setLoadingList(false);
        }
    }, [loadList]);

    useEffect(() => {
        if (open) refresh();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [open, refreshKey]);

    const handleOpen = async (id) => {
        setBusyId(id);
        setError("");
        try {
            const full = await loadDraft(id);
            onOpen?.(full);
        } catch (e) {
            setError(e?.response?.data?.detail || e.message || "Failed to open draft");
        } finally {
            setBusyId(null);
        }
    };

    const handleDelete = async (id) => {
        if (!window.confirm("Delete this draft? This can't be undone.")) return;
        setBusyId(id);
        setError("");
        try {
            await deleteDraft(id);
            setDrafts((prev) => prev.filter((d) => d.id !== id));
            onDelete?.(id);
        } catch (e) {
            setError(e?.response?.data?.detail || e.message || "Failed to delete draft");
        } finally {
            setBusyId(null);
        }
    };

    return (
        <div className={`bg-gilt-card rounded-xl border border-gilt-hairline ${className}`}>
            <button
                onClick={onToggle}
                className="w-full flex items-center justify-between px-5 py-3 hover:bg-gilt-sunken transition-colors rounded-xl"
            >
                <div className="flex items-center gap-2">
                    {open ? <ChevronDown className="h-4 w-4 text-gilt-faint" /> : <ChevronRight className="h-4 w-4 text-gilt-faint" />}
                    <FileClock className="h-4 w-4 text-gilt-brass" />
                    <h2 className="text-sm font-semibold text-gilt-soft uppercase tracking-wider">
                        Drafts {drafts.length ? <span className="text-gilt-faint normal-case font-medium font-mono tabular-nums">({drafts.length})</span> : null}
                    </h2>
                </div>
                {open && (
                    <span
                        role="button"
                        tabIndex={0}
                        onClick={(e) => { e.stopPropagation(); refresh(); }}
                        onKeyDown={(e) => { if (e.key === "Enter") { e.stopPropagation(); refresh(); } }}
                        className="text-xs text-gilt-soft hover:text-gilt-ink inline-flex items-center gap-1 cursor-pointer transition-colors"
                    >
                        <RefreshCw className={`h-3 w-3 ${loadingList ? "animate-spin" : ""}`} />
                        Refresh
                    </span>
                )}
            </button>
            {open && (
                <div className="px-5 pb-5 border-t border-gilt-hairline">
                    {error && (
                        <p className="text-xs text-gilt-debit mt-3">{error}</p>
                    )}
                    {loadingList && drafts.length === 0 ? (
                        <p className="text-xs text-gilt-soft inline-flex items-center gap-1 mt-3">
                            <Loader2 className="h-3 w-3 animate-spin" />
                            Loading drafts…
                        </p>
                    ) : drafts.length === 0 ? (
                        <p className="text-xs text-gilt-soft mt-3 italic">No drafts yet — hit "Save draft" to create one.</p>
                    ) : (
                        <ul className="mt-3 space-y-1.5">
                            {drafts.map((d) => {
                                const isActive = activeDraftId === d.id;
                                return (
                                    <li
                                        key={d.id}
                                        className={`flex items-center justify-between gap-2 border rounded-lg px-3 py-2 transition-colors ${
                                            isActive ? "border-gilt-brass/40 bg-gilt-sunken" : "border-gilt-hairline bg-gilt-card hover:bg-gilt-sunken"
                                        }`}
                                    >
                                        <button
                                            onClick={() => handleOpen(d.id)}
                                            disabled={busyId === d.id}
                                            className="flex-1 min-w-0 text-left disabled:opacity-50"
                                        >
                                            <div className="flex items-center gap-1.5 truncate">
                                                <span className="text-sm font-medium text-gilt-ink truncate">
                                                    {d.title || "Untitled draft"}
                                                </span>
                                                {isActive && (
                                                    <span className="font-mono text-[10px] font-medium text-gilt-brass uppercase tracking-[0.14em]">
                                                        loaded
                                                    </span>
                                                )}
                                            </div>
                                            <div className="text-[11px] font-mono tabular-nums text-gilt-faint">
                                                Updated {fmtTime(d.updated_at)}
                                            </div>
                                        </button>
                                        <button
                                            onClick={() => handleDelete(d.id)}
                                            disabled={busyId === d.id}
                                            className="text-gilt-faint hover:text-gilt-debit hover:bg-gilt-sunken p-1.5 rounded transition-colors"
                                            title="Delete draft"
                                        >
                                            {busyId === d.id ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
                                        </button>
                                    </li>
                                );
                            })}
                        </ul>
                    )}
                </div>
            )}
        </div>
    );
}

/**
 * SaveDraftButton — small inline button for action bars. Caller wires
 * onClick to the actual save call.
 */
export function SaveDraftButton({ onClick, busy, label = "Save draft", className = "" }) {
    return (
        <button
            onClick={onClick}
            disabled={busy}
            className={`inline-flex items-center gap-1.5 px-3 py-2 text-sm font-medium text-gilt-soft bg-gilt-card border border-gilt-hairline rounded-md hover:border-gilt-brass hover:text-gilt-brass transition-colors disabled:opacity-50 ${className}`}
        >
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
            {busy ? "Saving…" : label}
        </button>
    );
}
