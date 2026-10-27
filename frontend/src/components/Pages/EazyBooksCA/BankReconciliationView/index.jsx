/**
 * BankReconciliationView — top-level state machine for the reconciliation flow.
 *
 * Screens:
 *   LEDGER_SELECT  →  REPORT_LIST  →  MATCHING  →  SUMMARY
 *
 * Navigated from Tally.jsx when user picks "Bank Reconciliation" doc type.
 */

import React, { useState } from "react";
import { RefreshCw, WifiOff, ArrowLeft } from "lucide-react";
import BankLedgerSelect from "./BankLedgerSelect";
import ReconReportList from "./ReconReportList";
import NewReconDialog from "./NewReconDialog";
import ReconMatchScreen from "./ReconMatchScreen";
import ReconReportSummary from "./ReconReportSummary";
import { bankReconApi } from "../../../../services/api";

const SCREEN = {
  LEDGER_SELECT: "ledger_select",
  REPORT_LIST: "report_list",
  AWAITING_TALLY: "awaiting_tally",
  MATCHING: "matching",
  SUMMARY: "summary",
};

export default function BankReconciliationView({ selectedCompany, onBack }) {
  const [screen, setScreen] = useState(SCREEN.LEDGER_SELECT);
  const [selectedLedger, setSelectedLedger] = useState(null);
  const [currentReport, setCurrentReport] = useState(null);
  const [showNewDialog, setShowNewDialog] = useState(false);
  const [processing, setProcessing] = useState(false);
  const [processingError, setProcessingError] = useState(null);

  // ── Navigation handlers ──

  const handleLedgerSelect = (ledgerName) => {
    setSelectedLedger(ledgerName);
    setScreen(SCREEN.REPORT_LIST);
  };

  const handleOpenReport = (report, editMode = false) => {
    setCurrentReport(report);
    // Completed reports: go directly to summary; others: go to matching
    if (report.status === "completed" && !editMode) {
      setScreen(SCREEN.SUMMARY);
    } else if (report.status === "awaiting_tally") {
      setScreen(SCREEN.AWAITING_TALLY);
    } else {
      setScreen(SCREEN.MATCHING);
    }
  };

  const runProcess = async (report) => {
    setProcessing(true);
    setProcessingError(null);
    try {
      const result = await bankReconApi.processReport(report.report_id);
      const merged = { ...report, ...result };
      setCurrentReport(merged);
      if (result?.status === "awaiting_tally") {
        setScreen(SCREEN.AWAITING_TALLY);
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

  // Called after dialog creates the report record. The create response only
  // carries id/name/status, so stamp on the company + ledger (needed by the match
  // screen header and the adjustment ledger picker) before processing.
  const handleReportCreated = async (report) => {
    setShowNewDialog(false);
    const enriched = {
      ...report,
      company_name: report.company_name || selectedCompany,
      bank_ledger_name: report.bank_ledger_name || selectedLedger,
    };
    setCurrentReport(enriched);
    await runProcess(enriched);
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
            Fetching ledger transactions from Tally and running automatic matching.
            This may take a moment.
          </p>
        </div>
      </div>
    );
  }

  // ── Screen routing ──

  if (screen === SCREEN.LEDGER_SELECT) {
    return (
      <BankLedgerSelect
        selectedCompany={selectedCompany}
        onBack={onBack}
        onSelectLedger={handleLedgerSelect}
      />
    );
  }

  if (screen === SCREEN.REPORT_LIST) {
    return (
      <>
        <ReconReportList
          selectedCompany={selectedCompany}
          selectedLedger={selectedLedger}
          onBack={() => setScreen(SCREEN.LEDGER_SELECT)}
          onNewReport={() => setShowNewDialog(true)}
          onOpenReport={handleOpenReport}
        />
        {showNewDialog && (
          <NewReconDialog
            selectedCompany={selectedCompany}
            selectedLedger={selectedLedger}
            onClose={() => setShowNewDialog(false)}
            onCreate={handleReportCreated}
          />
        )}
      </>
    );
  }

  if (screen === SCREEN.AWAITING_TALLY) {
    return (
      <div className="min-h-screen text-gilt-ink flex flex-col items-center justify-center gap-6 px-4">
        <div className="w-16 h-16 rounded-full bg-gilt-sunken border border-gilt-brass/40 flex items-center justify-center">
          <WifiOff size={28} className="text-gilt-brass" />
        </div>
        <div className="text-center max-w-md">
          <h2 className="text-xl font-bold text-gilt-ink">Tally not connected</h2>
          <p className="text-gilt-soft mt-2">
            The bank statement is saved. Bank reconciliation needs ledger
            transactions from Tally, so we'll continue once the desktop bridge
            is online.
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
      <ReconMatchScreen
        report={currentReport}
        onBack={() => setScreen(SCREEN.REPORT_LIST)}
        onViewReport={() => setScreen(SCREEN.SUMMARY)}
      />
    );
  }

  if (screen === SCREEN.SUMMARY) {
    return (
      <ReconReportSummary
        report={currentReport}
        onBack={() => setScreen(SCREEN.REPORT_LIST)}
        onManualRecon={() => setScreen(SCREEN.MATCHING)}
      />
    );
  }

  return null;
}
