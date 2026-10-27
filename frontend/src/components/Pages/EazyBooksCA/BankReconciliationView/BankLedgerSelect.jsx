import React, { useEffect, useState } from "react";
import { ArrowLeft, Landmark, RefreshCw, Search, AlertCircle, ChevronRight } from "lucide-react";
import { bankReconApi } from "../../../../services/api";

export default function BankLedgerSelect({ selectedCompany, onBack, onSelectLedger }) {
  const [ledgers, setLedgers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [search, setSearch] = useState("");

  const fetchLedgers = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await bankReconApi.getBankLedgers(selectedCompany);
      const allLedgers = (data.ledgers || []).map((l) => ({
        name: l.name || l,
        group: l.group || l.parent || "",
      }));
      const bankLedgers = allLedgers.filter(
        (l) =>
          l.group === "Bank Accounts" ||
          l.group === "Bank OD A/c" ||
          l.group === "Bank OCC A/c" ||
          l.name?.toLowerCase().includes("bank")
      );
      setLedgers(bankLedgers);
    } catch (err) {
      setError(err.response?.data?.error || "Failed to fetch bank ledgers from Tally.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchLedgers();
  }, [selectedCompany]);

  const filtered = ledgers.filter((l) =>
    l.name?.toLowerCase().includes(search.toLowerCase())
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
            <h1 className="gl-display text-xl text-gilt-ink">Select Bank Ledger</h1>
            <p className="text-sm text-gilt-soft">{selectedCompany}</p>
          </div>
        </div>

        {/* Search */}
        <div className="relative mb-4">
          <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-gilt-faint" />
          <input
            type="text"
            placeholder="Search ledgers..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full pl-9 pr-4 py-2.5 border border-gilt-hairline rounded-lg text-sm text-gilt-ink bg-gilt-card placeholder:text-gilt-faint focus:outline-none focus:border-gilt-brass transition-colors duration-150"
          />
        </div>

        {/* Content */}
        <div className="bg-gilt-card rounded-xl border border-gilt-hairline">
          {loading ? (
            <div className="flex items-center justify-center py-16 gap-3">
              <RefreshCw size={20} className="text-gilt-brass animate-spin" />
              <span className="text-gilt-soft text-sm">Fetching bank ledgers from Tally...</span>
            </div>
          ) : error ? (
            <div className="flex flex-col items-center justify-center py-16 gap-3 px-6 text-center">
              <AlertCircle size={40} className="text-gilt-debit" />
              <p className="text-gilt-ink font-medium">Could not load ledgers</p>
              <p className="text-gilt-soft text-sm">{error}</p>
              <button
                onClick={fetchLedgers}
                className="mt-2 px-4 py-2 bg-gilt-brass text-gilt-paper text-sm rounded-md hover:opacity-90 transition-opacity duration-150"
              >
                Retry
              </button>
            </div>
          ) : filtered.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-16 gap-3 text-center">
              <Landmark size={40} className="text-gilt-faint" />
              <p className="text-gilt-ink font-medium">No bank ledgers found</p>
              <p className="text-gilt-soft text-sm">
                {search ? "Try a different search term." : "Ensure Tally has bank accounts configured under 'Bank Accounts' group."}
              </p>
            </div>
          ) : (
            <ul className="divide-y divide-gilt-hairline">
              {filtered.map((ledger, i) => {
                const name = ledger.name || ledger;
                const group = ledger.group || "";
                return (
                  <li key={i}>
                    <button
                      onClick={() => onSelectLedger(name)}
                      className="w-full flex items-center justify-between px-5 py-4 hover:bg-gilt-sunken transition-colors duration-150 text-left"
                    >
                      <div className="flex items-center gap-3">
                        <div className="w-9 h-9 rounded-lg bg-gilt-sunken border border-gilt-hairline flex items-center justify-center flex-shrink-0">
                          <Landmark size={16} className="text-gilt-brass" />
                        </div>
                        <div>
                          <p className="font-medium text-gilt-ink text-sm">{name}</p>
                          {group && (
                            <p className="text-xs text-gilt-soft mt-0.5">{group}</p>
                          )}
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
