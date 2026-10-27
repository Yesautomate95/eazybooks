import React, { useEffect, useState } from "react";
import { Activity, Database, FileText, ShieldCheck, Users } from "lucide-react";
import { rbacApi } from "../../../services/api";
import { PageHeader } from "../../ui/page-header";
import { Card } from "../../ui/card";
import { FullPageSpinner } from "../../ui/spinner";
import { useRbac } from "../../rbac";

const cards = [
  ["users", "Users", Users],
  ["roles", "Roles", ShieldCheck],
  ["clients", "Clients", Database],
  ["files", "Files", FileText],
  ["audit_events", "Audit events", Activity],
];

export default function AdminOverviewPage() {
  const { hasPermission } = useRbac();
  const [data, setData] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    rbacApi.overview().then((result) => {
      if (active) setData(result);
    }).catch((err) => {
      if (active) setError(err?.response?.data?.detail || err.message);
    });
    return () => { active = false; };
  }, []);

  if (!hasPermission("data.read")) return null;
  if (!data && !error) return <FullPageSpinner label="Loading administration overview..." />;

  return (
    <div className="min-h-screen">
      <PageHeader icon={ShieldCheck} title="Administration" description="Monitor access, activity, and application data from one place." />
      <div className="px-6 lg:px-8 py-6 space-y-6">
        {error && <Card padded><p className="text-sm text-gilt-debit">{error}</p></Card>}
        {data && <>
          <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
            {cards.map(([key, label, Icon]) => <Card key={key} padded>
              <Icon size={16} className="text-gilt-brass" />
              <p className="mt-4 text-2xl font-display text-gilt-ink">{data.counts?.[key] ?? 0}</p>
              <p className="mt-1 text-xs text-gilt-soft">{label}</p>
            </Card>)}
          </div>
          <Card>
            <div className="px-5 py-3 border-b border-gilt-hairline flex items-center gap-2">
              <Activity size={15} className="text-gilt-brass" />
              <h2 className="text-sm font-medium text-gilt-ink">Recent activity</h2>
            </div>
            <div className="divide-y divide-gilt-hairline">
              {(data.recent_activity || []).map((event, index) => <div key={`${event.created_at}-${index}`} className="px-5 py-3 flex items-center justify-between text-sm">
                <span className="font-mono text-xs text-gilt-soft">{event.action}</span>
                <span className="text-xs text-gilt-faint">{event.created_at ? new Date(event.created_at).toLocaleString() : ""}</span>
              </div>)}
              {!data.recent_activity?.length && <p className="px-5 py-8 text-sm text-gilt-soft">No activity recorded yet.</p>}
            </div>
          </Card>
        </>}
      </div>
    </div>
  );
}
