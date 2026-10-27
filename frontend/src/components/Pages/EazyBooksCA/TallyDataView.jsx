import React, { useState } from "react";
import { ChevronDown, ChevronUp, ArrowLeft, Edit, Trash, Eye, Plus } from "lucide-react";
import { tallyApi } from "../../../services/api";
import LedgerCreateModal from "./common/LedgerCreateModal";

function TallyDataView({ xmlData, ledgersAvailable, onBack, selectedCompany, wsService }) {
  const [selectedFile, setSelectedFile] = useState(xmlData[0]?.name || "");
  const [selectedLedger, setSelectedLedger] = useState(xmlData[0]?.selected_ledger || "");
  const [showFileDropdown, setShowFileDropdown] = useState(false);
  const [showLedgerDropdown, setShowLedgerDropdown] = useState(false);
  const [isSending, setIsSending] = useState(false);
  const [sendError, setSendError] = useState(null);
  const [sendSuccess, setSendSuccess] = useState(false);
  const [showNewLedgerModal, setShowNewLedgerModal] = useState(false);
  const [isCreatingLedger, setIsCreatingLedger] = useState(false);
  const [ledgerError, setLedgerError] = useState(null);
  const [localLedgers, setLocalLedgers] = useState(ledgersAvailable || []);
  const [tallyResponseData, setTallyResponseData] = useState(null);

  // Handle file selection from dropdown
  const handleFileSelect = (fileName) => {
    setSelectedFile(fileName);
    setShowFileDropdown(false);
  };

  // Handle ledger selection from dropdown
  const handleLedgerSelect = (ledgerName) => {
    setSelectedLedger(ledgerName);
    setShowLedgerDropdown(false);
  };

  // Get XML content for selected file
  const getSelectedXmlContent = () => {
    const selectedData = xmlData.find(item => item.name === selectedFile);
    return selectedData ? selectedData.xml : "";
  };

  // Parse XML to structured data
  const parseXmlToTableData = (xmlString) => {
    try {
      const parser = new DOMParser();
      const xmlDoc = parser.parseFromString(xmlString, "text/xml");
      
      // Find all VOUCHER nodes
      const vouchers = xmlDoc.querySelectorAll("VOUCHER");
      const tableData = [];
      
      vouchers.forEach((voucher, index) => {
        const description = voucher.querySelector("NARRATION")?.textContent || "";
        const type = voucher.querySelector("VOUCHERTYPENAME")?.textContent || "";
        const amount = voucher.querySelector("AMOUNT")?.textContent || "0";
        
        tableData.push({
          srNo: index + 1,
          description,
          type,
          amount: parseFloat(amount).toFixed(2),
        });
      });
      
      return tableData;
    } catch (error) {
      console.error("Error parsing XML:", error);
      return [];
    }
  };

  const tableData = parseXmlToTableData(getSelectedXmlContent());

  // Handle sending data to Tally
  const handleSendToTally = async () => {
    if (!selectedFile) {
      setSendError("Please select a file first");
      return;
    }

    if (!selectedCompany) {
      setSendError("Company name is missing");
      return;
    }

    setIsSending(true);
    setSendError(null);
    setSendSuccess(false);
    setTallyResponseData(null);

    try {
      const selectedData = xmlData.find(item => item.name === selectedFile);
      if (!selectedData) {
        throw new Error("Selected file data not found");
      }

      // Create the payload with proper JSON formatting
      const payload = {
        xml: selectedData.xml,
        company: selectedCompany
      };

      const response = await tallyApi.pushToTally(payload);

      if (response) {
        setSendSuccess(true);
        setTallyResponseData(response?.data || null);
      }
    } catch (err) {
      console.error("Error sending to Tally:", err);
      if (err.response?.status === 401) {
        setSendError("Session expired. Please login again.");
      } else {
        setSendError(err.response?.data?.message || "Failed to send data to Tally. Please try again.");
      }
    } finally {
      setIsSending(false);
    }
  };

  // Handle new ledger creation — invoked by the shared LedgerCreateModal with
  // a payload carrying the full set of user-entered fields. The Electron
  // bridge's createLedger handler reads the same field names we send here.
  const handleCreateLedger = async (payload) => {
    if (!payload?.name?.trim()) {
      setLedgerError("Ledger name is required");
      return;
    }
    setIsCreatingLedger(true);
    setLedgerError(null);
    try {
      const requestId = Math.random().toString(36).substring(7);
      const responsePromise = new Promise((resolve, reject) => {
        let timeoutId;
        const messageHandler = (response) => {
          if (response.type === 'create-ledger-response' && response.request_id === requestId) {
            clearTimeout(timeoutId);
            wsService.off('message', messageHandler);
            if (response.error) reject(new Error(response.error));
            else if (response.success) resolve(response);
            else reject(new Error('Invalid response from server'));
          }
        };
        timeoutId = setTimeout(() => {
          wsService.off('message', messageHandler);
          reject(new Error('Timeout waiting for ledger creation response'));
        }, 30000);
        wsService.on('message', messageHandler);
      });

      wsService.send({
        type: 'create-ledger',
        request_id: requestId,
        data: { ...payload, name: payload.name.trim(), company: selectedCompany },
      });

      await responsePromise;
      const newName = payload.name.trim();
      setLocalLedgers((prev) => [...prev, newName]);
      setSelectedLedger(newName);
      setShowNewLedgerModal(false);
      setLedgerError(null);
    } catch (err) {
      console.error("Error creating ledger:", err);
      setLedgerError(err.message || "Failed to create ledger. Please try again.");
    } finally {
      setIsCreatingLedger(false);
    }
  };

  return (
    <div className="min-h-screen bg-white p-6">
      <div className="max-w-6xl mx-auto">
        <div className="flex items-center mb-6">
          <button
            onClick={onBack}
            className="flex items-center text-gray-800 hover:text-black transition-colors"
          >
            <ArrowLeft className="h-5 w-5 mr-2" />
            Back to Upload
          </button>
        </div>

        <div className="flex justify-between items-center mb-6">
          <div>
            <h1 className="text-3xl font-bold text-black mb-2">Tally Entries</h1>
            <p className="text-gray-800">Manage and view your Tally-compatible entries</p>
          </div>
          <div className="flex flex-col items-end gap-2">
            {sendError && (
              <div className="text-red-600 text-sm">
                {sendError}
              </div>
            )}
            {sendSuccess && (
              <div className="flex flex-col">
                <div className="text-green-600 text-sm">
                  Data sent to Tally successfully!
                </div>
                {tallyResponseData && (
                  <div className="mt-2 p-3 bg-gray-50 rounded-md border border-gray-200">
                    <div className="grid grid-cols-2 gap-4">
                      <div>
                        <span className="text-sm text-gray-600">Created:</span>
                        <span className="ml-2 font-medium text-gray-900">{tallyResponseData.created}</span>
                      </div>
                      <div>
                        <span className="text-sm text-gray-600">Altered:</span>
                        <span className="ml-2 font-medium text-gray-900">{tallyResponseData.altered}</span>
                      </div>
                      <div>
                        <span className="text-sm text-gray-600">Deleted:</span>
                        <span className="ml-2 font-medium text-gray-900">{tallyResponseData.deleted}</span>
                      </div>
                      <div>
                        <span className="text-sm text-gray-600">Exceptions:</span>
                        <span className="ml-2 font-medium text-gray-900">{tallyResponseData.exception}</span>
                      </div>
                      <div className="col-span-2">
                        <span className="text-sm text-gray-600">Last Voucher Number:</span>
                        <span className="ml-2 font-medium text-gray-900">{tallyResponseData.lastVoucherNumber}</span>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            )}
            <button 
              onClick={handleSendToTally}
              disabled={isSending || !selectedFile}
              className={`px-4 py-2 bg-black text-white rounded-md hover:bg-gray-800 transition-colors ${
                isSending || !selectedFile ? 'opacity-50 cursor-not-allowed' : ''
              }`}
            >
              {isSending ? 'Sending...' : 'Send to Tally'}
            </button>
          </div>
        </div>

        {/* File and Ledger Selection */}
        <div className="bg-white border border-gray-300 rounded-lg p-6 mb-6">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {/* File Dropdown */}
            <div className="relative">
              <label className="block text-sm font-medium text-gray-800 mb-1">
                Selected File
              </label>
              <div
                className="flex items-center justify-between p-2 border border-gray-300 rounded-md cursor-pointer bg-white"
                onClick={() => setShowFileDropdown(!showFileDropdown)}
              >
                <span className="text-gray-900">
                  {selectedFile || "Select a file"}
                </span>
                {showFileDropdown ? (
                  <ChevronUp className="h-5 w-5 text-gray-500" />
                ) : (
                  <ChevronDown className="h-5 w-5 text-gray-500" />
                )}
              </div>
              
              {showFileDropdown && (
                <div className="absolute z-10 w-full mt-1 bg-white border border-gray-300 rounded-md shadow-lg max-h-60 overflow-auto">
                  {xmlData.map((item, index) => (
                    <div
                      key={index}
                      className="p-2 hover:bg-gray-100 cursor-pointer text-gray-900"
                      onClick={() => handleFileSelect(item.name)}
                    >
                      {item.name}
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Ledger Dropdown */}
            <div className="relative">
              <label className="block text-sm font-medium text-gray-800 mb-1">
                Selected Ledger
              </label>
              <div className="flex gap-2">
                <div
                  className="flex-1 flex items-center justify-between p-2 border border-gray-300 rounded-md cursor-pointer bg-white"
                  onClick={() => setShowLedgerDropdown(!showLedgerDropdown)}
                >
                  <span className="text-gray-900">
                    {selectedLedger || "Select a ledger"}
                  </span>
                  {showLedgerDropdown ? (
                    <ChevronUp className="h-5 w-5 text-gray-500" />
                  ) : (
                    <ChevronDown className="h-5 w-5 text-gray-500" />
                  )}
                </div>
                <button
                  onClick={() => setShowNewLedgerModal(true)}
                  className="px-3 py-2 bg-black text-white rounded-md hover:bg-gray-800 transition-colors flex items-center gap-2"
                >
                  <Plus className="h-4 w-4" />
                  New Ledger
                </button>
              </div>
              
              {showLedgerDropdown && (
                <div className="absolute z-10 w-full mt-1 bg-white border border-gray-300 rounded-md shadow-lg max-h-60 overflow-auto">
                  {localLedgers.map((ledger, index) => (
                    <div
                      key={index}
                      className="p-2 hover:bg-gray-100 cursor-pointer text-gray-900"
                      onClick={() => handleLedgerSelect(ledger)}
                    >
                      {ledger}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>

        {/* New Ledger Modal — backed by the shared LedgerCreateModal */}
        {showNewLedgerModal && (
          <LedgerCreateModal
            open
            ledgerType="other"
            busy={isCreatingLedger}
            onClose={() => { setShowNewLedgerModal(false); setLedgerError(null); }}
            onCreate={handleCreateLedger}
          />
        )}
        {showNewLedgerModal && ledgerError && (
          <div className="fixed bottom-4 right-4 z-[60] max-w-md p-3 bg-red-50 text-red-700 rounded-lg text-sm shadow-lg border border-red-200">
            <span>{ledgerError}</span>
            <button onClick={() => setLedgerError(null)} className="ml-2 text-red-500 hover:text-red-700">×</button>
          </div>
        )}

        {/* Data Table */}
        {selectedFile && tableData.length > 0 ? (
          <div className="bg-white border border-gray-300 rounded-lg overflow-hidden">
            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-gray-200">
                <thead className="bg-gray-100">
                  <tr>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-800 uppercase tracking-wider">
                      Sr. No.
                    </th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-800 uppercase tracking-wider">
                      Description
                    </th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-800 uppercase tracking-wider">
                      Type
                    </th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-800 uppercase tracking-wider">
                      Amount
                    </th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-800 uppercase tracking-wider">
                      Action
                    </th>
                  </tr>
                </thead>
                <tbody className="bg-white divide-y divide-gray-200">
                  {tableData.map((row) => (
                    <tr key={row.srNo} className="hover:bg-gray-50">
                      <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900">
                        {row.srNo}
                      </td>
                      <td className="px-6 py-4 text-sm text-gray-900">
                        {row.description}
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900">
                        {row.type}
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900">
                        ₹{row.amount}
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900">
                        <div className="flex gap-2">
                          <button className="p-1 text-gray-600 hover:text-black">
                            <Eye className="h-4 w-4" />
                          </button>
                          <button className="p-1 text-gray-600 hover:text-black">
                            <Edit className="h-4 w-4" />
                          </button>
                          <button className="p-1 text-gray-600 hover:text-black">
                            <Trash className="h-4 w-4" />
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ) : (
          <div className="text-center py-8 text-gray-600">
            {selectedFile
              ? "No entries found in this file"
              : "Select a file to view entries"}
          </div>
        )}
      </div>
    </div>
  );
}

export default TallyDataView; 