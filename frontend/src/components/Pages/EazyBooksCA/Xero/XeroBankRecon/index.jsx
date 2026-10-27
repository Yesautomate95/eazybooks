/**
 * XeroBankRecon — top-level state machine for the Xero reconciliation flow.
 *
 * Screens:
 *   ACCOUNT_SELECT  →  REPORT_LIST  →  MATCHING  →  SUMMARY
 *
 * Mounted as a route element (path "xero/bank-recon") with NO props, so it owns
 * its company(tenant) + bank-account selection itself.
 */

import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import { RefreshCw, WifiOff, ArrowLeft } from "lucide-react";
import XeroBankAccountSelect from "./XeroBankAccountSelect";
import XeroReconReportList from "./XeroReconReportList";
import XeroNewReconDialog from "./XeroNewReconDialog";
import XeroReconMatchScreen from "./XeroReconMatchScreen";
import XeroReconReportSummary from "./XeroReconReportSummary";
import { xeroBankReconApi } from "../../../../../services/api";

const SCREEN = {
  ACCOUNT_SELECT: "account_select",
  REPORT_LIST: "report_list",
  AWAITING_SOURCE: "awaiting_source",
  MATCHING: "matching",
  SUMMARY: "summary",
};

export default function XeroBankRecon() {
  const navigate = useNavigate();
  const [screen, setScreen] = useState(SCREEN.ACCOUNT_SELECT);
  const [selectedCompany, setSelectedCompany] = useState(null); // tenant id
  const [selectedAccount, setSelectedAccount] = useState(null); // { id, name }
  const [currentReport, setCurrentReport] = useState(null);
  const [showNewDialog, setShowNewDialog] = useState(false);
  const [processing, setProcessing] = useState(false);
  const [processingError, setProcessingError] = useState(null);

  // ── Navigation handlers ──

  const handleAccountSelect = (account, tenant) => {
    setSelectedAccount(account);
    setSelectedCompany(tenant);
    setScreen(SCREEN.REPORT_LIST);
  };

  const handleOpenReport = (report, editMode = false) => {
    setCurrentReport(report);
    // Completed reports: go directly to summary; others: go to matching
    if (report.status === "completed" && !editMode) {
      setScreen(SCREEN.SUMMARY);
    } else if (report.status === "awaiting_source") {
      setScreen(SCREEN.AWAITING_SOURCE);
    } else {
      setScreen(SCREEN.MATCHING);
    }
  };

  const runProcess = async (report) => {
    setProcessing(true);
    setProcessingError(null);
    try {
      const result = await xeroBankReconApi.processReport(report.report_id);
      const merged = { ...report, ...result };
      setCurrentReport(merged);
      if (result?.status === "awaiting_source") {
        setScreen(SCREEN.AWAITING_SOURCE);
      } else {
        setScreen(SCREEN.MATCHING);
      }
    } catch (err) {
      setProcessingError(
        err?.response?.data?.detail ||
          err?.response?.data?.message ||
          err?.message ||
          "Processing failed"
      );
      setScreen(SCREEN.MATCHING);
    } finally {
      setProcessing(false);
    }
  };

  // Called after dialog creates the report record
  const handleReportCreated = async (report) => {
    setShowNewDialog(false);
    setCurrentReport(report);
    await runProcess(report);
  };

  // ── Processing screen ──
  if (processing) {
    return (
      <div className="min-h-screen bg-gray-50 text-gray-900 flex flex-col items-center justify-center gap-6">
        <div className="w-16 h-16 rounded-full bg-orange-100 flex items-center justify-center">
          <RefreshCw size={28} className="text-orange-500 animate-spin" />
        </div>
        <div className="text-center">
          <h2 className="text-xl font-bold text-gray-900">Processing...</h2>
          <p className="text-gray-500 mt-2 max-w-sm">
            Fetching book transactions from Xero and running automatic
            matching. This may take a moment.
          </p>
        </div>
      </div>
    );
  }

  // ── Screen routing ──

  if (screen === SCREEN.ACCOUNT_SELECT) {
    return (
      <XeroBankAccountSelect
        onBack={() => navigate("/dashboard")}
        onSelectAccount={handleAccountSelect}
      />
    );
  }

  if (screen === SCREEN.REPORT_LIST) {
    return (
      <>
        <XeroReconReportList
          selectedCompany={selectedCompany}
          selectedAccount={selectedAccount}
          onBack={() => setScreen(SCREEN.ACCOUNT_SELECT)}
          onNewReport={() => setShowNewDialog(true)}
          onOpenReport={handleOpenReport}
        />
        {showNewDialog && (
          <XeroNewReconDialog
            selectedCompany={selectedCompany}
            selectedAccount={selectedAccount}
            onClose={() => setShowNewDialog(false)}
            onCreate={handleReportCreated}
          />
        )}
      </>
    );
  }

  if (screen === SCREEN.AWAITING_SOURCE) {
    return (
      <div className="min-h-screen bg-gray-50 text-gray-900 flex flex-col items-center justify-center gap-6 px-4">
        <div className="w-16 h-16 rounded-full bg-amber-100 flex items-center justify-center">
          <WifiOff size={28} className="text-amber-600" />
        </div>
        <div className="text-center max-w-md">
          <h2 className="text-xl font-bold text-gray-900">Xero not connected</h2>
          <p className="text-gray-500 mt-2">
            Xero is not connected. The bank statement is saved; reconnect
            Xero and run process again.
          </p>
          {currentReport?.report_name && (
            <p className="text-xs text-gray-400 mt-3">
              Report: {currentReport.report_name}
            </p>
          )}
          {processingError && (
            <p className="text-xs text-red-600 mt-3">{processingError}</p>
          )}
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setScreen(SCREEN.REPORT_LIST)}
            className="inline-flex items-center gap-1.5 px-3 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-lg hover:bg-gray-50"
          >
            <ArrowLeft className="w-4 h-4" /> Back to reports
          </button>
          <button
            onClick={() => currentReport && runProcess(currentReport)}
            className="inline-flex items-center gap-1.5 px-3 py-2 text-sm font-semibold text-white bg-indigo-600 rounded-lg hover:bg-indigo-700"
          >
            <RefreshCw className="w-4 h-4" /> Retry now
          </button>
        </div>
      </div>
    );
  }

  if (screen === SCREEN.MATCHING) {
    return (
      <XeroReconMatchScreen
        report={currentReport}
        onBack={() => setScreen(SCREEN.REPORT_LIST)}
        onViewReport={() => setScreen(SCREEN.SUMMARY)}
      />
    );
  }

  if (screen === SCREEN.SUMMARY) {
    return (
      <XeroReconReportSummary
        report={currentReport}
        onBack={() => setScreen(SCREEN.REPORT_LIST)}
        onManualRecon={() => setScreen(SCREEN.MATCHING)}
      />
    );
  }

  return null;
}
