import React, { useEffect, useMemo, useState } from "react";
import {
  Activity,
  Search,
  RefreshCcw,
  ChevronDown,
  Download,
  X,
  Filter,
} from "lucide-react";
import { rbacApi } from "../../../services/api";
import { PageHeader } from "../../ui/page-header";
import { Card } from "../../ui/card";
import { Badge } from "../../ui/badge";
import { Button } from "../../ui/button";
import { Input, Label, Select } from "../../ui/input";
import { FullPageSpinner } from "../../ui/spinner";
import { cn } from "../../../lib/utils";

/**
 * Read-only audit log viewer. Filterable by action and target type.
 *
 * Each row can be expanded to see the JSON diff (previous → new). For now
 * we render the raw JSON; a richer diff view is a follow-on improvement.
 */
const PAGE_SIZE = 50;

function actionBadgeVariant(action) {
  if (!action) return "neutral";
  if (action.endsWith(".create") || action.endsWith(".assign")) return "success";
  if (action.endsWith(".delete") || action.endsWith(".revoke")) return "danger";
  if (action.endsWith(".update") || action.endsWith(".set")) return "info";
  if (action.endsWith(".reset_password")) return "warning";
  if (action.startsWith("auth.")) return "brand";
  return "neutral";
}

function fmtTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("en-IN", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function AuditRow({ row }) {
  const [open, setOpen] = useState(false);
  const hasDetail = row.previous_value || row.new_value || row.metadata;
  return (
    <>
      <tr className="border-t border-gilt-hairline hover:bg-gilt-sunken transition-colors">
        <td className="px-4 py-2.5 font-mono text-xs text-gilt-soft tabular-nums whitespace-nowrap">
          {fmtTime(row.created_at)}
        </td>
        <td className="px-4 py-2.5">
          <Badge variant={actionBadgeVariant(row.action)} size="sm">
            {row.action}
          </Badge>
        </td>
        <td className="px-4 py-2.5 text-sm text-gilt-soft truncate max-w-[180px]">
          {row.actor_user_id || <span className="text-gilt-faint italic">system</span>}
        </td>
        <td className="px-4 py-2.5 text-sm text-gilt-soft truncate max-w-[200px]">
          {row.target_type ? (
            <span>
              <span className="text-gilt-faint">{row.target_type}</span>
              {row.target_id && <span className="ml-1.5 font-mono text-xs">{row.target_id}</span>}
            </span>
          ) : (
            <span className="text-gilt-faint">—</span>
          )}
        </td>
        <td className="px-2 py-2.5 text-right">
          {hasDetail && (
            <button
              onClick={() => setOpen((v) => !v)}
              className="inline-flex items-center h-6 px-2 text-xs text-gilt-soft hover:text-gilt-ink rounded hover:bg-gilt-sunken transition-colors"
            >
              <ChevronDown
                size={12}
                className={`transition-transform ${open ? "rotate-180" : ""}`}
              />
              <span className="ml-1">Details</span>
            </button>
          )}
        </td>
      </tr>
      {open && hasDetail && (
        <tr>
          <td colSpan={5} className="bg-gilt-sunken px-4 py-3">
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3 text-xs">
              {row.previous_value !== null && row.previous_value !== undefined && (
                <div>
                  <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-gilt-soft mb-1">
                    Previous
                  </p>
                  <pre className="bg-gilt-card border border-gilt-hairline rounded-md p-2 overflow-auto max-h-48 font-mono text-2xs text-gilt-soft">
                    {JSON.stringify(row.previous_value, null, 2)}
                  </pre>
                </div>
              )}
              {row.new_value !== null && row.new_value !== undefined && (
                <div>
                  <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-gilt-soft mb-1">
                    New
                  </p>
                  <pre className="bg-gilt-card border border-gilt-hairline rounded-md p-2 overflow-auto max-h-48 font-mono text-2xs text-gilt-soft">
                    {JSON.stringify(row.new_value, null, 2)}
                  </pre>
                </div>
              )}
              {row.metadata !== null && row.metadata !== undefined && (
                <div>
                  <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-gilt-soft mb-1">
                    Metadata
                  </p>
                  <pre className="bg-gilt-card border border-gilt-hairline rounded-md p-2 overflow-auto max-h-48 font-mono text-2xs text-gilt-soft">
                    {JSON.stringify(row.metadata, null, 2)}
                  </pre>
                </div>
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

// Convert a value from <input type="date"> (yyyy-mm-dd) to an ISO timestamp
// pinned to the start/end of the day in the user's local timezone. We send
// ISO strings so the backend handles tz coercion uniformly.
function dateToBoundary(value, endOfDay = false) {
  if (!value) return null;
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return null;
  if (endOfDay) d.setHours(23, 59, 59, 999);
  else d.setHours(0, 0, 0, 0);
  return d.toISOString();
}

export default function AuditLogPage() {
  const [rows, setRows] = useState([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // filters
  const [actionFilter, setActionFilter] = useState("");
  const [actorFilter, setActorFilter] = useState(""); // user_id or ""
  const [startDate, setStartDate] = useState(""); // yyyy-mm-dd
  const [endDate, setEndDate] = useState("");

  const [actors, setActors] = useState([]); // for picker
  const [downloading, setDownloading] = useState(false);

  // Resolve filter values to the API shape exactly once per call so paging
  // and refresh use the same canonical filter set.
  const buildFilters = (overrides = {}) => ({
    action: overrides.action ?? actionFilter ?? undefined,
    actor_user_id: overrides.actor_user_id ?? actorFilter ?? undefined,
    start_at: overrides.start_at ?? dateToBoundary(startDate, false) ?? undefined,
    end_at: overrides.end_at ?? dateToBoundary(endDate, true) ?? undefined,
  });

  const load = async (opts = {}) => {
    setLoading(true);
    setError("");
    try {
      const data = await rbacApi.listAudit({
        ...buildFilters(opts),
        limit: PAGE_SIZE,
        offset: opts.offset ?? offset,
      });
      setRows(data.items || []);
      setTotal(data.total || 0);
    } catch (err) {
      setError(err?.response?.data?.detail || err.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load({ offset: 0 });
    setOffset(0);
    // Load actors for the picker — non-blocking; failure leaves the picker empty.
    (async () => {
      try {
        const { items } = await rbacApi.listAuditActors(200);
        setActors(items || []);
      } catch {
        // best-effort
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const onSearch = () => {
    setOffset(0);
    load({ offset: 0 });
  };

  const onPage = (next) => {
    setOffset(next);
    load({ offset: next });
  };

  const onClearFilters = () => {
    setActionFilter("");
    setActorFilter("");
    setStartDate("");
    setEndDate("");
    setOffset(0);
    load({
      offset: 0,
      action: "",
      actor_user_id: "",
      start_at: null,
      end_at: null,
    });
  };

  const onDownloadCsv = async () => {
    setDownloading(true);
    setError("");
    try {
      await rbacApi.downloadAuditCsv(buildFilters());
    } catch (err) {
      setError(err.message || "Couldn't export CSV");
    } finally {
      setDownloading(false);
    }
  };

  const hasFilters = !!(actionFilter || actorFilter || startDate || endDate);
  const showingFrom = total === 0 ? 0 : offset + 1;
  const showingTo = Math.min(offset + rows.length, total);

  return (
    <div className="min-h-screen">
      <PageHeader
        icon={Activity}
        title="Audit log"
        description="Every authorized mutation and auth event, append-only and immutable."
        actions={
          <div className="flex items-center gap-2">
            <Button variant="secondary" leftIcon={<RefreshCcw />} onClick={onSearch}>
              Refresh
            </Button>
            <Button
              variant="primary"
              leftIcon={<Download />}
              loading={downloading}
              onClick={onDownloadCsv}
            >
              Export CSV
            </Button>
          </div>
        }
      >
        {/* Filter bar — lives under the page header. Each control updates state
            but only re-queries on "Apply" so admins can compose filters without
            firing a request on every keystroke. */}
        <div className="grid grid-cols-1 md:grid-cols-12 gap-3 items-end">
          <div className="md:col-span-4">
            <Label>Action</Label>
            <Input
              size="md"
              leftIcon={<Search />}
              placeholder="e.g. role.create, endpoint.tally.push"
              value={actionFilter}
              onChange={(e) => setActionFilter(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && onSearch()}
            />
          </div>
          <div className="md:col-span-3">
            <Label>Actor</Label>
            <Select
              size="md"
              value={actorFilter}
              onChange={(e) => setActorFilter(e.target.value)}
            >
              <option value="">All actors</option>
              <option value="__null__" disabled>
                — (system events shown when no actor)
              </option>
              {actors.map((a) =>
                a.actor_user_id ? (
                  <option key={a.actor_user_id} value={a.actor_user_id}>
                    {(a.username || a.email || a.actor_user_id)}{" "}
                    ({a.event_count})
                  </option>
                ) : null
              )}
            </Select>
          </div>
          <div className="md:col-span-2">
            <Label>From</Label>
            <Input
              size="md"
              type="date"
              value={startDate}
              onChange={(e) => setStartDate(e.target.value)}
            />
          </div>
          <div className="md:col-span-2">
            <Label>To</Label>
            <Input
              size="md"
              type="date"
              value={endDate}
              onChange={(e) => setEndDate(e.target.value)}
            />
          </div>
          <div className="md:col-span-1 flex items-end gap-2">
            <Button size="md" leftIcon={<Filter />} onClick={onSearch} className="w-full">
              Apply
            </Button>
          </div>
        </div>
        {hasFilters && (
          <div className="mt-2 flex items-center gap-2">
            <button
              type="button"
              onClick={onClearFilters}
              className="inline-flex items-center gap-1 text-xs text-gilt-soft hover:text-gilt-brass transition-colors"
            >
              <X size={12} /> Clear filters
            </button>
          </div>
        )}
      </PageHeader>

      <div className="px-6 lg:px-8 py-6 space-y-3">
        {error && (
          <Card padded className="border-gilt-debit/40 bg-gilt-sunken">
            <p className="text-sm text-gilt-debit">Couldn't load audit log: {error}</p>
          </Card>
        )}

        <Card>
          <div className="px-5 py-3 border-b border-gilt-hairline flex items-center justify-between">
            <p className="font-mono text-xs text-gilt-soft tabular-nums">
              {loading
                ? "Loading…"
                : total === 0
                ? "No events"
                : `Showing ${showingFrom}–${showingTo} of ${total}`}
            </p>
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                variant="secondary"
                disabled={offset === 0 || loading}
                onClick={() => onPage(Math.max(0, offset - PAGE_SIZE))}
              >
                Previous
              </Button>
              <Button
                size="sm"
                variant="secondary"
                disabled={offset + rows.length >= total || loading}
                onClick={() => onPage(offset + PAGE_SIZE)}
              >
                Next
              </Button>
            </div>
          </div>
          <div className="overflow-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gilt-hairline">
                  <th className="px-4 py-2.5 text-left font-mono text-[10px] uppercase tracking-[0.14em] text-gilt-soft w-44">
                    When
                  </th>
                  <th className="px-4 py-2.5 text-left font-mono text-[10px] uppercase tracking-[0.14em] text-gilt-soft w-44">
                    Action
                  </th>
                  <th className="px-4 py-2.5 text-left font-mono text-[10px] uppercase tracking-[0.14em] text-gilt-soft">
                    Actor
                  </th>
                  <th className="px-4 py-2.5 text-left font-mono text-[10px] uppercase tracking-[0.14em] text-gilt-soft">
                    Target
                  </th>
                  <th className="px-2 py-2.5 w-24" />
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <AuditRow key={r.audit_id} row={r} />
                ))}
                {!loading && rows.length === 0 && (
                  <tr>
                    <td colSpan={5} className="px-4 py-10 text-center text-sm text-gilt-soft">
                      No audit events match the filter.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </Card>
      </div>
    </div>
  );
}
