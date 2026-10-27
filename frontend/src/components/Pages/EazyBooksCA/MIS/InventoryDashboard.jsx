import React, { useState, useEffect } from 'react';
import {
  BarChart, Bar, LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend,
  ResponsiveContainer, PieChart, Pie, Cell, Treemap
} from 'recharts';
import {
  Package, AlertCircle, CheckCircle, ShoppingCart, TrendingUp, Search
} from 'lucide-react';
import { misApi } from '../../../../services/api';

function InventoryDashboard({ period = 'month' }) {
  const [loading, setLoading] = useState(true);
  const [inventory, setInventory] = useState([]);
  const [searchTerm, setSearchTerm] = useState('');
  const [sortBy, setSortBy] = useState('value');
  const [sortOrder, setSortOrder] = useState('desc');

  const COLORS = ['#C5972C', '#4F9874', '#C25E4C', '#B08A2E', '#8A7F6A', '#71A98A'];

  useEffect(() => {
    const fetchData = async () => {
      setLoading(true);
      try {
        const data = await misApi.getInventory();
        setInventory(data.data);
      } catch (error) {
        console.error("Failed to fetch inventory data:", error);
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

  // Calculate aggregated data
  const totalValue = inventory.reduce((sum, item) => sum + (item.quantity * item.unit_rate), 0);
  const totalItems = inventory.reduce((sum, item) => sum + item.quantity, 0);

  // Group by category
  const categorySummary = inventory.reduce((acc, item) => {
    if (!acc[item.category]) {
      acc[item.category] = {
        category: item.category,
        value: 0,
        quantity: 0,
        items: 0
      };
    }
    acc[item.category].value += (item.quantity * item.unit_rate);
    acc[item.category].quantity += item.quantity;
    acc[item.category].items += 1;
    return acc;
  }, {});

  const categoryData = Object.values(categorySummary);

  // Filter and sort data for the table
  const filteredInventory = inventory
    .filter(item =>
      item.item_name.toLowerCase().includes(searchTerm.toLowerCase()) ||
      item.category.toLowerCase().includes(searchTerm.toLowerCase())
    )
    .sort((a, b) => {
      const valueA = sortBy === 'item_name' ? a[sortBy] :
                    sortBy === 'value' ? (a.quantity * a.unit_rate) : a[sortBy];
      const valueB = sortBy === 'item_name' ? b[sortBy] :
                    sortBy === 'value' ? (b.quantity * b.unit_rate) : b[sortBy];

      if (sortBy === 'item_name') {
        return sortOrder === 'asc' ? valueA.localeCompare(valueB) : valueB.localeCompare(valueA);
      }
      return sortOrder === 'asc' ? valueA - valueB : valueB - valueA;
    });

  // Data for treemap
  const treemapData = inventory.map(item => ({
    name: item.item_name,
    size: item.quantity * item.unit_rate,
    category: item.category
  }));

  // Handler for sort changes
  const handleSort = (field) => {
    if (sortBy === field) {
      setSortOrder(sortOrder === 'asc' ? 'desc' : 'asc');
    } else {
      setSortBy(field);
      setSortOrder('desc');
    }
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
      {/* Key Metrics Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="bg-gilt-card p-6 rounded-lg border border-gilt-hairline">
          <div className="flex justify-between items-start">
            <div>
              <p className="gl-eyebrow">Total Inventory Value</p>
              <h3 className="text-2xl font-mono tabular-nums font-bold text-gilt-ink mt-1 gl-double-rule">
                {formatCurrency(totalValue)}
              </h3>
            </div>
            <div className="p-2 bg-gilt-sunken border border-gilt-hairline rounded-lg" aria-hidden="true">
              <Package className="text-gilt-brass" size={20} />
            </div>
          </div>
          <div className="flex items-center mt-4">
            <div className="flex items-center text-sm text-gilt-brass">
              <span className="ml-1 font-medium font-mono tabular-nums">{inventory.length}</span>
            </div>
            <span className="text-gilt-faint text-sm ml-2">unique products</span>
          </div>
        </div>

        <div className="bg-gilt-card p-6 rounded-lg border border-gilt-hairline">
          <div className="flex justify-between items-start">
            <div>
              <p className="gl-eyebrow">Total Quantity</p>
              <h3 className="text-2xl font-mono tabular-nums font-bold text-gilt-ink mt-1">
                {totalItems}
              </h3>
            </div>
            <div className="p-2 bg-gilt-sunken border border-gilt-hairline rounded-lg" aria-hidden="true">
              <CheckCircle className="text-gilt-credit" size={20} />
            </div>
          </div>
          <div className="flex items-center mt-4">
            <div className="flex items-center text-sm text-gilt-credit">
              <span className="ml-1 font-medium">In Stock</span>
            </div>
            <span className="text-gilt-faint text-sm ml-2">across all items</span>
          </div>
        </div>

        <div className="bg-gilt-card p-6 rounded-lg border border-gilt-hairline">
          <div className="flex justify-between items-start">
            <div>
              <p className="gl-eyebrow">Categories</p>
              <h3 className="text-2xl font-mono tabular-nums font-bold text-gilt-ink mt-1">
                {Object.keys(categorySummary).length}
              </h3>
            </div>
            <div className="p-2 bg-gilt-sunken border border-gilt-hairline rounded-lg" aria-hidden="true">
              <Package className="text-gilt-brass" size={20} />
            </div>
          </div>
          <div className="flex items-center mt-4">
            <div className="flex items-center text-sm text-gilt-brass">
              <span className="ml-1 font-medium">Active</span>
            </div>
            <span className="text-gilt-faint text-sm ml-2">product categories</span>
          </div>
        </div>

        <div className="bg-gilt-card p-6 rounded-lg border border-gilt-hairline">
          <div className="flex justify-between items-start">
            <div>
              <p className="gl-eyebrow">Average Value</p>
              <h3 className="text-2xl font-mono tabular-nums font-bold text-gilt-ink mt-1 gl-double-rule">
                {formatCurrency(totalValue / inventory.length)}
              </h3>
            </div>
            <div className="p-2 bg-gilt-sunken border border-gilt-hairline rounded-lg" aria-hidden="true">
              <TrendingUp className="text-gilt-brass" size={20} />
            </div>
          </div>
          <div className="flex items-center mt-4">
            <div className="flex items-center text-sm text-gilt-brass">
              <span className="ml-1 font-medium">Per Item</span>
            </div>
            <span className="text-gilt-faint text-sm ml-2">average value</span>
          </div>
        </div>
      </div>

      {/* Search Bar */}
      <div className="bg-gilt-card p-4 rounded-lg border border-gilt-hairline">
        <div className="relative">
          <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 text-gilt-faint" size={20} />
          <input
            type="text"
            placeholder="Search inventory items..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            className="w-full pl-10 pr-4 py-2 bg-gilt-paper border border-gilt-hairline rounded-lg placeholder:text-gilt-faint text-gilt-ink focus:outline-none focus:border-gilt-brass transition-colors duration-150"
          />
        </div>
      </div>

      {/* Inventory Table */}
      <div className="bg-gilt-card rounded-lg border border-gilt-hairline overflow-hidden">
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-gilt-hairline">
            <thead className="bg-gilt-sunken">
              <tr>
                <th
                  className="px-6 py-3 text-left font-mono text-[10px] font-medium text-gilt-soft uppercase tracking-wider cursor-pointer"
                  onClick={() => handleSort('item_name')}
                >
                  Item Name
                </th>
                <th
                  className="px-6 py-3 text-left font-mono text-[10px] font-medium text-gilt-soft uppercase tracking-wider cursor-pointer"
                  onClick={() => handleSort('category')}
                >
                  Category
                </th>
                <th
                  className="px-6 py-3 text-left font-mono text-[10px] font-medium text-gilt-soft uppercase tracking-wider cursor-pointer"
                  onClick={() => handleSort('quantity')}
                >
                  Quantity
                </th>
                <th
                  className="px-6 py-3 text-left font-mono text-[10px] font-medium text-gilt-soft uppercase tracking-wider cursor-pointer"
                  onClick={() => handleSort('unit_rate')}
                >
                  Unit Rate
                </th>
                <th
                  className="px-6 py-3 text-left font-mono text-[10px] font-medium text-gilt-soft uppercase tracking-wider cursor-pointer"
                  onClick={() => handleSort('value')}
                >
                  Total Value
                </th>
                <th className="px-6 py-3 text-left font-mono text-[10px] font-medium text-gilt-soft uppercase tracking-wider">
                  Status
                </th>
              </tr>
            </thead>
            <tbody className="bg-gilt-card divide-y divide-gilt-hairline">
              {filteredInventory.map((item, index) => (
                <tr key={index} className="hover:bg-gilt-sunken transition-colors duration-150">
                  <td className="px-6 py-4 whitespace-nowrap text-sm font-medium text-gilt-ink">
                    {item.item_name}
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm text-gilt-soft">
                    {item.category}
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm text-gilt-soft font-mono tabular-nums">
                    {item.quantity}
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm text-gilt-soft font-mono tabular-nums">
                    {formatCurrency(item.unit_rate)}
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm text-gilt-soft font-mono tabular-nums">
                    {formatCurrency(item.quantity * item.unit_rate)}
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm">
                    <span className={`px-2 inline-flex font-mono text-[10px] uppercase tracking-[0.14em] leading-5 border rounded-full ${
                      item.stock_status === 'Recent' ? 'text-gilt-credit border-gilt-credit/40' :
                      item.stock_status === 'Active' ? 'text-gilt-brass border-gilt-brass/40' :
                      item.stock_status === 'Inactive' ? 'text-gilt-debit border-gilt-debit/40' :
                      'text-gilt-soft border-gilt-hairline'
                    }`}>
                      {item.stock_status}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Category Distribution */}
      <div className="bg-gilt-card p-6 rounded-lg border border-gilt-hairline">
        <h3 className="text-lg font-semibold text-gilt-ink mb-4">Category Distribution</h3>
        <div className="h-80">
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie
                data={categoryData}
                cx="50%"
                cy="50%"
                labelLine={false}
                outerRadius={80}
                fill="#C5972C"
                dataKey="value"
                nameKey="category"
                label={({ category, percent }) => `${category}: ${(percent * 100).toFixed(0)}%`}
              >
                {categoryData.map((entry, index) => (
                  <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
                ))}
              </Pie>
              <Tooltip
                formatter={(value) => formatCurrency(value)}
                contentStyle={{
                  backgroundColor: 'rgb(var(--gl-card))',
                  border: '1px solid rgb(var(--gl-hairline))',
                  borderRadius: '8px',
                  color: 'rgb(var(--gl-ink))'
                }}
                labelStyle={{ color: 'rgb(var(--gl-ink))' }}
              />
              <Legend wrapperStyle={{ color: 'rgb(var(--gl-ink-soft))' }} />
            </PieChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  );
}

export default InventoryDashboard;