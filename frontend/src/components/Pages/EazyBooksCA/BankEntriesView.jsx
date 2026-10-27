/**
 * BankEntriesView - Comprehensive Bank Statement Processing
 *
 * Features:
 * - Parse and display bank transactions from uploaded statements
 * - AI-powered ledger suggestions with confidence scores
 * - Editable transaction fields
 * - Bulk ledger assignment with rules
 * - Duplicate detection
 * - Mapping rules management
 * - Generate Tally XML and push to Tally
 */

import React, { useState, useEffect, useCallback, useMemo } from "react";
import {
  ArrowLeft,
  Send,
  Check,
  CheckCircle,
  AlertCircle,
  Plus,
  Edit2,
  X,
  Eye,
  EyeOff,
  Sparkles,
  Settings,
  ChevronDown,
  ChevronUp,
  Search,
  Filter,
  Download,
  RefreshCw,
  Save,
  Trash2,
  Copy,
  CheckSquare,
  Square,
  Wand2,
  AlertTriangle,
  FileText,
  Landmark,
  ArrowUpDown,
  MoreHorizontal,
  Loader2,
} from "lucide-react";
import { Panel, Group as PanelGroup, Separator as PanelResizeHandle } from "react-resizable-panels";
import api, { tallyApi } from "../../../services/api";
import { SaveDraftButton } from "./common/DraftsPanel";
import PdfViewer from "./PdfViewer";
import ConfidenceBadge, { ConfidenceIndicator } from "./common/ConfidenceBadge";
import MappingRulesPanel from "./common/MappingRulesPanel";
import DuplicateWarning from "./common/DuplicateWarning";
import SearchableDropdown from "./common/SearchableDropdown";
import LedgerCreateModal from "./common/LedgerCreateModal";

// Voucher type options
const VOUCHER_TYPES = [
  { value: "Receipt", label: "Receipt", color: "green" },
  { value: "Payment", label: "Payment", color: "red" },
  { value: "Contra", label: "Contra", color: "blue" },
  { value: "Journal", label: "Journal", color: "purple" },
];

// Keyword-based ledger mapping rules for smart suggestions
const LEDGER_MAPPING_RULES = [
  { keywords: ["NEFT", "IITR", "IIT ROORKEE"], ledger: "IIT Roorkee" },
  { keywords: ["BHARTI AIRTEL", "AIRTEL"], ledger: "Bharti Airtel Limited" },
  { keywords: ["BHAVANS", "BHAVAN'S", "PROMINENT INTERNATIONAL SCHOOL"], ledger: "Bhavans Prominent International School" },
  { keywords: ["BOMBAY HOSPITAL"], ledger: "Bombay Hospital" },
  { keywords: ["BROKERAGE"], ledger: "Brokerage" },
  { keywords: ["BRAINTA", "BRAINTA IMPEX"], ledger: "Brainta Impex" },
  { keywords: ["BUILDER AGREEMENT"], ledger: "Builder Agreement" },
  { keywords: ["CAPITAL RECORD", "BHOPAL"], ledger: "Capital Record Centre Bhopal" },
  { keywords: ["SALARY", "WAGES"], ledger: "Salary" },
  { keywords: ["ELECTRICITY", "POWER", "DISCOM"], ledger: "Electricity Charges" },
  { keywords: ["TELEPHONE", "MOBILE", "RECHARGE"], ledger: "Telephone Charges" },
  { keywords: ["RENT"], ledger: "Rent" },
  { keywords: ["INSURANCE", "LIC", "POLICY"], ledger: "Insurance" },
  { keywords: ["GST", "CGST", "SGST", "IGST"], ledger: "GST Payable" },
  { keywords: ["TDS"], ledger: "TDS Payable" },
  { keywords: ["INTEREST"], ledger: "Interest" },
];

// Suggest a ledger based on narration text
function suggestLedgerFromNarration(narration, existingLedgers = []) {
  if (!narration) return "";
  const upperNarration = narration.toUpperCase();

  // Step 1: Check keyword rules
  for (const rule of LEDGER_MAPPING_RULES) {
    if (rule.keywords.some(kw => upperNarration.includes(kw.toUpperCase()))) {
      // Only return if this ledger exists in available ledgers, or always return as suggestion
      if (existingLedgers.length === 0 || existingLedgers.some(l => l.toLowerCase() === rule.ledger.toLowerCase())) {
        return rule.ledger;
      }
    }
  }

  // Step 2: Fuzzy match against existing ledger names (match 2+ words of length > 3)
  for (const ledger of existingLedgers) {
    const ledgerWords = ledger.toUpperCase().split(/\s+/);
    const matchScore = ledgerWords.filter(word =>
      word.length > 3 && upperNarration.includes(word)
    ).length;
    if (matchScore >= 2) return ledger;
  }

  // Step 3: Single strong word match (word length > 5)
  for (const ledger of existingLedgers) {
    const ledgerWords = ledger.toUpperCase().split(/\s+/);
    const strongMatch = ledgerWords.find(word =>
      word.length > 5 && upperNarration.includes(word)
    );
    if (strongMatch) return ledger;
  }

  // Step 4: Check localStorage for user-created rules
  try {
    const savedRules = JSON.parse(localStorage.getItem('ledgerMappingRules') || '[]');
    for (const rule of savedRules) {
      if (rule.keywords && rule.keywords.some(kw => upperNarration.includes(kw.toUpperCase()))) {
        return rule.ledger;
      }
    }
  } catch (e) { /* ignore parse errors */ }

  return ""; // Return empty instead of "Cash" — let user decide
}

function BankEntriesView({
  xmlData = [],
  ledgersAvailable = [],
  setLedgersAvailable,
  onBack,
  selectedCompany,
  draftId: propDraftId = null,
}) {
  // Transaction state
  const [transactions, setTransactions] = useState([]);
  const [filteredTransactions, setFilteredTransactions] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const [parseError, setParseError] = useState(null);

  // Selection state
  const [selectedRows, setSelectedRows] = useState(new Set());
  const [selectAll, setSelectAll] = useState(false);

  // UI state
  const [searchTerm, setSearchTerm] = useState("");
  const [sortConfig, setSortConfig] = useState({ key: "date", direction: "asc" });
  const [showPdfPanel, setShowPdfPanel] = useState(true);
  const [showMappingRules, setShowMappingRules] = useState(false);
  const [showBulkActions, setShowBulkActions] = useState(false);
  const [expandedRow, setExpandedRow] = useState(null);

  // Column filter state
  const [columnFilters, setColumnFilters] = useState({
    date: "",
    narration: "",
    debit: "",
    credit: "",
    voucherType: "",
    partyLedger: "",
  });
  const [activeFilterColumn, setActiveFilterColumn] = useState(null);

  // File state
  const [selectedFileIndex, setSelectedFileIndex] = useState(0);
  const [pdfData, setPdfData] = useState(null);

  // AI state
  const [isLoadingAI, setIsLoadingAI] = useState(false);
  const [aiSuggestions, setAiSuggestions] = useState({});

  // Duplicate state
  const [duplicates, setDuplicates] = useState({});
  const [isCheckingDuplicates, setIsCheckingDuplicates] = useState(false);

  // Mapping rules state
  const [mappingRules, setMappingRules] = useState([]);

  // Status state
  const [isSending, setIsSending] = useState(false);
  const [sendStatus, setSendStatus] = useState(null);
  const [statusMessage, setStatusMessage] = useState("");

  // Ledger state
  const [localLedgers, setLocalLedgers] = useState(ledgersAvailable || []);
  const [showNewLedgerModal, setShowNewLedgerModal] = useState(false);
  const [newLedgerForRow, setNewLedgerForRow] = useState(null);

  // Drafts: explicit Save Draft action; the library page is where prior
  // drafts are listed and resumed.
  const [draftId, setDraftId] = useState(propDraftId);
  const [savingDraft, setSavingDraft] = useState(false);
  // Separate flag — only set true when vouchers are actually pushed to Tally,
  // not when bulk-edit / skip / delete / AI-accept set sendStatus="success"
  const [tallyPushSucceeded, setTallyPushSucceeded] = useState(false);

  const handleSaveDraft = async () => {
    if (!transactions.length) return;
    setSavingDraft(true);
    try {
      const n = transactions.length;
      const res = await tallyApi.drafts.save({
        flow: "bank_entries",
        payload: {
          transactions,
          aiSuggestions,
          mappingRules,
          duplicates,
        },
        title: `${n} bank ${n === 1 ? "entry" : "entries"}`,
        draftId,
        companyName: selectedCompany,
      });
      if (res?.draft_id) setDraftId(res.draft_id);
    } catch (e) {
      console.error("save draft failed", e);
    } finally {
      setSavingDraft(false);
    }
  };

  // Bulk edit state
  const [bulkVoucherType, setBulkVoucherType] = useState("");
  const [bulkLedger, setBulkLedger] = useState("");

  // Bank ledger from parent
  const [bankLedger, setBankLedger] = useState("");

  // Tally connection state - default to null (unknown) rather than false (offline)
  const [tallyConnection, setTallyConnection] = useState({ connected: null, checking: true });

  // Push confirmation state
  const [showPushConfirm, setShowPushConfirm] = useState(false);
  const [pushProgress, setPushProgress] = useState(null);
  const [validationResult, setValidationResult] = useState(null);
  const [showMissingLedgersDialog, setShowMissingLedgersDialog] = useState(false);

  // Push status tracking per transaction
  const [pushStatuses, setPushStatuses] = useState({});

  // Mark draft as pushed only when vouchers were actually sent to Tally
  useEffect(() => {
    if (tallyPushSucceeded && draftId) {
      tallyApi.drafts.markPushed(draftId).catch(() => {});
    }
  }, [tallyPushSucceeded, draftId]);

  // Check Tally connection on mount and every 30 seconds
  // Uses direct HTTP check first, falls back to websocket check
  useEffect(() => {
    const checkConnection = async () => {
      setTallyConnection(prev => ({ ...prev, checking: true }));

      // Try direct HTTP connection first
      try {
        const directResult = await tallyApi.checkDirectConnection();
        if (directResult?.connected) {
          setTallyConnection({
            connected: true,
            checking: false,
            company: directResult.company || '',
            method: 'direct',
          });
          return;
        }
      } catch (directError) {
        // Direct check failed, continue to fallback
      }

      // Fallback to websocket-based check
      try {
        const wsResult = await tallyApi.checkConnection();
        if (wsResult?.connected) {
          setTallyConnection({
            connected: true,
            checking: false,
            company: wsResult.company || '',
            method: 'websocket',
          });
          return;
        }
      } catch (wsError) {
        // Websocket check also failed
      }

      // Both checks failed - but don't show as disconnected if we haven't tried pushing yet
      // The actual push will determine connectivity
      setTallyConnection(prev => ({
        ...prev,
        checking: false,
        // Keep previous connected state if it was true (avoid false negatives)
      }));
    };
    checkConnection();
    const interval = setInterval(checkConnection, 30000);
    return () => clearInterval(interval);
  }, []);

  // Parse XML data into transactions on mount
  useEffect(() => {
    parseXmlData();
  }, [xmlData]);

  // Update filtered transactions when search/sort changes
  useEffect(() => {
    filterAndSortTransactions();
  }, [transactions, searchTerm, sortConfig]);

  // Set PDF data when file changes
  useEffect(() => {
    const currentFile = xmlData[selectedFileIndex];
    console.log("BankEntriesView: Current file data:", {
      hasOriginalFile: !!currentFile?.originalFile,
      hasPdfData: !!currentFile?.pdfData,
      hasFileContent: !!currentFile?.fileContent,
      filename: currentFile?.filename,
      allKeys: currentFile ? Object.keys(currentFile) : [],
    });

    if (currentFile) {
      // Try different sources for PDF data
      let foundPdfData = null;

      if (currentFile.originalFile) {
        foundPdfData = currentFile.originalFile;
        console.log("PDF data set from originalFile, length:", currentFile.originalFile.length);
      } else if (currentFile.pdfData) {
        foundPdfData = currentFile.pdfData;
        console.log("PDF data set from pdfData");
      } else if (currentFile.fileContent) {
        foundPdfData = currentFile.fileContent;
        console.log("PDF data set from fileContent");
      } else {
        console.log("No PDF data found in currentFile");
      }

      setPdfData(foundPdfData);

      if (currentFile.bankLedger) {
        setBankLedger(currentFile.bankLedger);
      }
    }
  }, [selectedFileIndex, xmlData]);

  // Parse XML data into transaction objects
  const parseXmlData = useCallback(() => {
    setIsLoading(true);
    setParseError(null);

    try {
      if (!xmlData || xmlData.length === 0) {
        setTransactions([]);
        setIsLoading(false);
        return;
      }

      console.log("Parsing xmlData:", xmlData);

      const allTransactions = [];
      let transactionId = 0;

      xmlData.forEach((file, fileIndex) => {
        // Get transactions from either direct array or parse from XML
        let fileTransactions = [];

        // Check for transactions array from backend (AI extracted)
        if (file.transactions && Array.isArray(file.transactions) && file.transactions.length > 0) {
          console.log(`File ${fileIndex}: Found ${file.transactions.length} pre-extracted transactions`);
          fileTransactions = file.transactions;
        }
        // Fall back to XML parsing
        else if (file.xml || file.tallyxml) {
          console.log(`File ${fileIndex}: Parsing XML for transactions`);
          fileTransactions = parseXmlString(file.xml || file.tallyxml);
        }

        console.log(`File ${fileIndex} transactions:`, fileTransactions);

        fileTransactions.forEach((tx, txIndex) => {
          // Determine party ledger from backend fields
          // Backend returns credit_ledger (party for receipts) and debit_ledger (party for payments)
          const txType = (tx.type || "").toLowerCase();
          let partyLedger = "";

          // For receipts: party paid us, so debit_ledger is the party
          // For payments: we paid party, so credit_ledger is the party
          if (txType === "receipt") {
            partyLedger = tx.debit_ledger || tx.credit_ledger || tx.partyLedger || tx.party_ledger || "";
          } else if (txType === "payment") {
            partyLedger = tx.credit_ledger || tx.debit_ledger || tx.partyLedger || tx.party_ledger || "";
          } else {
            partyLedger = tx.credit_ledger || tx.debit_ledger || tx.partyLedger || tx.party_ledger || "";
          }

          // Map transaction type to voucher type
          let voucherType = tx.voucherType || tx.voucher_type || "";
          if (!voucherType) {
            if (txType === "receipt") voucherType = "Receipt";
            else if (txType === "payment") voucherType = "Payment";
            else voucherType = determineVoucherType(tx);
          }

          // If partyLedger is empty or "Cash", try to suggest a better one
          const txNarration = tx.narration || tx.description || tx.particulars || "";
          if (!partyLedger || partyLedger.toLowerCase() === "cash") {
            const suggested = suggestLedgerFromNarration(txNarration, ledgersAvailable);
            if (suggested) partyLedger = suggested;
          }

          allTransactions.push({
            id: transactionId++,
            fileIndex,
            fileName: file.filename || `File ${fileIndex + 1}`,
            // Core transaction data
            date: tx.date || tx.transaction_date || "",
            narration: txNarration,
            amount: parseFloat(tx.amount) || 0,
            type: tx.type || (parseFloat(tx.amount) >= 0 ? "Credit" : "Debit"),
            reference: tx.reference || tx.ref_no || tx.cheque_no || "",
            balance: parseFloat(tx.balance) || null,
            // Voucher details
            voucherType: voucherType,
            voucherNumber: tx.voucherNumber || tx.voucher_number || "",
            // Ledger details (party is the counterparty, NOT the bank)
            partyLedger: partyLedger,
            creditLedger: tx.credit_ledger || "",
            debitLedger: tx.debit_ledger || "",
            suggestedLedger: null,
            suggestedConfidence: null,
            // Status
            isEdited: false,
            isSkipped: false,
            isDuplicate: false,
            // Original data reference
            originalData: tx,
          });
        });
      });

      console.log("All parsed transactions:", allTransactions);
      setTransactions(allTransactions);

      // Auto-fetch AI suggestions if we have transactions
      if (allTransactions.length > 0) {
        fetchAISuggestions(allTransactions);
      }
    } catch (error) {
      console.error("Error parsing XML data:", error);
      setParseError("Failed to parse bank statement. Please check the file format.");
    } finally {
      setIsLoading(false);
    }
  }, [xmlData]);

  // Parse XML string to extract transactions
  const parseXmlString = (xmlString) => {
    if (!xmlString) return [];

    try {
      const parser = new DOMParser();
      const xmlDoc = parser.parseFromString(xmlString, "text/xml");

      // Try different XML structures
      const vouchers = xmlDoc.querySelectorAll("VOUCHER, voucher, TALLYMESSAGE");
      const transactions = [];

      vouchers.forEach((voucher) => {
        const date = voucher.querySelector("DATE, date, VOUCHERDATE")?.textContent || "";
        const narration = voucher.querySelector("NARRATION, narration, PARTYLEDGERNAME")?.textContent || "";
        const amount = voucher.querySelector("AMOUNT, amount")?.textContent || "0";
        const voucherType = voucher.querySelector("VOUCHERTYPENAME, vouchertype")?.textContent || "";

        if (date || narration || amount !== "0") {
          transactions.push({
            date: formatDateFromXml(date),
            narration,
            amount: parseFloat(amount.replace(/,/g, "")) || 0,
            voucherType,
          });
        }
      });

      return transactions;
    } catch (e) {
      console.error("XML parsing error:", e);
      return [];
    }
  };

  // Format date from XML format to DD/MM/YYYY for display
  const formatDateFromXml = (dateStr) => {
    if (!dateStr) return "";
    // Handle YYYYMMDD format (Tally standard)
    if (/^\d{8}$/.test(dateStr)) {
      return `${dateStr.slice(6, 8)}/${dateStr.slice(4, 6)}/${dateStr.slice(0, 4)}`;
    }
    // Handle YYYY-MM-DD format (ISO 8601)
    if (/^\d{4}-\d{2}-\d{2}$/.test(dateStr)) {
      const [year, month, day] = dateStr.split('-');
      return `${day}/${month}/${year}`;
    }
    return dateStr;
  };

  // Determine voucher type based on transaction data
  const determineVoucherType = (tx) => {
    const amount = parseFloat(tx.amount) || 0;
    const narration = (tx.narration || tx.description || "").toLowerCase();

    // Check narration for hints
    if (narration.includes("salary") || narration.includes("wages")) {
      return "Payment";
    }
    if (narration.includes("received") || narration.includes("collection")) {
      return "Receipt";
    }
    if (narration.includes("transfer") && narration.includes("self")) {
      return "Contra";
    }

    // Based on amount sign
    return amount >= 0 ? "Receipt" : "Payment";
  };

  // Filter and sort transactions
  const filterAndSortTransactions = useCallback(() => {
    let filtered = [...transactions];

    // Apply global search filter
    if (searchTerm) {
      const term = searchTerm.toLowerCase();
      filtered = filtered.filter(
        (tx) =>
          tx.narration.toLowerCase().includes(term) ||
          tx.partyLedger.toLowerCase().includes(term) ||
          tx.reference.toLowerCase().includes(term) ||
          tx.amount.toString().includes(term)
      );
    }

    // Apply column-specific filters
    if (columnFilters.date) {
      const term = columnFilters.date.toLowerCase();
      filtered = filtered.filter((tx) => tx.date.toLowerCase().includes(term));
    }
    if (columnFilters.narration) {
      const term = columnFilters.narration.toLowerCase();
      filtered = filtered.filter((tx) =>
        tx.narration.toLowerCase().includes(term) ||
        (tx.reference && tx.reference.toLowerCase().includes(term))
      );
    }
    if (columnFilters.debit) {
      const term = columnFilters.debit;
      filtered = filtered.filter((tx) => {
        const debit = tx.amount < 0 ? Math.abs(tx.amount) : 0;
        return debit > 0 && debit.toString().includes(term);
      });
    }
    if (columnFilters.credit) {
      const term = columnFilters.credit;
      filtered = filtered.filter((tx) => {
        const credit = tx.amount >= 0 ? tx.amount : 0;
        return credit > 0 && credit.toString().includes(term);
      });
    }
    if (columnFilters.voucherType) {
      const term = columnFilters.voucherType.toLowerCase();
      filtered = filtered.filter((tx) => tx.type.toLowerCase().includes(term));
    }
    if (columnFilters.partyLedger) {
      const term = columnFilters.partyLedger.toLowerCase();
      filtered = filtered.filter((tx) => tx.partyLedger.toLowerCase().includes(term));
    }

    // Apply sorting
    filtered.sort((a, b) => {
      let aVal = a[sortConfig.key];
      let bVal = b[sortConfig.key];

      if (sortConfig.key === "date") {
        aVal = new Date(aVal);
        bVal = new Date(bVal);
      } else if (sortConfig.key === "amount") {
        aVal = parseFloat(aVal) || 0;
        bVal = parseFloat(bVal) || 0;
      }

      if (aVal < bVal) return sortConfig.direction === "asc" ? -1 : 1;
      if (aVal > bVal) return sortConfig.direction === "asc" ? 1 : -1;
      return 0;
    });

    setFilteredTransactions(filtered);
  }, [transactions, searchTerm, sortConfig, columnFilters]);

  // Fetch AI suggestions for ledgers
  const fetchAISuggestions = async (txList = transactions) => {
    if (txList.length === 0) return;

    setIsLoadingAI(true);
    try {
      const transactionsForAI = txList.map((tx, index) => ({
        index,
        narration: tx.narration,
        amount: tx.amount,
        type: tx.type,
        date: tx.date,
        reference: tx.reference,
      }));

      const response = await tallyApi.aiSuggestFields({
        document_type: "Bank Entries",
        transactions: transactionsForAI,
        company: selectedCompany,
        available_ledgers: localLedgers,
        bank_ledger: bankLedger,
      });

      if (response.suggestions) {
        const suggestionsMap = {};
        response.suggestions.forEach((suggestion) => {
          suggestionsMap[suggestion.index] = {
            ledger: suggestion.suggested_ledger || suggestion.partyLedger,
            confidence: suggestion.confidence || 0.5,
            reason: suggestion.reason || "",
          };
        });
        setAiSuggestions(suggestionsMap);

        // Auto-apply high-confidence suggestions
        setTransactions((prev) =>
          prev.map((tx, index) => {
            const suggestion = suggestionsMap[index];
            if (suggestion && suggestion.confidence >= 0.8 && !tx.partyLedger) {
              return {
                ...tx,
                suggestedLedger: suggestion.ledger,
                suggestedConfidence: suggestion.confidence,
                partyLedger: suggestion.ledger,
              };
            } else if (suggestion) {
              return {
                ...tx,
                suggestedLedger: suggestion.ledger,
                suggestedConfidence: suggestion.confidence,
              };
            }
            return tx;
          })
        );
      }
    } catch (error) {
      console.error("Error fetching AI suggestions:", error);
      setSendStatus("error");
      setStatusMessage("Failed to get AI suggestions");
    } finally {
      setIsLoadingAI(false);
    }
  };

  // Check for duplicate transactions within the current list
  const checkDuplicates = async () => {
    setIsCheckingDuplicates(true);
    try {
      const duplicatesMap = {};
      let exactDuplicateCount = 0;
      let similarCount = 0;

      // Check for duplicates within the current transaction list
      for (let i = 0; i < transactions.length; i++) {
        const tx1 = transactions[i];
        const matches = [];
        let isExactDuplicate = false;

        for (let j = 0; j < transactions.length; j++) {
          if (i === j) continue;

          const tx2 = transactions[j];

          // Check for exact duplicate (same date, amount, and narration)
          const sameDate = tx1.date === tx2.date;
          const sameAmount = Math.abs(tx1.amount) === Math.abs(tx2.amount);
          const sameNarration = tx1.narration.trim().toLowerCase() === tx2.narration.trim().toLowerCase();
          const sameReference = tx1.reference && tx2.reference && tx1.reference === tx2.reference;
          const samePartyLedger = tx1.partyLedger && tx2.partyLedger &&
            tx1.partyLedger.toLowerCase() === tx2.partyLedger.toLowerCase();

          if (sameDate && sameAmount && (sameNarration || sameReference)) {
            isExactDuplicate = true;
            matches.push({
              row: j + 1,
              date: tx2.date,
              amount: tx2.amount,
              narration: tx2.narration.slice(0, 50),
              matchType: "exact",
            });
          } else {
            // Check for similar transactions (fuzzy match)
            let similarityScore = 0;
            if (sameDate) similarityScore += 30;
            if (sameAmount) similarityScore += 30;
            if (samePartyLedger) similarityScore += 20;

            // Check narration similarity (contains significant words)
            const words1 = tx1.narration.toUpperCase().split(/\s+/).filter(w => w.length > 3);
            const words2 = tx2.narration.toUpperCase().split(/\s+/).filter(w => w.length > 3);
            const commonWords = words1.filter(w => words2.includes(w));
            if (commonWords.length >= 2) similarityScore += 20;

            if (similarityScore >= 60) {
              matches.push({
                row: j + 1,
                date: tx2.date,
                amount: tx2.amount,
                narration: tx2.narration.slice(0, 50),
                matchType: "similar",
                similarity: similarityScore,
              });
            }
          }
        }

        if (isExactDuplicate) {
          duplicatesMap[tx1.id] = {
            isDuplicate: true,
            info: `Exact match with row(s): ${matches.filter(m => m.matchType === "exact").map(m => m.row).join(", ")}`,
            matches: matches.filter(m => m.matchType === "exact"),
            status: "duplicate",
          };
          exactDuplicateCount++;
        } else if (matches.length > 0) {
          duplicatesMap[tx1.id] = {
            isPotentialDuplicate: true,
            info: `Similar to row(s): ${matches.map(m => m.row).join(", ")}`,
            matches: matches,
            status: "similar",
          };
          similarCount++;
        }
      }

      setDuplicates(duplicatesMap);

      if (exactDuplicateCount > 0 || similarCount > 0) {
        setSendStatus("warning");
        setStatusMessage(`Found ${exactDuplicateCount} exact duplicates and ${similarCount} similar transactions within uploaded data`);
      } else {
        setSendStatus("success");
        setStatusMessage("No duplicates found - all transactions are unique");
      }
    } catch (error) {
      console.error("Error checking duplicates:", error);
      setSendStatus("error");
      setStatusMessage("Failed to check duplicates: " + (error.response?.data?.detail || error.message));
    } finally {
      setIsCheckingDuplicates(false);
    }
  };

  // Apply mapping rules to transactions
  const applyMappingRules = async () => {
    try {
      const txData = transactions.map((tx, index) => ({
        index,
        description: tx.narration,
        amount: tx.amount,
        party_name: tx.partyLedger,
      }));

      const response = await tallyApi.applyMappingRules(
        selectedCompany,
        "bank_statement",
        txData
      );

      if (response.results) {
        let appliedCount = 0;
        setTransactions((prev) =>
          prev.map((tx, index) => {
            const result = response.results.find((r) => r.index === index);
            if (result?.matched_rule && result?.target_ledger) {
              appliedCount++;
              return {
                ...tx,
                partyLedger: result.target_ledger,
                appliedRule: result.matched_rule,
              };
            }
            return tx;
          })
        );

        setSendStatus("success");
        setStatusMessage(`Applied rules to ${appliedCount} transactions`);
      }
    } catch (error) {
      console.error("Error applying rules:", error);
      setSendStatus("error");
      setStatusMessage("Failed to apply mapping rules");
    }
  };

  // Update single transaction field
  const updateTransaction = (id, field, value) => {
    setTransactions((prev) =>
      prev.map((tx) =>
        tx.id === id ? { ...tx, [field]: value, isEdited: true } : tx
      )
    );
  };

  // Toggle row selection
  const toggleRowSelection = (id) => {
    setSelectedRows((prev) => {
      const newSet = new Set(prev);
      if (newSet.has(id)) {
        newSet.delete(id);
      } else {
        newSet.add(id);
      }
      return newSet;
    });
  };

  // Toggle select all
  const toggleSelectAll = () => {
    const allSelected = filteredTransactions.length > 0 && selectedRows.size === filteredTransactions.length;
    if (allSelected) {
      setSelectedRows(new Set());
      setSelectAll(false);
    } else {
      setSelectedRows(new Set(filteredTransactions.map((tx) => tx.id)));
      setSelectAll(true);
    }
  };

  // Apply bulk voucher type
  const applyBulkVoucherType = () => {
    if (!bulkVoucherType || selectedRows.size === 0) return;

    setTransactions((prev) =>
      prev.map((tx) =>
        selectedRows.has(tx.id)
          ? { ...tx, voucherType: bulkVoucherType, isEdited: true }
          : tx
      )
    );

    setSendStatus("success");
    setStatusMessage(`Applied ${bulkVoucherType} to ${selectedRows.size} transactions`);
    setBulkVoucherType("");
  };

  // Apply bulk ledger
  const applyBulkLedger = () => {
    if (!bulkLedger || selectedRows.size === 0) return;

    setTransactions((prev) =>
      prev.map((tx) =>
        selectedRows.has(tx.id)
          ? { ...tx, partyLedger: bulkLedger, isEdited: true }
          : tx
      )
    );

    setSendStatus("success");
    setStatusMessage(`Applied ledger to ${selectedRows.size} transactions`);
    setBulkLedger("");
  };

  // Skip selected transactions
  const skipSelectedTransactions = () => {
    setTransactions((prev) =>
      prev.map((tx) =>
        selectedRows.has(tx.id) ? { ...tx, isSkipped: true } : tx
      )
    );
    setSelectedRows(new Set());
    setSendStatus("success");
    setStatusMessage(`Skipped ${selectedRows.size} transactions`);
  };

  // Delete selected transactions
  const deleteSelectedTransactions = () => {
    setTransactions((prev) => prev.filter((tx) => !selectedRows.has(tx.id)));
    setSelectedRows(new Set());
    setSendStatus("success");
    setStatusMessage("Removed selected transactions");
  };

  // Accept AI suggestion for a transaction
  const acceptSuggestion = (id) => {
    const suggestion = aiSuggestions[transactions.findIndex((tx) => tx.id === id)];
    if (suggestion) {
      updateTransaction(id, "partyLedger", suggestion.ledger);
    }
  };

  // Accept all AI suggestions
  const acceptAllSuggestions = () => {
    setTransactions((prev) =>
      prev.map((tx, index) => {
        const suggestion = aiSuggestions[index];
        if (suggestion && suggestion.confidence >= 0.5) {
          return { ...tx, partyLedger: suggestion.ledger, isEdited: true };
        }
        return tx;
      })
    );

    setSendStatus("success");
    setStatusMessage("Applied all AI suggestions");
  };

  // Step 1: Validate and show confirmation
  const handlePushToTally = async () => {
    // If no rows selected, auto-select all valid transactions
    let effectiveSelection = selectedRows;
    if (selectedRows.size === 0) {
      const validIds = transactions
        .filter((tx) => !tx.isSkipped && tx.partyLedger && tx.voucherType)
        .map((tx) => tx.id);
      if (validIds.length === 0) {
        setSendStatus("error");
        setStatusMessage("No valid transactions to push. Ensure transactions have Party Ledger and Voucher Type assigned.");
        return;
      }
      effectiveSelection = new Set(validIds);
      setSelectedRows(effectiveSelection);
    }

    const validTransactions = transactions.filter((tx) => effectiveSelection.has(tx.id) && !tx.isSkipped);
    const invalidTx = validTransactions.filter((tx) => !tx.partyLedger || !tx.voucherType);
    const missingDateTx = validTransactions.filter((tx) => !tx.date || !tx.date.trim());

    if (validTransactions.length === 0) {
      setSendStatus("error");
      setStatusMessage("No valid transactions in selection. Please select non-skipped transactions with ledger mappings.");
      return;
    }

    if (missingDateTx.length > 0) {
      setSendStatus("error");
      setStatusMessage(`${missingDateTx.length} transaction(s) missing date. Please fill in all dates before pushing.`);
      return;
    }

    if (invalidTx.length > 0) {
      setSendStatus("error");
      setStatusMessage(`${invalidTx.length} transactions missing required fields (Party Ledger or Voucher Type)`);
      return;
    }

    if (!bankLedger) {
      setSendStatus("error");
      setStatusMessage("Please select a Bank Ledger before pushing to Tally");
      return;
    }

    // Show confirmation modal - connection will be verified during actual push
    setShowPushConfirm(true);
  };

  // Step 2: Confirm and push
  const confirmPushToTally = async () => {
    setShowPushConfirm(false);
    setIsSending(true);
    setSendStatus(null);
    setPushProgress({ status: 'validating', message: 'Validating transactions...' });

    try {
      // Only push selected rows (selection is required by handlePushToTally)
      const validTransactions = transactions.filter((tx) => selectedRows.has(tx.id) && !tx.isSkipped);

      console.log(`[Push] Pushing ${validTransactions.length} of ${transactions.length} transactions (${selectedRows.size} selected)`);

      const vouchers = validTransactions.map((tx) => ({
        date: tx.date,
        voucher_type: tx.voucherType,
        narration: tx.narration,
        amount: Math.abs(tx.amount),
        party_ledger: tx.partyLedger,
        bank_ledger: bankLedger,
        reference: tx.reference || '',
        voucher_number: tx.voucherNumber || '',
        _txId: tx.id, // Track which transaction this is
      }));

      // Try validation but don't block on connection failures (validation requires direct Tally connection)
      // In production, the backend cannot reach user's local Tally, so we skip validation and proceed with push
      try {
        const validation = await tallyApi.validatePrePush({
          company: selectedCompany,
          bank_ledger: bankLedger,
          transactions: vouchers,
        });

        // Check if validation failed due to connection issues - skip and proceed with push
        const hasConnectionError = validation.errors?.some(err =>
          err.toLowerCase().includes('connect') ||
          err.toLowerCase().includes('connection') ||
          err.toLowerCase().includes('failed')
        );

        if (hasConnectionError) {
          console.warn("Pre-push validation skipped (Tally connection issue):", validation.errors);
          // Don't block - proceed to push which uses websocket
        } else if (validation.missing_ledgers && validation.missing_ledgers.length > 0) {
          setValidationResult(validation);
          setShowMissingLedgersDialog(true);
          setIsSending(false);
          setPushProgress(null);
          return;
        } else if (validation.errors && validation.errors.length > 0) {
          // Only block on non-connection errors
          setSendStatus("error");
          setStatusMessage(`Validation errors: ${validation.errors.join('; ')}`);
          setIsSending(false);
          setPushProgress(null);
          return;
        }
      } catch (validationError) {
        // Validation API call failed - skip and try push anyway
        console.warn("Pre-push validation skipped (API error):", validationError.message);
      }

      // Execute push
      await executePush(vouchers);
    } catch (error) {
      console.error("Error pushing to Tally:", error);
      setSendStatus("error");
      setStatusMessage(error.response?.data?.detail || error.response?.data?.error || "Failed to push to Tally");
      setIsSending(false);
      setPushProgress(null);
    }
  };

  // Step 3: Execute the actual push using websocket-based push (via desktop app)
  const executePush = async (vouchers) => {
    try {
      setPushProgress({ status: 'pushing', message: `Pushing ${vouchers.length} vouchers to Tally...` });

      // DEBUG: Log vouchers being pushed
      console.group("TALLY PUSH DEBUG");
      console.log("Company:", selectedCompany);
      console.log("Bank Ledger:", bankLedger);
      console.log("Number of vouchers:", vouchers.length);
      console.log("Vouchers:", JSON.stringify(vouchers, null, 2));

      // Validate dates
      vouchers.forEach((v, i) => {
        if (!v.date || !v.date.trim()) {
          console.warn(`Voucher ${i}: MISSING DATE`);
        }
        if (!v.party_ledger || !v.party_ledger.trim()) {
          console.warn(`Voucher ${i}: MISSING PARTY LEDGER`);
        }
        if (!v.amount || v.amount === 0) {
          console.warn(`Voucher ${i}: ZERO AMOUNT`);
        }
      });

      // Log unique ledgers being referenced
      const uniqueLedgers = [...new Set(vouchers.map(v => v.party_ledger))];
      console.log("Unique party ledgers:", uniqueLedgers);
      console.log("Ensure ALL these ledgers exist in Tally!");
      console.groupEnd();

      // Mark all transactions as pushing
      const newStatuses = {};
      vouchers.forEach(v => {
        if (v._txId) newStatuses[v._txId] = { status: 'pushing' };
      });
      setPushStatuses(prev => ({ ...prev, ...newStatuses }));

      // Step 1: Generate XML on backend
      setPushProgress({ status: 'generating', message: 'Generating Tally XML...' });
      const xmlResponse = await tallyApi.generateBankXml({
        company: selectedCompany,
        bank_ledger: bankLedger,
        vouchers: vouchers,  // Backend expects "vouchers" not "transactions"
      });

      if (!xmlResponse.xml) {
        throw new Error("Failed to generate XML");
      }

      // Step 2: Push XML via websocket (works through desktop app)
      setPushProgress({ status: 'pushing', message: `Pushing ${vouchers.length} vouchers to Tally...` });
      const pushResponse = await tallyApi.pushToTally({
        company: selectedCompany,
        xml: xmlResponse.xml,
      });

      // DEBUG: Log full response
      console.group("TALLY PUSH RESPONSE");
      console.log("Full response:", JSON.stringify(pushResponse, null, 2));
      console.groupEnd();

      // Handle pushToTally response format (different from pushVouchersBatch)
      // Check for explicit failure
      if (pushResponse?.success === false) {
        const errMsg = pushResponse?.message || pushResponse?.error || "Push failed";
        const failedStatuses = {};
        vouchers.forEach(v => {
          if (v._txId) failedStatuses[v._txId] = { status: 'failed', error: errMsg };
        });
        setPushStatuses(prev => ({ ...prev, ...failedStatuses }));
        setSendStatus("error");
        setStatusMessage(errMsg);
        return;
      }

      // Check for Tally-level error in data
      if (pushResponse?.data?.error) {
        const tallyErr = pushResponse.data.error;
        const errStr = typeof tallyErr === "string" ? tallyErr : JSON.stringify(tallyErr);
        const failedStatuses = {};
        vouchers.forEach(v => {
          if (v._txId) failedStatuses[v._txId] = { status: 'failed', error: errStr };
        });
        setPushStatuses(prev => ({ ...prev, ...failedStatuses }));
        setSendStatus("error");
        setStatusMessage(`Tally Error: ${errStr}`);
        return;
      }

      // Check for Tally exception
      if (pushResponse?.data?.exception > 0 || pushResponse?.exception > 0) {
        const lineError = pushResponse?.data?.line_error || pushResponse?.line_error
          || pushResponse?.data?.LINEERROR || pushResponse?.LINEERROR
          || pushResponse?.data?.description || pushResponse?.description
          || "Tally reported an exception. Check the voucher data.";
        const failedStatuses = {};
        vouchers.forEach(v => {
          if (v._txId) failedStatuses[v._txId] = { status: 'failed', error: lineError };
        });
        setPushStatuses(prev => ({ ...prev, ...failedStatuses }));
        setSendStatus("error");
        setStatusMessage(`Tally Exception: ${lineError}`);
        return;
      }

      // Success - mark all vouchers as pushed
      const updatedStatuses = {};
      vouchers.forEach(v => {
        if (v._txId) {
          updatedStatuses[v._txId] = {
            status: 'pushed',
            voucherId: pushResponse?.data?.voucher_id || '',
          };
        }
      });
      setPushStatuses(prev => ({ ...prev, ...updatedStatuses }));

      setSendStatus("success");
      setTallyPushSucceeded(true);
      setStatusMessage(`Successfully pushed ${vouchers.length} voucher(s) to Tally!`);
      // Clear selection after successful push
      setSelectedRows(new Set());
      setSelectAll(false);
    } catch (error) {
      console.groupEnd(); // close TALLY PUSH RESPONSE group if still open
      console.group("TALLY PUSH ERROR");
      console.error("Error during push:", error);
      console.error("Response status:", error.response?.status);
      console.error("Response data:", error.response?.data);
      if (error.response?.status === 503) {
        console.error("Possible causes: Tally not running, wrong port, firewall blocking localhost:9000");
      }
      console.groupEnd();

      // Extract error message - handle FastAPI validation errors (array of objects)
      let errorDetail = error.response?.data?.detail || error.response?.data?.error || error.message;
      if (Array.isArray(errorDetail)) {
        // FastAPI validation error format: [{type, loc, msg, input}, ...]
        errorDetail = errorDetail.map(e => e.msg || JSON.stringify(e)).join('; ');
      } else if (typeof errorDetail === 'object') {
        errorDetail = JSON.stringify(errorDetail);
      }

      // Mark all as failed
      const failedStatuses = {};
      vouchers.forEach(v => {
        if (v._txId) failedStatuses[v._txId] = { status: 'failed', error: errorDetail };
      });
      setPushStatuses(prev => ({ ...prev, ...failedStatuses }));

      setSendStatus("error");
      if (error.response?.status === 503) {
        setStatusMessage("Cannot connect to Tally. Ensure Tally Prime is running with XML Server enabled (F12 > Advanced Configuration > Enable ODBC Server > Yes).");
      } else {
        setStatusMessage(errorDetail || "Failed to push to Tally");
      }
    } finally {
      setIsSending(false);
      setPushProgress(null);
    }
  };

  // Handle creating missing ledgers and retry push
  const handleCreateMissingLedgers = async () => {
    setShowMissingLedgersDialog(false);
    setIsSending(true);

    try {
      const ledgersToCreate = validationResult.missing_ledgers.map(name => ({
        name,
        parent_group: "Sundry Debtors"
      }));

      await tallyApi.createMissingLedgers({
        company: selectedCompany,
        ledgers: ledgersToCreate,
      });

      // Retry the push
      const validTransactions = transactions.filter((tx) => !tx.isSkipped);
      const vouchers = validTransactions.map((tx) => ({
        date: tx.date,
        voucher_type: tx.voucherType,
        narration: tx.narration,
        amount: Math.abs(tx.amount),
        party_ledger: tx.partyLedger,
        bank_ledger: bankLedger,
        reference: tx.reference || '',
        voucher_number: tx.voucherNumber || '',
      }));

      await executePush(vouchers);
    } catch (error) {
      console.error("Error creating ledgers:", error);
      setSendStatus("error");
      setStatusMessage("Failed to create missing ledgers: " + (error.response?.data?.detail || error.message));
      setIsSending(false);
    }
  };

  // Generate and download XML
  const handleDownloadXml = async () => {
    // If rows are selected, export only those; otherwise export all valid
    let validTransactions;
    if (selectedRows.size > 0) {
      validTransactions = transactions.filter((tx) => selectedRows.has(tx.id) && !tx.isSkipped && tx.partyLedger);
    } else {
      validTransactions = transactions.filter((tx) => !tx.isSkipped && tx.partyLedger);
    }

    if (validTransactions.length === 0) {
      setSendStatus("error");
      setStatusMessage(selectedRows.size > 0 ? "No valid transactions in selection to export" : "No valid transactions to export");
      return;
    }

    try {
      // Prepare vouchers
      const vouchers = validTransactions.map((tx) => ({
        date: tx.date,
        voucher_type: tx.voucherType,
        narration: tx.narration,
        amount: Math.abs(tx.amount),
        party_ledger: tx.partyLedger,
        bank_ledger: bankLedger,
        reference: tx.reference,
        voucher_number: tx.voucherNumber,
      }));

      const response = await tallyApi.generateBankXml({
        vouchers,
        company: selectedCompany,
        bank_ledger: bankLedger,
      });

      if (response.success && response.xml) {
        // Create a download link
        const blob = new Blob([response.xml], { type: "application/xml" });
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = `bank_vouchers_${selectedCompany.replace(/[^a-zA-Z0-9]/g, "_")}_${new Date().toISOString().slice(0, 10)}.xml`;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        URL.revokeObjectURL(url);

        setSendStatus("success");
        setStatusMessage(`Downloaded XML with ${vouchers.length} vouchers`);
      } else {
        throw new Error("Failed to generate XML");
      }
    } catch (error) {
      console.error("Error generating XML:", error);
      setSendStatus("error");
      setStatusMessage(error.response?.data?.error || "Failed to generate XML");
    }
  };

  // Handle sort
  const handleSort = (key) => {
    setSortConfig((prev) => ({
      key,
      direction: prev.key === key && prev.direction === "asc" ? "desc" : "asc",
    }));
  };

  // Get voucher type color
  const getVoucherTypeColor = (type) => {
    const found = VOUCHER_TYPES.find((vt) => vt.value === type);
    return found?.color || "gray";
  };

  // Stats - use type field (receipt/payment) instead of amount sign
  const stats = useMemo(() => {
    const total = transactions.length;
    const withLedger = transactions.filter((tx) => tx.partyLedger).length;
    const skipped = transactions.filter((tx) => tx.isSkipped).length;
    // Use type field to determine credit/debit
    const totalCredit = transactions
      .filter((tx) => tx.type?.toLowerCase() === "receipt" || tx.voucherType === "Receipt")
      .reduce((sum, tx) => sum + Math.abs(tx.amount), 0);
    const totalDebit = transactions
      .filter((tx) => tx.type?.toLowerCase() === "payment" || tx.voucherType === "Payment")
      .reduce((sum, tx) => sum + Math.abs(tx.amount), 0);

    return { total, withLedger, skipped, totalCredit, totalDebit };
  }, [transactions]);

  // Render loading state
  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="text-center">
          <Loader2 className="h-12 w-12 text-gilt-brass animate-spin mx-auto mb-4" />
          <p className="text-gilt-soft">Parsing bank statement...</p>
        </div>
      </div>
    );
  }

  // Render error state
  if (parseError) {
    return (
      <div className="min-h-screen p-6">
        <div className="max-w-2xl mx-auto">
          <button onClick={onBack} className="flex items-center text-gilt-soft hover:text-gilt-ink transition-colors mb-6">
            <ArrowLeft className="h-5 w-5 mr-2" />
            Back
          </button>
          <div className="bg-gilt-card border border-gilt-hairline rounded-xl p-8 text-center">
            <AlertCircle className="h-16 w-16 text-gilt-debit mx-auto mb-4" />
            <h2 className="text-xl font-bold text-gilt-ink mb-2">Failed to Parse Statement</h2>
            <p className="text-gilt-soft mb-6">{parseError}</p>
            <button onClick={onBack} className="px-6 py-2 bg-gilt-brass text-gilt-paper rounded-md hover:opacity-90 transition-opacity">
              Try Again
            </button>
          </div>
        </div>
      </div>
    );
  }

  // Render empty state
  if (transactions.length === 0) {
    return (
      <div className="min-h-screen p-6">
        <div className="max-w-2xl mx-auto">
          <button onClick={onBack} className="flex items-center text-gilt-soft hover:text-gilt-ink transition-colors mb-6">
            <ArrowLeft className="h-5 w-5 mr-2" />
            Back
          </button>
          <div className="bg-gilt-card border border-gilt-hairline rounded-xl p-8 text-center">
            <FileText className="h-16 w-16 text-gilt-faint mx-auto mb-4" />
            <h2 className="text-xl font-bold text-gilt-ink mb-2">No Transactions Found</h2>
            <p className="text-gilt-soft mb-6">Could not extract any transactions from the uploaded file.</p>
            <button onClick={onBack} className="px-6 py-2 bg-gilt-brass text-gilt-paper rounded-md hover:opacity-90 transition-opacity">
              Upload Different File
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="h-screen flex flex-col">
      {/* Header */}
      <div className="bg-gilt-card border-b border-gilt-hairline flex-shrink-0 z-30">
        <div className="px-6 py-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-4">
              <button onClick={onBack} className="p-2 hover:bg-gilt-sunken rounded-lg transition-colors">
                <ArrowLeft className="h-5 w-5 text-gilt-soft" />
              </button>
              <div>
                <h1 className="gl-display text-xl text-gilt-ink">Bank Statement Entries</h1>
                <p className="text-sm text-gilt-soft">
                  {selectedCompany} • {bankLedger || "No bank selected"}
                </p>
              </div>
            </div>

            {/* Status Message */}
            {sendStatus && (
              <div className={`flex items-center gap-2 px-4 py-2 rounded-lg border bg-gilt-sunken ${
                sendStatus === "success" ? "border-gilt-credit/40 text-gilt-credit" :
                sendStatus === "warning" ? "border-gilt-brass/40 text-gilt-brass" :
                "border-gilt-debit/40 text-gilt-debit"
              }`}>
                {sendStatus === "success" ? <CheckCircle className="h-4 w-4" /> :
                 sendStatus === "warning" ? <AlertTriangle className="h-4 w-4" /> :
                 <AlertCircle className="h-4 w-4" />}
                <span className="text-sm">{statusMessage}</span>
                <button onClick={() => setSendStatus(null)} className="ml-2">
                  <X className="h-4 w-4" />
                </button>
              </div>
            )}

            <div className="flex items-center gap-3">
              <button
                onClick={() => setShowMappingRules(!showMappingRules)}
                className={`flex items-center gap-2 px-4 py-2 rounded-lg border transition-colors ${
                  showMappingRules ? "bg-gilt-sunken border-gilt-brass text-gilt-brass" : "border-gilt-hairline text-gilt-soft hover:border-gilt-brass hover:text-gilt-brass"
                }`}
              >
                <Settings className="h-4 w-4" />
                Rules
              </button>
              <button
                onClick={handleDownloadXml}
                disabled={isSending || stats.withLedger === 0}
                className={`flex items-center gap-2 px-4 py-2 rounded-lg border transition-colors ${
                  stats.withLedger === 0
                    ? "border-gilt-hairline text-gilt-faint cursor-not-allowed"
                    : "border-gilt-hairline text-gilt-soft hover:border-gilt-brass hover:text-gilt-brass"
                }`}
              >
                <Download className="h-4 w-4" />
                Download XML
              </button>
              {/* Tally Connection Status - only show if we have definitive status */}
              <div className="flex items-center gap-1.5 mr-3" title={
                tallyConnection.connected === false
                  ? "Tally connection check failed. Push may still work if Tally is running."
                  : tallyConnection.company ? `Connected to: ${tallyConnection.company}` : "Ready to push"
              }>
                <div className={`w-2 h-2 rounded-full ${
                  tallyConnection.checking ? 'bg-gilt-gold animate-pulse' :
                  tallyConnection.connected === true ? 'bg-gilt-credit' :
                  tallyConnection.connected === false ? 'bg-gilt-faint' : 'bg-gilt-gold'
                }`} />
                <span className={`text-xs ${
                  tallyConnection.connected === true ? 'text-gilt-credit' :
                  tallyConnection.connected === false ? 'text-gilt-soft' : 'text-gilt-brass'
                }`}>
                  {tallyConnection.checking ? 'Checking...' :
                   tallyConnection.connected === true
                     ? `Tally Connected${tallyConnection.company ? ` (${tallyConnection.company})` : ''}`
                     : tallyConnection.connected === false ? 'Tally Status Unknown' : 'Ready'}
                </span>
              </div>
              <SaveDraftButton onClick={handleSaveDraft} busy={savingDraft} label={draftId ? "Update draft" : "Save draft"} />
              <button
                onClick={handlePushToTally}
                disabled={isSending}
                title={selectedRows.size === 0 ? "Select transactions to push, or click to push all valid transactions" : ""}
                className={`flex items-center gap-2 px-6 py-2 rounded-md font-medium transition-opacity ${
                  isSending
                    ? "bg-gilt-sunken text-gilt-faint cursor-not-allowed opacity-50"
                    : "bg-gilt-brass text-gilt-paper hover:opacity-90"
                }`}
              >
                {isSending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
                Push to Tally{selectedRows.size > 0 ? <span className="font-mono tabular-nums"> ({selectedRows.size})</span> : ''}
              </button>
            </div>
          </div>
        </div>

        {/* Stats Bar */}
        <div className="px-6 py-3 bg-gilt-sunken border-t border-gilt-hairline flex items-center justify-between">
          <div className="flex items-center gap-6 text-sm">
            <span className="text-gilt-soft">
              <span className="font-semibold font-mono tabular-nums text-gilt-ink">{stats.total}</span> transactions
            </span>
            <span className="text-gilt-soft">
              <span className="font-semibold font-mono tabular-nums text-gilt-credit">{stats.withLedger}</span> mapped
            </span>
            <span className="text-gilt-soft">
              <span className="font-semibold font-mono tabular-nums text-gilt-faint">{stats.skipped}</span> skipped
            </span>
            <span className="text-gilt-credit">
              Credits: <span className="font-mono tabular-nums">₹{stats.totalCredit.toLocaleString("en-IN")}</span>
            </span>
            <span className="text-gilt-debit">
              Debits: <span className="font-mono tabular-nums">₹{stats.totalDebit.toLocaleString("en-IN")}</span>
            </span>
          </div>

          <div className="flex items-center gap-2">
            <button
              onClick={checkDuplicates}
              disabled={isCheckingDuplicates}
              className="flex items-center gap-2 px-3 py-1.5 text-sm border border-gilt-brass/40 text-gilt-brass rounded-lg hover:border-gilt-brass transition-colors"
            >
              {isCheckingDuplicates ? <Loader2 className="h-4 w-4 animate-spin" /> : <Copy className="h-4 w-4" />}
              Check Duplicates
            </button>
            <button
              onClick={applyMappingRules}
              className="flex items-center gap-2 px-3 py-1.5 text-sm border border-gilt-hairline text-gilt-soft rounded-lg hover:border-gilt-brass hover:text-gilt-brass transition-colors"
            >
              <Wand2 className="h-4 w-4" />
              Apply Rules
            </button>
          </div>
        </div>
      </div>

      {/* Main Content - Resizable Panels */}
      <PanelGroup direction="horizontal" className="flex-1 min-h-0">
        {/* PDF Panel */}
        {showPdfPanel && (
          <>
            <Panel defaultSize={50} minSize={25}>
              <div className="h-full bg-gilt-sunken">
                <div className="p-3 border-b border-gilt-hairline flex items-center justify-between">
                  <span className="text-sm font-medium text-gilt-soft">Document Preview</span>
                  <button
                    onClick={() => setShowPdfPanel(false)}
                    className="p-1.5 rounded transition-colors bg-gilt-ink/80 hover:bg-gilt-ink"
                    title="Hide PDF Preview"
                  >
                    <EyeOff className="h-4 w-4 text-gilt-paper" />
                  </button>
                </div>
                <div className="h-[calc(100%-48px)]">
                  {pdfData ? (
                    <PdfViewer pdfData={pdfData} />
                  ) : (
                    <div className="flex flex-col items-center justify-center h-full text-gilt-faint">
                      <FileText className="h-12 w-12 mb-2 text-gilt-faint" />
                      <p className="text-sm">No preview available</p>
                      <p className="text-xs text-gilt-faint mt-1">PDF data not found</p>
                    </div>
                  )}
                </div>
              </div>
            </Panel>
            <PanelResizeHandle className="w-1.5 bg-gilt-hairline hover:bg-gilt-gold transition-colors cursor-col-resize" />
          </>
        )}

        {/* Transactions Table Panel */}
        <Panel defaultSize={showPdfPanel ? 50 : 100} minSize={25}>
          <div className="h-full overflow-y-auto p-6">
          {/* Toolbar */}
          <div className="bg-gilt-card border border-gilt-hairline rounded-lg mb-4 p-4">
            <div className="flex items-center justify-between gap-4">
              {/* Search */}
              <div className="relative flex-1 max-w-md">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gilt-faint" />
                <input
                  type="text"
                  value={searchTerm}
                  onChange={(e) => setSearchTerm(e.target.value)}
                  placeholder="Search transactions..."
                  className="w-full pl-10 pr-4 py-2 text-gilt-ink bg-gilt-paper border border-gilt-hairline rounded-lg focus:outline-none focus:border-gilt-brass transition-colors placeholder:text-gilt-faint"
                />
              </div>

              <div className="flex items-center gap-2">
                {!showPdfPanel && (
                  <button
                    onClick={() => setShowPdfPanel(true)}
                    className="flex items-center gap-2 px-3 py-2 text-sm font-medium bg-gilt-ink text-gilt-paper rounded-md transition-opacity hover:opacity-90"
                    title="Show PDF Preview"
                  >
                    <Eye className="h-4 w-4" />
                    Show PDF
                  </button>
                )}

                {/* File selector */}
                {xmlData.length > 1 && (
                  <select
                    value={selectedFileIndex}
                    onChange={(e) => setSelectedFileIndex(parseInt(e.target.value))}
                    className="px-3 py-2 text-gilt-ink bg-gilt-paper border border-gilt-hairline rounded-lg focus:outline-none focus:border-gilt-brass"
                  >
                    {xmlData.map((file, index) => (
                      <option key={index} value={index} className="text-gilt-ink bg-gilt-card">
                        {file.filename || `File ${index + 1}`}
                      </option>
                    ))}
                  </select>
                )}
              </div>
            </div>

            {/* Bulk Actions */}
            {selectedRows.size > 0 && (
              <div className="mt-4 pt-4 border-t border-gilt-hairline bg-gilt-sunken -mx-6 px-6 py-4 rounded-b-lg">
                <div className="flex items-center gap-4 flex-wrap">
                  {/* Selection count badge */}
                  <div className="flex items-center gap-2 bg-gilt-ink text-gilt-paper px-3 py-1.5 rounded-full">
                    <CheckSquare className="h-4 w-4" />
                    <span className="text-sm font-medium font-mono tabular-nums">{selectedRows.size} selected</span>
                  </div>

                  {/* Divider */}
                  <div className="h-6 w-px bg-gilt-hairline" />

                  {/* Voucher Type selector */}
                  <div className="flex items-center gap-2">
                    <label className="text-sm font-medium text-gilt-soft">Voucher:</label>
                    <select
                      value={bulkVoucherType}
                      onChange={(e) => setBulkVoucherType(e.target.value)}
                      className="px-3 py-2 border border-gilt-hairline rounded-lg text-sm text-gilt-ink bg-gilt-card focus:outline-none focus:border-gilt-brass transition-colors min-w-[140px]"
                    >
                      <option value="" className="text-gilt-faint">Select type...</option>
                      {VOUCHER_TYPES.map((vt) => (
                        <option key={vt.value} value={vt.value} className="text-gilt-ink">{vt.label}</option>
                      ))}
                    </select>
                    {bulkVoucherType && (
                      <button
                        onClick={applyBulkVoucherType}
                        className="px-3 py-2 bg-gilt-brass text-gilt-paper text-sm rounded-md hover:opacity-90 transition-opacity font-medium flex items-center gap-1"
                      >
                        <Check className="h-4 w-4" />
                        Apply
                      </button>
                    )}
                  </div>

                  {/* Divider */}
                  <div className="h-6 w-px bg-gilt-hairline" />

                  {/* Ledger selector */}
                  <div className="flex items-center gap-2">
                    <label className="text-sm font-medium text-gilt-soft">Ledger:</label>
                    <select
                      value={bulkLedger}
                      onChange={(e) => setBulkLedger(e.target.value)}
                      className="px-3 py-2 border border-gilt-hairline rounded-lg text-sm text-gilt-ink bg-gilt-card focus:outline-none focus:border-gilt-brass transition-colors min-w-[180px] max-w-[250px]"
                    >
                      <option value="" className="text-gilt-faint">Select ledger...</option>
                      {localLedgers.map((ledger, i) => (
                        <option key={i} value={ledger} className="text-gilt-ink">{ledger}</option>
                      ))}
                    </select>
                    {bulkLedger && (
                      <button
                        onClick={applyBulkLedger}
                        className="px-3 py-2 bg-gilt-brass text-gilt-paper text-sm rounded-md hover:opacity-90 transition-opacity font-medium flex items-center gap-1"
                      >
                        <Check className="h-4 w-4" />
                        Apply
                      </button>
                    )}
                  </div>

                  <div className="flex-1" />

                  {/* Action buttons */}
                  <div className="flex items-center gap-2">
                    <button
                      onClick={skipSelectedTransactions}
                      className="px-3 py-2 text-sm text-gilt-brass border border-gilt-brass/40 rounded-lg hover:border-gilt-brass transition-colors font-medium flex items-center gap-1"
                    >
                      <EyeOff className="h-4 w-4" />
                      Skip
                    </button>
                    <button
                      onClick={deleteSelectedTransactions}
                      className="px-3 py-2 text-sm text-gilt-debit border border-gilt-debit/40 rounded-lg hover:border-gilt-debit transition-colors font-medium flex items-center gap-1"
                    >
                      <Trash2 className="h-4 w-4" />
                      Remove
                    </button>
                    <button
                      onClick={() => setSelectedRows(new Set())}
                      className="px-3 py-2 text-sm text-gilt-soft hover:text-gilt-ink hover:bg-gilt-card rounded-lg transition-colors font-medium flex items-center gap-1"
                    >
                      <X className="h-4 w-4" />
                      Clear
                    </button>
                  </div>
                </div>
              </div>
            )}
          </div>

          {/* Table */}
          <div className="bg-gilt-card border border-gilt-hairline rounded-lg overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead className="bg-gilt-sunken border-b border-gilt-hairline">
                  <tr>
                    <th className="px-3 py-3 text-left">
                      <input
                        type="checkbox"
                        checked={filteredTransactions.length > 0 && selectedRows.size === filteredTransactions.length}
                        onChange={toggleSelectAll}
                        className="rounded border-gilt-hairline"
                      />
                    </th>
                    <th className="px-3 py-3 text-left font-mono text-[10px] font-medium text-gilt-soft uppercase tracking-wider w-10">
                      #
                    </th>
                    {/* Date Column with Filter */}
                    <th className="px-3 py-2 text-left font-mono text-[10px] font-medium text-gilt-soft uppercase tracking-wider">
                      <div className="flex flex-col gap-1">
                        <div
                          className="flex items-center gap-1 cursor-pointer hover:text-gilt-ink transition-colors"
                          onClick={() => handleSort("date")}
                        >
                          Date
                          <ArrowUpDown className="h-3 w-3" />
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              setActiveFilterColumn(activeFilterColumn === "date" ? null : "date");
                            }}
                            className={`ml-1 p-0.5 rounded hover:bg-gilt-hairline ${columnFilters.date ? "text-gilt-brass" : ""}`}
                          >
                            <Filter className="h-3 w-3" />
                          </button>
                        </div>
                        {activeFilterColumn === "date" && (
                          <input
                            type="text"
                            value={columnFilters.date}
                            onChange={(e) => setColumnFilters(prev => ({ ...prev, date: e.target.value }))}
                            placeholder="Filter..."
                            className="w-24 px-1 py-0.5 text-xs bg-gilt-card text-gilt-ink border border-gilt-hairline rounded focus:outline-none focus:border-gilt-brass placeholder:text-gilt-faint"
                            autoFocus
                            onClick={(e) => e.stopPropagation()}
                          />
                        )}
                      </div>
                    </th>
                    {/* Narration Column with Filter */}
                    <th className="px-3 py-2 text-left font-mono text-[10px] font-medium text-gilt-soft uppercase tracking-wider">
                      <div className="flex flex-col gap-1">
                        <div className="flex items-center gap-1">
                          Narration / Description
                          <button
                            onClick={() => setActiveFilterColumn(activeFilterColumn === "narration" ? null : "narration")}
                            className={`ml-1 p-0.5 rounded hover:bg-gilt-hairline ${columnFilters.narration ? "text-gilt-brass" : ""}`}
                          >
                            <Filter className="h-3 w-3" />
                          </button>
                        </div>
                        {activeFilterColumn === "narration" && (
                          <input
                            type="text"
                            value={columnFilters.narration}
                            onChange={(e) => setColumnFilters(prev => ({ ...prev, narration: e.target.value }))}
                            placeholder="Filter..."
                            className="w-full px-1 py-0.5 text-xs bg-gilt-card text-gilt-ink border border-gilt-hairline rounded focus:outline-none focus:border-gilt-brass placeholder:text-gilt-faint"
                            autoFocus
                          />
                        )}
                      </div>
                    </th>
                    {/* Debit Column with Filter */}
                    <th className="px-3 py-2 text-right font-mono text-[10px] font-medium text-gilt-debit uppercase tracking-wider">
                      <div className="flex flex-col gap-1 items-end">
                        <div className="flex items-center gap-1">
                          Debit
                          <button
                            onClick={() => setActiveFilterColumn(activeFilterColumn === "debit" ? null : "debit")}
                            className={`ml-1 p-0.5 rounded hover:bg-gilt-hairline ${columnFilters.debit ? "text-gilt-brass" : ""}`}
                          >
                            <Filter className="h-3 w-3" />
                          </button>
                        </div>
                        {activeFilterColumn === "debit" && (
                          <input
                            type="text"
                            value={columnFilters.debit}
                            onChange={(e) => setColumnFilters(prev => ({ ...prev, debit: e.target.value }))}
                            placeholder="Filter..."
                            className="w-20 px-1 py-0.5 text-xs bg-gilt-card text-gilt-ink border border-gilt-hairline rounded focus:outline-none focus:border-gilt-brass placeholder:text-gilt-faint text-right"
                            autoFocus
                          />
                        )}
                      </div>
                    </th>
                    {/* Credit Column with Filter */}
                    <th className="px-3 py-2 text-right font-mono text-[10px] font-medium text-gilt-credit uppercase tracking-wider">
                      <div className="flex flex-col gap-1 items-end">
                        <div className="flex items-center gap-1">
                          Credit
                          <button
                            onClick={() => setActiveFilterColumn(activeFilterColumn === "credit" ? null : "credit")}
                            className={`ml-1 p-0.5 rounded hover:bg-gilt-hairline ${columnFilters.credit ? "text-gilt-brass" : ""}`}
                          >
                            <Filter className="h-3 w-3" />
                          </button>
                        </div>
                        {activeFilterColumn === "credit" && (
                          <input
                            type="text"
                            value={columnFilters.credit}
                            onChange={(e) => setColumnFilters(prev => ({ ...prev, credit: e.target.value }))}
                            placeholder="Filter..."
                            className="w-20 px-1 py-0.5 text-xs bg-gilt-card text-gilt-ink border border-gilt-hairline rounded focus:outline-none focus:border-gilt-brass placeholder:text-gilt-faint text-right"
                            autoFocus
                          />
                        )}
                      </div>
                    </th>
                    {/* Voucher Type Column with Filter */}
                    <th className="px-3 py-2 text-left font-mono text-[10px] font-medium text-gilt-soft uppercase tracking-wider">
                      <div className="flex flex-col gap-1">
                        <div className="flex items-center gap-1">
                          Voucher Type
                          <button
                            onClick={() => setActiveFilterColumn(activeFilterColumn === "voucherType" ? null : "voucherType")}
                            className={`ml-1 p-0.5 rounded hover:bg-gilt-hairline ${columnFilters.voucherType ? "text-gilt-brass" : ""}`}
                          >
                            <Filter className="h-3 w-3" />
                          </button>
                        </div>
                        {activeFilterColumn === "voucherType" && (
                          <select
                            value={columnFilters.voucherType}
                            onChange={(e) => setColumnFilters(prev => ({ ...prev, voucherType: e.target.value }))}
                            className="w-full px-1 py-0.5 text-xs bg-gilt-card text-gilt-ink border border-gilt-hairline rounded focus:outline-none focus:border-gilt-brass"
                            autoFocus
                          >
                            <option value="">All</option>
                            <option value="Receipt">Receipt</option>
                            <option value="Payment">Payment</option>
                            <option value="Contra">Contra</option>
                            <option value="Journal">Journal</option>
                          </select>
                        )}
                      </div>
                    </th>
                    {/* Party Ledger Column with Filter */}
                    <th className="px-3 py-2 text-left font-mono text-[10px] font-medium text-gilt-soft uppercase tracking-wider">
                      <div className="flex flex-col gap-1">
                        <div className="flex items-center gap-1">
                          Party Ledger
                          <button
                            onClick={() => setActiveFilterColumn(activeFilterColumn === "partyLedger" ? null : "partyLedger")}
                            className={`ml-1 p-0.5 rounded hover:bg-gilt-hairline ${columnFilters.partyLedger ? "text-gilt-brass" : ""}`}
                          >
                            <Filter className="h-3 w-3" />
                          </button>
                        </div>
                        {activeFilterColumn === "partyLedger" && (
                          <input
                            type="text"
                            value={columnFilters.partyLedger}
                            onChange={(e) => setColumnFilters(prev => ({ ...prev, partyLedger: e.target.value }))}
                            placeholder="Filter..."
                            className="w-full px-1 py-0.5 text-xs bg-gilt-card text-gilt-ink border border-gilt-hairline rounded focus:outline-none focus:border-gilt-brass placeholder:text-gilt-faint"
                            autoFocus
                          />
                        )}
                      </div>
                    </th>
                    <th className="px-3 py-3 text-center font-mono text-[10px] font-medium text-gilt-soft uppercase tracking-wider w-20">
                      Actions
                    </th>
                  </tr>
                  {/* Clear Filters Row */}
                  {Object.values(columnFilters).some(v => v) && (
                    <tr className="bg-gilt-sunken border-b border-gilt-hairline">
                      <td colSpan={9} className="px-3 py-1">
                        <div className="flex items-center justify-between text-xs">
                          <span className="text-gilt-soft">
                            Filters active: {Object.entries(columnFilters).filter(([_, v]) => v).map(([k]) => k).join(", ")}
                          </span>
                          <button
                            onClick={() => {
                              setColumnFilters({ date: "", narration: "", debit: "", credit: "", voucherType: "", partyLedger: "" });
                              setActiveFilterColumn(null);
                            }}
                            className="text-gilt-brass hover:text-gilt-ink transition-colors font-medium"
                          >
                            Clear All Filters
                          </button>
                        </div>
                      </td>
                    </tr>
                  )}
                </thead>
                <tbody className="divide-y divide-gilt-hairline">
                  {filteredTransactions.map((tx, index) => {
                    const duplicate = duplicates[tx.id];

                    return (
                      <tr
                        key={tx.id}
                        className={`transition-colors hover:bg-gilt-sunken ${
                          selectedRows.has(tx.id) ? "bg-gilt-sunken" : ""
                        } ${tx.isSkipped ? "opacity-50" : ""} ${
                          duplicate?.isDuplicate ? "bg-gilt-debit/10" : duplicate?.isPotentialDuplicate ? "bg-gilt-brass/10" : ""
                        }`}
                      >
                        {/* Checkbox */}
                        <td className="px-3 py-3">
                          <input
                            type="checkbox"
                            checked={selectedRows.has(tx.id)}
                            onChange={() => toggleRowSelection(tx.id)}
                            className="rounded border-gilt-hairline"
                          />
                        </td>

                        {/* Row Number & Duplicate Warning */}
                        <td className="px-3 py-3 text-sm font-mono tabular-nums text-gilt-faint">
                          <div className="flex items-center gap-1">
                            {index + 1}
                            {duplicate && (
                              <DuplicateWarning
                                isDuplicate={duplicate.isDuplicate}
                                isPotentialDuplicate={duplicate.isPotentialDuplicate}
                                duplicateInfo={duplicate.info}
                                fuzzyMatches={duplicate.matches}
                                onIgnoreDuplicate={() => {
                                  setDuplicates((prev) => {
                                    const newDups = { ...prev };
                                    delete newDups[tx.id];
                                    return newDups;
                                  });
                                }}
                              />
                            )}
                          </div>
                        </td>

                        {/* Date */}
                        <td className="px-3 py-3">
                          <input
                            type="text"
                            value={tx.date}
                            onChange={(e) => updateTransaction(tx.id, "date", e.target.value)}
                            className="w-24 px-2 py-1 text-sm font-mono tabular-nums text-gilt-ink bg-gilt-paper border border-gilt-hairline rounded focus:outline-none focus:border-gilt-brass transition-colors"
                          />
                        </td>

                        {/* Narration */}
                        <td className="px-3 py-3">
                          <div className="max-w-xs">
                            <input
                              type="text"
                              value={tx.narration}
                              onChange={(e) => updateTransaction(tx.id, "narration", e.target.value)}
                              className="w-full px-2 py-1 text-sm text-gilt-ink bg-gilt-paper border border-gilt-hairline rounded focus:outline-none focus:border-gilt-brass transition-colors"
                              title={tx.narration}
                            />
                            {tx.reference && (
                              <p className="text-xs font-mono text-gilt-faint mt-1">Ref: {tx.reference}</p>
                            )}
                          </div>
                        </td>

                        {/* Debit (Payment) */}
                        <td className="px-3 py-3 text-right">
                          <input
                            type="number"
                            value={
                              (tx.type?.toLowerCase() === "payment" || tx.voucherType === "Payment")
                                ? Math.abs(tx.amount) || ''
                                : ''
                            }
                            onChange={(e) => {
                              const val = parseFloat(e.target.value) || 0;
                              if (val > 0) {
                                updateTransaction(tx.id, "amount", val);
                                updateTransaction(tx.id, "voucherType", "Payment");
                                updateTransaction(tx.id, "type", "payment");
                              } else if (e.target.value === '' || val === 0) {
                                // If clearing debit, don't auto-switch
                                updateTransaction(tx.id, "amount", 0);
                              }
                            }}
                            className="w-24 px-2 py-1 text-sm text-right font-mono tabular-nums border border-gilt-hairline rounded text-gilt-debit font-medium bg-gilt-paper focus:outline-none focus:border-gilt-brass transition-colors placeholder:text-gilt-faint"
                            placeholder="0.00"
                            step="0.01"
                            min="0"
                          />
                        </td>

                        {/* Credit (Receipt) */}
                        <td className="px-3 py-3 text-right">
                          <input
                            type="number"
                            value={
                              (tx.type?.toLowerCase() === "receipt" || tx.voucherType === "Receipt")
                                ? Math.abs(tx.amount) || ''
                                : ''
                            }
                            onChange={(e) => {
                              const val = parseFloat(e.target.value) || 0;
                              if (val > 0) {
                                updateTransaction(tx.id, "amount", val);
                                updateTransaction(tx.id, "voucherType", "Receipt");
                                updateTransaction(tx.id, "type", "receipt");
                              } else if (e.target.value === '' || val === 0) {
                                updateTransaction(tx.id, "amount", 0);
                              }
                            }}
                            className="w-24 px-2 py-1 text-sm text-right font-mono tabular-nums border border-gilt-hairline rounded text-gilt-credit font-medium bg-gilt-paper focus:outline-none focus:border-gilt-brass transition-colors placeholder:text-gilt-faint"
                            placeholder="0.00"
                            step="0.01"
                            min="0"
                          />
                        </td>

                        {/* Voucher Type */}
                        <td className="px-3 py-3">
                          <select
                            value={tx.voucherType}
                            onChange={(e) => {
                              updateTransaction(tx.id, "voucherType", e.target.value);
                              // Also update the type field to match
                              const newType = e.target.value === "Receipt" ? "receipt" : "payment";
                              updateTransaction(tx.id, "type", newType);
                            }}
                            className={`px-2 py-1 text-sm font-mono border rounded focus:outline-none focus:border-gilt-brass transition-colors ${
                              tx.voucherType === "Receipt" ? "bg-gilt-sunken border-gilt-credit/40 text-gilt-credit" :
                              tx.voucherType === "Payment" ? "bg-gilt-sunken border-gilt-debit/40 text-gilt-debit" :
                              tx.voucherType === "Contra" ? "bg-gilt-sunken border-gilt-brass/40 text-gilt-brass" :
                              "bg-gilt-paper border-gilt-hairline text-gilt-ink"
                            }`}
                          >
                            {VOUCHER_TYPES.map((vt) => (
                              <option key={vt.value} value={vt.value} className="text-gilt-ink bg-gilt-card">{vt.label}</option>
                            ))}
                          </select>
                        </td>

                        {/* Party Ledger */}
                        <td className="px-3 py-3">
                          <SearchableDropdown
                            options={localLedgers}
                            value={tx.partyLedger}
                            onChange={(val) => updateTransaction(tx.id, "partyLedger", val)}
                            onCreateNew={() => {
                              setNewLedgerForRow(tx.id);
                              setShowNewLedgerModal(true);
                            }}
                            placeholder="Select ledger..."
                          />
                        </td>

                        {/* Actions */}
                        <td className="px-3 py-3 text-center">
                          <div className="flex items-center justify-center gap-1">
                            {/* Push status indicator */}
                            {pushStatuses[tx.id] && (
                              <span
                                title={
                                  pushStatuses[tx.id].status === 'pushed'
                                    ? `Pushed${pushStatuses[tx.id].voucherId ? ` (ID: ${pushStatuses[tx.id].voucherId})` : ''}`
                                    : pushStatuses[tx.id].status === 'failed'
                                    ? `Failed: ${pushStatuses[tx.id].error || 'Unknown error'}`
                                    : 'Pushing...'
                                }
                                className={`p-1 rounded ${
                                  pushStatuses[tx.id].status === 'pushed'
                                    ? 'text-gilt-credit bg-gilt-credit/10'
                                    : pushStatuses[tx.id].status === 'failed'
                                    ? 'text-gilt-debit bg-gilt-debit/10'
                                    : 'text-gilt-brass bg-gilt-brass/10'
                                }`}
                              >
                                {pushStatuses[tx.id].status === 'pushed' ? (
                                  <CheckCircle className="h-4 w-4" />
                                ) : pushStatuses[tx.id].status === 'failed' ? (
                                  <AlertCircle className="h-4 w-4" />
                                ) : (
                                  <Loader2 className="h-4 w-4 animate-spin" />
                                )}
                              </span>
                            )}
                            <button
                              onClick={() => setExpandedRow(expandedRow === tx.id ? null : tx.id)}
                              className="p-1 text-gilt-faint hover:text-gilt-soft hover:bg-gilt-sunken rounded transition-colors"
                              title="Edit details"
                            >
                              <Edit2 className="h-4 w-4" />
                            </button>
                            <button
                              onClick={() => updateTransaction(tx.id, "isSkipped", !tx.isSkipped)}
                              className={`p-1 rounded transition-colors ${tx.isSkipped ? "text-gilt-brass bg-gilt-brass/10" : "text-gilt-faint hover:text-gilt-brass hover:bg-gilt-brass/10"}`}
                              title={tx.isSkipped ? "Include" : "Skip"}
                            >
                              {tx.isSkipped ? <Eye className="h-4 w-4" /> : <EyeOff className="h-4 w-4" />}
                            </button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            {filteredTransactions.length === 0 && (
              <div className="py-12 text-center text-gilt-soft">
                <Search className="h-12 w-12 mx-auto mb-4 text-gilt-faint" />
                <p>No transactions match your search</p>
              </div>
            )}

          </div>
          </div>
        </Panel>

        {/* Mapping Rules Panel */}
        {showMappingRules && (
          <>
            <PanelResizeHandle className="w-1.5 bg-gilt-hairline hover:bg-gilt-gold transition-colors cursor-col-resize" />
            <Panel defaultSize={20} minSize={15} maxSize={30}>
              <div className="h-full bg-gilt-card overflow-y-auto">
                <MappingRulesPanel
                  documentType="bank_statement"
                  company={selectedCompany}
                  availableLedgers={localLedgers}
                  onRulesChange={applyMappingRules}
                />
              </div>
            </Panel>
          </>
        )}
      </PanelGroup>

      {/* New Ledger Modal */}
      {showNewLedgerModal && (
        <NewLedgerModal
          onClose={() => {
            setShowNewLedgerModal(false);
            setNewLedgerForRow(null);
          }}
          onCreated={(ledgerName) => {
            setLocalLedgers((prev) => [...prev, ledgerName]);
            if (newLedgerForRow !== null) {
              updateTransaction(newLedgerForRow, "partyLedger", ledgerName);
            }
            setShowNewLedgerModal(false);
            setNewLedgerForRow(null);
          }}
          selectedCompany={selectedCompany}
        />
      )}

      {/* Push Confirmation Modal */}
      {showPushConfirm && (() => {
        const validTx = transactions.filter(tx => selectedRows.has(tx.id) && !tx.isSkipped && tx.partyLedger && tx.voucherType);
        const receipts = validTx.filter(tx => tx.voucherType === 'Receipt');
        const payments = validTx.filter(tx => tx.voucherType === 'Payment');
        const contras = validTx.filter(tx => tx.voucherType === 'Contra');
        const receiptCount = receipts.length;
        const paymentCount = payments.length;
        const contraCount = contras.length;
        const receiptTotal = receipts.reduce((sum, tx) => sum + Math.abs(tx.amount), 0);
        const paymentTotal = payments.reduce((sum, tx) => sum + Math.abs(tx.amount), 0);
        const alreadyPushed = validTx.filter(tx => pushStatuses[tx.id]?.status === 'pushed').length;

        return (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-gilt-ink/40">
            <div className="bg-gilt-card border border-gilt-hairline rounded-lg p-6 max-w-md w-full mx-4">
              <h3 className="text-lg font-semibold mb-2 text-gilt-ink">Push to Tally</h3>
              <p className="text-gilt-soft mb-4">
                Push {validTx.length} transactions to Tally?
              </p>
              {alreadyPushed > 0 && (
                <div className="bg-gilt-sunken border border-gilt-brass/40 rounded p-2 mb-3 text-sm text-gilt-brass">
                  {alreadyPushed} transaction(s) were already pushed. They will be pushed again (may create duplicates).
                </div>
              )}
              <div className="bg-gilt-sunken rounded p-3 mb-4 text-sm space-y-1">
                <div className="flex justify-between">
                  <span className="text-gilt-soft">Company:</span>
                  <span className="font-medium text-gilt-ink">{selectedCompany}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-gilt-soft">Bank Ledger:</span>
                  <span className="font-medium text-gilt-ink">{bankLedger || '(not set)'}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-gilt-soft">Receipts:</span>
                  <span className="font-medium font-mono tabular-nums text-gilt-credit">{receiptCount} (&#8377;{receiptTotal.toLocaleString('en-IN', { minimumFractionDigits: 2 })})</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-gilt-soft">Payments:</span>
                  <span className="font-medium font-mono tabular-nums text-gilt-debit">{paymentCount} (&#8377;{paymentTotal.toLocaleString('en-IN', { minimumFractionDigits: 2 })})</span>
                </div>
                {contraCount > 0 && (
                  <div className="flex justify-between">
                    <span className="text-gilt-soft">Contra:</span>
                    <span className="font-medium font-mono tabular-nums text-gilt-brass">{contraCount}</span>
                  </div>
                )}
              </div>
              <div className="flex justify-end gap-3">
                <button
                  onClick={() => setShowPushConfirm(false)}
                  className="px-4 py-2 text-sm text-gilt-soft hover:text-gilt-ink border border-gilt-hairline hover:border-gilt-brass rounded transition-colors"
                >
                  Cancel
                </button>
                <button
                  onClick={confirmPushToTally}
                  className="px-4 py-2 text-sm bg-gilt-brass text-gilt-paper rounded hover:opacity-90 transition-opacity"
                >
                  Push to Tally
                </button>
              </div>
            </div>
          </div>
        );
      })()}

      {/* Missing Ledgers Dialog */}
      {showMissingLedgersDialog && validationResult && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-gilt-ink/40">
          <div className="bg-gilt-card border border-gilt-hairline rounded-lg p-6 max-w-md w-full mx-4">
            <h3 className="text-lg font-semibold mb-2 flex items-center gap-2 text-gilt-ink">
              <AlertTriangle className="h-5 w-5 text-gilt-brass" />
              Missing Ledgers
            </h3>
            <p className="text-gilt-soft mb-3 text-sm">
              The following ledgers don't exist in Tally. Create them automatically as "Sundry Debtors"?
            </p>
            <div className="bg-gilt-sunken border border-gilt-brass/40 rounded p-3 mb-4 max-h-40 overflow-y-auto">
              {validationResult.missing_ledgers.map((name, i) => (
                <div key={i} className="text-sm text-gilt-brass py-0.5">{name}</div>
              ))}
            </div>
            <div className="flex justify-end gap-3">
              <button
                onClick={() => { setShowMissingLedgersDialog(false); setIsSending(false); }}
                className="px-4 py-2 text-sm text-gilt-soft hover:text-gilt-ink border border-gilt-hairline hover:border-gilt-brass rounded transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleCreateMissingLedgers}
                className="px-4 py-2 text-sm bg-gilt-brass text-gilt-paper rounded hover:opacity-90 transition-opacity"
              >
                Create & Push
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Push Progress Overlay */}
      {pushProgress && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-gilt-ink/40">
          <div className="bg-gilt-card border border-gilt-hairline rounded-lg p-6 max-w-sm w-full mx-4 text-center">
            <Loader2 className="h-8 w-8 text-gilt-brass animate-spin mx-auto mb-3" />
            <p className="text-gilt-ink font-medium">{pushProgress.message || 'Pushing to Tally...'}</p>
            {pushProgress.current && pushProgress.total && (
              <p className="text-sm text-gilt-soft font-mono tabular-nums mt-1">
                Batch {pushProgress.current} of {pushProgress.total}
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ── NewLedgerModal: wraps the shared LedgerCreateModal, preserving the
// {onClose, onCreated, selectedCompany} contract that BankEntriesView uses.
// Defaults to "party" (Sundry Debtors) but the user can pick any parent group.
function NewLedgerModal({ onClose, onCreated, selectedCompany }) {
  const [isCreating, setIsCreating] = useState(false);
  const [error, setError] = useState(null);

  const handleCreate = async (payload) => {
    if (!payload?.name?.trim()) {
      setError("Ledger name is required");
      return;
    }
    setIsCreating(true);
    setError(null);
    try {
      const response = await tallyApi.createLedger({
        ...payload,
        company: selectedCompany,
        name: payload.name.trim(),
      });
      if (response.success || response.created > 0 || response.combined > 0) {
        onCreated(payload.name.trim());
      } else {
        const errorMsg = response.error || response.detail || "Tally did not create the ledger. Check that Tally is running and the company is open.";
        setError(errorMsg);
      }
    } catch (err) {
      console.error("Create ledger error:", err);
      const detail = err.response?.data?.detail || err.response?.data?.error || err.message || "Failed to create ledger";
      setError(detail);
    } finally {
      setIsCreating(false);
    }
  };

  return (
    <>
      <LedgerCreateModal
        open
        ledgerType="party"
        defaultParentGroup="Sundry Debtors"
        busy={isCreating}
        onClose={onClose}
        onCreate={handleCreate}
      />
      {error && (
        <div className="fixed bottom-4 right-4 z-[60] max-w-md p-3 bg-gilt-card text-gilt-debit rounded-lg text-sm flex items-center gap-2 shadow-sm border border-gilt-debit/40">
          <AlertCircle className="h-4 w-4 flex-shrink-0" />
          <span>{error}</span>
          <button onClick={() => setError(null)} className="ml-2 text-gilt-debit hover:opacity-70 transition-opacity">
            <X className="h-4 w-4" />
          </button>
        </div>
      )}
    </>
  );
}

export default BankEntriesView;
