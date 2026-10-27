import React, { useEffect, useMemo, useState } from "react";
import { KeyRound, Search, ShieldCheck } from "lucide-react";
import { rbacApi } from "../../../services/api";
import { PageHeader } from "../../ui/page-header";
import { Card } from "../../ui/card";
import { Badge } from "../../ui/badge";
import { Input } from "../../ui/input";
import { FullPageSpinner } from "../../ui/spinner";

/**
 * Permissions registry viewer.
 *
 * Read-only by design — the permission set is driven by code. Admins can
 * add custom permissions through the API, but the day-to-day workflow is
 * to assign existing permissions to roles. We display them grouped by module
 * so it matches the mental model used in the Role editor.
 */
export default function PermissionsPage() {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const data = await rbacApi.listPermissions();
        if (alive) setItems(data.items || []);
      } catch (err) {
        if (alive) setError(err?.response?.data?.detail || err.message);
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  const grouped = useMemo(() => {
    const q = search.trim().toLowerCase();
    const matching = q
      ? items.filter(
          (p) =>
            p.key.toLowerCase().includes(q) ||
            p.description.toLowerCase().includes(q) ||
            p.module.toLowerCase().includes(q)
        )
      : items;
    const map = new Map();
    matching.forEach((p) => {
      const list = map.get(p.module) || [];
      list.push(p);
      map.set(p.module, list);
    });
    return Array.from(map.entries()).sort((a, b) => a[0].localeCompare(b[0]));
  }, [items, search]);

  return (
    <div className="min-h-screen">
      <PageHeader
        icon={KeyRound}
        title="Permissions"
        description="The catalogue of permission keys available across the platform. New modules register permissions in code; they appear here automatically."
        actions={
          <div className="w-72">
            <Input
              size="md"
              leftIcon={<Search />}
              placeholder="Filter permissions…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
        }
      />

      <div className="px-6 lg:px-8 py-6 space-y-4">
        {loading ? (
          <FullPageSpinner label="Loading permissions…" />
        ) : error ? (
          <Card padded className="border-gilt-debit/40 bg-gilt-sunken">
            <p className="text-sm text-gilt-debit">Couldn't load permissions: {error}</p>
          </Card>
        ) : grouped.length === 0 ? (
          <Card padded>
            <p className="text-sm text-gilt-soft text-center py-8">
              No permissions match "{search}".
            </p>
          </Card>
        ) : (
          grouped.map(([module, perms]) => (
            <Card key={module}>
              <div className="px-5 py-3 border-b border-gilt-hairline flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <ShieldCheck size={14} className="text-gilt-brass" />
                  <span className="font-mono text-[10px] tracking-[0.14em] uppercase text-gilt-soft">
                    {module}
                  </span>
                </div>
                <Badge variant="neutral" size="sm">
                  {perms.length}
                </Badge>
              </div>
              <div className="divide-y divide-gilt-hairline">
                {perms.map((p) => (
                  <div key={p.permission_id} className="px-5 py-3 flex items-start gap-4 hover:bg-gilt-sunken transition-colors">
                    <code className="font-mono text-sm text-gilt-ink tabular-nums shrink-0 w-56 truncate">
                      {p.key}
                    </code>
                    <p className="flex-1 text-sm text-gilt-soft">
                      {p.description || <span className="text-gilt-faint italic">No description</span>}
                    </p>
                    {p.is_system ? (
                      <Badge variant="brand" size="sm">System</Badge>
                    ) : (
                      <Badge variant="neutral" size="sm">Custom</Badge>
                    )}
                  </div>
                ))}
              </div>
            </Card>
          ))
        )}
      </div>
    </div>
  );
}
