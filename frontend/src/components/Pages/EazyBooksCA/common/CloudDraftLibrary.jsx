import React, { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  FileText,
  RefreshCw,
  Trash2,
  ExternalLink,
  Inbox,
  CheckCircle2,
  XCircle,
  Plug,
  X,
} from "lucide-react";
import { PageHeader } from "../../../ui/page-header";
import { Button } from "../../../ui/button";
import { EmptyState } from "../../../ui/empty-state";
import { FullPageSpinner } from "../../../ui/spinner";
import { CLOUD_ADAPTERS } from "./cloudDraftAdapters";
import { makePushDraft } from "./cloudPushers";
import CloudDraftGroupHeader from "./CloudDraftGroupHeader";

const FLOW_LABEL = { sales: "Sales", purchase: "Purchase", bank_entries: "Bank" };
const FLOW_PATH = { sales: "sales", purchase: "purchase", bank_entries: "bank-entries" };

const TABS = [
  { key: "drafts", label: "Drafts" },
  { key: "pushed", label: "Pushed" },
  { key: "all", label: "All" },
];

const statusBadge = (status) => {
  switch (status) {
    case "pushed":
      return { label: "Pushed", cls: "text-gilt-credit border-gilt-credit/40" };
    case "error":
      return { label: "Error", cls: "text-gilt-debit border-gilt-debit/40" };
    case "draft":
      return { label: "Draft", cls: "text-gilt-brass border-gilt-brass/40" };
    case "pushing":
      return { label: "Pushing…", cls: "text-gilt-brass border-gilt-brass/40" };
    default:
      return { label: status || "—", cls: "text-gilt-soft border-gilt-hairline" };
  }
};

const fmtDate = (value) => {
  if (!value) return "";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
};

/**
 * Unified, draft-first document library for a cloud accounting platform
 * (QuickBooks / Zoho / Xero / SAP). Lists every saved draft, groups by company,
 * and supports a per-group bulk push that validates → pushes → marks-pushed
 * each draft — the cloud equivalent of the Tally library's bulk-push-by-company.
 *
 * Props:
 *   platform: "quickbooks" | "zoho" | "xero" | "sap"
 *   embedded: when true, drop the outer page-shell + PageHeader so this can be
 *             rendered inside another page (e.g. the QB dashboard, matching
 *             Tally's single-page-everything-in-one-place pattern). The inner
 *             tabs + body still render so callers don't lose any functionality.
 */
export default function CloudDraftLibrary({ platform, embedded = false }) {
  const adapter = CLOUD_ADAPTERS[platform];
  const pushDraft = useMemo(() => (adapter ? makePushDraft(adapter) : null), [adapter]);

  const [loading, setLoading] = useState(true);
  const [connected, setConnected] = useState(null);
  const [companyNames, setCompanyNames] = useState({});
  const [drafts, setDrafts] = useState([]);
  const [tab, setTab] = useState("drafts");
  const [pushingCompany, setPushingCompany] = useState(null);
  const [bulkSummary, setBulkSummary] = useState(null);
  const [error, setError] = useState("");

  const loadDrafts = useCallback(async () => {
    if (!adapter) return;
    try {
      const res = await adapter.drafts.list({});
      setDrafts(res?.drafts || []);
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || "Failed to load drafts");
    }
  }, [adapter]);

  const loadAll = useCallback(async () => {
    if (!adapter) return;
    setLoading(true);
    setError("");
    // Connection + company-name resolution (best effort — a failure here just
    // means we render ids instead of names and gate pushing).
    try {
      const res = await adapter.getCompanies();
      const list = res?.companies || [];
      const map = {};
      list.forEach((c) => {
        const key = adapter.companyKeyOf(c);
        if (key) map[String(key)] = c.name || c.company_name || String(key);
      });
      setCompanyNames(map);
      setConnected(true);
    } catch {
      setConnected(false);
    }
    await loadDrafts();
    setLoading(false);
  }, [adapter, loadDrafts]);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  const companyNameOf = useCallback(
    (id) => companyNames[String(id)] || (id ? `${adapter.companyNoun} ${id}` : "Unassigned"),
    [companyNames, adapter]
  );

  // Filter by tab, then group by company id.
  const groups = useMemo(() => {
    const filtered = drafts.filter((d) => {
      if (tab === "pushed") return d.status === "pushed";
      if (tab === "drafts") return d.status !== "pushed";
      return true;
    });
    const byCompany = new Map();
    for (const d of filtered) {
      const cid = d[adapter.companyField] || "";
      if (!byCompany.has(cid)) byCompany.set(cid, []);
      byCompany.get(cid).push(d);
    }
    return Array.from(byCompany.entries()).map(([cid, rows]) => ({
      companyId: cid,
      companyName: companyNameOf(cid),
      rows,
    }));
  }, [drafts, tab, adapter, companyNameOf]);

  const counts = useMemo(
    () => ({
      drafts: drafts.filter((d) => d.status !== "pushed").length,
      pushed: drafts.filter((d) => d.status === "pushed").length,
      all: drafts.length,
    }),
    [drafts]
  );

  const handleBulkPush = useCallback(
    async (companyId, companyName, pushable) => {
      if (!pushDraft) return;
      setPushingCompany(companyId);
      setBulkSummary(null);
      const results = [];
      for (const d of pushable) {
        const title = d.title || FLOW_LABEL[d.flow] || "Draft";
        try {
          // Pre-flight: platform connected + company still resolves.
          const v = await adapter.drafts.validatePushable(d.id);
          if (v && v.pushable === false) {
            results.push({
              ok: false,
              title,
              message: (v.issues || []).join("; ") || "Not pushable right now",
            });
            continue;
          }
          const full = await adapter.drafts.get(d.id);
          const res = await pushDraft(full);
          if (res.ok) {
            await adapter.drafts.markPushed(d.id);
            results.push({ ok: true, title, message: `Pushed ${res.pushed}/${res.total}` });
          } else {
            results.push({
              ok: false,
              title,
              message:
                res.errors.map((e) => e.message).join("; ") ||
                `Pushed ${res.pushed}/${res.total}`,
            });
          }
        } catch (err) {
          results.push({
            ok: false,
            title,
            message: err?.response?.data?.detail || err.message || "Push failed",
          });
        }
      }
      setBulkSummary({ companyName, results });
      setPushingCompany(null);
      await loadDrafts();
    },
    [adapter, pushDraft, loadDrafts]
  );

  const handleDelete = useCallback(
    async (draftId) => {
      // eslint-disable-next-line no-alert
      if (!window.confirm("Delete this draft? This cannot be undone.")) return;
      try {
        await adapter.drafts.delete(draftId);
        await loadDrafts();
      } catch (err) {
        setError(err?.response?.data?.detail || err.message || "Delete failed");
      }
    },
    [adapter, loadDrafts]
  );

  if (!adapter) {
    return <div className="p-8 text-sm text-gilt-debit">Unknown platform: {String(platform)}</div>;
  }

  // Loading: a full-page spinner only makes sense in standalone mode; inside an
  // embedded section we use a small inline indicator so the host page's layout
  // (e.g. the QB dashboard's financial cards above) keeps rendering.
  if (loading) {
    return embedded ? (
      <section>
        <div className="flex items-center gap-2 text-sm text-gilt-soft py-6">
          <RefreshCw size={14} className="animate-spin" />
          Loading {adapter.label} drafts…
        </div>
      </section>
    ) : (
      <FullPageSpinner label={`Loading ${adapter.label} drafts`} />
    );
  }

  const tabStrip = (
    <div className="flex items-center gap-1">
      {TABS.map((t) => (
        <button
          key={t.key}
          type="button"
          onClick={() => setTab(t.key)}
          className={`px-3 py-1.5 text-sm font-medium border-b-2 transition-colors ${
            tab === t.key
              ? "border-gilt-brass text-gilt-ink"
              : "border-transparent text-gilt-soft hover:text-gilt-ink"
          }`}
        >
          {t.label}
          <span className="ml-1.5 font-mono tabular-nums text-xs text-gilt-faint">{counts[t.key]}</span>
        </button>
      ))}
    </div>
  );

  const body = (
    <>
      {error && (
        <div className="mb-4 rounded-lg border border-gilt-debit/40 bg-gilt-sunken px-4 py-2.5 text-sm text-gilt-debit">
          {error}
        </div>
      )}

      {connected === false && (
        <div className="mb-4 rounded-lg border border-gilt-brass/40 bg-gilt-sunken px-4 py-3 text-sm text-gilt-brass flex items-center justify-between gap-3">
          <span>{adapter.label} isn’t connected — drafts can’t be pushed until you reconnect.</span>
          <Link to={adapter.viewBase}>
            <Button variant="secondary" size="xs" leftIcon={<Plug size={13} />}>
              Connect
            </Button>
          </Link>
        </div>
      )}

      {bulkSummary && (
        <BulkSummaryBanner summary={bulkSummary} onDismiss={() => setBulkSummary(null)} />
      )}

      {groups.length === 0 ? (
        <EmptyState
          icon={Inbox}
          title="No drafts here"
          description={
            tab === "pushed"
              ? "Nothing has been pushed yet."
              : "Saved drafts from the sales, purchase and bank-entry views show up here."
          }
        />
      ) : (
        <div className="space-y-8">
          {groups.map((g) => (
            <section key={g.companyId || "unassigned"}>
              <CloudDraftGroupHeader
                companyName={g.companyName}
                drafts={g.rows}
                connected={connected}
                isPushing={pushingCompany === g.companyId}
                onBulkPush={(pushable) =>
                  handleBulkPush(g.companyId, g.companyName, pushable)
                }
              />
              <div className="space-y-2">
                {g.rows.map((d) => (
                  <DraftRow
                    key={d.id}
                    draft={d}
                    viewBase={adapter.viewBase}
                    onDelete={() => handleDelete(d.id)}
                  />
                ))}
              </div>
            </section>
          ))}
        </div>
      )}
    </>
  );

  // Embedded: drop the page shell + PageHeader; render a compact in-card
  // heading + the tab strip + body. Used by the QB dashboard (and could be
  // adopted by Zoho/Xero/SAP later) to consolidate the landing page like Tally.
  if (embedded) {
    return (
      <section>
        <div className="flex items-center justify-between gap-3 flex-wrap mb-4">
          <div className="flex items-center gap-2">
            <FileText size={16} className="text-gilt-faint" />
            <h2 className="text-sm font-semibold text-gilt-ink">Document drafts</h2>
            <span className="text-xs text-gilt-soft">— review, then bulk-push per company.</span>
          </div>
          <div className="flex items-center gap-2">
            {tabStrip}
            <Button
              variant="secondary"
              size="sm"
              leftIcon={<RefreshCw size={14} />}
              onClick={loadAll}
            >
              Refresh
            </Button>
          </div>
        </div>
        {body}
      </section>
    );
  }

  return (
    <div className="min-h-full">
      <PageHeader
        eyebrow={adapter.label}
        title="Document Drafts"
        description="Saved purchase, sales and bank-entry drafts. Review, then bulk-push per company."
        icon={FileText}
        actions={
          <Button
            variant="secondary"
            size="sm"
            leftIcon={<RefreshCw size={14} />}
            onClick={loadAll}
          >
            Refresh
          </Button>
        }
      >
        {tabStrip}
      </PageHeader>

      <div className="px-6 lg:px-8 py-6">{body}</div>
    </div>
  );
}

function DraftRow({ draft, viewBase, onDelete }) {
  const badge = statusBadge(draft.status);
  const flowLabel = FLOW_LABEL[draft.flow] || draft.flow;
  const openHref = `${viewBase}/${FLOW_PATH[draft.flow] || ""}?draftId=${draft.id}`;
  return (
    <div className="flex items-center gap-3 rounded-lg border border-gilt-hairline bg-gilt-card px-4 py-3 hover:border-gilt-brass transition-colors">
      <span className="inline-flex shrink-0 items-center rounded border border-gilt-hairline bg-gilt-sunken px-2 py-0.5 font-mono text-[10px] uppercase tracking-[0.14em] text-gilt-soft">
        {flowLabel}
      </span>
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium text-gilt-ink">{draft.title || "Untitled draft"}</p>
        <p className="text-xs font-mono tabular-nums text-gilt-faint">
          Updated {fmtDate(draft.updated_at)}
          {draft.error_message ? ` · ${draft.error_message}` : ""}
        </p>
      </div>
      <span className={`shrink-0 rounded border px-2 py-0.5 font-mono text-[10px] uppercase tracking-[0.14em] ${badge.cls}`}>
        {badge.label}
      </span>
      <Link
        to={openHref}
        className="shrink-0 inline-flex items-center gap-1 rounded-md border border-gilt-hairline px-2.5 py-1.5 text-xs font-medium text-gilt-soft hover:border-gilt-brass hover:text-gilt-brass transition-colors"
        title="Open in editor"
      >
        <ExternalLink size={13} />
        Open
      </Link>
      <button
        type="button"
        onClick={onDelete}
        className="shrink-0 inline-flex items-center rounded-md border border-gilt-hairline p-1.5 text-gilt-faint hover:text-gilt-debit hover:border-gilt-debit/40 transition-colors"
        title="Delete draft"
      >
        <Trash2 size={14} />
      </button>
    </div>
  );
}

function BulkSummaryBanner({ summary, onDismiss }) {
  const ok = summary.results.filter((r) => r.ok).length;
  const failed = summary.results.length - ok;
  return (
    <div className="mb-4 rounded-xl border border-gilt-hairline bg-gilt-card p-4">
      <div className="flex items-center justify-between gap-3 mb-2">
        <p className="text-sm font-semibold text-gilt-ink">
          {summary.companyName}: {ok} pushed
          {failed ? `, ${failed} failed` : ""}
        </p>
        <button type="button" onClick={onDismiss} className="text-gilt-faint hover:text-gilt-ink transition-colors">
          <X size={16} />
        </button>
      </div>
      <ul className="space-y-1">
        {summary.results.map((r, i) => (
          <li key={i} className="flex items-start gap-2 text-xs">
            {r.ok ? (
              <CheckCircle2 size={14} className="mt-0.5 shrink-0 text-gilt-credit" />
            ) : (
              <XCircle size={14} className="mt-0.5 shrink-0 text-gilt-debit" />
            )}
            <span className="text-gilt-soft">
              <span className="font-medium text-gilt-ink">{r.title}</span> — {r.message}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
