import React, { useEffect, useState } from "react";
import {
  ArrowLeft, Landmark, RefreshCw, Search, AlertCircle, ChevronRight, Building2,
} from "lucide-react";
import { qbApi, qbBankReconApi } from "../../../../../services/api";

export default function QBBankAccountSelect({ onBack, onSelectAccount }) {
  const [companies, setCompanies] = useState([]);
  const [company, setCompany] = useState(null); // realm id (or name fallback)
  const [accounts, setAccounts] = useState([]);
  const [loadingCompanies, setLoadingCompanies] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [search, setSearch] = useState("");

  // Load companies (realms) once
  useEffect(() => {
    let active = true;
    (async () => {
      setLoadingCompanies(true);
      setError(null);
      try {
        const res = await qbApi.getCompanies();
        const list = res?.companies || [];
        if (!active) return;
        setCompanies(list);
        if (list[0]) setCompany(list[0].realm_id || list[0].name);
      } catch (err) {
        if (active) setError(err?.response?.data?.detail || "Failed to load companies.");
      } finally {
        if (active) setLoadingCompanies(false);
      }
    })();
    return () => { active = false; };
  }, []);

  const fetchAccounts = async () => {
    if (!company) return;
    setLoading(true);
    setError(null);
    try {
      const data = await qbBankReconApi.getBankAccounts(company);
      setAccounts(data.accounts || []);
    } catch (err) {
      setError(err.response?.data?.detail || "Failed to fetch bank accounts from QuickBooks.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchAccounts();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [company]);

  const filtered = accounts.filter((a) =>
    (a.name || "").toLowerCase().includes(search.toLowerCase())
  );

  return (
    <div className="min-h-screen text-gilt-ink">
      <div className="max-w-2xl mx-auto px-4 py-8">
        {/* Header */}
        <div className="flex items-center gap-3 mb-6">
          <button
            onClick={onBack}
            className="p-2 rounded-lg hover:bg-gilt-sunken transition-colors duration-150"
          >
            <ArrowLeft size={20} className="text-gilt-soft" />
          </button>
          <div>
            <h1 className="gl-display text-xl text-gilt-ink">Select Bank Account</h1>
            <p className="text-sm text-gilt-soft">QuickBooks Bank Reconciliation</p>
          </div>
        </div>

        {/* Company selector */}
        <div className="mb-4">
          <label className="block text-xs font-medium text-gilt-soft mb-1.5">
            <Building2 className="inline h-3.5 w-3.5 mr-1 text-gilt-faint" />
            Company
          </label>
          <select
            value={company || ""}
            onChange={(e) => setCompany(e.target.value)}
            disabled={loadingCompanies}
            className="w-full px-3 py-2.5 border border-gilt-hairline rounded-lg text-sm text-gilt-ink bg-gilt-card focus:outline-none focus:border-gilt-brass transition-colors duration-150 disabled:opacity-60"
          >
            {companies.length === 0 && <option value="">No companies</option>}
            {companies.map((c) => {
              const v = c.realm_id || c.name;
              return <option key={v} value={v}>{c.name}</option>;
            })}
          </select>
        </div>

        {/* Search */}
        <div className="relative mb-4">
          <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-gilt-faint" />
          <input
            type="text"
            placeholder="Search accounts..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full pl-9 pr-4 py-2.5 border border-gilt-hairline rounded-lg text-sm text-gilt-ink bg-gilt-card placeholder:text-gilt-faint focus:outline-none focus:border-gilt-brass transition-colors duration-150"
          />
        </div>

        {/* Content */}
        <div className="bg-gilt-card rounded-xl border border-gilt-hairline">
          {loading || loadingCompanies ? (
            <div className="flex items-center justify-center py-16 gap-3">
              <RefreshCw size={20} className="text-gilt-brass animate-spin" />
              <span className="text-gilt-soft text-sm">Fetching bank accounts from QuickBooks...</span>
            </div>
          ) : error ? (
            <div className="flex flex-col items-center justify-center py-16 gap-3 px-6 text-center">
              <AlertCircle size={40} className="text-gilt-debit" />
              <p className="text-gilt-ink font-medium">Could not load accounts</p>
              <p className="text-gilt-soft text-sm">{error}</p>
              <button
                onClick={fetchAccounts}
                className="mt-2 px-4 py-2 bg-gilt-brass text-gilt-paper text-sm rounded-md hover:opacity-90 transition-opacity duration-150"
              >
                Retry
              </button>
            </div>
          ) : filtered.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-16 gap-3 text-center">
              <Landmark size={40} className="text-gilt-faint" />
              <p className="text-gilt-ink font-medium">No bank accounts found</p>
              <p className="text-gilt-soft text-sm">
                {search ? "Try a different search term." : "Ensure QuickBooks has bank accounts configured for this company."}
              </p>
            </div>
          ) : (
            <ul className="divide-y divide-gilt-hairline">
              {filtered.map((account) => {
                const sub = account.account_sub_type || account.account_type || "";
                return (
                  <li key={account.id}>
                    <button
                      onClick={() => onSelectAccount({ id: account.id, name: account.name }, company)}
                      className="w-full flex items-center justify-between px-5 py-4 hover:bg-gilt-sunken transition-colors duration-150 text-left"
                    >
                      <div className="flex items-center gap-3">
                        <div className="w-9 h-9 rounded-lg bg-gilt-sunken border border-gilt-hairline flex items-center justify-center flex-shrink-0">
                          <Landmark size={16} className="text-gilt-brass" />
                        </div>
                        <div>
                          <p className="font-medium text-gilt-ink text-sm">{account.name}</p>
                          <p className="text-xs text-gilt-soft mt-0.5">
                            {sub}
                            {account.currency && <span className="ml-2">· {account.currency}</span>}
                          </p>
                        </div>
                      </div>
                      <ChevronRight size={16} className="text-gilt-faint" />
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}
