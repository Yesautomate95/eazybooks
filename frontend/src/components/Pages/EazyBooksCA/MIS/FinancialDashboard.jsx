import React, { useState, useEffect } from 'react';
import {
  BarChart, Bar, LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend,
  ResponsiveContainer, PieChart, Pie, Cell
} from 'recharts';
import {
  DollarSign, TrendingUp, TrendingDown, ArrowUp, ArrowDown
} from 'lucide-react';
import { misApi } from '../../../../services/api';

function FinancialDashboard({ period = 'month' }) {
  const [loading, setLoading] = useState(true);
  const [summary, setSummary] = useState(null);
  const [monthlyTrends, setMonthlyTrends] = useState([]);
  const [costCenterData, setCostCenterData] = useState([]);

  const COLORS = ['#C5972C', '#4F9874', '#C25E4C', '#B08A2E', '#8A7F6A', '#71A98A'];

  useEffect(() => {
    const fetchData = async () => {
      setLoading(true);
      try {
        const [summaryRes, monthlyRes, costCenterRes] = await Promise.all([
          misApi.getFinancialSummary(period),
          misApi.getMonthlyTrends(12),
          misApi.getCostCenter()
        ]);

        setSummary(summaryRes.data.summary);
        setMonthlyTrends(monthlyRes.data);
        setCostCenterData(costCenterRes.data);
      } catch (error) {
        console.error("Failed to fetch financial data:", error);
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

  if (loading || !summary) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-gilt-brass"></div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Key Metrics Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="bg-gilt-card p-6 rounded-lg border border-gilt-hairline">
          <div className="flex justify-between items-start">
            <div>
              <p className="gl-eyebrow">Total Revenue</p>
              <h3 className="text-2xl font-mono tabular-nums font-bold text-gilt-ink mt-1 gl-double-rule">
                {formatCurrency(summary.revenue)}
              </h3>
            </div>
            <div className="p-2 bg-gilt-sunken border border-gilt-hairline rounded-lg" aria-hidden="true">
              <DollarSign className="text-gilt-credit" size={20} />
            </div>
          </div>
          <div className="flex items-center mt-4">
            <div className="flex items-center text-sm text-gilt-soft">
              <span className="ml-1 font-medium font-mono tabular-nums">{summary.transaction_count}</span>
            </div>
            <span className="text-gilt-faint text-sm ml-2">transactions</span>
          </div>
        </div>

        <div className="bg-gilt-card p-6 rounded-lg border border-gilt-hairline">
          <div className="flex justify-between items-start">
            <div>
              <p className="gl-eyebrow">Total Expenses</p>
              <h3 className="text-2xl font-mono tabular-nums font-bold text-gilt-ink mt-1 gl-double-rule">
                {formatCurrency(summary.expenses)}
              </h3>
            </div>
            <div className="p-2 bg-gilt-sunken border border-gilt-hairline rounded-lg" aria-hidden="true">
              <TrendingDown className="text-gilt-debit" size={20} />
            </div>
          </div>
          <div className="flex items-center mt-4">
            <div className="flex items-center text-sm text-gilt-soft">
              <span className="ml-1 font-medium font-mono tabular-nums">{summary.expense_ratio}%</span>
            </div>
            <span className="text-gilt-faint text-sm ml-2">of revenue</span>
          </div>
        </div>

        <div className="bg-gilt-card p-6 rounded-lg border border-gilt-hairline">
          <div className="flex justify-between items-start">
            <div>
              <p className="gl-eyebrow">Net Profit</p>
              <h3 className="text-2xl font-mono tabular-nums font-bold text-gilt-ink mt-1 gl-double-rule">
                {formatCurrency(summary.net_profit)}
              </h3>
            </div>
            <div className="p-2 bg-gilt-sunken border border-gilt-hairline rounded-lg" aria-hidden="true">
              <TrendingUp className="text-gilt-brass" size={20} />
            </div>
          </div>
          <div className="flex items-center mt-4">
            <div className="flex items-center text-sm text-gilt-credit">
              <span className="ml-1 font-medium font-mono tabular-nums">{((summary.net_profit / summary.revenue) * 100).toFixed(1)}%</span>
            </div>
            <span className="text-gilt-faint text-sm ml-2">profit margin</span>
          </div>
        </div>

        <div className="bg-gilt-card p-6 rounded-lg border border-gilt-hairline">
          <div className="flex justify-between items-start">
            <div>
              <p className="gl-eyebrow">Active Accounts</p>
              <h3 className="text-2xl font-mono tabular-nums font-bold text-gilt-ink mt-1">
                {summary.active_accounts}
              </h3>
            </div>
            <div className="p-2 bg-gilt-sunken border border-gilt-hairline rounded-lg" aria-hidden="true">
              <TrendingUp className="text-gilt-brass" size={20} />
            </div>
          </div>
          <div className="flex items-center mt-4">
            <div className="flex items-center text-sm text-gilt-brass">
              <span className="ml-1 font-medium font-mono tabular-nums">{summary.revenue_accounts}</span>
            </div>
            <span className="text-gilt-faint text-sm ml-2">revenue accounts</span>
          </div>
        </div>
      </div>

      {/* Monthly Trends Chart */}
      <div className="bg-gilt-card p-6 rounded-lg border border-gilt-hairline">
        <h3 className="text-lg font-semibold text-gilt-ink mb-4">Monthly Trends</h3>
        <div className="h-80">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart
              data={monthlyTrends}
              margin={{ top: 5, right: 30, left: 20, bottom: 5 }}
            >
              <CartesianGrid strokeDasharray="3 3" stroke="#A99C84" strokeOpacity={0.35} />
              <XAxis
                dataKey="period"
                stroke="#A99C84"
                tick={{ fill: '#8A7F6A' }}
              />
              <YAxis
                stroke="#A99C84"
                tick={{ fill: '#8A7F6A' }}
                tickFormatter={(value) => formatCurrency(value).replace('₹', '')}
              />
              <Tooltip
                formatter={(value) => [formatCurrency(value), 'Amount']}
                labelFormatter={(label) => `Period: ${label}`}
                contentStyle={{
                  backgroundColor: 'rgb(var(--gl-card))',
                  border: '1px solid rgb(var(--gl-hairline))',
                  borderRadius: '8px',
                  color: 'rgb(var(--gl-ink))'
                }}
                labelStyle={{ color: 'rgb(var(--gl-ink))' }}
              />
              <Legend wrapperStyle={{ color: 'rgb(var(--gl-ink-soft))' }} />
              <Line
                type="monotone"
                dataKey="revenue"
                stroke="#4F9874"
                name="Revenue"
                strokeWidth={2}
                dot={false}
              />
              <Line
                type="monotone"
                dataKey="expenses"
                stroke="#C25E4C"
                name="Expenses"
                strokeWidth={2}
                dot={false}
              />
              <Line
                type="monotone"
                dataKey="profit"
                stroke="#C5972C"
                name="Profit"
                strokeWidth={2}
                dot={false}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Cost Center Allocation */}
      <div className="bg-gilt-card p-6 rounded-lg border border-gilt-hairline">
        <h3 className="text-lg font-semibold text-gilt-ink mb-4">Cost Center Allocation</h3>
        <div className="h-80">
          {costCenterData.every(item => item.allocated_amount === 0) ? (
            <div className="flex items-center justify-center h-full">
              <p className="text-gilt-faint">No cost center allocations available</p>
            </div>
          ) : (
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie
                  data={costCenterData.filter(item => item.allocated_amount > 0)}
                  cx="50%"
                  cy="50%"
                  labelLine={false}
                  outerRadius={80}
                  fill="#C5972C"
                  dataKey="allocated_amount"
                  nameKey="cost_center"
                  label={({ cost_center, percent }) => `${cost_center}: ${(percent * 100).toFixed(0)}%`}
                >
                  {costCenterData.map((entry, index) => (
                    <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
                  ))}
                </Pie>
                <Tooltip
                  content={({ active, payload }) => {
                    if (active && payload && payload.length) {
                      const data = payload[0].payload;
                      return (
                        <div className="bg-gilt-card p-4 rounded border border-gilt-hairline">
                          <p className="font-medium text-gilt-ink">{data.cost_center}</p>
                          <p className="text-sm text-gilt-soft mt-1">Path: {data.full_path}</p>
                          <div className="mt-2">
                            <p className="text-sm text-gilt-soft">Amount: <span className="font-mono tabular-nums">{formatCurrency(data.allocated_amount)}</span></p>
                            <p className="text-sm text-gilt-soft">Transactions: <span className="font-mono tabular-nums">{data.transaction_count}</span></p>
                            <p className="text-sm text-gilt-soft">Unique Ledgers: <span className="font-mono tabular-nums">{data.unique_ledgers}</span></p>
                            <p className="text-sm text-gilt-soft">Status: {data.usage_status}</p>
                          </div>
                          {data.allocation_percentage > 0 && (
                            <p className="text-sm mt-1 text-gilt-soft">
                              Allocation: <span className="font-mono tabular-nums">{(data.allocation_percentage * 100).toFixed(1)}%</span>
                            </p>
                          )}
                        </div>
                      );
                    }
                    return null;
                  }}
                />
                <Legend wrapperStyle={{ color: 'rgb(var(--gl-ink-soft))' }} />
              </PieChart>
            </ResponsiveContainer>
          )}
        </div>
      </div>
    </div>
  );
}

export default FinancialDashboard;