import React, { useState } from "react";
import {
  FileText,
  CreditCard,
  Receipt,
  ShoppingCart,
  MoreVertical,
  Trash2,
  Download,
  Loader2,
  AlertCircle,
  CheckCircle2,
  Clock,
  Zap,
} from "lucide-react";

const FLOW_META = {
  sales: {
    label: "Sales Invoice",
    Icon: Receipt,
    iconBg: "bg-gilt-sunken border border-gilt-hairline",
    iconColor: "text-gilt-brass",
  },
  purchase: {
    label: "Purchase Invoice",
    Icon: ShoppingCart,
    iconBg: "bg-gilt-sunken border border-gilt-hairline",
    iconColor: "text-gilt-brass",
  },
  bank_entries: {
    label: "Bank Statement",
    Icon: CreditCard,
    iconBg: "bg-gilt-sunken border border-gilt-hairline",
    iconColor: "text-gilt-brass",
  },
};

const STATUS_CONFIG = {
  uploaded: {
    label: "Pending Extraction",
    chip: "text-gilt-soft border-gilt-hairline",
    Icon: Clock,
  },
  extracting: {
    label: "Extracting…",
    chip: "text-gilt-brass border-gilt-brass/40",
    Icon: Loader2,
    spin: true,
  },
  extracted: {
    label: "Ready to Review",
    chip: "text-gilt-brass border-gilt-brass/40",
    Icon: FileText,
  },
  draft: {
    label: "Draft",
    chip: "text-gilt-soft border-gilt-hairline",
    Icon: FileText,
  },
  pushing: {
    label: "Pushing…",
    chip: "text-gilt-brass border-gilt-brass/40",
    Icon: Loader2,
    spin: true,
  },
  pushed: {
    label: "Pushed to Tally",
    chip: "text-gilt-credit border-gilt-credit/40",
    Icon: CheckCircle2,
  },
  error: {
    label: "Error",
    chip: "text-gilt-debit border-gilt-debit/40",
    Icon: AlertCircle,
  },
};

function formatDate(dateStr) {
  if (!dateStr) return "";
  try {
    return new Date(dateStr).toLocaleDateString("en-IN", {
      day: "numeric",
      month: "short",
      year: "numeric",
    });
  } catch {
    return dateStr;
  }
}

export default function DraftCard({ draft, onExtract, onOpen, onDelete, onPush }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const meta = FLOW_META[draft.flow] || FLOW_META.bank_entries;
  const status = STATUS_CONFIG[draft.status] || STATUS_CONFIG.draft;
  const { Icon: FlowIcon } = meta;
  const { Icon: StatusIcon } = status;

  const isLoading = draft.status === "extracting" || draft.status === "pushing";
  const canExtract = draft.status === "uploaded" || draft.status === "error";
  // Stuck-extraction recovery: extraction shouldn't take more than a minute,
  // so always offer a manual restart on extracting cards. The handler
  // (`tallyApi.extractDraft`) just re-fires the extract job.
  const canRetryExtract = draft.status === "extracting";
  // 'extracted' (auto-extracted, never saved) is openable too — opening lands
  // the user in the editor where they can click Save Draft to promote it.
  const canOpen =
    draft.status === "draft" ||
    draft.status === "pushed" ||
    draft.status === "extracted";
  // Allow push from both 'extracted' (AI-ready) and 'draft' (human-reviewed).
  // The 'Save Draft' step is no longer a gate — it remains as an audit signal
  // but doesn't block push.
  const canPush = draft.status === "draft" || draft.status === "extracted";

  const displayName = draft.file_name || draft.title || "Untitled document";
  const dateLabel =
    draft.status === "pushed"
      ? `Pushed ${formatDate(draft.pushed_at || draft.updated_at)}`
      : `Updated ${formatDate(draft.updated_at)}`;

  return (
    <div className="relative bg-gilt-card border border-gilt-hairline rounded-lg p-4 hover:border-gilt-brass transition-colors group">
      {/* Header row */}
      <div className="flex items-start gap-3">
        {/* Flow icon */}
        <div className={`flex-shrink-0 w-10 h-10 rounded-lg flex items-center justify-center ${meta.iconBg}`}>
          <FlowIcon className={`w-5 h-5 ${meta.iconColor}`} />
        </div>

        {/* File info */}
        <div className="flex-1 min-w-0">
          <p
            className="text-sm font-medium text-gilt-ink truncate"
            title={displayName}
          >
            {displayName}
          </p>
          <p className="text-xs text-gilt-soft mt-0.5">
            {draft.company_name} · {meta.label}
          </p>
          <p className="text-xs font-mono tabular-nums text-gilt-faint mt-0.5">{dateLabel}</p>
        </div>

        {/* ⋮ menu */}
        <div className="relative flex-shrink-0">
          <button
            onClick={() => setMenuOpen((v) => !v)}
            className="p-1 rounded-md text-gilt-faint hover:text-gilt-ink hover:bg-gilt-sunken opacity-0 group-hover:opacity-100 transition-opacity"
          >
            <MoreVertical className="w-4 h-4" />
          </button>
          {menuOpen && (
            <>
              <div
                className="fixed inset-0 z-10"
                onClick={() => setMenuOpen(false)}
              />
              <div className="absolute right-0 top-7 z-20 bg-gilt-card border border-gilt-hairline rounded-lg shadow-sm py-1 w-40">
                {draft.file_url && (
                  <a
                    href={draft.file_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex items-center gap-2 px-3 py-2 text-sm text-gilt-soft hover:bg-gilt-sunken"
                    onClick={() => setMenuOpen(false)}
                  >
                    <Download className="w-3.5 h-3.5" />
                    Download file
                  </a>
                )}
                <button
                  className="w-full flex items-center gap-2 px-3 py-2 text-sm text-gilt-debit hover:bg-gilt-sunken"
                  onClick={() => {
                    setMenuOpen(false);
                    setConfirmDelete(true);
                  }}
                >
                  <Trash2 className="w-3.5 h-3.5" />
                  Delete
                </button>
              </div>
            </>
          )}
        </div>
      </div>

      {/* Status badge */}
      <div className="mt-3 flex items-center gap-2">
        <span
          className={`inline-flex items-center gap-1 px-2 py-0.5 rounded border font-mono text-[10px] uppercase tracking-[0.14em] ${status.chip}`}
        >
          <StatusIcon className={`w-3 h-3 ${status.spin ? "animate-spin" : ""}`} />
          {status.label}
        </span>
        {draft.error_message && draft.status === "error" && (
          <span className="text-xs text-gilt-debit truncate" title={draft.error_message}>
            {draft.error_message.slice(0, 60)}
          </span>
        )}
      </div>

      {/* Actions */}
      <div className="mt-3 flex items-center gap-2">
        {canExtract && (
          <button
            onClick={() => onExtract && onExtract(draft)}
            disabled={isLoading}
            className="flex-1 inline-flex items-center justify-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-gilt-brass text-gilt-paper rounded-md hover:bg-gilt-brass/90 disabled:opacity-50 transition-colors"
          >
            <Zap className="w-3.5 h-3.5" />
            Extract Details
          </button>
        )}
        {canRetryExtract && (
          <button
            onClick={() => onExtract && onExtract(draft)}
            className="flex-1 inline-flex items-center justify-center gap-1.5 px-3 py-1.5 text-xs font-medium border border-gilt-hairline text-gilt-soft rounded-md hover:border-gilt-brass hover:text-gilt-brass transition-colors"
            title="Re-run the AI extraction"
          >
            <Zap className="w-3.5 h-3.5" />
            Restart extraction
          </button>
        )}
        {canOpen && (
          <button
            onClick={() => onOpen && onOpen(draft)}
            disabled={isLoading}
            className="flex-1 inline-flex items-center justify-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-gilt-ink text-gilt-paper rounded-md hover:bg-gilt-ink/90 disabled:opacity-50 transition-colors"
          >
            Open
          </button>
        )}
        {canPush && (
          <button
            onClick={() => onPush && onPush(draft)}
            disabled={isLoading}
            className="flex-1 inline-flex items-center justify-center gap-1.5 px-3 py-1.5 text-xs font-medium border border-gilt-credit/40 text-gilt-credit rounded-md hover:border-gilt-credit hover:bg-gilt-sunken disabled:opacity-50 transition-colors"
          >
            Push to Tally
          </button>
        )}
      </div>

      {/* Delete confirmation overlay */}
      {confirmDelete && (
        <div className="absolute inset-0 bg-gilt-card/70 backdrop-blur-sm rounded-lg flex flex-col items-center justify-center gap-3 z-10 p-4">
          <p className="text-sm font-medium text-gilt-ink text-center">
            Delete this document?
          </p>
          <p className="text-xs text-gilt-soft text-center">
            This will permanently remove the file and all extracted data.
          </p>
          <div className="flex gap-2">
            <button
              onClick={() => setConfirmDelete(false)}
              className="px-3 py-1.5 text-xs border border-gilt-hairline text-gilt-soft rounded-md hover:border-gilt-brass hover:text-gilt-brass transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={() => {
                setConfirmDelete(false);
                onDelete && onDelete(draft);
              }}
              className="px-3 py-1.5 text-xs border border-gilt-debit text-gilt-debit rounded-md hover:bg-gilt-sunken transition-colors"
            >
              Delete
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
