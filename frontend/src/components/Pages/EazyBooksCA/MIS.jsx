import React, { useState, useEffect, useRef, lazy, Suspense } from "react";
import {
  BarChart3,
  RefreshCcw,
  Download,
  Calendar,
  ChevronDown,
  PieChart,
  DollarSign,
  Package,
} from "lucide-react";
import { PageHeader } from "../../ui/page-header";
import { Button } from "../../ui/button";
import { FullPageSpinner } from "../../ui/spinner";
import { cn } from "../../../lib/utils";

const FinancialDashboard = lazy(() => import("./MIS/FinancialDashboard"));
const InventoryDashboard = lazy(() => import("./MIS/InventoryDashboard"));
const LedgerDashboard = lazy(() => import("./MIS/LedgerDashboard"));

const DATE_RANGES = [
  { key: "week", label: "Last 7 days" },
  { key: "month", label: "Last 30 days" },
  { key: "quarter", label: "Last quarter" },
  { key: "year", label: "This year" },
];

const TABS = [
  { key: "financial", label: "Financial analysis", icon: DollarSign },
  { key: "inventory", label: "Inventory reports", icon: Package },
  { key: "ledger", label: "Ledger analysis", icon: PieChart },
];

function MIS() {
  const [loading, setLoading] = useState(false);
  const [activeTab, setActiveTab] = useState("financial");
  const [dateRange, setDateRange] = useState("month");
  const [showDateDropdown, setShowDateDropdown] = useState(false);
  const dropdownRef = useRef(null);

  useEffect(() => {
    const handler = (e) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target)) {
        setShowDateDropdown(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const renderDashboard = () => {
    switch (activeTab) {
      case "financial":
        return <FinancialDashboard period={dateRange} />;
      case "inventory":
        return <InventoryDashboard period={dateRange} />;
      case "ledger":
        return <LedgerDashboard period={dateRange} />;
      default:
        return <FinancialDashboard period={dateRange} />;
    }
  };

  const currentLabel =
    DATE_RANGES.find((r) => r.key === dateRange)?.label || "Last 30 days";

  return (
    <div className="min-h-screen">
      <PageHeader
        icon={BarChart3}
        title="MIS Reports"
        description="Real-time financial, inventory, and ledger insights across companies."
        actions={
          <div className="flex items-center gap-2">
            {/* Date range */}
            <div className="relative" ref={dropdownRef}>
              <Button
                variant="secondary"
                size="md"
                leftIcon={<Calendar />}
                rightIcon={<ChevronDown />}
                onClick={() => setShowDateDropdown((v) => !v)}
              >
                {currentLabel}
              </Button>
              {showDateDropdown && (
                <div className="absolute right-0 mt-1.5 w-52 rounded-lg shadow-sm bg-gilt-card border border-gilt-hairline py-1 z-20 animate-scale-in origin-top-right">
                  {DATE_RANGES.map((range) => {
                    const active = dateRange === range.key;
                    return (
                      <button
                        key={range.key}
                        onClick={() => {
                          setDateRange(range.key);
                          setShowDateDropdown(false);
                        }}
                        className={cn(
                          "block px-3 py-1.5 text-sm text-left w-full transition-colors",
                          active
                            ? "bg-gilt-sunken text-gilt-brass font-medium"
                            : "text-gilt-soft hover:bg-gilt-sunken"
                        )}
                      >
                        {range.label}
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
            <Button
              variant="secondary"
              size="md"
              leftIcon={<RefreshCcw className={loading ? "animate-spin" : ""} />}
              onClick={() => setLoading(true)}
            >
              Refresh
            </Button>
            <Button variant="primary" size="md" leftIcon={<Download />}>
              Export
            </Button>
          </div>
        }
      >
        {/* Tabs */}
        <nav className="flex items-center gap-1 -mb-5 border-b border-transparent">
          {TABS.map((tab) => {
            const Icon = tab.icon;
            const active = activeTab === tab.key;
            return (
              <button
                key={tab.key}
                onClick={() => setActiveTab(tab.key)}
                className={cn(
                  "relative inline-flex items-center gap-2 px-3 py-2.5 text-sm font-medium transition-colors",
                  active
                    ? "text-gilt-ink"
                    : "text-gilt-soft hover:text-gilt-ink"
                )}
              >
                <Icon size={14} strokeWidth={active ? 2.25 : 2} />
                {tab.label}
                {active && (
                  <span className="absolute left-2 right-2 -bottom-px h-0.5 rounded-full bg-gilt-brass" />
                )}
              </button>
            );
          })}
        </nav>
      </PageHeader>

      <div className="px-6 lg:px-8 py-6">
        <Suspense fallback={<FullPageSpinner label="Loading dashboard…" />}>
          {renderDashboard()}
        </Suspense>
      </div>
    </div>
  );
}

export default MIS;
