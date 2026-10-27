import React, { useEffect, useState } from "react";
import {
  ArrowLeft, Landmark, RefreshCw, Search, AlertCircle, ChevronRight, Building2,
} from "lucide-react";
import { xeroApi, xeroBankReconApi } from "../../../../../services/api";

export default function XeroBankAccountSelect({ onBack, onSelectAccount }) {
  const [companies, setCompanies] = useState([]);
  const [company, setCompany] = useState(null); // tenant id (or name fallback)
  const [accounts, setAccounts] = useState([]);
  const [loadingCompanies, setLoadingCompanies] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [search, setSearch] = useState("");

  // Load companies (tenants) once
  useEffect(() => {
    let active = true;
    (async () => {
      setLoadingCompanies(true);
      setError(null);
      try {
        const res = await xeroApi.getCompanies();
        const list = res?.tenants || [];
        if (!active) return;
        setCompanies(list);
        if (list[0]) setCompany(list[0].tenant_id || list[0].name);
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
      const data = await xeroBankReconApi.getBankAccounts(company);
      setAccounts(data.accounts || []);
    } catch (err) {
      setError(err.response?.data?.detail || "Failed to fetch bank accounts from Xero.");
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
    <div className="min-h-screen bg-gray-50 text-gray-900">
      <div className="max-w-2xl mx-auto px-4 py-8">
        {/* Header */}
        <div className="flex items-center gap-3 mb-6">
          <button
            onClick={onBack}
            className="p-2 rounded-lg hover:bg-gray-200 transition-colors"
          >
            <ArrowLeft size={20} className="text-gray-600" />
          </button>
          <div>
            <h1 className="text-xl font-bold text-gray-900">Select Bank Account</h1>
            <p className="text-sm text-gray-500">Xero Bank Reconciliation</p>
          </div>
        </div>

        {/* Company selector */}
        <div className="mb-4">
          <label className="block text-xs font-medium text-gray-500 mb-1.5">
            <Building2 className="inline h-3.5 w-3.5 mr-1 text-gray-400" />
            Company
          </label>
          <select
            value={company || ""}
            onChange={(e) => setCompany(e.target.value)}
            disabled={loadingCompanies}
            className="w-full px-3 py-2.5 border border-gray-200 rounded-lg text-sm text-gray-900 bg-white focus:outline-none focus:ring-2 focus:ring-orange-500 focus:border-transparent disabled:opacity-60"
          >
            {companies.length === 0 && <option value="">No companies</option>}
            {companies.map((c) => {
              const v = c.tenant_id || c.name;
              return <option key={v} value={v}>{c.name}</option>;
            })}
          </select>
        </div>

        {/* Search */}
        <div className="relative mb-4">
          <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
          <input
            type="text"
            placeholder="Search accounts..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full pl-9 pr-4 py-2.5 border border-gray-200 rounded-lg text-sm text-gray-900 bg-white focus:outline-none focus:ring-2 focus:ring-orange-500 focus:border-transparent"
          />
        </div>

        {/* Content */}
        <div className="bg-white rounded-xl shadow-sm border border-gray-200">
          {loading || loadingCompanies ? (
            <div className="flex items-center justify-center py-16 gap-3">
              <RefreshCw size={20} className="text-orange-500 animate-spin" />
              <span className="text-gray-500 text-sm">Fetching bank accounts from Xero...</span>
            </div>
          ) : error ? (
            <div className="flex flex-col items-center justify-center py-16 gap-3 px-6 text-center">
              <AlertCircle size={40} className="text-red-400" />
              <p className="text-gray-700 font-medium">Could not load accounts</p>
              <p className="text-gray-500 text-sm">{error}</p>
              <button
                onClick={fetchAccounts}
                className="mt-2 px-4 py-2 bg-orange-500 text-white text-sm rounded-lg hover:bg-orange-600 transition-colors"
              >
                Retry
              </button>
            </div>
          ) : filtered.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-16 gap-3 text-center">
              <Landmark size={40} className="text-gray-300" />
              <p className="text-gray-600 font-medium">No bank accounts found</p>
              <p className="text-gray-500 text-sm">
                {search ? "Try a different search term." : "Ensure Xero has bank accounts configured for this company."}
              </p>
            </div>
          ) : (
            <ul className="divide-y divide-gray-100">
              {filtered.map((account) => {
                const sub = account.account_sub_type || account.account_type || "";
                return (
                  <li key={account.id}>
                    <button
                      onClick={() => onSelectAccount({ id: account.id, name: account.name }, company)}
                      className="w-full flex items-center justify-between px-5 py-4 hover:bg-orange-50 transition-colors text-left"
                    >
                      <div className="flex items-center gap-3">
                        <div className="w-9 h-9 rounded-lg bg-orange-100 flex items-center justify-center flex-shrink-0">
                          <Landmark size={16} className="text-orange-600" />
                        </div>
                        <div>
                          <p className="font-medium text-gray-900 text-sm">{account.name}</p>
                          <p className="text-xs text-gray-500 mt-0.5">
                            {sub}
                            {account.currency && <span className="ml-2">· {account.currency}</span>}
                          </p>
                        </div>
                      </div>
                      <ChevronRight size={16} className="text-gray-400" />
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
