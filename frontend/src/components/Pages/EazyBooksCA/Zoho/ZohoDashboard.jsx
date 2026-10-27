import React, { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { OAUTH_RETURN_KEY } from "../Dashboard/services/dashboardService";
import {
  LayoutDashboard,
  Building2,
  Loader2,
  CheckCircle2,
  AlertTriangle,
  X,
  Plug,
  Unplug,
  Landmark,
  ArrowDownToLine,
  ArrowUpFromLine,
  Upload,
} from "lucide-react";
import { zohoApi, zohoOAuthApi } from "../../../../services/api";
import { formatZohoError } from "./components/zohoError";
import { PageHeader } from "../../../ui/page-header";
import { Button } from "../../../ui/button";
import { Card, CardHeader } from "../../../ui/card";
import { Badge } from "../../../ui/badge";
import { Select } from "../../../ui/input";
import { Skeleton } from "../../../ui/skeleton";
import { EmptyState } from "../../../ui/empty-state";
import { cn } from "../../../../lib/utils";
import { usePermissions, PermissionGuard } from "../../../rbac";
import ZohoUploadWizard from "./ZohoUploadWizard";
import CloudDraftLibrary from "../common/CloudDraftLibrary";

/**
 * Zoho Books dashboard.
 *
 * Zoho is cloud-OAuth only: show "Connect to Zoho Books" until OAuth
 * completes, then list the organization + masters from Zoho's REST API.
 * Mirrors the QuickBooks dashboard (cash + outstanding snapshot + inlined
 * draft library) so the modules look and flow identically.
 */
export default function ZohoDashboard() {
  const { hasPermission } = usePermissions();
  const canConnect = hasPermission("zoho.connect");
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [connected, setConnected] = useState(null);
  const [companies, setCompanies] = useState([]);
  const [activeCompany, setActiveCompany] = useState("");
  const [accounts, setAccounts] = useState([]);
  const [customers, setCustomers] = useState([]);
  const [vendors, setVendors] = useState([]);
  const [loading, setLoading] = useState(false);
  const [loadingCompanies, setLoadingCompanies] = useState(false);
  const [checkingConnection, setCheckingConnection] = useState(true);
  const [error, setError] = useState("");
  const [connecting, setConnecting] = useState(false);
  const [showConnectedToast, setShowConnectedToast] = useState(false);

  // Tally-style unified upload wizard + draft library (replaces the old tiles).
  const [wizardOpen, setWizardOpen] = useState(false);

  const oauthFlag = searchParams.get("zoho");

  useEffect(() => {
    runConnectionCheck();
  }, []);

  useEffect(() => {
    if (oauthFlag === "connected") {
      // The provider callback always lands here; if OAuth was started elsewhere
      // (e.g. /dashboard/analytics) bounce the user back to where they were.
      let returnTo = null;
      try {
        returnTo = sessionStorage.getItem(OAUTH_RETURN_KEY);
        if (returnTo) sessionStorage.removeItem(OAUTH_RETURN_KEY);
      } catch {
        /* sessionStorage unavailable — fall through */
      }
      if (returnTo && returnTo !== window.location.pathname) {
        const sep = returnTo.includes("?") ? "&" : "?";
        navigate(`${returnTo}${sep}zoho=connected`, { replace: true });
        return undefined;
      }

      const next = new URLSearchParams(searchParams);
      next.delete("zoho");
      next.delete("organization_id");
      setSearchParams(next, { replace: true });
      setShowConnectedToast(true);
      const t = setTimeout(() => setShowConnectedToast(false), 4000);
      runConnectionCheck();
      return () => clearTimeout(t);
    }
  }, [oauthFlag]); // eslint-disable-line react-hooks/exhaustive-deps

  const runConnectionCheck = async () => {
    setError("");
    setCheckingConnection(true);
    try {
      const status = await zohoApi.checkConnection();
      setConnected(!!status?.connected);
      if (status?.connected) {
        await loadCompanies();
      }
    } catch (err) {
      setError(formatZohoError(err, "Connection check failed"));
      setConnected(false);
    } finally {
      setCheckingConnection(false);
    }
  };

  const loadCompanies = async () => {
    setLoadingCompanies(true);
    try {
      const res = await zohoApi.getCompanies();
      const list = res?.organizations || [];
      setCompanies(list);
      if (list[0]) {
        const key = list[0].organization_id || list[0].name;
        setActiveCompany(key);
        await loadMasters(key);
      }
    } catch (err) {
      setError(formatZohoError(err, "Failed to load companies"));
    } finally {
      setLoadingCompanies(false);
    }
  };

  const loadMasters = async (companyKey) => {
    setLoading(true);
    setError("");
    try {
      const [a, c, v] = await Promise.all([
        zohoApi.getAccounts(companyKey),
        zohoApi.getCustomers(companyKey),
        zohoApi.getVendors(companyKey),
      ]);
      setAccounts(a?.accounts || []);
      setCustomers(c?.customers || []);
      setVendors(v?.vendors || []);
    } catch (err) {
      setError(formatZohoError(err, "Failed to load master data"));
    } finally {
      setLoading(false);
    }
  };

  const onCompanyChange = (e) => {
    const key = e.target.value;
    setActiveCompany(key);
    loadMasters(key);
  };

  const onConnect = async () => {
    setConnecting(true);
    setError("");
    try {
      const { auth_url } = await zohoOAuthApi.getAuthUrl();
      window.location.href = auth_url;
    } catch (err) {
      setConnecting(false);
      setError(formatZohoError(err, "Could not start OAuth"));
    }
  };

  const onDisconnect = async () => {
    if (
      !window.confirm(
        "Disconnect Zoho Books? You'll need to reconnect to access this organization."
      )
    )
      return;
    try {
      await zohoOAuthApi.disconnect();
      setConnected(false);
      setCompanies([]);
      setAccounts([]);
      setCustomers([]);
      setVendors([]);
    } catch (err) {
      setError(formatZohoError(err, "Disconnect failed"));
    }
  };

  return (
    <div className="min-h-screen flex flex-col">
      <PageHeader
        className="bg-transparent border-gilt-hairline"
        icon={LayoutDashboard}
        title={<span className="gl-display text-gilt-ink">Zoho Books</span>}
        description="Upload documents, manage drafts, and track cash + outstanding balances per Zoho organization."
        actions={
          <div className="flex items-center gap-2">
            <ConnectionBadge checking={checkingConnection} connected={connected} />
            {connected && (
              <PermissionGuard permission="zoho.connect">
                <Button
                  variant="danger-outline"
                  size="sm"
                  leftIcon={<Unplug />}
                  onClick={onDisconnect}
                  className="bg-transparent text-gilt-debit border-gilt-hairline rounded-md shadow-none hover:bg-gilt-sunken hover:border-gilt-debit transition-colors duration-200"
                >
                  Disconnect
                </Button>
              </PermissionGuard>
            )}
          </div>
        }
      />

      <div className="flex-1 px-6 lg:px-8 py-6 space-y-5">
        {showConnectedToast && (
          <Card className="bg-gilt-card border-gilt-hairline shadow-none">
            <div className="flex items-center gap-2 px-4 py-2.5 text-sm text-gilt-ink">
              <CheckCircle2 className="h-4 w-4 text-gilt-credit" />
              <span className="font-medium text-gilt-credit">Connected to Zoho Books.</span>
              <span className="text-gilt-soft">Loading your organization data…</span>
            </div>
          </Card>
        )}
        {oauthFlag === "error" && (
          <Card className="bg-gilt-card border-gilt-hairline shadow-none">
            <div className="flex items-center gap-2 px-4 py-2.5 text-sm text-gilt-soft">
              <AlertTriangle className="h-4 w-4 text-gilt-brass" />
              Zoho Books authorization was cancelled or failed. Try connecting again.
            </div>
          </Card>
        )}
        {error && (
          <Card className="bg-gilt-card border-gilt-hairline shadow-none">
            <div className="flex items-start gap-2 px-4 py-2.5 text-sm text-gilt-debit">
              <AlertTriangle className="h-4 w-4 flex-shrink-0 mt-0.5 text-gilt-debit" />
              <span className="flex-1">{error}</span>
              <button
                onClick={() => setError("")}
                className="text-gilt-faint hover:text-gilt-ink transition-colors duration-200"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
          </Card>
        )}

        {checkingConnection && <InitialSkeleton />}

        {!checkingConnection && !connected && (
          <ConnectCard
            onConnect={onConnect}
            connecting={connecting}
            canConnect={canConnect}
          />
        )}

        {!checkingConnection && connected && (
          <>
            {/* Organization picker + upload entry */}
            <Card padded className="bg-gilt-card border-gilt-hairline shadow-none">
              <div className="flex items-center gap-4 flex-wrap">
                <div className="flex items-center gap-2">
                  <Building2 className="h-4 w-4 text-gilt-brass" />
                  <label className="font-mono text-[10px] font-medium uppercase tracking-[0.14em] text-gilt-brass">Organization</label>
                </div>
                <div className="min-w-[280px]">
                  <Select
                    value={activeCompany}
                    onChange={onCompanyChange}
                    disabled={loadingCompanies || !companies.length}
                    className="bg-gilt-card text-gilt-ink border-gilt-hairline rounded-md focus:border-gilt-brass focus:shadow-none disabled:bg-gilt-sunken disabled:text-gilt-faint transition-colors duration-200"
                  >
                    {loadingCompanies && <option value="">Loading organizations…</option>}
                    {!loadingCompanies && !companies.length && (
                      <option value="">No organizations found</option>
                    )}
                    {companies.map((c) => {
                      const value = c.organization_id || c.name;
                      return (
                        <option key={value} value={value}>
                          {c.name}
                        </option>
                      );
                    })}
                  </Select>
                </div>
                {(loadingCompanies || loading) && (
                  <span className="inline-flex items-center gap-1.5 text-xs text-gilt-faint">
                    <Loader2 className="h-3 w-3 animate-spin" />
                    {loadingCompanies ? "Loading organizations…" : "Loading data…"}
                  </span>
                )}
              </div>
              <div className="mt-4">
                <Button
                  variant="primary"
                  size="md"
                  leftIcon={<Upload />}
                  onClick={() => setWizardOpen(true)}
                  disabled={!companies.length}
                  className="bg-gilt-brass text-gilt-paper rounded-md shadow-none hover:bg-gilt-brass/90 active:bg-gilt-ink focus-visible:ring-gilt-brass transition-colors duration-200"
                >
                  Upload document
                </Button>
                <p className="mt-2 text-xs text-gilt-faint">
                  One place for Sales, Purchase, Bank Entries, and Bank Reconciliation — pick the type after you start.
                </p>
              </div>
            </Card>

            {/* Document drafts — inlined here (Tally-parity), no separate
                /dashboard/zoho/drafts page. The `embedded` prop drops the outer
                page shell so it sits inside the dashboard. */}
            <Card padded className="bg-gilt-card border-gilt-hairline shadow-none">
              <CloudDraftLibrary platform="zoho" embedded />
            </Card>

            {/* Financial snapshot — same data source (the loadMasters
                Promise.all), summarized to what a CA wants at a glance: where the
                cash is, who owes them, and who they owe. */}
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <CashPositionCard accounts={accounts} loading={loading} />
              <OutstandingCard
                title="Top receivables"
                subtitle="Customers with open balances"
                icon={ArrowDownToLine}
                accent="emerald"
                rows={customers}
                loading={loading}
                emptyLabel="No open customer balances"
              />
              <OutstandingCard
                title="Top payables"
                subtitle="Vendors with open balances"
                icon={ArrowUpFromLine}
                accent="amber"
                rows={vendors}
                loading={loading}
                emptyLabel="No open vendor balances"
              />
            </div>
          </>
        )}
      </div>

      <ZohoUploadWizard
        open={wizardOpen}
        onClose={() => setWizardOpen(false)}
        companies={companies}
        initialCompany={activeCompany}
      />
    </div>
  );
}

function ConnectCard({ onConnect, connecting, canConnect = true }) {
  return (
    <Card padded className="max-w-xl bg-gilt-card border-gilt-hairline shadow-none">
      <div className="flex items-start gap-3">
        <div className="shrink-0 h-10 w-10 rounded-lg bg-gilt-sunken border border-gilt-hairline text-gilt-brass flex items-center justify-center">
          <Plug className="h-5 w-5" strokeWidth={1.75} />
        </div>
        <div className="min-w-0">
          <h2 className="text-base font-semibold text-gilt-ink">Connect to Zoho Books</h2>
          <p className="mt-1 text-sm text-gilt-soft leading-relaxed">
            Authorize EazyBooks to read your Zoho Books customers, vendors, and accounts, and to
            push invoices and bills on your behalf. You can revoke access at any time from
            Zoho&nbsp;&rsaquo;&nbsp;Connected&nbsp;Apps.
          </p>
          <div className="mt-4">
            {canConnect ? (
              <Button
                variant="primary"
                size="md"
                onClick={onConnect}
                loading={connecting}
                leftIcon={!connecting ? <Plug /> : undefined}
                className="bg-gilt-brass text-gilt-paper rounded-md shadow-none hover:bg-gilt-brass/90 active:bg-gilt-ink focus-visible:ring-gilt-brass transition-colors duration-200"
              >
                {connecting ? "Redirecting to Zoho…" : "Connect to Zoho Books"}
              </Button>
            ) : (
              <p className="text-xs text-gilt-soft bg-gilt-sunken border border-gilt-hairline rounded-md px-3 py-2 inline-block">
                You don't have permission to connect Zoho Books. Ask an administrator for{" "}
                <code className="font-mono text-gilt-brass">zoho.connect</code>.
              </p>
            )}
          </div>
        </div>
      </div>
    </Card>
  );
}

function InitialSkeleton() {
  return (
    <div className="space-y-5">
      <Card padded className="flex items-center gap-3 bg-gilt-card border-gilt-hairline shadow-none">
        <Loader2 className="h-5 w-5 animate-spin text-gilt-brass" />
        <div>
          <div className="text-sm font-medium text-gilt-ink">
            Checking Zoho Books connection…
          </div>
          <div className="text-xs text-gilt-soft">Verifying your authorization with Zoho.</div>
        </div>
      </Card>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {[0, 1, 2].map((i) => (
          <Card key={i} padded className="bg-gilt-card border-gilt-hairline shadow-none">
            <Skeleton className="h-3 w-1/3 mb-3 from-gilt-sunken via-gilt-hairline/70 to-gilt-sunken" />
            <div className="space-y-2">
              <Skeleton className="h-2.5 w-full from-gilt-sunken via-gilt-hairline/70 to-gilt-sunken" />
              <Skeleton className="h-2.5 w-5/6 from-gilt-sunken via-gilt-hairline/70 to-gilt-sunken" />
              <Skeleton className="h-2.5 w-4/6 from-gilt-sunken via-gilt-hairline/70 to-gilt-sunken" />
            </div>
          </Card>
        ))}
      </div>
    </div>
  );
}

function ConnectionBadge({ checking, connected }) {
  if (checking) {
    return (
      <Badge
        variant="neutral"
        size="md"
        className="bg-transparent border-gilt-hairline text-gilt-soft font-mono text-[10px] uppercase tracking-[0.08em]"
      >
        <Loader2 className="h-3 w-3 animate-spin" />
        Checking…
      </Badge>
    );
  }
  return (
    <Badge
      variant={connected ? "success" : "danger"}
      size="md"
      dot
      className={cn(
        "bg-transparent border-gilt-hairline font-mono text-[10px] uppercase tracking-[0.08em] transition-colors duration-200",
        connected ? "text-gilt-credit" : "text-gilt-debit"
      )}
    >
      {connected ? "Connected" : "Disconnected"}
    </Badge>
  );
}

// Two decimals, Indian (lakh/crore) digit grouping, no fixed currency symbol —
// Zoho organizations vary by currency and we don't have it at this level, so we
// keep the symbol neutral.
const fmtAmount = (n) => {
  const v = Number(n);
  if (n == null || Number.isNaN(v)) return "—";
  return v.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
};

function CashPositionCard({ accounts, loading }) {
  const bankAccounts = (accounts || [])
    .map((a) => ({ ...a, balanceNum: Number(a.balance) || 0 }))
    .filter((a) => (a.account_type || "").toLowerCase() === "bank")
    .sort((a, b) => b.balanceNum - a.balanceNum);
  const total = bankAccounts.reduce((sum, a) => sum + a.balanceNum, 0);

  return (
    <Card className="bg-gilt-card border-gilt-hairline shadow-none">
      <CardHeader
        className="border-gilt-hairline"
        title={
          <span className="inline-flex items-center gap-2">
            <Landmark className="h-3.5 w-3.5 text-gilt-brass" aria-hidden="true" />
            <span className="font-mono text-[10px] font-medium uppercase tracking-[0.14em] text-gilt-brass">
              Cash position
            </span>
          </span>
        }
        action={
          loading ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin text-gilt-faint" />
          ) : (
            <Badge
              variant="neutral"
              size="sm"
              className="bg-transparent border-gilt-hairline text-gilt-soft font-mono tabular-nums text-[10px]"
            >{bankAccounts.length}</Badge>
          )
        }
      />
      <div className="px-5 py-4">
        {loading ? (
          <div className="space-y-2">
            <Skeleton className="h-6 w-1/2 mb-2 from-gilt-sunken via-gilt-hairline/70 to-gilt-sunken" />
            {[0, 1, 2].map((i) => <Skeleton key={i} className="h-2.5 from-gilt-sunken via-gilt-hairline/70 to-gilt-sunken" style={{ width: `${80 - i * 10}%` }} />)}
          </div>
        ) : bankAccounts.length === 0 ? (
          <EmptyState size="sm" description="No bank accounts in this organization." className="py-4" />
        ) : (
          <>
            <div className="text-2xl font-mono font-medium tabular-nums text-gilt-ink">
              <span className="gl-double-rule inline-block pb-0.5">{fmtAmount(total)}</span>
            </div>
            <div className="text-xs text-gilt-faint mt-1 mb-3">across <span className="font-mono tabular-nums">{bankAccounts.length}</span> bank account{bankAccounts.length === 1 ? "" : "s"}</div>
            <ul className="space-y-1.5 text-sm">
              {bankAccounts.slice(0, 5).map((a) => (
                <li key={a.list_id} className="flex items-center justify-between gap-3 min-w-0 rounded-sm -mx-1 px-1 hover:bg-gilt-sunken transition-colors duration-200">
                  <span className="truncate text-gilt-soft" title={a.name}>{a.name}</span>
                  <span className={cn("font-mono tabular-nums flex-shrink-0", a.balanceNum < 0 ? "text-gilt-debit" : "text-gilt-ink")}>{fmtAmount(a.balanceNum)}</span>
                </li>
              ))}
            </ul>
          </>
        )}
      </div>
    </Card>
  );
}

// Book-keeping semantics: receivables (emerald) read as credit-green,
// payables (amber) read as madder debit-red.
const ACCENT_STYLES = {
  emerald: { icon: "text-gilt-credit", number: "text-gilt-credit" },
  amber: { icon: "text-gilt-debit", number: "text-gilt-debit" },
};

function OutstandingCard({ title, subtitle, icon: Icon, accent = "emerald", rows, loading, emptyLabel }) {
  const open = (rows || [])
    .map((r) => ({ ...r, balanceNum: Number(r.balance) || 0 }))
    .filter((r) => r.balanceNum > 0)
    .sort((a, b) => b.balanceNum - a.balanceNum);
  const total = open.reduce((sum, r) => sum + r.balanceNum, 0);
  const styles = ACCENT_STYLES[accent] || ACCENT_STYLES.emerald;

  return (
    <Card className="bg-gilt-card border-gilt-hairline shadow-none">
      <CardHeader
        className="border-gilt-hairline"
        title={
          <span className="inline-flex items-center gap-2">
            {Icon && <Icon className={cn("h-3.5 w-3.5", styles.icon)} aria-hidden="true" />}
            <span className="font-mono text-[10px] font-medium uppercase tracking-[0.14em] text-gilt-brass">
              {title}
            </span>
          </span>
        }
        action={
          loading ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin text-gilt-faint" />
          ) : (
            <Badge
              variant="neutral"
              size="sm"
              className="bg-transparent border-gilt-hairline text-gilt-soft font-mono tabular-nums text-[10px]"
            >{open.length}</Badge>
          )
        }
      />
      <div className="px-5 py-4">
        {loading ? (
          <div className="space-y-2">
            <Skeleton className="h-6 w-1/2 mb-2 from-gilt-sunken via-gilt-hairline/70 to-gilt-sunken" />
            {[0, 1, 2].map((i) => <Skeleton key={i} className="h-2.5 from-gilt-sunken via-gilt-hairline/70 to-gilt-sunken" style={{ width: `${80 - i * 10}%` }} />)}
          </div>
        ) : open.length === 0 ? (
          <EmptyState size="sm" description={emptyLabel} className="py-4" />
        ) : (
          <>
            <div className={cn("text-2xl font-mono font-medium tabular-nums", styles.number)}>
              <span className="gl-double-rule inline-block pb-0.5">{fmtAmount(total)}</span>
            </div>
            <div className="text-xs text-gilt-faint mt-1 mb-3">{subtitle.toLowerCase()} · top 5 shown</div>
            <ul className="space-y-1.5 text-sm">
              {open.slice(0, 5).map((r) => (
                <li key={r.list_id} className="flex items-center justify-between gap-3 min-w-0 rounded-sm -mx-1 px-1 hover:bg-gilt-sunken transition-colors duration-200">
                  <span className="truncate text-gilt-soft" title={r.name}>{r.name}</span>
                  <span className="font-mono tabular-nums text-gilt-ink flex-shrink-0">{fmtAmount(r.balanceNum)}</span>
                </li>
              ))}
            </ul>
          </>
        )}
      </div>
    </Card>
  );
}
