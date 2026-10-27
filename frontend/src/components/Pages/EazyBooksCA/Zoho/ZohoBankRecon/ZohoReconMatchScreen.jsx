import React, { useState } from "react";
import { ArrowLeft, Zap, Hand } from "lucide-react";
import ZohoAutoMatchTab from "./ZohoAutoMatchTab";
import ZohoManualMatchTab from "./ZohoManualMatchTab";
import { ToastHost } from "./toast";

export default function ZohoReconMatchScreen({ report, onBack, onViewReport }) {
  const [activeTab, setActiveTab] = useState("auto");

  const tabs = [
    { id: "auto", label: "Automatic Reconciliation", icon: Zap },
    { id: "manual", label: "Manual Reconciliation", icon: Hand },
  ];

  return (
    <div className="min-h-screen text-gilt-ink">
      <ToastHost />
      <div className="max-w-7xl mx-auto px-4 py-6">
        {/* Header */}
        <div className="flex items-center gap-3 mb-6">
          <button onClick={onBack} className="p-2 rounded-lg hover:bg-gilt-sunken transition-colors duration-150">
            <ArrowLeft size={20} className="text-gilt-soft" />
          </button>
          <div>
            <h1 className="gl-display text-xl text-gilt-ink">{report.report_name}</h1>
            <p className="text-sm text-gilt-soft">
              {report.account_name} · <span className="font-mono tabular-nums">{report.period_from} → {report.period_to}</span>
            </p>
          </div>
        </div>

        {/* Tab bar */}
        <div className="flex gap-1 mb-6 border-b border-gilt-hairline w-fit">
          {tabs.map((tab) => {
            const Icon = tab.icon;
            return (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`flex items-center gap-2 px-5 py-2.5 text-sm font-medium border-b-2 -mb-px transition-colors duration-150 ${
                  activeTab === tab.id
                    ? "border-gilt-brass text-gilt-ink"
                    : "border-transparent text-gilt-soft hover:text-gilt-ink"
                }`}
              >
                <Icon size={15} />
                {tab.label}
              </button>
            );
          })}
        </div>

        {/* Tab content */}
        {activeTab === "auto" ? (
          <ZohoAutoMatchTab
            reportId={report.report_id}
            onAllReviewed={() => setActiveTab("manual")}
          />
        ) : (
          <ZohoManualMatchTab
            reportId={report.report_id}
            onViewReport={onViewReport}
          />
        )}
      </div>
    </div>
  );
}
