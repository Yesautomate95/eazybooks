import React, { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
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
  ArrowRight,
  Wallet,
  Users,
  Truck,
  FileSignature,
  Receipt,
  Scale,
  Banknote,
  FileClock,
} from "lucide-react";
import { xeroApi, xeroOAuthApi } from "../../../../services/api";
import { PageHeader } from "../../../ui/page-header";
import { Button } from "../../../ui/button";
import { Card, CardHeader } from "../../../ui/card";
import { Badge } from "../../../ui/badge";
import { Select } from "../../../ui/input";
import { Skeleton } from "../../../ui/skeleton";
import { EmptyState } from "../../../ui/empty-state";
import { cn } from "../../../../lib/utils";
import { usePermissions, PermissionGuard } from "../../../rbac";

/**
 * Xero dashboard.
 *
 * Xero is cloud-OAuth only: show "Connect to Xero" until OAuth
 * completes, then list the organization + masters from Xero's REST API.
 */
export default function XeroDashboard() {
  const { hasPermission } = usePermissions();
  const navigate = useNavigate();
  const canConnect = hasPermission("xero.connect");
  const [searchParams, setSearchParams] = useSearchParams();
  const [mode, setMode] = useState(null);
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

  const oauthFlag = searchParams.get("xero");

  useEffect(() => {
    runConnectionCheck();
  }, []);

  useEffect(() => {
    if (oauthFlag === "connected") {
      let returnTo = null;
      try {
        returnTo = sessionStorage.getItem(OAUTH_RETURN_KEY);
        if (returnTo) sessionStorage.removeItem(OAUTH_RETURN_KEY);
      } catch {
        /* sessionStorage unavailable */
      }
      if (returnTo && returnTo !== window.location.pathname) {
        const sep = returnTo.includes("?") ? "&" : "?";
        navigate(`${returnTo}${sep}xero=connected`, { replace: true });
        return undefined;
      }

      const next = new URLSearchParams(searchParams);
      next.delete("xero");
      next.delete("tenant_id");
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
      const status = await xeroApi.checkConnection();
      setMode(status?.mode || null);
      setConnected(!!status?.connected);
      if (status?.connected) {
        await loadCompanies(status.mode);
      }
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || "Connection check failed");
      setConnected(false);
    } finally {
      setCheckingConnection(false);
    }
  };

  const loadCompanies = async () => {
    setLoadingCompanies(true);
    try {
      const res = await xeroApi.getCompanies();
      const list = res?.tenants || [];
      setCompanies(list);
      if (list[0]) {
        const key = list[0].tenant_id;
        setActiveCompany(key);
        await loadMasters(key);
      }
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || "Failed to load companies");
    } finally {
      setLoadingCompanies(false);
    }
  };

  const loadMasters = async (companyKey) => {
    setLoading(true);
    setError("");
    try {
      const [a, c, v] = await Promise.all([
        xeroApi.getAccounts(companyKey),
        xeroApi.getCustomers(companyKey),
        xeroApi.getVendors(companyKey),
      ]);
      setAccounts(a?.accounts || []);
      setCustomers(c?.customers || []);
      setVendors(v?.vendors || []);
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || "Failed to load master data");
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
      const { auth_url } = await xeroOAuthApi.getAuthUrl();
      window.location.href = auth_url;
    } catch (err) {
      setConnecting(false);
      setError(err?.response?.data?.detail || err.message || "Could not start OAuth");
    }
  };

  const onDisconnect = async () => {
    if (
      !window.confirm(
        "Disconnect Xero? You'll need to reconnect to access this organization."
      )
    )
      return;
    try {
      await xeroOAuthApi.disconnect();
      setConnected(false);
      setCompanies([]);
      setAccounts([]);
      setCustomers([]);
      setVendors([]);
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || "Disconnect failed");
    }
  };

  const navTiles = [
    { to: "/dashboard/xero/sales", label: "Sales Invoices", icon: FileSignature },
    { to: "/dashboard/xero/purchase", label: "Purchase Bills", icon: Receipt },
    { to: "/dashboard/xero/bank-recon", label: "Bank Reconciliation", icon: Scale },
    { to: "/dashboard/xero/bank-entries", label: "Bank Entries", icon: Banknote },
  ];

  return (
    <div className="min-h-screen flex flex-col">
      <PageHeader
        icon={LayoutDashboard}
        title="Xero"
        description="Sync tenants, customers, vendors, and push entries directly to Xero."
        actions={
          <div className="flex items-center gap-2">
            <ConnectionBadge checking={checkingConnection} connected={connected} />
            {connected && (
              <PermissionGuard permission="xero.connect">
                <Button
                  variant="danger-outline"
                  size="sm"
                  leftIcon={<Unplug />}
                  onClick={onDisconnect}
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
          <Card className="bg-success-50 border-success-200">
            <div className="flex items-center gap-2 px-4 py-2.5 text-sm text-success-700">
              <CheckCircle2 className="h-4 w-4 text-success-600" />
              <span className="font-medium">Connected to Xero.</span>
              <span className="text-success-700/80">Loading your organization data…</span>
            </div>
          </Card>
        )}
        {oauthFlag === "error" && (
          <Card className="bg-warning-50 border-warning-200">
            <div className="flex items-center gap-2 px-4 py-2.5 text-sm text-warning-700">
              <AlertTriangle className="h-4 w-4 text-warning-600" />
              Xero authorization was cancelled or failed. Try connecting again.
            </div>
          </Card>
        )}
        {error && (
          <Card className="bg-danger-50 border-danger-200">
            <div className="flex items-start gap-2 px-4 py-2.5 text-sm text-danger-700">
              <AlertTriangle className="h-4 w-4 flex-shrink-0 mt-0.5 text-danger-600" />
              <span className="flex-1">{error}</span>
              <button
                onClick={() => setError("")}
                className="text-danger-500 hover:text-danger-700 transition-colors"
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
            {/* Organization picker + module nav */}
            <Card padded>
              <div className="flex items-center gap-4 flex-wrap">
                <div className="flex items-center gap-2">
                  <Building2 className="h-4 w-4 text-ink-500" />
                  <label className="text-sm font-medium text-ink-700">Organization</label>
                </div>
                <div className="min-w-[280px]">
                  <Select
                    value={activeCompany}
                    onChange={onCompanyChange}
                    disabled={loadingCompanies || !companies.length}
                  >
                    {loadingCompanies && <option value="">Loading tenants…</option>}
                    {!loadingCompanies && !companies.length && (
                      <option value="">No tenants found</option>
                    )}
                    {companies.map((c) => (
                      <option key={c.tenant_id} value={c.tenant_id}>
                        {c.name}
                      </option>
                    ))}
                  </Select>
                </div>
                {(loadingCompanies || loading) && (
                  <span className="inline-flex items-center gap-1.5 text-xs text-ink-500">
                    <Loader2 className="h-3 w-3 animate-spin" />
                    {loadingCompanies ? "Loading tenants…" : "Loading data…"}
                  </span>
                )}
              </div>
              <div className="mt-4 grid grid-cols-2 md:grid-cols-4 gap-3">
                {navTiles.map((tile) => (
                  <Link
                    key={tile.to}
                    to={tile.to}
                    className="group flex items-center justify-between gap-2 px-3.5 py-3 rounded-lg border border-ink-200 bg-white hover:border-brand-300 hover:bg-brand-50/40 transition-colors"
                  >
                    <span className="inline-flex items-center gap-2.5 text-sm font-medium text-ink-700 group-hover:text-brand-700">
                      <tile.icon className="h-4 w-4 text-brand-500" />
                      {tile.label}
                    </span>
                    <ArrowRight className="h-4 w-4 text-ink-300 group-hover:text-brand-500 transition-colors" />
                  </Link>
                ))}
              </div>
            </Card>

            {/* Document drafts — links to the cross-flow library
                (CloudDraftLibrary) which lists Sales/Purchase/Bank drafts,
                groups by organization, and supports per-org bulk push. */}
            <Card padded>
              <div className="flex items-center justify-between gap-3 flex-wrap">
                <div className="flex items-center gap-2">
                  <FileClock className="h-4 w-4 text-ink-500" />
                  <div>
                    <div className="text-sm font-medium text-ink-900">Document drafts</div>
                    <div className="text-xs text-ink-500">Saved Sales, Purchase &amp; Bank-entry drafts — review and bulk-push per organization.</div>
                  </div>
                </div>
                <Link to="/dashboard/xero/drafts">
                  <Button variant="secondary" size="sm" rightIcon={<ArrowRight />}>
                    Open drafts
                  </Button>
                </Link>
              </div>
            </Card>

            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <MasterCard
                title="Accounts"
                icon={Wallet}
                rows={accounts}
                loading={loading}
                fields={["name", "account_type", "balance"]}
              />
              <MasterCard
                title="Customers"
                icon={Users}
                rows={customers}
                loading={loading}
                fields={["name", "company", "balance"]}
              />
              <MasterCard
                title="Vendors"
                icon={Truck}
                rows={vendors}
                loading={loading}
                fields={["name", "company", "balance"]}
              />
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function ConnectCard({ onConnect, connecting, canConnect = true }) {
  return (
    <Card padded className="max-w-xl">
      <div className="flex items-start gap-3">
        <div className="shrink-0 h-10 w-10 rounded-xl bg-brand-50 border border-brand-100 text-brand-600 flex items-center justify-center">
          <Plug className="h-5 w-5" strokeWidth={1.75} />
        </div>
        <div className="min-w-0">
          <h2 className="text-base font-semibold text-ink-900">Connect to Xero</h2>
          <p className="mt-1 text-sm text-ink-500 leading-relaxed">
            Authorize Aureus to read your Xero customers, vendors, and accounts, and to push
            invoices and bills on your behalf. You can revoke access at any time from Xero
            Books&nbsp;&rsaquo;&nbsp;Apps.
          </p>
          <div className="mt-4">
            {canConnect ? (
              <Button
                variant="primary"
                size="md"
                onClick={onConnect}
                loading={connecting}
                leftIcon={!connecting ? <Plug /> : undefined}
              >
                {connecting ? "Redirecting to Xero…" : "Connect to Xero"}
              </Button>
            ) : (
              <p className="text-xs text-ink-500 bg-ink-50 border border-ink-200 rounded-md px-3 py-2 inline-block">
                You don't have permission to connect Xero. Ask an administrator for{" "}
                <code className="font-mono">xero.connect</code>.
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
      <Card padded className="flex items-center gap-3">
        <Loader2 className="h-5 w-5 animate-spin text-brand-600" />
        <div>
          <div className="text-sm font-medium text-ink-900">
            Checking Xero connection…
          </div>
          <div className="text-xs text-ink-500">Verifying your authorization with Xero.</div>
        </div>
      </Card>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {[0, 1, 2].map((i) => (
          <Card key={i} padded>
            <Skeleton className="h-3 w-1/3 mb-3" />
            <div className="space-y-2">
              <Skeleton className="h-2.5 w-full" />
              <Skeleton className="h-2.5 w-5/6" />
              <Skeleton className="h-2.5 w-4/6" />
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
      <Badge variant="neutral" size="md">
        <Loader2 className="h-3 w-3 animate-spin" />
        Checking…
      </Badge>
    );
  }
  return (
    <Badge variant={connected ? "success" : "danger"} size="md" dot>
      {connected ? "Connected" : "Disconnected"}
    </Badge>
  );
}

function MasterCard({ title, icon: Icon, rows, loading, fields }) {
  return (
    <Card>
      <CardHeader
        title={
          <span className="inline-flex items-center gap-2">
            {Icon && <Icon className="h-3.5 w-3.5 text-ink-500" />}
            <span className="text-2xs font-semibold tracking-wider uppercase text-ink-500">
              {title}
            </span>
          </span>
        }
        action={
          loading ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin text-ink-400" />
          ) : (
            <Badge variant="neutral" size="sm">
              {rows.length}
            </Badge>
          )
        }
      />
      <div className="px-5 py-4">
        {loading ? (
          <div className="space-y-2">
            {[0, 1, 2, 3, 4].map((i) => (
              <Skeleton key={i} className="h-2.5" style={{ width: `${90 - i * 10}%` }} />
            ))}
          </div>
        ) : rows.length === 0 ? (
          <EmptyState
            size="sm"
            description="No records yet."
            className="py-4"
          />
        ) : (
          <div className="max-h-72 overflow-auto -mx-5 text-sm">
            <table className="w-full">
              <tbody>
                {rows.slice(0, 100).map((row, i) => (
                  <tr
                    key={row.list_id || i}
                    className="border-b border-ink-100 last:border-0 hover:bg-ink-50/60 transition-colors"
                  >
                    {fields.map((f, fi) => (
                      <td
                        key={f}
                        className={cn(
                          "px-3 py-1.5 truncate",
                          fi === 0
                            ? "text-ink-900 font-medium"
                            : "text-ink-600 tabular-nums"
                        )}
                      >
                        {row[f] ?? ""}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </Card>
  );
}
