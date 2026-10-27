/**
 * ZohoBankRecon — top-level state machine for the Zoho Books reconciliation flow.
 *
 * Screens:
 *   ACCOUNT_SELECT  →  REPORT_LIST  →  MATCHING  →  SUMMARY
 *
 * Mounted as a route element (path "zoho/bank-recon") with NO props, so it owns
 * its organization + bank-account selection itself.
 */

import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import { RefreshCw, WifiOff, ArrowLeft } from "lucide-react";
import ZohoBankAccountSelect from "./ZohoBankAccountSelect";
import ZohoReconReportList from "./ZohoReconReportList";
import ZohoNewReconDialog from "./ZohoNewReconDialog";
import ZohoReconMatchScreen from "./ZohoReconMatchScreen";
import ZohoReconReportSummary from "./ZohoReconReportSummary";
import { zohoBankReconApi } from "../../../../../services/api";

const SCREEN = {
  ACCOUNT_SELECT: "account_select",
  REPORT_LIST: "report_list",
  AWAITING_SOURCE: "awaiting_source",
  MATCHING: "matching",
  SUMMARY: "summary",
};

export default function ZohoBankRecon() {
  const navigate = useNavigate();
  const [screen, setScreen] = useState(SCREEN.ACCOUNT_SELECT);
  const [selectedCompany, setSelectedCompany] = useState(null); // organization id
  const [selectedAccount, setSelectedAccount] = useState(null); // { id, name }
  const [currentReport, setCurrentReport] = useState(null);
  const [showNewDialog, setShowNewDialog] = useState(false);
  const [processing, setProcessing] = useState(false);
  const [processingError, setProcessingError] = useState(null);

  // ── Navigation handlers ──

  const handleAccountSelect = (account, organizationId) => {
    setSelectedAccount(account);
    setSelectedCompany(organizationId);
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
      const result = await zohoBankReconApi.processReport(report.report_id);
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
      <div className="min-h-screen text-gilt-ink flex flex-col items-center justify-center gap-6">
        <div className="w-16 h-16 rounded-full bg-gilt-sunken border border-gilt-hairline flex items-center justify-center">
          <RefreshCw size={28} className="text-gilt-brass animate-spin" />
        </div>
        <div className="text-center">
          <h2 className="text-xl font-bold text-gilt-ink">Processing...</h2>
          <p className="text-gilt-soft mt-2 max-w-sm">
            Fetching book transactions from Zoho Books and running automatic
            matching. This may take a moment.
          </p>
        </div>
      </div>
    );
  }

  // ── Screen routing ──

  if (screen === SCREEN.ACCOUNT_SELECT) {
    return (
      <ZohoBankAccountSelect
        onBack={() => navigate("/dashboard")}
        onSelectAccount={handleAccountSelect}
      />
    );
  }

  if (screen === SCREEN.REPORT_LIST) {
    return (
      <>
        <ZohoReconReportList
          selectedCompany={selectedCompany}
          selectedAccount={selectedAccount}
          onBack={() => setScreen(SCREEN.ACCOUNT_SELECT)}
          onNewReport={() => setShowNewDialog(true)}
          onOpenReport={handleOpenReport}
        />
        {showNewDialog && (
          <ZohoNewReconDialog
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
      <div className="min-h-screen text-gilt-ink flex flex-col items-center justify-center gap-6 px-4">
        <div className="w-16 h-16 rounded-full bg-gilt-sunken border border-gilt-brass/40 flex items-center justify-center">
          <WifiOff size={28} className="text-gilt-brass" />
        </div>
        <div className="text-center max-w-md">
          <h2 className="text-xl font-bold text-gilt-ink">Zoho Books not connected</h2>
          <p className="text-gilt-soft mt-2">
            Zoho Books is not connected. The bank statement is saved; reconnect
            Zoho Books and run process again.
          </p>
          {currentReport?.report_name && (
            <p className="text-xs text-gilt-faint mt-3">
              Report: {currentReport.report_name}
            </p>
          )}
          {processingError && (
            <p className="text-xs text-gilt-debit mt-3">{processingError}</p>
          )}
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setScreen(SCREEN.REPORT_LIST)}
            className="inline-flex items-center gap-1.5 px-3 py-2 text-sm font-medium text-gilt-soft bg-gilt-card border border-gilt-hairline rounded-md hover:border-gilt-brass hover:text-gilt-brass transition-colors duration-150"
          >
            <ArrowLeft className="w-4 h-4" /> Back to reports
          </button>
          <button
            onClick={() => currentReport && runProcess(currentReport)}
            className="inline-flex items-center gap-1.5 px-3 py-2 text-sm font-semibold text-gilt-paper bg-gilt-brass rounded-md hover:opacity-90 transition-opacity duration-150"
          >
            <RefreshCw className="w-4 h-4" /> Retry now
          </button>
        </div>
      </div>
    );
  }

  if (screen === SCREEN.MATCHING) {
    return (
      <ZohoReconMatchScreen
        report={currentReport}
        onBack={() => setScreen(SCREEN.REPORT_LIST)}
        onViewReport={() => setScreen(SCREEN.SUMMARY)}
      />
    );
  }

  if (screen === SCREEN.SUMMARY) {
    return (
      <ZohoReconReportSummary
        report={currentReport}
        onBack={() => setScreen(SCREEN.REPORT_LIST)}
        onManualRecon={() => setScreen(SCREEN.MATCHING)}
      />
    );
  }

  return null;
}
