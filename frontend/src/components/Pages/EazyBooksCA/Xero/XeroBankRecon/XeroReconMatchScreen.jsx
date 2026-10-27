import React, { useState } from "react";
import { ArrowLeft, Zap, Hand } from "lucide-react";
import XeroAutoMatchTab from "./XeroAutoMatchTab";
import XeroManualMatchTab from "./XeroManualMatchTab";

export default function XeroReconMatchScreen({ report, onBack, onViewReport }) {
  const [activeTab, setActiveTab] = useState("auto");

  const tabs = [
    { id: "auto", label: "Automatic Reconciliation", icon: Zap },
    { id: "manual", label: "Manual Reconciliation", icon: Hand },
  ];

  return (
    <div className="min-h-screen bg-gray-50 text-gray-900">
      <div className="max-w-7xl mx-auto px-4 py-6">
        {/* Header */}
        <div className="flex items-center gap-3 mb-6">
          <button onClick={onBack} className="p-2 rounded-lg hover:bg-gray-200 transition-colors">
            <ArrowLeft size={20} className="text-gray-600" />
          </button>
          <div>
            <h1 className="text-xl font-bold text-gray-900">{report.report_name}</h1>
            <p className="text-sm text-gray-500">
              {report.account_name} · {report.period_from} → {report.period_to}
            </p>
          </div>
        </div>

        {/* Tab bar */}
        <div className="flex gap-1 mb-6 bg-white border border-gray-200 rounded-xl p-1 w-fit">
          {tabs.map((tab) => {
            const Icon = tab.icon;
            return (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`flex items-center gap-2 px-5 py-2.5 rounded-lg text-sm font-medium transition-all ${
                  activeTab === tab.id
                    ? "bg-orange-500 text-white shadow-sm"
                    : "text-gray-600 hover:text-gray-900 hover:bg-gray-50"
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
          <XeroAutoMatchTab
            reportId={report.report_id}
            onAllReviewed={() => setActiveTab("manual")}
          />
        ) : (
          <XeroManualMatchTab
            reportId={report.report_id}
            onViewReport={onViewReport}
          />
        )}
      </div>
    </div>
  );
}
