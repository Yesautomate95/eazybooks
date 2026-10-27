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
} from "lucide-react";
import { sapApi, sapOAuthApi } from "../../../../services/api";
import { errorText } from "../common/errorText";
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
 * SAP S/4HANA dashboard.
 *
 * SAP connects via OAuth — show "Connect to SAP S/4HANA" until the connection
 * completes, then list the company codes + masters from SAP's OData API. The
 * connect flow is dual-mode: authorization_code (browser redirect) or
 * client_credentials (server-to-server, no redirect).
 */
export default function SAPDashboard() {
  const { hasPermission } = usePermissions();
  const navigate = useNavigate();
  const canConnect = hasPermission("sap.connect");
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

  const oauthFlag = searchParams.get("sap");

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
        navigate(`${returnTo}${sep}sap=connected`, { replace: true });
        return undefined;
      }

      const next = new URLSearchParams(searchParams);
      next.delete("sap");
      next.delete("company_code");
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
      const status = await sapApi.checkConnection();
      setMode(status?.mode || null);
      setConnected(!!status?.connected);
      if (status?.connected) {
        await loadCompanies(status.mode);
      }
    } catch (err) {
      setError(errorText(err, "Connection check failed"));
      setConnected(false);
    } finally {
      setCheckingConnection(false);
    }
  };

  const loadCompanies = async () => {
    setLoadingCompanies(true);
    try {
      const res = await sapApi.getCompanies();
      const list = res?.organizations || [];
      setCompanies(list);
      if (list[0]) {
        const key = list[0].company_code;
        setActiveCompany(key);
        await loadMasters(key);
      }
    } catch (err) {
      setError(errorText(err, "Failed to load companies"));
    } finally {
      setLoadingCompanies(false);
    }
  };

  const loadMasters = async (companyKey) => {
    setLoading(true);
    setError("");
    try {
      const [a, c, v] = await Promise.all([
        sapApi.getAccounts(companyKey),
        sapApi.getCustomers(companyKey),
        sapApi.getVendors(companyKey),
      ]);
      setAccounts(a?.accounts || []);
      setCustomers(c?.customers || []);
      setVendors(v?.vendors || []);
    } catch (err) {
      setError(errorText(err, "Failed to load master data"));
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
      // Dual-mode: authorization_code returns an auth_url to redirect to;
      // client_credentials connects server-side and returns {connected:true}
      // with no URL — in that case just confirm and re-check the connection.
      const res = await sapOAuthApi.getAuthUrl();
      if (res?.auth_url) {
        window.location.href = res.auth_url;
        return;
      }
      // client_credentials / already-connected: no redirect needed.
      setConnecting(false);
      setShowConnectedToast(true);
      const t = setTimeout(() => setShowConnectedToast(false), 4000);
      await runConnectionCheck();
      return () => clearTimeout(t);
    } catch (err) {
      setConnecting(false);
      setError(errorText(err, "Could not start OAuth"));
    }
  };

  const onDisconnect = async () => {
    if (
      !window.confirm(
        "Disconnect SAP S/4HANA? You'll need to reconnect to access this company code."
      )
    )
      return;
    try {
      await sapOAuthApi.disconnect();
      setConnected(false);
      setCompanies([]);
      setAccounts([]);
      setCustomers([]);
      setVendors([]);
    } catch (err) {
      setError(errorText(err, "Disconnect failed"));
    }
  };

  const navTiles = [
    { to: "/dashboard/sap/sales", label: "Sales Invoices", icon: FileSignature },
    { to: "/dashboard/sap/purchase", label: "Purchase Bills", icon: Receipt },
    { to: "/dashboard/sap/bank-recon", label: "Bank Reconciliation", icon: Scale },
    { to: "/dashboard/sap/bank-entries", label: "Bank Entries", icon: Banknote },
  ];

  return (
    <div className="min-h-screen flex flex-col">
      <PageHeader
        icon={LayoutDashboard}
        className="bg-transparent border-gilt-hairline"
        title={<span className="gl-display text-gilt-ink">SAP S/4HANA</span>}
        description="Sync company codes, customers, vendors, and push entries directly to SAP."
        actions={
          <div className="flex items-center gap-2">
            <ConnectionBadge checking={checkingConnection} connected={connected} />
            {connected && (
              <PermissionGuard permission="sap.connect">
                <Button
                  variant="danger-outline"
                  size="sm"
                  className="bg-transparent text-gilt-debit border-gilt-hairline rounded-md shadow-none hover:bg-gilt-sunken hover:border-gilt-debit transition-colors duration-200"
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
          <Card className="bg-gilt-card border-gilt-hairline shadow-none">
            <div className="flex items-center gap-2 px-4 py-2.5 text-sm text-gilt-ink">
              <CheckCircle2 className="h-4 w-4 text-gilt-credit" />
              <span className="font-medium text-gilt-credit">Connected to SAP S/4HANA.</span>
              <span className="text-gilt-soft">Loading your company data…</span>
            </div>
          </Card>
        )}
        {oauthFlag === "error" && (
          <Card className="bg-gilt-card border-gilt-hairline shadow-none">
            <div className="flex items-center gap-2 px-4 py-2.5 text-sm text-gilt-soft">
              <AlertTriangle className="h-4 w-4 text-gilt-brass" />
              SAP S/4HANA authorization was cancelled or failed. Try connecting again.
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
            {/* Company-code picker + module nav */}
            <Card padded className="bg-gilt-card border-gilt-hairline shadow-none">
              <div className="flex items-center gap-4 flex-wrap">
                <div className="flex items-center gap-2">
                  <Building2 className="h-4 w-4 text-gilt-brass" />
                  <label className="font-mono text-[10px] font-medium uppercase tracking-[0.14em] text-gilt-brass">Company code</label>
                </div>
                <div className="min-w-[280px]">
                  <Select
                    value={activeCompany}
                    onChange={onCompanyChange}
                    className="bg-gilt-card text-gilt-ink border-gilt-hairline rounded-md focus:border-gilt-brass focus:shadow-none disabled:bg-gilt-sunken disabled:text-gilt-faint transition-colors duration-200"
                    disabled={loadingCompanies || !companies.length}
                  >
                    {loadingCompanies && <option value="">Loading company codes…</option>}
                    {!loadingCompanies && !companies.length && (
                      <option value="">No company codes found</option>
                    )}
                    {companies.map((c) => (
                      <option key={c.company_code} value={c.company_code}>
                        {c.name}
                      </option>
                    ))}
                  </Select>
                </div>
                {(loadingCompanies || loading) && (
                  <span className="inline-flex items-center gap-1.5 text-xs text-gilt-faint">
                    <Loader2 className="h-3 w-3 animate-spin" />
                    {loadingCompanies ? "Loading company codes…" : "Loading data…"}
                  </span>
                )}
              </div>
              <div className="mt-4 grid grid-cols-2 md:grid-cols-4 gap-3">
                {navTiles.map((tile, idx) => (
                  <Link
                    key={tile.to}
                    to={tile.to}
                    className="group flex items-center justify-between gap-2 px-3.5 py-3 rounded-lg border border-gilt-hairline bg-gilt-card hover:border-gilt-brass transition-colors duration-150"
                  >
                    <span className="min-w-0">
                      <span
                        aria-hidden="true"
                        className="block font-mono text-[10px] uppercase tracking-[0.14em] text-gilt-brass mb-1"
                      >
                        № {String(idx + 1).padStart(2, "0")}
                      </span>
                      <span className="inline-flex items-center gap-2.5 text-sm font-medium text-gilt-soft group-hover:text-gilt-ink transition-colors duration-150">
                        <tile.icon className="h-4 w-4 text-gilt-brass" />
                        {tile.label}
                      </span>
                    </span>
                    <ArrowRight className="h-4 w-4 text-gilt-faint group-hover:text-gilt-brass transition-colors duration-150" />
                  </Link>
                ))}
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
    <Card padded className="max-w-xl bg-gilt-card border-gilt-hairline shadow-none">
      <div className="flex items-start gap-3">
        <div className="shrink-0 h-10 w-10 rounded-lg bg-gilt-sunken border border-gilt-hairline text-gilt-brass flex items-center justify-center">
          <Plug className="h-5 w-5" strokeWidth={1.75} />
        </div>
        <div className="min-w-0">
          <h2 className="text-base font-semibold text-gilt-ink">Connect to SAP S/4HANA</h2>
          <p className="mt-1 text-sm text-gilt-soft leading-relaxed">
            Authorize EazyBooks to read your SAP S/4HANA customers, vendors, and GL accounts, and to
            post invoices and bills on your behalf. You can revoke access at any time from your SAP
            communication arrangement.
          </p>
          <div className="mt-4">
            {canConnect ? (
              <Button
                variant="primary"
                size="md"
                className="bg-gilt-brass text-gilt-paper rounded-md shadow-none hover:bg-gilt-brass/90 active:bg-gilt-ink focus-visible:ring-gilt-brass transition-colors duration-200"
                onClick={onConnect}
                loading={connecting}
                leftIcon={!connecting ? <Plug /> : undefined}
              >
                {connecting ? "Connecting to SAP…" : "Connect to SAP S/4HANA"}
              </Button>
            ) : (
              <p className="text-xs text-gilt-soft bg-gilt-sunken border border-gilt-hairline rounded-md px-3 py-2 inline-block">
                You don't have permission to connect SAP. Ask an administrator for{" "}
                <code className="font-mono text-gilt-brass">sap.connect</code>.
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
            Checking SAP S/4HANA connection…
          </div>
          <div className="text-xs text-gilt-soft">Verifying your authorization with SAP.</div>
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

function MasterCard({ title, icon: Icon, rows, loading, fields }) {
  return (
    <Card className="bg-gilt-card border-gilt-hairline shadow-none">
      <CardHeader
        className="border-gilt-hairline"
        title={
          <span className="inline-flex items-center gap-2">
            {Icon && <Icon className="h-3.5 w-3.5 text-gilt-brass" aria-hidden="true" />}
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
            >
              {rows.length}
            </Badge>
          )
        }
      />
      <div className="px-5 py-4">
        {loading ? (
          <div className="space-y-2">
            {[0, 1, 2, 3, 4].map((i) => (
              <Skeleton key={i} className="h-2.5 from-gilt-sunken via-gilt-hairline/70 to-gilt-sunken" style={{ width: `${90 - i * 10}%` }} />
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
                    className="border-b border-gilt-hairline last:border-0 hover:bg-gilt-sunken transition-colors duration-150"
                  >
                    {fields.map((f, fi) => (
                      <td
                        key={f}
                        className={cn(
                          "px-3 py-1.5 truncate",
                          fi === 0
                            ? "text-gilt-ink font-medium"
                            : "text-gilt-soft font-mono tabular-nums"
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
