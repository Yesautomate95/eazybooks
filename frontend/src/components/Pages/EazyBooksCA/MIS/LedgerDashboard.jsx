import React, { useState, useEffect } from 'react';
import {
  BarChart, Bar, LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend,
  ResponsiveContainer, PieChart, Pie, Cell
} from 'recharts';
import {
  CreditCard, TrendingUp, Search, ChevronDown, ChevronUp
} from 'lucide-react';
import { misApi } from '../../../../services/api';

function LedgerDashboard({ period = 'month' }) {
  const [loading, setLoading] = useState(true);
  const [ledgers, setLedgers] = useState([]);
  const [gstData, setGstData] = useState(null);
  const [searchTerm, setSearchTerm] = useState('');
  const [activeTab, setActiveTab] = useState('all');
  const [sortBy, setSortBy] = useState('balance');
  const [sortOrder, setSortOrder] = useState('desc');
  const [expandedLedger, setExpandedLedger] = useState(null);

  const COLORS = ['#C5972C', '#4F9874', '#C25E4C', '#B08A2E', '#8A7F6A', '#71A98A'];

  useEffect(() => {
    const fetchData = async () => {
      setLoading(true);
      try {
        const [ledgersRes, gstRes] = await Promise.all([
          misApi.getLedgers(),
          misApi.getGstSummary()
        ]);

        setLedgers(ledgersRes.data);
        setGstData(gstRes.data);
      } catch (error) {
        console.error("Failed to fetch ledger data:", error);
      } finally {
        setLoading(false);
      }
    };

    fetchData();
  }, [period]);

  const formatCurrency = (value) => {
    return new Intl.NumberFormat('en-IN', {
      style: 'currency',
      currency: 'INR',
      maximumFractionDigits: 0,
      notation: 'compact',
      compactDisplay: 'short'
    }).format(value);
  };

  // Filter and sort ledgers for the table
  const filteredLedgers = ledgers
    .filter(ledger =>
      (activeTab === 'all' || ledger.primary_group.toLowerCase().includes(activeTab)) &&
      ledger.ledger_name.toLowerCase().includes(searchTerm.toLowerCase())
    )
    .sort((a, b) => {
      const valueA = sortBy === 'ledger_name' ? a[sortBy] : Math.abs(a.balance);
      const valueB = sortBy === 'ledger_name' ? b[sortBy] : Math.abs(b.balance);

      if (sortBy === 'ledger_name') {
        return sortOrder === 'asc'
          ? valueA.localeCompare(valueB)
          : valueB.localeCompare(valueA);
      }

      return sortOrder === 'asc' ? valueA - valueB : valueB - valueA;
    });

  // Group ledgers by type for summary charts
  const ledgersByType = ledgers.reduce((acc, ledger) => {
    const type = ledger.primary_group.toLowerCase();
    if (!acc[type]) {
      acc[type] = {
        type,
        totalBalance: 0,
        count: 0
      };
    }
    acc[type].totalBalance += Math.abs(ledger.balance);
    acc[type].count += 1;
    return acc;
  }, {});

  const ledgerTypeSummary = Object.values(ledgersByType)
    .sort((a, b) => b.totalBalance - a.totalBalance)
    .slice(0, 6); // Show only top 6 categories for cleaner visualization

  // Add a simplified color scheme
  const CHART_COLORS = ['#C5972C', '#4F9874', '#C25E4C', '#B08A2E', '#8A7F6A', '#71A98A'];

  // Handle sort changes
  const handleSort = (field) => {
    if (sortBy === field) {
      setSortOrder(sortOrder === 'asc' ? 'desc' : 'asc');
    } else {
      setSortBy(field);
      setSortOrder('desc');
    }
  };

  // Toggle expanded ledger
  const toggleExpandLedger = (name) => {
    setExpandedLedger(expandedLedger === name ? null : name);
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-gilt-brass"></div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Key Metrics Cards - Simplified layout */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="bg-gilt-card p-6 rounded-lg border border-gilt-hairline">
          <div className="flex items-center justify-between mb-4">
            <h3 className="gl-eyebrow">Total Ledgers</h3>
            <div className="p-2 bg-gilt-sunken border border-gilt-hairline rounded-lg" aria-hidden="true">
              <CreditCard className="text-gilt-brass h-5 w-5" />
            </div>
          </div>
          <div className="flex items-baseline">
            <p className="text-2xl font-semibold font-mono tabular-nums text-gilt-ink">{ledgers.length}</p>
            <p className="ml-2 text-sm text-gilt-faint">accounts</p>
          </div>
        </div>

        {gstData && (
          <>
            <div className="bg-gilt-card p-6 rounded-lg border border-gilt-hairline">
              <div className="flex items-center justify-between mb-4">
                <h3 className="gl-eyebrow">Input GST</h3>
                <div className="p-2 bg-gilt-sunken border border-gilt-hairline rounded-lg" aria-hidden="true">
                  <TrendingUp className="text-gilt-credit h-5 w-5" />
                </div>
              </div>
              <div className="flex items-baseline">
                <p className="text-2xl font-semibold font-mono tabular-nums text-gilt-ink">{formatCurrency(gstData.input_gst)}</p>
                <p className="ml-2 text-sm text-gilt-faint">{gstData.input_transaction_days} days</p>
              </div>
            </div>

            <div className="bg-gilt-card p-6 rounded-lg border border-gilt-hairline">
              <div className="flex items-center justify-between mb-4">
                <h3 className="gl-eyebrow">Output GST</h3>
                <div className="p-2 bg-gilt-sunken border border-gilt-hairline rounded-lg" aria-hidden="true">
                  <TrendingUp className="text-gilt-brass h-5 w-5" />
                </div>
              </div>
              <div className="flex items-baseline">
                <p className="text-2xl font-semibold font-mono tabular-nums text-gilt-ink">{formatCurrency(gstData.output_gst)}</p>
                <p className="ml-2 text-sm text-gilt-faint">{gstData.output_transaction_days} days</p>
              </div>
            </div>

            <div className="bg-gilt-card p-6 rounded-lg border border-gilt-hairline">
              <div className="flex items-center justify-between mb-4">
                <h3 className="gl-eyebrow">GST Payable</h3>
                <div className="p-2 bg-gilt-sunken border border-gilt-hairline rounded-lg" aria-hidden="true">
                  <TrendingUp className="text-gilt-brass h-5 w-5" />
                </div>
              </div>
              <div className="flex items-baseline">
                <p className="text-2xl font-semibold font-mono tabular-nums text-gilt-ink gl-double-rule">
                  {formatCurrency(gstData.output_gst - gstData.input_gst)}
                </p>
                <p className="ml-2 text-sm text-gilt-faint">{gstData.active_months} months</p>
              </div>
            </div>
          </>
        )}
      </div>

      {/* Search and Filter Section - Simplified */}
      <div className="bg-gilt-card rounded-lg border border-gilt-hairline">
        <div className="p-4">
          <div className="flex flex-col sm:flex-row gap-4">
            <div className="flex-1 relative">
              <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 text-gilt-faint h-5 w-5" />
              <input
                type="text"
                placeholder="Search ledgers..."
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                className="w-full pl-10 pr-4 py-2 bg-gilt-paper border border-gilt-hairline rounded-lg placeholder:text-gilt-faint text-gilt-ink focus:outline-none focus:border-gilt-brass transition-colors duration-150"
              />
            </div>
            <div className="flex space-x-2">
              {['all', 'asset', 'liability', 'revenue', 'expense'].map((tab) => (
                <button
                  key={tab}
                  onClick={() => setActiveTab(tab)}
                  className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors duration-150 ${
                    activeTab === tab
                      ? 'bg-gilt-sunken text-gilt-brass'
                      : 'text-gilt-soft hover:bg-gilt-sunken'
                  }`}
                >
                  {tab.charAt(0).toUpperCase() + tab.slice(1)}
                </button>
              ))}
            </div>
          </div>
        </div>
      </div>

      {/* Distribution Chart and Table Layout */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Ledger Type Distribution - Simplified */}
        <div className="bg-gilt-card p-6 rounded-lg border border-gilt-hairline">
          <h3 className="text-lg font-semibold text-gilt-ink mb-4">Ledger Distribution</h3>
          <div className="h-80">
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie
                  data={ledgerTypeSummary}
                  cx="50%"
                  cy="50%"
                  labelLine={false}
                  outerRadius={80}
                  fill="#C5972C"
                  dataKey="totalBalance"
                  nameKey="type"
                  label={({ type, percent }) =>
                    `${type.charAt(0).toUpperCase() + type.slice(1)}: ${(percent * 100).toFixed(0)}%`
                  }
                >
                  {ledgerTypeSummary.map((entry, index) => (
                    <Cell key={`cell-${index}`} fill={CHART_COLORS[index % CHART_COLORS.length]} />
                  ))}
                </Pie>
                <Tooltip
                  content={({ active, payload }) => {
                    if (active && payload && payload.length) {
                      const data = payload[0].payload;
                      return (
                        <div className="bg-gilt-card p-3 rounded border border-gilt-hairline shadow-sm">
                          <p className="font-medium text-gilt-ink">{data.type.charAt(0).toUpperCase() + data.type.slice(1)}</p>
                          <p className="text-sm text-gilt-soft mt-1">
                            Total: <span className="font-mono tabular-nums">{formatCurrency(data.totalBalance)}</span>
                          </p>
                          <p className="text-sm text-gilt-soft">
                            Count: <span className="font-mono tabular-nums">{data.count}</span> ledgers
                          </p>
                        </div>
                      );
                    }
                    return null;
                  }}
                />
              </PieChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* Ledger Table - Simplified */}
        <div className="bg-gilt-card rounded-lg border border-gilt-hairline">
          <div className="p-6">
            <h3 className="text-lg font-semibold text-gilt-ink mb-4">Ledger Details</h3>
            <div className="overflow-x-auto">
              <table className="min-w-full">
                <thead>
                  <tr className="border-b border-gilt-hairline">
                    <th className="text-left py-3 px-4 font-mono text-[10px] uppercase tracking-wider font-medium text-gilt-soft">
                      <button
                        onClick={() => handleSort('ledger_name')}
                        className="flex items-center space-x-1 hover:text-gilt-ink transition-colors duration-150"
                      >
                        <span>Name</span>
                      </button>
                    </th>
                    <th className="text-left py-3 px-4 font-mono text-[10px] uppercase tracking-wider font-medium text-gilt-soft">Group</th>
                    <th className="text-right py-3 px-4 font-mono text-[10px] uppercase tracking-wider font-medium text-gilt-soft">
                      <button
                        onClick={() => handleSort('balance')}
                        className="flex items-center justify-end space-x-1 hover:text-gilt-ink transition-colors duration-150"
                      >
                        <span>Balance</span>
                      </button>
                    </th>
                    <th className="w-10"></th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gilt-hairline">
                  {filteredLedgers.map((ledger) => (
                    <React.Fragment key={ledger.ledger_name}>
                      <tr className="hover:bg-gilt-sunken transition-colors duration-150">
                        <td className="py-3 px-4 text-sm text-gilt-ink">{ledger.ledger_name}</td>
                        <td className="py-3 px-4 text-sm text-gilt-soft">{ledger.group_name}</td>
                        <td className="py-3 px-4 text-sm text-right text-gilt-ink font-mono tabular-nums">
                          {formatCurrency(ledger.balance)}
                        </td>
                        <td className="py-3 px-4 text-right">
                          <button
                            onClick={() => toggleExpandLedger(ledger.ledger_name)}
                            className="text-gilt-faint hover:text-gilt-soft transition-colors duration-150"
                          >
                            {expandedLedger === ledger.ledger_name ? (
                              <ChevronUp className="h-5 w-5" />
                            ) : (
                              <ChevronDown className="h-5 w-5" />
                            )}
                          </button>
                        </td>
                      </tr>
                      {expandedLedger === ledger.ledger_name && (
                        <tr className="bg-gilt-sunken">
                          <td colSpan="4" className="px-4 py-3">
                            <div className="grid grid-cols-2 gap-4 text-sm">
                              <div>
                                <p className="text-gilt-faint">Transactions</p>
                                <p className="font-medium font-mono tabular-nums text-gilt-ink">{ledger.transaction_count}</p>
                              </div>
                              <div>
                                <p className="text-gilt-faint">First Transaction</p>
                                <p className="font-medium font-mono tabular-nums text-gilt-ink">
                                  {new Date(ledger.first_transaction).toLocaleDateString()}
                                </p>
                              </div>
                              <div>
                                <p className="text-gilt-faint">Last Transaction</p>
                                <p className="font-medium font-mono tabular-nums text-gilt-ink">
                                  {new Date(ledger.last_transaction).toLocaleDateString()}
                                </p>
                              </div>
                              <div>
                                <p className="text-gilt-faint">Primary Group</p>
                                <p className="font-medium text-gilt-ink">{ledger.primary_group}</p>
                              </div>
                            </div>
                          </td>
                        </tr>
                      )}
                    </React.Fragment>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export default LedgerDashboard;