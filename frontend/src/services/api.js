/**
 * Centralized API service.
 * All API calls go through this module instead of scattered axios/fetch calls.
 */
import axios from 'axios';
import { getToken, refreshToken, logout } from './auth';

const API_URL = process.env.REACT_APP_API_URL || 'http://localhost:8000';

console.log('[API] Initialized with base URL:', API_URL);

// ── Axios Instance ──

const api = axios.create({
    baseURL: API_URL,
    timeout: 30000,
});

// Request interceptor — attach auth token
api.interceptors.request.use((config) => {
    const token = getToken();
    console.log(`[API] ${config.method?.toUpperCase()} ${config.url}`);
    if (token) {
        config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
});
                                                          
// Response interceptor — handle 401s
api.interceptors.response.use(
    (response) => {
        console.log(`[API] Response ${response.status} from ${response.config.url}`);
        return response;
    },
    async (error) => {
        console.error(`[API] Error ${error.response?.status || 'network'} from ${error.config?.url}:`, error.message);
        const originalRequest = error.config;

        if (error.response?.status === 401 && !originalRequest._retry) {
            console.log('[API] 401 received, attempting token refresh...');
            originalRequest._retry = true;
            const newToken = await refreshToken();
            if (newToken) {
                console.log('[API] Token refreshed, retrying request...');
                originalRequest.headers.Authorization = `Bearer ${newToken}`;
                return api(originalRequest);
            }
            console.log('[API] Token refresh failed, logging out...');
            logout();
        }

        return Promise.reject(error);
    }
);

// ── Auth API ──

export const authApi = {
    googleSignIn: async (idToken) => {
        console.log('[Auth API] Google sign-in request to:', `${API_URL}/ca/auth/google-signin`);
        try {
            const response = await fetch(`${API_URL}/ca/auth/google-signin`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ id_token: idToken }),
            });
            console.log('[Auth API] Google sign-in response status:', response.status);
            const data = await response.json();
            console.log('[Auth API] Google sign-in response data:', data);
            if (!response.ok) {
                throw new Error(data.detail || 'Google sign-in failed');
            }
            return data;
        } catch (error) {
            console.error('[Auth API] Google sign-in error:', error);
            throw error;
        }
    },

    emailSignUp: async (email, password, name) => {
        const response = await fetch(`${API_URL}/ca/auth/email-signup`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email, password, name }),
        });
        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.detail || 'Sign up failed');
        }
        return data;
    },

    emailSignIn: async (email, password) => {
        const response = await fetch(`${API_URL}/ca/auth/email-signin`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email, password }),
        });
        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.detail || 'Sign in failed');
        }
        return data;
    },
};

// ── Chat API ──

export const chatApi = {
    getMessages: async (sessionId) => {
        const response = await api.post('/ca/messages', { session_id: sessionId });
        return response.data;
    },

    getChatsByEntity: async (entityId, entityType) => {
        const response = await api.post('/ca/chats', {
            [`${entityType}_id`]: entityId,
        });
        return response.data;
    },

    /**
     * Stream chat response via fetch (not axios — needs ReadableStream).
     * Returns the raw Response object for streaming.
     */
    streamChat: async (formData) => {
        const token = getToken();
        const response = await fetch(`${API_URL}/ca/chat/langgraph`, {
            method: 'POST',
            headers: {
                Authorization: `Bearer ${token}`,
            },
            body: formData,
        });
        return response;
    },
};

// ── Tally API ──

export const tallyApi = {
    getCompanies: async (userId) => {
        // Use WebSocket-based endpoint (works with Railway deployment)
        const response = await api.get('/ca/get-companies', {
            params: { user_id: userId },
        });
        return response.data;
    },

    getDirectCompanies: async () => {
        const response = await api.get('/ca/direct/get-companies');
        return response.data;
    },

    getInventory: async (company) => {
        const response = await api.get('/ca/get-inventory', {
            params: { company },
        });
        return response.data;
    },

    getUniqueCompanies: async () => {
        const token = getToken();
        const response = await fetch(`${API_URL}/ca/get-unique-companies`, {
            headers: { Authorization: `Bearer ${token}` },
        });
        return response.json();
    },

    pushToTally: async (data) => {
        const response = await api.post('/ca/push-to-tally', data, {
            timeout: 180000, // 3 minutes for large batch imports
        });
        return response.data;
    },

    tallyChat: async ({ signal, ...formData }) => {
        const token = getToken();
        const response = await fetch(`${API_URL}/ca/tally-chat`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                Authorization: `Bearer ${token}`,
            },
            body: JSON.stringify(formData),
            signal,
        });
        return response;
    },

    // Kick off a Tally sync for TallyChat through the desktop bridge (the same
    // tally-database-loader the analytics page uses). Returns a quick "started"
    // ack — the loader runs in the background; poll getTallyChatSyncStatus for
    // progress. mode "full" reconciles (default), "incremental" applies deltas.
    syncTallyToDb: async (company, mode = 'incremental') => {
        const response = await api.post('/ca/tally-chat/sync', { company, mode });
        return response.data;
    },

    // Poll the loader's per-(user, company) sync state. Shared with the analytics
    // page (same tally_sync_state row). status: idle | syncing | completed | failed.
    getTallyChatSyncStatus: async (company) => {
        const response = await api.get('/ca/tally/analytics/sync-status', {
            params: { company },
        });
        return response.data;
    },

    // Persisted Tally-chat history. Omit threadId to get the latest thread
    // for (user, company). Returns { thread_id, messages: [{role, content, ...}] }.
    getTallyChatHistory: async (company, threadId) => {
        const params = { company };
        if (threadId) params.thread_id = threadId;
        const response = await api.get('/ca/tally-chat/history', { params });
        return response.data;
    },

    // Clear a Tally-chat thread (omit threadId to clear the latest one). The
    // backend treats this as "start a fresh chat".
    clearTallyChatHistory: async (company, threadId) => {
        const params = { company };
        if (threadId) params.thread_id = threadId;
        const response = await api.delete('/ca/tally-chat/history', { params });
        return response.data;
    },

    checkConnection: async () => {
        const response = await api.get('/ca/check-tally-connection');
        return response.data;
    },

    // Direct HTTP connection check (no websocket needed)
    checkDirectConnection: async () => {
        const response = await api.get('/ca/tally/status');
        return response.data;
    },

    // Filtered ledgers from Tally
    getFilteredLedgers: async (company, type = 'all') => {
        const response = await api.get('/ca/tally/ledgers', {
            params: { company, type },
        });
        return response.data;
    },

    // Refresh ledger cache
    refreshLedgerCache: async (company) => {
        const response = await api.get('/ca/tally/ledgers/refresh', {
            params: { company },
        });
        return response.data;
    },

    // Persisted companies the user has seen before. Returned even when Tally
    // is offline so the upload modal and library can render immediately.
    getCachedCompanies: async () => {
        const response = await api.get('/ca/get-cached-companies');
        return response.data;
    },

    // Last-known ledger list for a company. Used by the draft editor so
    // matching works offline; refreshed whenever a live fetch succeeds.
    getCachedLedgers: async (company) => {
        const response = await api.get('/ca/get-cached-ledgers', {
            params: { company },
        });
        return response.data;
    },

    // Returns whether we have an offline-usable cached snapshot of the
    // grouped (party/purchase/sales) ledger universe for this company, and
    // when it was last refreshed. Drives the "Tally offline — showing
    // cached suggestions" banner in the editor views. Decoupled from
    // suggestLedger so the banner survives page transitions even if no
    // suggestion has been requested yet.
    getLedgerCacheStatus: async (company) => {
        const response = await api.get('/ca/ledger-cache-status', {
            params: { company },
        });
        return response.data;
    },

    // Pre-push gate: confirms the draft's company exists in Tally now and
    // that every ledger it references is available. Powers the bulk-push
    // pre-flight check and the "Fix before pushing" hint.
    validateDraftPushable: async (draftId) => {
        const response = await api.post('/ca/validate-draft-pushable', {
            draft_id: draftId,
        });
        return response.data;
    },

    // Check for duplicates in Tally
    checkTallyDuplicates: async (data) => {
        const response = await api.post('/ca/tally/check-duplicates', data);
        return response.data;
    },

    // Alter voucher in Tally
    alterVoucher: async (data) => {
        const response = await api.post('/ca/tally/alter-voucher', data);
        return response.data;
    },

    // Delete voucher from Tally
    deleteVoucher: async (data) => {
        const response = await api.post('/ca/tally/delete-voucher', data);
        return response.data;
    },

    syncCompanyFolders: async () => {
        const response = await api.post('/ca/sync-company-folders', {});
        return response.data;
    },

    getDesktopVersion: async () => {
        const response = await api.get('/ca/desktop/version');
        return response.data;
    },

    createLedger: async (data) => {
        const response = await api.post('/ca/create-ledger', data);
        return response.data;
    },

    createGroup: async (data) => {
        const response = await api.post('/ca/create-group', data);
        return response.data;
    },

    updateLedger: async (data) => {
        const response = await api.put('/ca/update-ledger', data);
        return response.data;
    },

    getLedgerGroups: async () => {
        const response = await api.get('/ca/get-ledger-groups');
        return response.data;
    },

    getLedgerDetails: async (params) => {
        const response = await api.get('/ca/get-ledger-details', { params });
        return response.data;
    },

    generateXml: async (formData) => {
        const response = await api.post('/ca/generate-xml', formData, {
            headers: { 'Content-Type': 'multipart/form-data' },
        });
        return response.data;
    },

    getLedgersForGroup: async (group) => {
        const response = await api.get('/ca/get-ledgers-for-group', {
            params: { group },
        });
        return response.data;
    },

    aiSuggestFields: async (data) => {
        const response = await api.post('/ca/ai-suggest-fields', data);
        return response.data;
    },

    // ── Mapping Rules ──

    getMappingRules: async (company, documentType = null) => {
        const params = { company };
        if (documentType) params.document_type = documentType;
        const response = await api.get('/ca/mapping-rules', { params });
        return response.data;
    },

    createMappingRule: async (company, rule) => {
        const response = await api.post('/ca/mapping-rules', rule, {
            params: { company },
        });
        return response.data;
    },

    updateMappingRule: async (ruleId, updates) => {
        const response = await api.put(`/ca/mapping-rules/${ruleId}`, updates);
        return response.data;
    },

    deleteMappingRule: async (ruleId) => {
        const response = await api.delete(`/ca/mapping-rules/${ruleId}`);
        return response.data;
    },

    applyMappingRules: async (company, documentType, transactions) => {
        const response = await api.post('/ca/mapping-rules/apply', transactions, {
            params: { company, document_type: documentType },
        });
        return response.data;
    },

    getRuleStatistics: async (company, documentType = null) => {
        const params = { company };
        if (documentType) params.document_type = documentType;
        const response = await api.get('/ca/mapping-rules/statistics', { params });
        return response.data;
    },

    getRuleSuggestions: async (company, documentType) => {
        const params = { company };
        if (documentType) params.document_type = documentType;
        const response = await api.get('/ca/mapping-rules/suggestions', { params });
        return response.data;
    },

    // Test a draft rule (composite or legacy) against the user's recent
    // drafts; backend returns counts + sample matches so the rule builder
    // can preview before save.
    previewMappingRule: async ({
        company, ruleType, documentType, conditions, keywords, pattern,
        vendorName, amountMin, amountMax, sampleSize = 50,
    }) => {
        const response = await api.post('/ca/mapping-rules/preview', {
            company_name: company,
            rule_type: ruleType,
            document_type: documentType,
            conditions,
            keywords,
            pattern,
            vendor_name: vendorName,
            amount_min: amountMin,
            amount_max: amountMax,
            sample_size: sampleSize,
        });
        return response.data;
    },

    // Fire-and-forget: tells the backend the user picked a ledger different
    // from the AI's suggestion. Drives correction-aware Layer 0 scoring and
    // rule mining; never throws to the caller.
    submitSuggestionFeedback: async (payload) => {
        try {
            const response = await api.post('/ca/suggest-ledger/feedback', payload);
            return response.data;
        } catch (e) {
            // Silent — feedback is best-effort.
            return null;
        }
    },

    // ── Bank Statement Import ──

    uploadBankStatement: async (formData, company, bankLedger = null, applyRules = true) => {
        formData.append('company', company);
        if (bankLedger) formData.append('bank_ledger', bankLedger);
        formData.append('apply_rules', applyRules);
        const response = await api.post('/ca/bank-statement/upload', formData, {
            headers: { 'Content-Type': 'multipart/form-data' },
            timeout: 120000, // 2 minutes for large files
        });
        return response.data;
    },

    getBankStatementPreview: async (batchId) => {
        const response = await api.get(`/ca/bank-statement/preview/${batchId}`);
        return response.data;
    },

    updateBankStatementTransactions: async (company, importIds, debitLedger = null, creditLedger = null) => {
        const response = await api.put('/ca/bank-statement/transactions', {
            import_ids: importIds,
            debit_ledger: debitLedger,
            credit_ledger: creditLedger,
        }, {
            params: { company },
        });
        return response.data;
    },

    checkBankStatementDuplicates: async (company, transactions) => {
        const response = await api.post('/ca/bank-statement/check-duplicates', transactions, {
            params: { company },
        });
        return response.data;
    },

    // ── Purchase Invoice Import ──

    uploadPurchaseInvoices: async (formData, company, applyRules = true) => {
        formData.append('company', company);
        formData.append('apply_rules', applyRules);
        const response = await api.post('/ca/purchase-invoice/upload', formData, {
            headers: { 'Content-Type': 'multipart/form-data' },
            timeout: 180000, // 3 minutes for multiple invoices
        });
        return response.data;
    },

    /**
     * Streaming multi-file purchase invoice upload (SSE).
     * @param {File[]} files
     * @param {string} company
     * @param {(event: object) => void} onEvent — called for each SSE event
     * @param {AbortSignal} [signal]
     */
    uploadPurchaseInvoicesStream: async (files, company, onEvent, signal) => {
        const formData = new FormData();
        files.forEach((f) => formData.append('files', f));
        formData.append('company', company || '');

        const token = getToken();
        const response = await fetch(`${API_URL}/ca/purchase-invoice/upload-stream`, {
            method: 'POST',
            headers: { Authorization: `Bearer ${token}` },
            body: formData,
            signal,
        });

        if (!response.ok) {
            const text = await response.text().catch(() => '');
            throw new Error(`Upload failed (${response.status}): ${text || response.statusText}`);
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
            const { value, done } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });

            let sepIdx;
            while ((sepIdx = buffer.indexOf('\n\n')) !== -1) {
                const chunk = buffer.slice(0, sepIdx);
                buffer = buffer.slice(sepIdx + 2);
                for (const line of chunk.split('\n')) {
                    if (line.startsWith('data: ')) {
                        try {
                            onEvent(JSON.parse(line.slice(6)));
                        } catch (e) {
                            console.warn('[SSE] Parse error:', e, line);
                        }
                    }
                }
            }
        }
    },

    getPurchaseInvoicePreview: async (batchId) => {
        const response = await api.get(`/ca/purchase-invoice/preview/${batchId}`);
        return response.data;
    },

    matchPurchaseInvoiceVendors: async (batchId, company, invoiceIndices, vendorName, vendorLedger, createIfNotExists = false) => {
        const response = await api.post(`/ca/purchase-invoice/match-vendors/${batchId}`, {
            invoice_indices: invoiceIndices,
            vendor_name: vendorName,
            vendor_ledger: vendorLedger,
            create_if_not_exists: createIfNotExists,
        }, {
            params: { company },
        });
        return response.data;
    },

    // ── AI Correction Feedback ──

    recordCorrection: async (company, importId, field, originalValue, correctedValue, documentType) => {
        const response = await api.post('/ca/record-correction', {
            import_id: importId,
            field,
            original_value: originalValue,
            corrected_value: correctedValue,
            document_type: documentType,
        }, {
            params: { company },
        });
        return response.data;
    },

    // ── Get Ledgers ──

    getLedgers: async (company) => {
        const response = await api.get('/ca/get-ledgers', {
            params: { company },
        });
        return response.data;
    },

    // ── Generate Bank XML ──

    generateBankXml: async (data) => {
        const response = await api.post('/ca/generate-bank-xml', data);
        return response.data;
    },

    // Validate before push
    validatePrePush: async (data) => {
        const response = await api.post('/ca/validate-pre-push', data);
        return response.data;
    },

    // Create missing ledgers
    createMissingLedgers: async (data) => {
        const response = await api.post('/ca/create-missing-ledgers', data);
        return response.data;
    },

    // Batch push vouchers
    pushVouchersBatch: async (data) => {
        const response = await api.post('/ca/push-vouchers-batch', data, {
            timeout: 300000, // 5 min for large batches
        });
        return response.data;
    },

    // ── Two-Stage Upsert: Push with auto-creation of missing masters ──

    // Push vouchers with automatic ledger/stock item creation (recommended)
    pushVouchersWithUpsert: async (data) => {
        const response = await api.post('/ca/direct/push-vouchers-with-upsert', data, {
            timeout: 300000, // 5 min for large batches
        });
        return response.data;
    },

    // Ensure ledgers and stock items exist (standalone upsert check)
    ensureMasters: async (data) => {
        const response = await api.post('/ca/direct/ensure-masters', data);
        return response.data;
    },

    // Create stock item in Tally
    createStockItem: async (data) => {
        const response = await api.post('/ca/direct/create-stock-item', data);
        return response.data;
    },

    // ── Tally Master Data (via WebSocket bridge) ──

    // Get all ledgers from Tally (with parent group info)
    getDirectLedgers: async (company) => {
        const response = await api.get('/ca/get-ledgers', { params: { company } });
        return response.data;
    },

    // Get all stock items from Tally (with units)
    getDirectInventory: async (company) => {
        const response = await api.get('/ca/get-inventory', { params: { company } });
        return response.data;
    },

    // Get all godowns/warehouses from Tally
    getGodowns: async (company) => {
        const response = await api.get('/ca/get-godowns', { params: { company } });
        return response.data;
    },

    // Get all ledger groups from Tally
    getGroups: async (company) => {
        const response = await api.get('/ca/get-groups', { params: { company } });
        return response.data;
    },

    // Get voucher types from Tally (still uses direct - may need WebSocket endpoint)
    getVoucherTypes: async (company) => {
        const response = await api.get('/ca/direct/get-voucher-types', { params: { company } });
        return response.data;
    },

    // Generate Sales/Purchase invoice XML (without pushing)
    generateInvoiceXml: async (data) => {
        const response = await api.post('/ca/direct/generate-invoice-xml', data);
        return response.data;
    },

    // ── AI Ledger Suggestion ──

    // Get AI-powered ledger suggestions for a bill/invoice
    //
    // Budget breakdown (backend bounds, see ledger_matcher.LLM_TIMEOUT_SECONDS
    // and _fetch_ledgers_for_voucher_type WebSocket timeout):
    //   WebSocket grouped-ledgers fetch ............ 15s
    //   LLM party match ............................ 12s
    //   LLM category match ......................... 12s
    //   Worst-case backend total ................... ~39s
    // 45s gives the backend room to finish before axios aborts, so the user
    // doesn't see a false timeout that leaves the spinner stuck.
    suggestLedger: async (billData) => {
        const response = await api.post('/ca/suggest-ledger', billData, {
            timeout: 45000,
        });
        return response.data;
    },

    // Get all ledgers organized by account groups
    getGroupedLedgers: async (company) => {
        const response = await api.get('/ca/ledgers/grouped', { params: { company } });
        return response.data;
    },

    // Extract structured data from a bill PDF
    extractBill: async (formData) => {
        const response = await api.post('/ca/extract-bill', formData, {
            headers: { 'Content-Type': 'multipart/form-data' },
            timeout: 120000, // 2 min for OCR + extraction
        });
        return response.data;
    },

    // ── Sales Invoice Import ──

    // Upload sales invoice PDF and extract structured data
    uploadSalesInvoice: async (formData) => {
        const response = await api.post('/ca/upload-sales-invoice', formData, {
            headers: { 'Content-Type': 'multipart/form-data' },
            timeout: 180000, // 3 min for AI extraction
        });
        return response.data;
    },

    /**
     * Streaming multi-file sales invoice upload (SSE).
     * Mirrors uploadPurchaseInvoicesStream — backend endpoint is /ca/sales-invoice/upload-stream.
     */
    uploadSalesInvoicesStream: async (files, company, onEvent, signal) => {
        const formData = new FormData();
        files.forEach((f) => formData.append('files', f));
        formData.append('company', company || '');

        const token = getToken();
        const response = await fetch(`${API_URL}/ca/sales-invoice/upload-stream`, {
            method: 'POST',
            headers: { Authorization: `Bearer ${token}` },
            body: formData,
            signal,
        });

        if (!response.ok) {
            const text = await response.text().catch(() => '');
            throw new Error(`Upload failed (${response.status}): ${text || response.statusText}`);
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
            const { value, done } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });

            let sepIdx;
            while ((sepIdx = buffer.indexOf('\n\n')) !== -1) {
                const chunk = buffer.slice(0, sepIdx);
                buffer = buffer.slice(sepIdx + 2);
                for (const line of chunk.split('\n')) {
                    if (line.startsWith('data: ')) {
                        try {
                            onEvent(JSON.parse(line.slice(6)));
                        } catch (e) {
                            console.warn('[SSE] Parse error:', e, line);
                        }
                    }
                }
            }
        }
    },

    // Generate Tally Sales voucher XML from edited form data
    generateSalesVoucherXml: async (data) => {
        const response = await api.post('/ca/generate-sales-voucher-xml', data);
        return response.data;
    },

    // Generate Tally Purchase voucher XML from purchase invoice data
    generatePurchaseXml: async ({ invoices, company }) => {
        const transactions = invoices.map(inv => ({
            date: inv.invoice_date || inv.date || "",
            party_ledger: inv.vendor_ledger || inv.party_ledger || "",
            narration: inv.description || inv.narration || "",
            reference_number: inv.invoice_number || inv.reference_number || "",
            place_of_supply: inv.place_of_supply || "",
            amount: inv.subtotal || inv.amount || 0,
            gst_percentage: inv.gst_percentage || 0,
            gst_amount: inv.gst_amount || inv.tax_amount || 0,
            total_amount: inv.total_amount || 0,
            debit_ledger: inv.purchase_ledger || "Purchase",
            credit_ledger: inv.vendor_ledger || inv.party_ledger || "",
            // Pass actual tax entries from the form so XML uses real ledger names
            tax_entries: inv.tax_entries || [],
            // TDS fields (optional — reduces vendor net payable)
            tds_ledger: inv.tds_ledger || "",
            tds_amount: inv.tds_amount || 0,
        }));
        const response = await api.post('/ca/direct/generate-invoice-xml', {
            company,
            voucher_type: "Purchase",
            transactions,
            purchase_ledger: invoices[0]?.purchase_ledger || "Purchase",
            cgst_ledger: invoices[0]?.cgst_ledger || "CGST ITC",
            sgst_ledger: invoices[0]?.sgst_ledger || "SGST ITC",
        });
        return response.data;
    },

    // Per-user drafts for in-progress work in Tally purchase / sales /
    // bank-entries flows. PDFs are not persisted — extracted state + edits only.
    drafts: {
        save: async ({ flow, payload, title, draftId, companyName }) => {
            const response = await api.post('/ca/drafts', {
                flow,
                payload,
                title,
                draft_id: draftId,
                company_name: companyName,
            });
            return response.data;
        },
        list: async ({ flow, companyName, status } = {}) => {
            const response = await api.get('/ca/drafts', {
                params: { flow, company_name: companyName, status },
            });
            return response.data;
        },
        get: async (draftId) => {
            const response = await api.get(`/ca/drafts/${draftId}`);
            return response.data;
        },
        // Resolve a short-lived presigned download URL for a draft's file.
        // Pass `r2Key` for sales/purchase (per-invoice file); omit for bank
        // entries (single draft-level file). Bypasses the stored file_url
        // which can break when R2_PUBLIC_URL is misconfigured.
        getFileUrl: async (draftId, r2Key) => {
            const params = r2Key ? { r2_key: r2Key } : {};
            const response = await api.get(`/ca/drafts/${draftId}/file-url`, { params });
            return response.data;
        },
        update: async (draftId, payload) => {
            const response = await api.put(`/ca/drafts/${draftId}`, { payload });
            return response.data;
        },
        markPushed: async (draftId) => {
            const response = await api.patch(`/ca/drafts/${draftId}/push`);
            return response.data;
        },
        delete: async (draftId) => {
            const response = await api.delete(`/ca/drafts/${draftId}`);
            return response.data;
        },
    },

    // Tally Document Library — upload file to R2, trigger extraction
    uploadFile: async (file, company, flow, bankLedger) => {
        const formData = new FormData();
        formData.append('file', file);
        formData.append('company', company);
        formData.append('flow', flow);
        if (bankLedger) formData.append('bank_ledger', bankLedger);
        const response = await api.post('/ca/tally-upload', formData, {
            headers: { 'Content-Type': 'multipart/form-data' },
            timeout: 60000,
        });
        return response.data;
    },

    extractDraft: async (draftId) => {
        const response = await api.post(`/ca/drafts/${draftId}/extract`, {}, {
            timeout: 300000, // 5 min — extraction can be slow
        });
        return response.data;
    },
};

// ── QuickBooks Desktop API ──
//
// Mirrors tallyApi for the QB Desktop ERP path. All routes namespaced /ca/qb/*.
// Production uses the WebSocket-via-Electron bridge (same as Tally). Dev can
// hit /ca/qb/status for a direct mock_qb HTTP check.

export const qbApi = {
    // Connection
    checkConnection: async () => {
        const response = await api.get('/ca/qb/check-connection');
        return response.data;
    },

    checkDirectConnection: async () => {
        const response = await api.get('/ca/qb/status');
        return response.data;
    },

    // Master reads
    getCompanies: async () => {
        const response = await api.get('/ca/qb/get-companies');
        return response.data;
    },

    getAccounts: async (company) => {
        const response = await api.get('/ca/qb/get-accounts', {
            params: company ? { company } : {},
        });
        return response.data;
    },

    getCustomers: async (company) => {
        const response = await api.get('/ca/qb/get-customers', {
            params: company ? { company } : {},
        });
        return response.data;
    },

    getVendors: async (company) => {
        const response = await api.get('/ca/qb/get-vendors', {
            params: company ? { company } : {},
        });
        return response.data;
    },

    getItems: async (company) => {
        const response = await api.get('/ca/qb/get-items', {
            params: company ? { company } : {},
        });
        return response.data;
    },

    // Master writes
    createCustomer: async (data) => {
        const response = await api.post('/ca/qb/create-customer', data);
        return response.data;
    },

    createVendor: async (data) => {
        const response = await api.post('/ca/qb/create-vendor', data);
        return response.data;
    },

    createAccount: async (data) => {
        const response = await api.post('/ca/qb/create-account', data);
        return response.data;
    },

    // Tier-1 / Tier-5 master creation helpers.
    // `createItem` wraps QBO's stricter Item requirements (Type + IncomeAccountRef)
    // so callers can pass just {name, qb_company} for the common Service case.
    createItem: async (data) => {
        const response = await api.post('/ca/qb/create-item', data);
        return response.data;
    },

    // Tier-5: bulk-create a batch of master entities in one round-trip.
    // `items` is [{entity_type: 'customer'|'vendor'|'item'|'account', name, ...}].
    createBatch: async ({ items, qb_company }) => {
        const response = await api.post('/ca/qb/create-batch', { items, qb_company });
        return response.data;
    },

    // Tier-4: persist a user-confirmed (alias text) -> (qbo entity id) mapping
    // so future extractions of the same variant text auto-resolve. Called when
    // the user picks an existing candidate from MatchPicker suggestions.
    saveAlias: async ({ entity_type, alias_text, qbo_entity_id, qbo_entity_name, qb_company }) => {
        const response = await api.post('/ca/qb/alias', {
            entity_type, alias_text, qbo_entity_id, qbo_entity_name, qb_company,
        });
        return response.data;
    },

    // Mapping rules (Phase 3) — user-authorable rules that auto-map a line to a
    // QBO account/party (keyword/pattern/vendor/amount/composite).
    mappingRules: {
        list: async ({ qb_company, document_type, account_slot } = {}) => {
            const response = await api.get('/ca/qb/mapping-rules', {
                params: { qb_company, document_type, account_slot },
            });
            return response.data;
        },
        create: async (rule) => {
            const response = await api.post('/ca/qb/mapping-rules', rule);
            return response.data;
        },
        update: async (ruleId, rule) => {
            const response = await api.put(`/ca/qb/mapping-rules/${ruleId}`, rule);
            return response.data;
        },
        remove: async (ruleId) => {
            const response = await api.delete(`/ca/qb/mapping-rules/${ruleId}`);
            return response.data;
        },
        preview: async ({ qb_company, rule, transactions }) => {
            const response = await api.post('/ca/qb/mapping-rules/preview', {
                qb_company, rule, transactions,
            });
            return response.data;
        },
    },

    // Phase 6: presigned URL to preview a reopened draft's source document.
    draftFileUrl: async (r2_key) => {
        try {
            const response = await api.post('/ca/qb/draft-file-url', { r2_key });
            return response.data?.url || null;
        } catch (_) {
            return null;
        }
    },

    // Phase 4: record AI suggestion accept/override so the system learns.
    recordSuggestionFeedback: async (body) => {
        try {
            const response = await api.post('/ca/qb/suggest-account/feedback', body);
            return response.data;
        } catch (_) {
            return null; // never let feedback break the UI
        }
    },

    // Phase 2: surface near-duplicate accounts before creating a new one so
    // names stay clean ("Stationary" vs "Stationery" etc.).
    previewCreateAccount: async ({ proposed_name, account_type, qb_company }) => {
        const response = await api.post('/ca/qb/preview-create-account', {
            proposed_name, account_type, qb_company,
        });
        return response.data;
    },

    // Document push
    pushInvoice: async ({ qbxml, qb_company }) => {
        const response = await api.post('/ca/qb/push-invoice', { qbxml, qb_company }, {
            timeout: 180000,
        });
        return response.data;
    },

    pushBill: async ({ qbxml, qb_company, payload }) => {
        const response = await api.post('/ca/qb/push-bill', { qbxml, qb_company, payload }, {
            timeout: 180000,
        });
        return response.data;
    },

    pushInvoiceQBO: async ({ payload, qb_company, extracted_total, skip_duplicate_check }) => {
        const response = await api.post('/ca/qb/push-invoice', { payload, qb_company, extracted_total, skip_duplicate_check });
        return response.data;
    },

    pushBillQBO: async ({ payload, qb_company, extracted_total, skip_duplicate_check }) => {
        const response = await api.post('/ca/qb/push-bill', { payload, qb_company, extracted_total, skip_duplicate_check });
        return response.data;
    },

    extractInvoice: async (file, qbCompany) => {
        const formData = new FormData();
        formData.append('file', file);
        if (qbCompany) formData.append('qb_company', qbCompany);
        const response = await api.post('/ca/qb/extract-invoice', formData, {
            timeout: 120000,
            headers: { 'Content-Type': 'multipart/form-data' },
        });
        return response.data;
    },

    extractBill: async (file, qbCompany) => {
        const formData = new FormData();
        formData.append('file', file);
        if (qbCompany) formData.append('qb_company', qbCompany);
        const response = await api.post('/ca/qb/extract-bill', formData, {
            timeout: 120000,
            headers: { 'Content-Type': 'multipart/form-data' },
        });
        return response.data;
    },

    // Bank reconciliation
    bank: {
        listAccounts: async (qbCompany) => {
            const response = await api.get('/ca/qb/bank/list-accounts', {
                params: qbCompany ? { qb_company: qbCompany } : {},
            });
            return response.data;
        },

        extractAndMatch: async ({ file, accountId, qbCompany, periodStart, periodEnd, qboClosingBalance }) => {
            const formData = new FormData();
            formData.append('file', file);
            formData.append('account_id', accountId);
            if (qbCompany) formData.append('qb_company', qbCompany);
            if (periodStart) formData.append('period_start', periodStart);
            if (periodEnd) formData.append('period_end', periodEnd);
            if (qboClosingBalance !== undefined && qboClosingBalance !== null && qboClosingBalance !== '') {
                formData.append('qbo_closing_balance', qboClosingBalance);
            }
            const response = await api.post('/ca/qb/bank/extract-and-match', formData, {
                timeout: 180000,
                headers: { 'Content-Type': 'multipart/form-data' },
            });
            return response.data;
        },

        // Pass the current unmatched lists + closing balances to get a fresh
        // summary back in the same round-trip. Backwards-compatible: if the
        // optional fields are omitted, the response still includes match fields
        // at the top level.
        manualMatch: async ({
            qboTxn, statementRow,
            unmatchedQbo, unmatchedStatement,
            qboClosingBalance, bankClosingBalance,
        }) => {
            const response = await api.post('/ca/qb/bank/manual-match', {
                qbo_txn: qboTxn,
                statement_row: statementRow,
                unmatched_qbo: unmatchedQbo,
                unmatched_statement: unmatchedStatement,
                qbo_closing_balance: qboClosingBalance ?? null,
                bank_closing_balance: bankClosingBalance ?? null,
            });
            return response.data;
        },

        // mode: 'mark_pending' | 'post_to_suspense'
        // post_to_suspense requires bankAccountId, suspenseAccountId, qbCompany,
        // and statementRowIndices (defaults to all when omitted).
        resolvePending: async ({
            mode,
            unmatchedQbo,
            unmatchedStatement,
            qboClosingBalance,
            bankClosingBalance,
            qbCompany,
            bankAccountId,
            bankAccountName,
            suspenseAccountId,
            suspenseAccountName,
            statementRowIndices,
        }) => {
            const response = await api.post('/ca/qb/bank/resolve-pending', {
                mode,
                unmatched_qbo: unmatchedQbo,
                unmatched_statement: unmatchedStatement,
                qbo_closing_balance: qboClosingBalance ?? null,
                bank_closing_balance: bankClosingBalance ?? null,
                qb_company: qbCompany,
                bank_account_id: bankAccountId,
                bank_account_name: bankAccountName,
                suspense_account_id: suspenseAccountId,
                suspense_account_name: suspenseAccountName,
                statement_row_indices: statementRowIndices,
            }, { timeout: 180000 });
            return response.data;
        },

        saveReport: async (payload) => {
            const response = await api.post('/ca/qb/bank/save', payload);
            return response.data;
        },

        listReports: async (qbCompany) => {
            const response = await api.get('/ca/qb/bank/reports', {
                params: qbCompany ? { qb_company: qbCompany } : {},
            });
            return response.data;
        },

        getReport: async (id) => {
            const response = await api.get(`/ca/qb/bank/reports/${id}`);
            return response.data;
        },

        deleteReport: async (id) => {
            const response = await api.delete(`/ca/qb/bank/reports/${id}`);
            return response.data;
        },
    },

    // Per-user drafts for in-progress work in QB purchase / sales /
    // bank-entries flows. Extract endpoints best-effort upload the source PDF
    // to R2; the r2_key rides on each invoice's `result`, and the restore path
    // calls `drafts.getFileUrl(r2_key)` to mint a short-lived signed URL so
    // the PDF preview pane re-renders after reopening the draft.
    drafts: {
        save: async ({ flow, payload, title, draftId, qbCompany, fileUrl, fileName, r2Key }) => {
            const response = await api.post('/ca/qb/drafts', {
                flow,
                payload,
                title,
                draft_id: draftId,
                qb_company: qbCompany,
                file_url: fileUrl,
                file_name: fileName,
                r2_key: r2Key,
            });
            return response.data;
        },
        list: async ({ flow, qbCompany, status } = {}) => {
            const response = await api.get('/ca/qb/drafts', {
                params: { flow, qb_company: qbCompany, status },
            });
            return response.data;
        },
        get: async (draftId) => {
            const response = await api.get(`/ca/qb/drafts/${draftId}`);
            return response.data;
        },
        update: async (draftId, payload) => {
            const response = await api.put(`/ca/qb/drafts/${draftId}`, { payload });
            return response.data;
        },
        markPushed: async (draftId) => {
            const response = await api.patch(`/ca/qb/drafts/${draftId}/push`);
            return response.data;
        },
        // Mint a short-lived presigned URL for an R2-stored source PDF. Used by
        // QBSalesView / QBPurchaseView when restoring a draft so the preview
        // pane can re-render the PDF without re-uploading.
        getFileUrl: async (r2Key) => {
            const response = await api.post('/ca/qb/draft-file-url', { r2_key: r2Key });
            return response.data;
        },
        // Pre-push gate: confirms QuickBooks is connected and the draft's
        // company (realm) still resolves. Powers the bulk-push pre-flight check.
        validatePushable: async (draftId) => {
            const response = await api.post('/ca/qb/validate-draft-pushable', {
                draft_id: draftId,
            });
            return response.data;
        },
        delete: async (draftId) => {
            const response = await api.delete(`/ca/qb/drafts/${draftId}`);
            return response.data;
        },
    },

    // QB Chat (NDJSON SSE stream)
    chatStream: async ({ message, qbCompany, history, threadId, onEvent, signal }) => {
        const token = localStorage.getItem('jwt_token');
        const response = await fetch(`${API_URL}/ca/qb/chat`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                Authorization: `Bearer ${token}`,
            },
            body: JSON.stringify({
                message,
                qb_company: qbCompany,
                history,
                thread_id: threadId,
            }),
            signal,
        });
        if (!response.ok) {
            const text = await response.text();
            throw new Error(text || `HTTP ${response.status}`);
        }
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        while (true) {
            const { value, done } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            // NDJSON: events separated by \n\n (matches backend emit format)
            const parts = buffer.split("\n\n");
            buffer = parts.pop();
            for (const raw of parts) {
                const line = raw.trim();
                if (!line) continue;
                try {
                    const evt = JSON.parse(line);
                    onEvent?.(evt);
                } catch {
                    // ignore non-JSON heartbeats
                }
            }
        }
        if (buffer.trim()) {
            try { onEvent?.(JSON.parse(buffer.trim())); } catch { /* ignore */ }
        }
    },

    chatHistory: async ({ qbCompany, threadId } = {}) => {
        const response = await api.get('/ca/qb/chat/history', {
            params: {
                ...(qbCompany ? { qb_company: qbCompany } : {}),
                ...(threadId ? { thread_id: threadId } : {}),
            },
        });
        return response.data;
    },

    clearChat: async ({ qbCompany, threadId } = {}) => {
        const response = await api.delete('/ca/qb/chat/history', {
            params: {
                ...(qbCompany ? { qb_company: qbCompany } : {}),
                ...(threadId ? { thread_id: threadId } : {}),
            },
        });
        return response.data;
    },

    // Bank entries — statement -> create QBO txns (Deposit / Purchase)
    bankEntries: {
        extract: async ({ file, accountId, accountName, qbCompany }) => {
            const formData = new FormData();
            formData.append('file', file);
            formData.append('account_id', accountId);
            if (accountName) formData.append('account_name', accountName);
            if (qbCompany) formData.append('qb_company', qbCompany);
            const response = await api.post('/ca/qb/bank-entries/extract', formData, {
                timeout: 180000,
                headers: { 'Content-Type': 'multipart/form-data' },
            });
            return response.data;
        },

        pushOne: async ({ entryType, payload, qbCompany }) => {
            const response = await api.post('/ca/qb/bank-entries/push-one', {
                qb_company: qbCompany,
                entry_type: entryType,
                payload,
            });
            return response.data;
        },

        // Normalized-contract path (Tally parity). The frontend sends plain
        // vouchers; the backend builds every QBO payload via build_qbo_bank_payload.
        validate: async ({ qbCompany, bankAccountId, bankAccountName, vouchers }) => {
            const response = await api.post('/ca/qb/bank-entries/validate', {
                qb_company: qbCompany,
                bank_account_id: bankAccountId,
                bank_account_name: bankAccountName,
                vouchers,
            });
            return response.data;
        },

        pushBatch: async ({ qbCompany, bankAccountId, bankAccountName, vouchers, autoCreateMasters = false }) => {
            const response = await api.post('/ca/qb/bank-entries/push-batch', {
                qb_company: qbCompany,
                bank_account_id: bankAccountId,
                bank_account_name: bankAccountName,
                vouchers,
                auto_create_masters: autoCreateMasters,
            }, { timeout: 180000 });
            return response.data;
        },
    },
};

// ── QuickBooks Online OAuth ──

export const qboOAuthApi = {
    /** Get the Intuit consent URL — frontend then sets window.location to it. */
    getAuthUrl: async () => {
        const response = await api.get('/ca/qb/oauth/connect');
        return response.data;
    },

    /** Returns { connected: bool, realms: [...] }. */
    status: async () => {
        const response = await api.get('/ca/qb/oauth/status');
        return response.data;
    },

    disconnect: async (realmId) => {
        const response = await api.post('/ca/qb/oauth/disconnect', null, {
            params: realmId ? { realm_id: realmId } : {},
        });
        return response.data;
    },
};

// ── Files API ──

export const filesApi = {
    upload: async (formData) => {
        const response = await api.post('/ca/upload/', formData);
        return response.data;
    },

    uploadDb: async (formData) => {
        const response = await api.post('/ca/upload_db/', formData);
        return response.data;
    },

    smartUpload: async (formData) => {
        const response = await api.post('/ca/smart-upload/', formData);
        return response.data;
    },

    deleteFile: async (fileId, userId) => {
        const response = await api.delete('/ca/delete-file/', {
            data: { file_id: fileId, user_id: userId },
        });
        return response.data;
    },

    searchDocuments: async (query, userId) => {
        const response = await api.get('/ca/search-documents/', {
            params: { query, user_id: userId },
        });
        return response.data;
    },
};

// ── Clients & Lockrs API ──

export const clientsApi = {
    getClientsWithDetails: async () => {
        const response = await api.get('/ca/get-clients-with-details');
        return response.data;
    },

    deleteClient: async (clientId) => {
        const response = await api.delete(`/ca/delete-client?client_id=${clientId}`);
        return response.data;
    },

    getLockersWithDetails: async () => {
        const response = await api.get('/ca/get-lockers-with-details');
        return response.data;
    },

    deleteLocker: async (lockerId) => {
        const response = await api.delete(`/ca/delete-locker?lockr_id=${lockerId}`);
        return response.data;
    },

    createClient: async (data) => {
        const response = await api.post('/ca/create-client', data);
        return response.data;
    },

    createLocker: async (data) => {
        const response = await api.post('/ca/create-locker', data);
        return response.data;
    },
};

// ── MIS API ──

export const misApi = {
    getFinancialSummary: async (period) => {
        const response = await api.get(`/ca/api/mis/financial-summary?period=${period}`);
        return response.data;
    },

    getMonthlyTrends: async (months = 12) => {
        const response = await api.get(`/ca/api/mis/monthly-trends?months=${months}`);
        return response.data;
    },

    getCostCenter: async () => {
        const response = await api.get('/ca/api/mis/cost-center');
        return response.data;
    },

    getInventory: async () => {
        const response = await api.get('/ca/api/mis/inventory');
        return response.data;
    },

    getLedgers: async () => {
        const response = await api.get('/ca/api/mis/ledgers');
        return response.data;
    },

    getGstSummary: async () => {
        const response = await api.get('/ca/api/mis/gst-summary');
        return response.data;
    },
};

// ── History API ──

export const historyApi = {
    getChatHistory: async (data) => {
        const response = await api.post('/ca/chat-history', data);
        return response.data;
    },

    getChatMessages: async (data) => {
        const response = await api.post('/ca/messages', data);
        return response.data;
    },
};

// ── Bank Reconciliation API ──

export const bankReconApi = {

    // Bank ledgers — reuses existing get-ledgers endpoint, filtering done client-side
    getBankLedgers: async (companyName) => {
        const response = await api.get('/ca/get-ledgers', {
            params: { company: companyName },
        });
        return response.data;
    },

    // Non-bank ledgers for the adjusting-entry counter-account dropdown.
    // Returns { accounts: [{ name, group }], from_cache }.
    getPostingAccounts: async (companyName) => {
        const response = await api.get('/ca/bank-recon/posting-accounts', {
            params: { company_name: companyName },
        });
        return response.data;
    },

    // Auto-generated serial name for new report
    getNextReportName: async () => {
        const response = await api.get('/ca/bank-recon/next-report-name');
        return response.data;
    },

    // List reports for a company + ledger
    listReports: async (companyName, bankLedgerName) => {
        const response = await api.get('/ca/bank-recon/reports', {
            params: { company_name: companyName, bank_ledger_name: bankLedgerName },
        });
        return response.data;
    },

    // Create report + upload bank statement
    createReport: async (formData) => {
        const response = await api.post('/ca/bank-recon/reports', formData, {
            headers: { 'Content-Type': 'multipart/form-data' },
            timeout: 60000,
        });
        return response.data;
    },

    // Get single report details
    getReport: async (reportId) => {
        const response = await api.get(`/ca/bank-recon/reports/${reportId}`);
        return response.data;
    },

    // Update report (name, status)
    updateReport: async (reportId, data) => {
        const response = await api.patch(`/ca/bank-recon/reports/${reportId}`, data);
        return response.data;
    },

    // Delete report
    deleteReport: async (reportId) => {
        const response = await api.delete(`/ca/bank-recon/reports/${reportId}`);
        return response.data;
    },

    // Trigger backend processing (fetch Tally ledger txns + run auto-match).
    // In-flight dedupe: if caller fires twice for same reportId, second call
    // reuses the first promise instead of hitting the backend again.
    // Backend is also idempotent, but this avoids needless network round-trips.
    _inflightProcess: new Map(),
    processReport: async function (reportId) {
        const key = String(reportId);
        if (this._inflightProcess.has(key)) return this._inflightProcess.get(key);
        const p = (async () => {
            try {
                const response = await api.post(`/ca/bank-recon/reports/${reportId}/process`, {}, {
                    timeout: 600000, // 10 min — large Tally fetches + 8k×141 matching can exceed 2 min
                });
                return response.data;
            } finally {
                this._inflightProcess.delete(key);
            }
        })();
        this._inflightProcess.set(key, p);
        return p;
    },

    // Get all transactions for a report
    getTransactions: async (reportId, params = {}) => {
        const response = await api.get(`/ca/bank-recon/reports/${reportId}/transactions`, {
            params,
        });
        return response.data;
    },

    // Get matches (optionally filtered by status)
    getMatches: async (reportId, status = null) => {
        const response = await api.get(`/ca/bank-recon/reports/${reportId}/matches`, {
            params: status ? { status } : {},
        });
        return response.data;
    },

    // Accept or reject a single match
    updateMatchStatus: async (matchId, status) => {
        const response = await api.patch(`/ca/bank-recon/matches/${matchId}`, { status });
        return response.data;
    },

    // Bulk accept or reject matches
    bulkUpdateMatches: async (matchIds, status) => {
        const response = await api.post('/ca/bank-recon/matches/bulk', {
            match_ids: matchIds,
            status,
        });
        return response.data;
    },

    // Create a manual match (1:1 back-compat)
    createManualMatch: async (reportId, ledgerTxnId, bankTxnId, notes = '') => {
        const response = await api.post(`/ca/bank-recon/reports/${reportId}/manual-match`, {
            ledger_txn_id: ledgerTxnId,
            bank_txn_id: bankTxnId,
            notes,
        });
        return response.data;
    },

    // Create a many-to-many manual match (arrays of ledger + bank txn ids)
    createManualMatchGroup: async (reportId, ledgerTxnIds, bankTxnIds, notes = '') => {
        const response = await api.post(`/ca/bank-recon/reports/${reportId}/manual-match`, {
            ledger_txn_ids: ledgerTxnIds,
            bank_txn_ids: bankTxnIds,
            notes,
        });
        return response.data;
    },

    // Create an adjusting entry: builds a voucher, auto-creates the counter ledger
    // if missing, pushes it to Tally via the desktop bridge, and (optionally)
    // matches it to the bank line(s) it explains.
    createAdjustment: async (reportId, adjustment) => {
        const response = await api.post(`/ca/bank-recon/reports/${reportId}/adjustment`, adjustment, {
            timeout: 200000, // voucher push + ledger upsert round-trips through the bridge
        });
        return response.data;
    },

    // Remove a manual match (returns all member txns to unmatched)
    deleteMatch: async (matchId) => {
        const response = await api.delete(`/ca/bank-recon/matches/${matchId}`);
        return response.data;
    },

    // Add a bank-statement row the parser missed (local-only, flagged manual)
    createBankEntry: async (reportId, entry) => {
        const response = await api.post(`/ca/bank-recon/reports/${reportId}/bank-entries`, entry);
        return response.data;
    },

    // Edit a manually added bank entry (only while unreconciled)
    updateBankEntry: async (reportId, txnId, entry) => {
        const response = await api.patch(`/ca/bank-recon/reports/${reportId}/bank-entries/${txnId}`, entry);
        return response.data;
    },

    // Delete a manually added bank entry (only while unreconciled)
    deleteBankEntry: async (reportId, txnId) => {
        const response = await api.delete(`/ca/bank-recon/reports/${reportId}/bank-entries/${txnId}`);
        return response.data;
    },

    // Mark-pending triage: set aside unmatched txns for later (no Tally writes)
    markPending: async (reportId, txnIds) => {
        const response = await api.post(`/ca/bank-recon/reports/${reportId}/mark-pending`, {
            txn_ids: txnIds,
        });
        return response.data;
    },

    // Return pending txns to the active unmatched list
    unmarkPending: async (reportId, txnIds) => {
        const response = await api.post(`/ca/bank-recon/reports/${reportId}/unmark-pending`, {
            txn_ids: txnIds,
        });
        return response.data;
    },

    // Get reconciliation summary
    getSummary: async (reportId) => {
        const response = await api.get(`/ca/bank-recon/reports/${reportId}/summary`);
        return response.data;
    },

    // Finalize and save report
    saveReport: async (reportId) => {
        const response = await api.post(`/ca/bank-recon/reports/${reportId}/save`);
        return response.data;
    },

    // Download Excel report
    downloadReport: async (reportId, reportName) => {
        const response = await api.get(`/ca/bank-recon/reports/${reportId}/download`, {
            responseType: 'blob',
        });
        const url = window.URL.createObjectURL(new Blob([response.data]));
        const link = document.createElement('a');
        link.href = url;
        link.setAttribute('download', `${reportName || 'bank_recon'}.xlsx`);
        document.body.appendChild(link);
        link.click();
        link.remove();
        window.URL.revokeObjectURL(url);
    },
};

// ── QuickBooks Bank Reconciliation API ──
//
// Faithful clone of bankReconApi for the QuickBooks (cloud-OAuth) ERP path. All
// routes namespaced /ca/qb/bank-recon/*. The company key is `qb_company` (the QB
// realm) everywhere bankReconApi used `company_name`, and the bank "ledger" is a
// QB bank account (`account_id`). The ledger side of a match/txn is the "book"
// side here (`book_transaction` / `book_txn_id`). QB is cloud-only — there is no
// desktop bridge; when QuickBooks is offline the backend parks the report in an
// `awaiting_source` state.

export const qbBankReconApi = {

    // Bank accounts — backend filters QB chart-of-accounts to bank accounts
    getBankAccounts: async (qbCompany) => {
        const response = await api.get('/ca/qb/bank-recon/accounts', {
            params: qbCompany ? { qb_company: qbCompany } : {},
        });
        return response.data;
    },

    // Income/expense (and other postable) accounts for the adjusting-entry
    // "Account" dropdown. Returns { accounts: [{ id, name, account_type, ... }] }.
    getPostingAccounts: async (qbCompany) => {
        const response = await api.get('/ca/qb/bank-recon/posting-accounts', {
            params: qbCompany ? { qb_company: qbCompany } : {},
        });
        return response.data;
    },

    // Auto-generated serial name for new report
    getNextReportName: async () => {
        const response = await api.get('/ca/qb/bank-recon/next-report-name');
        return response.data;
    },

    // List reports for a bank account (within a company/realm)
    listReports: async (accountId, qbCompany) => {
        const response = await api.get('/ca/qb/bank-recon/reports', {
            params: { account_id: accountId, qb_company: qbCompany },
        });
        return response.data;
    },

    // Create report + upload bank statement
    createReport: async (formData) => {
        const response = await api.post('/ca/qb/bank-recon/reports', formData, {
            headers: { 'Content-Type': 'multipart/form-data' },
            timeout: 60000,
        });
        return response.data;
    },

    // Get single report details
    getReport: async (reportId) => {
        const response = await api.get(`/ca/qb/bank-recon/reports/${reportId}`);
        return response.data;
    },

    // Update report (name, status)
    updateReport: async (reportId, data) => {
        const response = await api.patch(`/ca/qb/bank-recon/reports/${reportId}`, data);
        return response.data;
    },

    // Delete report
    deleteReport: async (reportId) => {
        const response = await api.delete(`/ca/qb/bank-recon/reports/${reportId}`);
        return response.data;
    },

    // Trigger backend processing (fetch QB book txns + run auto-match).
    // In-flight dedupe: if caller fires twice for same reportId, second call
    // reuses the first promise instead of hitting the backend again.
    // Backend is also idempotent, but this avoids needless network round-trips.
    _inflightProcess: new Map(),
    processReport: async function (reportId) {
        const key = String(reportId);
        if (this._inflightProcess.has(key)) return this._inflightProcess.get(key);
        const p = (async () => {
            try {
                const response = await api.post(`/ca/qb/bank-recon/reports/${reportId}/process`, {}, {
                    timeout: 600000, // 10 min — large QB fetches + matching can exceed 2 min
                });
                return response.data;
            } finally {
                this._inflightProcess.delete(key);
            }
        })();
        this._inflightProcess.set(key, p);
        return p;
    },

    // Get all transactions for a report
    getTransactions: async (reportId, params = {}) => {
        const response = await api.get(`/ca/qb/bank-recon/reports/${reportId}/transactions`, {
            params,
        });
        return response.data;
    },

    // Get matches (optionally filtered by status)
    getMatches: async (reportId, status = null) => {
        const response = await api.get(`/ca/qb/bank-recon/reports/${reportId}/matches`, {
            params: status ? { status } : {},
        });
        return response.data;
    },

    // Accept or reject a single match
    updateMatchStatus: async (matchId, status) => {
        const response = await api.patch(`/ca/qb/bank-recon/matches/${matchId}`, { status });
        return response.data;
    },

    // Bulk accept or reject matches
    bulkUpdateMatches: async (matchIds, status) => {
        const response = await api.post('/ca/qb/bank-recon/matches/bulk', {
            match_ids: matchIds,
            status,
        });
        return response.data;
    },

    // Create a manual match (book side + bank side) — 1:1 back-compat
    createManualMatch: async (reportId, bookTxnId, bankTxnId, notes = '') => {
        const response = await api.post(`/ca/qb/bank-recon/reports/${reportId}/manual-match`, {
            book_txn_id: bookTxnId,
            bank_txn_id: bankTxnId,
            notes,
        });
        return response.data;
    },

    // Create a many-to-many manual match (arrays of book + bank txn ids)
    createManualMatchGroup: async (reportId, bookTxnIds, bankTxnIds, notes = '') => {
        const response = await api.post(`/ca/qb/bank-recon/reports/${reportId}/manual-match`, {
            book_txn_ids: bookTxnIds,
            bank_txn_ids: bankTxnIds,
            notes,
        });
        return response.data;
    },

    // Create an adjusting entry: posts a missing book entry to QuickBooks and
    // (optionally) matches it to the bank line(s) it explains.
    createAdjustment: async (reportId, adjustment) => {
        const response = await api.post(`/ca/qb/bank-recon/reports/${reportId}/adjustment`, adjustment);
        return response.data;
    },

    // Remove a manual match (returns both txns to unmatched)
    deleteMatch: async (matchId) => {
        const response = await api.delete(`/ca/qb/bank-recon/matches/${matchId}`);
        return response.data;
    },

    // Add a bank-statement row the parser missed (local-only, flagged manual)
    createBankEntry: async (reportId, entry) => {
        const response = await api.post(`/ca/qb/bank-recon/reports/${reportId}/bank-entries`, entry);
        return response.data;
    },

    // Edit a manually added bank entry (only while unreconciled)
    updateBankEntry: async (reportId, txnId, entry) => {
        const response = await api.patch(`/ca/qb/bank-recon/reports/${reportId}/bank-entries/${txnId}`, entry);
        return response.data;
    },

    // Delete a manually added bank entry (only while unreconciled)
    deleteBankEntry: async (reportId, txnId) => {
        const response = await api.delete(`/ca/qb/bank-recon/reports/${reportId}/bank-entries/${txnId}`);
        return response.data;
    },

    // Mark-pending triage: set aside unmatched txns for later (no QBO writes)
    markPending: async (reportId, txnIds) => {
        const response = await api.post(`/ca/qb/bank-recon/reports/${reportId}/mark-pending`, {
            txn_ids: txnIds,
        });
        return response.data;
    },

    // Return pending txns to the active unmatched list
    unmarkPending: async (reportId, txnIds) => {
        const response = await api.post(`/ca/qb/bank-recon/reports/${reportId}/unmark-pending`, {
            txn_ids: txnIds,
        });
        return response.data;
    },

    // Get reconciliation summary
    getSummary: async (reportId) => {
        const response = await api.get(`/ca/qb/bank-recon/reports/${reportId}/summary`);
        return response.data;
    },

    // Finalize and save report
    saveReport: async (reportId) => {
        const response = await api.post(`/ca/qb/bank-recon/reports/${reportId}/save`);
        return response.data;
    },

    // Download Excel report
    downloadReport: async (reportId, reportName) => {
        const response = await api.get(`/ca/qb/bank-recon/reports/${reportId}/download`, {
            responseType: 'blob',
        });
        const url = window.URL.createObjectURL(new Blob([response.data]));
        const link = document.createElement('a');
        link.href = url;
        link.setAttribute('download', `${reportName || 'qb_bank_recon'}.xlsx`);
        document.body.appendChild(link);
        link.click();
        link.remove();
        window.URL.revokeObjectURL(url);
    },
};

// Export the raw axios instance for edge cases

// ── Zoho Books API ──
//
// Parallel copy of qbApi for the Zoho Books (cloud-OAuth) ERP path. All routes
// namespaced /ca/zoho/*. The company key is `organization_id` (Zoho org)
// everywhere qbApi used `qb_company`. Zoho is cloud-only — there is no desktop
// mode or WebSocket bridge.

export const zohoApi = {
    // Connection
    checkConnection: async () => {
        const response = await api.get('/ca/zoho/check-connection');
        return response.data;
    },

    checkDirectConnection: async () => {
        const response = await api.get('/ca/zoho/status');
        return response.data;
    },

    // Master reads
    getCompanies: async () => {
        const response = await api.get('/ca/zoho/get-companies');
        return response.data;
    },

    getAccounts: async (company) => {
        const response = await api.get('/ca/zoho/get-accounts', {
            params: company ? { organization_id: company } : {},
        });
        return response.data;
    },

    getCustomers: async (company) => {
        const response = await api.get('/ca/zoho/get-customers', {
            params: company ? { organization_id: company } : {},
        });
        return response.data;
    },

    getVendors: async (company) => {
        const response = await api.get('/ca/zoho/get-vendors', {
            params: company ? { organization_id: company } : {},
        });
        return response.data;
    },

    getItems: async (company) => {
        const response = await api.get('/ca/zoho/get-items', {
            params: company ? { organization_id: company } : {},
        });
        return response.data;
    },

    // Master writes
    createCustomer: async (data) => {
        const response = await api.post('/ca/zoho/create-customer', data);
        return response.data;
    },

    createVendor: async (data) => {
        const response = await api.post('/ca/zoho/create-vendor', data);
        return response.data;
    },

    createAccount: async (data) => {
        const response = await api.post('/ca/zoho/create-account', data);
        return response.data;
    },

    // Tier-1 / Tier-5 master creation helpers.
    // `createItem` wraps Zoho's Item requirements so callers can pass just
    // {name, organization_id} for the common Service case.
    createItem: async (data) => {
        const response = await api.post('/ca/zoho/create-item', data);
        return response.data;
    },

    // Tier-5: bulk-create a batch of master entities in one round-trip.
    // `items` is [{entity_type: 'customer'|'vendor'|'item'|'account', name, ...}].
    createBatch: async ({ items, organization_id }) => {
        const response = await api.post('/ca/zoho/create-batch', { items, organization_id });
        return response.data;
    },

    // Tier-4: persist a user-confirmed (alias text) -> (zoho entity id) mapping
    // so future extractions of the same variant text auto-resolve. Called when
    // the user picks an existing candidate from MatchPicker suggestions.
    saveAlias: async ({ entity_type, alias_text, zoho_entity_id, zoho_entity_name, organization_id }) => {
        const response = await api.post('/ca/zoho/alias', {
            entity_type, alias_text, zoho_entity_id, zoho_entity_name, organization_id,
        });
        return response.data;
    },

    // Mapping rules (Phase 3) — user-authorable rules that auto-map a line to a
    // Zoho account/party (keyword/pattern/vendor/amount/composite).
    mappingRules: {
        list: async ({ organization_id, document_type, account_slot } = {}) => {
            const response = await api.get('/ca/zoho/mapping-rules', {
                params: { organization_id, document_type, account_slot },
            });
            return response.data;
        },
        create: async (rule) => {
            const response = await api.post('/ca/zoho/mapping-rules', rule);
            return response.data;
        },
        update: async (ruleId, rule) => {
            const response = await api.put(`/ca/zoho/mapping-rules/${ruleId}`, rule);
            return response.data;
        },
        remove: async (ruleId) => {
            const response = await api.delete(`/ca/zoho/mapping-rules/${ruleId}`);
            return response.data;
        },
        preview: async ({ organization_id, rule, transactions }) => {
            const response = await api.post('/ca/zoho/mapping-rules/preview', {
                organization_id, rule, transactions,
            });
            return response.data;
        },
    },

    // Phase 6: presigned URL to preview a reopened draft's source document.
    draftFileUrl: async (r2_key) => {
        try {
            const response = await api.post('/ca/zoho/draft-file-url', { r2_key });
            return response.data?.url || null;
        } catch (_) {
            return null;
        }
    },

    // Phase 4: record AI suggestion accept/override so the system learns.
    recordSuggestionFeedback: async (body) => {
        try {
            const response = await api.post('/ca/zoho/suggest-account/feedback', body);
            return response.data;
        } catch (_) {
            return null; // never let feedback break the UI
        }
    },

    // Phase 2: surface near-duplicate accounts before creating a new one so
    // names stay clean ("Stationary" vs "Stationery" etc.).
    previewCreateAccount: async ({ proposed_name, account_type, organization_id }) => {
        const response = await api.post('/ca/zoho/preview-create-account', {
            proposed_name, account_type, organization_id,
        });
        return response.data;
    },

    // Document push
    pushInvoice: async ({ payload, organization_id }) => {
        const response = await api.post('/ca/zoho/push-invoice', { payload, organization_id }, {
            timeout: 180000,
        });
        return response.data;
    },

    pushBill: async ({ payload, organization_id }) => {
        const response = await api.post('/ca/zoho/push-bill', { payload, organization_id }, {
            timeout: 180000,
        });
        return response.data;
    },

    pushInvoiceZoho: async ({ payload, organization_id, extracted_total, skip_duplicate_check }) => {
        const response = await api.post('/ca/zoho/push-invoice', {
            payload, organization_id, extracted_total, skip_duplicate_check,
        });
        return response.data;
    },

    pushBillZoho: async ({ payload, organization_id, extracted_total, skip_duplicate_check }) => {
        const response = await api.post('/ca/zoho/push-bill', {
            payload, organization_id, extracted_total, skip_duplicate_check,
        });
        return response.data;
    },

    extractInvoice: async (file, organizationId) => {
        const formData = new FormData();
        formData.append('file', file);
        if (organizationId) formData.append('organization_id', organizationId);
        const response = await api.post('/ca/zoho/extract-invoice', formData, {
            timeout: 120000,
            headers: { 'Content-Type': 'multipart/form-data' },
        });
        return response.data;
    },

    extractBill: async (file, organizationId) => {
        const formData = new FormData();
        formData.append('file', file);
        if (organizationId) formData.append('organization_id', organizationId);
        const response = await api.post('/ca/zoho/extract-bill', formData, {
            timeout: 120000,
            headers: { 'Content-Type': 'multipart/form-data' },
        });
        return response.data;
    },

    // Bank reconciliation
    bank: {
        listAccounts: async (organizationId) => {
            const response = await api.get('/ca/zoho/bank/list-accounts', {
                params: organizationId ? { organization_id: organizationId } : {},
            });
            return response.data;
        },

        extractAndMatch: async ({ file, accountId, organizationId, periodStart, periodEnd, zohoClosingBalance }) => {
            const formData = new FormData();
            formData.append('file', file);
            formData.append('account_id', accountId);
            if (organizationId) formData.append('organization_id', organizationId);
            if (periodStart) formData.append('period_start', periodStart);
            if (periodEnd) formData.append('period_end', periodEnd);
            if (zohoClosingBalance !== undefined && zohoClosingBalance !== null && zohoClosingBalance !== '') {
                formData.append('zoho_closing_balance', zohoClosingBalance);
            }
            const response = await api.post('/ca/zoho/bank/extract-and-match', formData, {
                timeout: 180000,
                headers: { 'Content-Type': 'multipart/form-data' },
            });
            return response.data;
        },

        // Pass the current unmatched lists + closing balances to get a fresh
        // summary back in the same round-trip. Backwards-compatible: if the
        // optional fields are omitted, the response still includes match fields
        // at the top level.
        manualMatch: async ({
            zohoTxn, statementRow,
            unmatchedZoho, unmatchedStatement,
            zohoClosingBalance, bankClosingBalance,
        }) => {
            const response = await api.post('/ca/zoho/bank/manual-match', {
                zoho_txn: zohoTxn,
                statement_row: statementRow,
                unmatched_zoho: unmatchedZoho,
                unmatched_statement: unmatchedStatement,
                zoho_closing_balance: zohoClosingBalance ?? null,
                bank_closing_balance: bankClosingBalance ?? null,
            });
            return response.data;
        },

        // mode: 'mark_pending' | 'post_to_suspense'
        // post_to_suspense requires bankAccountId, suspenseAccountId, organizationId,
        // and statementRowIndices (defaults to all when omitted).
        resolvePending: async ({
            mode,
            unmatchedZoho,
            unmatchedStatement,
            zohoClosingBalance,
            bankClosingBalance,
            organizationId,
            bankAccountId,
            bankAccountName,
            suspenseAccountId,
            suspenseAccountName,
            statementRowIndices,
        }) => {
            const response = await api.post('/ca/zoho/bank/resolve-pending', {
                mode,
                unmatched_zoho: unmatchedZoho,
                unmatched_statement: unmatchedStatement,
                zoho_closing_balance: zohoClosingBalance ?? null,
                bank_closing_balance: bankClosingBalance ?? null,
                organization_id: organizationId,
                bank_account_id: bankAccountId,
                bank_account_name: bankAccountName,
                suspense_account_id: suspenseAccountId,
                suspense_account_name: suspenseAccountName,
                statement_row_indices: statementRowIndices,
            }, { timeout: 180000 });
            return response.data;
        },

        saveReport: async (payload) => {
            const response = await api.post('/ca/zoho/bank/save', payload);
            return response.data;
        },

        listReports: async (organizationId) => {
            const response = await api.get('/ca/zoho/bank/reports', {
                params: organizationId ? { organization_id: organizationId } : {},
            });
            return response.data;
        },

        getReport: async (id) => {
            const response = await api.get(`/ca/zoho/bank/reports/${id}`);
            return response.data;
        },

        deleteReport: async (id) => {
            const response = await api.delete(`/ca/zoho/bank/reports/${id}`);
            return response.data;
        },
    },

    // Per-user drafts for in-progress work in Zoho purchase / sales /
    // bank-entries flows. Extract endpoints best-effort upload the source PDF
    // to R2; the r2_key rides on each invoice/bill result, and the restore path
    // calls `drafts.getFileUrl(r2_key)` to mint a short-lived signed URL so
    // the PDF preview pane re-renders after reopening the draft.
    drafts: {
        save: async ({ flow, payload, title, draftId, organizationId, fileUrl, fileName, r2Key }) => {
            const response = await api.post('/ca/zoho/drafts', {
                flow,
                payload,
                title,
                draft_id: draftId,
                organization_id: organizationId,
                file_url: fileUrl,
                file_name: fileName,
                r2_key: r2Key,
            });
            return response.data;
        },
        list: async ({ flow, organizationId, status } = {}) => {
            const response = await api.get('/ca/zoho/drafts', {
                params: { flow, organization_id: organizationId, status },
            });
            return response.data;
        },
        get: async (draftId) => {
            const response = await api.get(`/ca/zoho/drafts/${draftId}`);
            return response.data;
        },
        update: async (draftId, payload) => {
            const response = await api.put(`/ca/zoho/drafts/${draftId}`, { payload });
            return response.data;
        },
        markPushed: async (draftId) => {
            const response = await api.patch(`/ca/zoho/drafts/${draftId}/push`);
            return response.data;
        },
        // Mint a short-lived presigned URL for an R2-stored source PDF. Used by
        // ZohoSalesView / ZohoPurchaseView when restoring a draft so the preview
        // pane can re-render the PDF without re-uploading.
        getFileUrl: async (r2Key) => {
            const response = await api.post('/ca/zoho/draft-file-url', { r2_key: r2Key });
            return response.data;
        },
        // Pre-push gate: confirms Zoho is connected and the draft's organization
        // still resolves. Powers the bulk-push pre-flight check.
        validatePushable: async (draftId) => {
            const response = await api.post('/ca/zoho/validate-draft-pushable', {
                draft_id: draftId,
            });
            return response.data;
        },
        delete: async (draftId) => {
            const response = await api.delete(`/ca/zoho/drafts/${draftId}`);
            return response.data;
        },
    },

    // Zoho Chat (NDJSON SSE stream)
    chatStream: async ({ message, organizationId, history, threadId, onEvent, signal }) => {
        const token = localStorage.getItem('jwt_token');
        const response = await fetch(`${API_URL}/ca/zoho/chat`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                Authorization: `Bearer ${token}`,
            },
            body: JSON.stringify({
                message,
                organization_id: organizationId,
                history,
                thread_id: threadId,
            }),
            signal,
        });
        if (!response.ok) {
            const text = await response.text();
            throw new Error(text || `HTTP ${response.status}`);
        }
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        while (true) {
            const { value, done } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            // NDJSON: events separated by \n\n (matches backend emit format)
            const parts = buffer.split("\n\n");
            buffer = parts.pop();
            for (const raw of parts) {
                const line = raw.trim();
                if (!line) continue;
                try {
                    const evt = JSON.parse(line);
                    onEvent?.(evt);
                } catch {
                    // ignore non-JSON heartbeats
                }
            }
        }
        if (buffer.trim()) {
            try { onEvent?.(JSON.parse(buffer.trim())); } catch { /* ignore */ }
        }
    },

    chatHistory: async ({ organizationId, threadId } = {}) => {
        const response = await api.get('/ca/zoho/chat/history', {
            params: {
                ...(organizationId ? { organization_id: organizationId } : {}),
                ...(threadId ? { thread_id: threadId } : {}),
            },
        });
        return response.data;
    },

    clearChat: async ({ organizationId, threadId } = {}) => {
        const response = await api.delete('/ca/zoho/chat/history', {
            params: {
                ...(organizationId ? { organization_id: organizationId } : {}),
                ...(threadId ? { thread_id: threadId } : {}),
            },
        });
        return response.data;
    },

    // Bank entries — statement -> create Zoho txns (Deposit / Purchase)
    bankEntries: {
        extract: async ({ file, accountId, accountName, organizationId }) => {
            const formData = new FormData();
            formData.append('file', file);
            formData.append('account_id', accountId);
            if (accountName) formData.append('account_name', accountName);
            if (organizationId) formData.append('organization_id', organizationId);
            const response = await api.post('/ca/zoho/bank-entries/extract', formData, {
                timeout: 180000,
                headers: { 'Content-Type': 'multipart/form-data' },
            });
            return response.data;
        },

        pushOne: async ({ entryType, payload, organizationId }) => {
            const response = await api.post('/ca/zoho/bank-entries/push-one', {
                organization_id: organizationId,
                entry_type: entryType,
                payload,
            });
            return response.data;
        },

        // Normalized-contract path (Tally parity). The frontend sends plain
        // vouchers; the backend builds every Zoho payload via build_zoho_bank_payload.
        validate: async ({ organizationId, bankAccountId, bankAccountName, vouchers }) => {
            const response = await api.post('/ca/zoho/bank-entries/validate', {
                organization_id: organizationId,
                bank_account_id: bankAccountId,
                bank_account_name: bankAccountName,
                vouchers,
            });
            return response.data;
        },

        pushBatch: async ({ organizationId, bankAccountId, bankAccountName, vouchers, autoCreateMasters = false }) => {
            const response = await api.post('/ca/zoho/bank-entries/push-batch', {
                organization_id: organizationId,
                bank_account_id: bankAccountId,
                bank_account_name: bankAccountName,
                vouchers,
                auto_create_masters: autoCreateMasters,
            }, { timeout: 180000 });
            return response.data;
        },
    },
};

// ── Zoho Books OAuth ──

export const zohoOAuthApi = {
    /** Get the Zoho consent URL — frontend then sets window.location to it. */
    getAuthUrl: async () => {
        const response = await api.get('/ca/zoho/oauth/connect');
        return response.data;
    },

    /** Returns { connected: bool, dc, api_domain, updated_at }. */
    status: async () => {
        const response = await api.get('/ca/zoho/oauth/status');
        return response.data;
    },

    disconnect: async () => {
        const response = await api.post('/ca/zoho/oauth/disconnect');
        return response.data;
    },
};

// ── Zoho stateful Bank Reconciliation ──
//
// Parallel copy of qbBankReconApi for the Zoho Books ERP path. All routes
// namespaced /ca/zoho/bank-recon/*. The company key is `organization_id`
// everywhere qbBankReconApi used `qb_company`.

export const zohoBankReconApi = {

    // Bank accounts — backend filters Zoho chart-of-accounts to bank accounts
    getBankAccounts: async (organizationId) => {
        const response = await api.get('/ca/zoho/bank-recon/accounts', {
            params: organizationId ? { organization_id: organizationId } : {},
        });
        return response.data;
    },

    // Auto-generated serial name for new report
    getNextReportName: async () => {
        const response = await api.get('/ca/zoho/bank-recon/next-report-name');
        return response.data;
    },

    // List reports for a bank account (within an organization)
    listReports: async (accountId, organizationId) => {
        const response = await api.get('/ca/zoho/bank-recon/reports', {
            params: { account_id: accountId, organization_id: organizationId },
        });
        return response.data;
    },

    // Create report + upload bank statement
    createReport: async (formData) => {
        const response = await api.post('/ca/zoho/bank-recon/reports', formData, {
            headers: { 'Content-Type': 'multipart/form-data' },
            timeout: 60000,
        });
        return response.data;
    },

    // Get single report details
    getReport: async (reportId) => {
        const response = await api.get(`/ca/zoho/bank-recon/reports/${reportId}`);
        return response.data;
    },

    // Update report (name, status)
    updateReport: async (reportId, data) => {
        const response = await api.patch(`/ca/zoho/bank-recon/reports/${reportId}`, data);
        return response.data;
    },

    // Delete report
    deleteReport: async (reportId) => {
        const response = await api.delete(`/ca/zoho/bank-recon/reports/${reportId}`);
        return response.data;
    },

    // Trigger backend processing (fetch Zoho book txns + run auto-match).
    // In-flight dedupe: if caller fires twice for same reportId, second call
    // reuses the first promise instead of hitting the backend again.
    // Backend is also idempotent, but this avoids needless network round-trips.
    _inflightProcess: new Map(),
    processReport: async function (reportId) {
        const key = String(reportId);
        if (this._inflightProcess.has(key)) return this._inflightProcess.get(key);
        const p = (async () => {
            try {
                const response = await api.post(`/ca/zoho/bank-recon/reports/${reportId}/process`, {}, {
                    timeout: 600000, // 10 min — large Zoho fetches + matching can exceed 2 min
                });
                return response.data;
            } finally {
                this._inflightProcess.delete(key);
            }
        })();
        this._inflightProcess.set(key, p);
        return p;
    },

    // Get all transactions for a report
    getTransactions: async (reportId, params = {}) => {
        const response = await api.get(`/ca/zoho/bank-recon/reports/${reportId}/transactions`, {
            params,
        });
        return response.data;
    },

    // Get matches (optionally filtered by status)
    getMatches: async (reportId, status = null) => {
        const response = await api.get(`/ca/zoho/bank-recon/reports/${reportId}/matches`, {
            params: status ? { status } : {},
        });
        return response.data;
    },

    // Accept or reject a single match
    updateMatchStatus: async (matchId, status) => {
        const response = await api.patch(`/ca/zoho/bank-recon/matches/${matchId}`, { status });
        return response.data;
    },

    // Bulk accept or reject matches
    bulkUpdateMatches: async (matchIds, status) => {
        const response = await api.post('/ca/zoho/bank-recon/matches/bulk', {
            match_ids: matchIds,
            status,
        });
        return response.data;
    },

    // Create a manual match (book side + bank side)
    createManualMatch: async (reportId, bookTxnId, bankTxnId, notes = '') => {
        const response = await api.post(`/ca/zoho/bank-recon/reports/${reportId}/manual-match`, {
            book_txn_id: bookTxnId,
            bank_txn_id: bankTxnId,
            notes,
        });
        return response.data;
    },

    // Remove a manual match (returns both txns to unmatched)
    deleteMatch: async (matchId) => {
        const response = await api.delete(`/ca/zoho/bank-recon/matches/${matchId}`);
        return response.data;
    },

    // Active income/expense accounts for the adjusting-entry 'Account' dropdown
    getPostingAccounts: async (organizationId) => {
        const response = await api.get('/ca/zoho/bank-recon/posting-accounts', {
            params: organizationId ? { organization_id: organizationId } : {},
        });
        return response.data;
    },

    // Create a many-to-many manual match (arrays of book + bank txn ids)
    createManualMatchGroup: async (reportId, bookTxnIds, bankTxnIds, notes = '') => {
        const response = await api.post(`/ca/zoho/bank-recon/reports/${reportId}/manual-match`, {
            book_txn_ids: bookTxnIds,
            bank_txn_ids: bankTxnIds,
            notes,
        });
        return response.data;
    },

    // Create an adjusting entry: posts a missing book entry to Zoho Books and
    // (optionally) matches it to the bank line(s) it explains.
    createAdjustment: async (reportId, adjustment) => {
        const response = await api.post(`/ca/zoho/bank-recon/reports/${reportId}/adjustment`, adjustment);
        return response.data;
    },

    // Add a bank-statement row the parser missed (local-only, flagged manual)
    createBankEntry: async (reportId, entry) => {
        const response = await api.post(`/ca/zoho/bank-recon/reports/${reportId}/bank-entries`, entry);
        return response.data;
    },

    // Edit a manually added bank entry (only while unreconciled)
    updateBankEntry: async (reportId, txnId, entry) => {
        const response = await api.patch(`/ca/zoho/bank-recon/reports/${reportId}/bank-entries/${txnId}`, entry);
        return response.data;
    },

    // Delete a manually added bank entry (only while unreconciled)
    deleteBankEntry: async (reportId, txnId) => {
        const response = await api.delete(`/ca/zoho/bank-recon/reports/${reportId}/bank-entries/${txnId}`);
        return response.data;
    },

    // Mark-pending triage: set aside unmatched txns for later (no Zoho writes)
    markPending: async (reportId, txnIds) => {
        const response = await api.post(`/ca/zoho/bank-recon/reports/${reportId}/mark-pending`, {
            txn_ids: txnIds,
        });
        return response.data;
    },

    // Return pending txns to the active unmatched list
    unmarkPending: async (reportId, txnIds) => {
        const response = await api.post(`/ca/zoho/bank-recon/reports/${reportId}/unmark-pending`, {
            txn_ids: txnIds,
        });
        return response.data;
    },

    // Get reconciliation summary
    getSummary: async (reportId) => {
        const response = await api.get(`/ca/zoho/bank-recon/reports/${reportId}/summary`);
        return response.data;
    },

    // Finalize and save report
    saveReport: async (reportId) => {
        const response = await api.post(`/ca/zoho/bank-recon/reports/${reportId}/save`);
        return response.data;
    },

    // Download Excel report
    downloadReport: async (reportId, reportName) => {
        const response = await api.get(`/ca/zoho/bank-recon/reports/${reportId}/download`, {
            responseType: 'blob',
        });
        const url = window.URL.createObjectURL(new Blob([response.data]));
        const link = document.createElement('a');
        link.href = url;
        link.setAttribute('download', `${reportName || 'zoho_bank_recon'}.xlsx`);
        document.body.appendChild(link);
        link.click();
        link.remove();
        window.URL.revokeObjectURL(url);
    },
};

// Parallel copy of zohoApi for the Xero (cloud-OAuth) ERP path. All routes
// namespaced /ca/xero/*. The company key is `tenant_id` (Xero tenant)
// everywhere zohoApi used `organization_id`. Xero is cloud-only — there is no
// desktop mode or WebSocket bridge.

export const xeroApi = {
    // Connection
    checkConnection: async () => {
        const response = await api.get('/ca/xero/check-connection');
        return response.data;
    },

    checkDirectConnection: async () => {
        const response = await api.get('/ca/xero/status');
        return response.data;
    },

    // Master reads
    getCompanies: async () => {
        const response = await api.get('/ca/xero/get-companies');
        return response.data;
    },

    getAccounts: async (company) => {
        const response = await api.get('/ca/xero/get-accounts', {
            params: company ? { tenant_id: company } : {},
        });
        return response.data;
    },

    getCustomers: async (company) => {
        const response = await api.get('/ca/xero/get-customers', {
            params: company ? { tenant_id: company } : {},
        });
        return response.data;
    },

    getVendors: async (company) => {
        const response = await api.get('/ca/xero/get-vendors', {
            params: company ? { tenant_id: company } : {},
        });
        return response.data;
    },

    getItems: async (company) => {
        const response = await api.get('/ca/xero/get-items', {
            params: company ? { tenant_id: company } : {},
        });
        return response.data;
    },

    // Master writes
    createCustomer: async (data) => {
        const response = await api.post('/ca/xero/create-customer', data);
        return response.data;
    },

    createVendor: async (data) => {
        const response = await api.post('/ca/xero/create-vendor', data);
        return response.data;
    },

    createAccount: async (data) => {
        const response = await api.post('/ca/xero/create-account', data);
        return response.data;
    },

    // Tier-1 / Tier-5 master creation helpers.
    // `createItem` wraps Xero's Item requirements so callers can pass just
    // {name, tenant_id} for the common Service case.
    createItem: async (data) => {
        const response = await api.post('/ca/xero/create-item', data);
        return response.data;
    },

    // Tier-5: bulk-create a batch of master entities in one round-trip.
    // `items` is [{entity_type: 'customer'|'vendor'|'item'|'account', name, ...}].
    createBatch: async ({ items, tenant_id }) => {
        const response = await api.post('/ca/xero/create-batch', { items, tenant_id });
        return response.data;
    },

    // Tier-4: persist a user-confirmed (alias text) -> (xero entity id) mapping
    // so future extractions of the same variant text auto-resolve. Called when
    // the user picks an existing candidate from MatchPicker suggestions.
    saveAlias: async ({ entity_type, alias_text, xero_entity_id, xero_entity_name, tenant_id }) => {
        const response = await api.post('/ca/xero/alias', {
            entity_type, alias_text, xero_entity_id, xero_entity_name, tenant_id,
        });
        return response.data;
    },

    // Mapping rules (Phase 3) — user-authorable rules that auto-map a line to a
    // Xero account/party (keyword/pattern/vendor/amount/composite).
    mappingRules: {
        list: async ({ tenant_id, document_type, account_slot } = {}) => {
            const response = await api.get('/ca/xero/mapping-rules', {
                params: { tenant_id, document_type, account_slot },
            });
            return response.data;
        },
        create: async (rule) => {
            const response = await api.post('/ca/xero/mapping-rules', rule);
            return response.data;
        },
        update: async (ruleId, rule) => {
            const response = await api.put(`/ca/xero/mapping-rules/${ruleId}`, rule);
            return response.data;
        },
        remove: async (ruleId) => {
            const response = await api.delete(`/ca/xero/mapping-rules/${ruleId}`);
            return response.data;
        },
        preview: async ({ tenant_id, rule, transactions }) => {
            const response = await api.post('/ca/xero/mapping-rules/preview', {
                tenant_id, rule, transactions,
            });
            return response.data;
        },
    },

    // Phase 6: presigned URL to preview a reopened draft's source document.
    draftFileUrl: async (r2_key) => {
        try {
            const response = await api.post('/ca/xero/draft-file-url', { r2_key });
            return response.data?.url || null;
        } catch (_) {
            return null;
        }
    },

    // Phase 4: record AI suggestion accept/override so the system learns.
    recordSuggestionFeedback: async (body) => {
        try {
            const response = await api.post('/ca/xero/suggest-account/feedback', body);
            return response.data;
        } catch (_) {
            return null; // never let feedback break the UI
        }
    },

    // Phase 2: surface near-duplicate accounts before creating a new one so
    // names stay clean ("Stationary" vs "Stationery" etc.).
    previewCreateAccount: async ({ proposed_name, account_type, tenant_id }) => {
        const response = await api.post('/ca/xero/preview-create-account', {
            proposed_name, account_type, tenant_id,
        });
        return response.data;
    },

    // Document push
    pushInvoice: async ({ payload, tenant_id }) => {
        const response = await api.post('/ca/xero/push-invoice', { payload, tenant_id }, {
            timeout: 180000,
        });
        return response.data;
    },

    pushBill: async ({ payload, tenant_id }) => {
        const response = await api.post('/ca/xero/push-bill', { payload, tenant_id }, {
            timeout: 180000,
        });
        return response.data;
    },

    pushInvoiceXero: async ({ payload, tenant_id, extracted_total, skip_duplicate_check }) => {
        const response = await api.post('/ca/xero/push-invoice', {
            payload, tenant_id, extracted_total, skip_duplicate_check,
        });
        return response.data;
    },

    pushBillXero: async ({ payload, tenant_id, extracted_total, skip_duplicate_check }) => {
        const response = await api.post('/ca/xero/push-bill', {
            payload, tenant_id, extracted_total, skip_duplicate_check,
        });
        return response.data;
    },

    extractInvoice: async (file, tenantId) => {
        const formData = new FormData();
        formData.append('file', file);
        if (tenantId) formData.append('tenant_id', tenantId);
        const response = await api.post('/ca/xero/extract-invoice', formData, {
            timeout: 120000,
            headers: { 'Content-Type': 'multipart/form-data' },
        });
        return response.data;
    },

    extractBill: async (file, tenantId) => {
        const formData = new FormData();
        formData.append('file', file);
        if (tenantId) formData.append('tenant_id', tenantId);
        const response = await api.post('/ca/xero/extract-bill', formData, {
            timeout: 120000,
            headers: { 'Content-Type': 'multipart/form-data' },
        });
        return response.data;
    },

    // Bank reconciliation
    bank: {
        listAccounts: async (tenantId) => {
            const response = await api.get('/ca/xero/bank/list-accounts', {
                params: tenantId ? { tenant_id: tenantId } : {},
            });
            return response.data;
        },

        extractAndMatch: async ({ file, accountId, tenantId, periodStart, periodEnd, xeroClosingBalance }) => {
            const formData = new FormData();
            formData.append('file', file);
            formData.append('account_id', accountId);
            if (tenantId) formData.append('tenant_id', tenantId);
            if (periodStart) formData.append('period_start', periodStart);
            if (periodEnd) formData.append('period_end', periodEnd);
            if (xeroClosingBalance !== undefined && xeroClosingBalance !== null && xeroClosingBalance !== '') {
                formData.append('xero_closing_balance', xeroClosingBalance);
            }
            const response = await api.post('/ca/xero/bank/extract-and-match', formData, {
                timeout: 180000,
                headers: { 'Content-Type': 'multipart/form-data' },
            });
            return response.data;
        },

        // Pass the current unmatched lists + closing balances to get a fresh
        // summary back in the same round-trip. Backwards-compatible: if the
        // optional fields are omitted, the response still includes match fields
        // at the top level.
        manualMatch: async ({
            xeroTxn, statementRow,
            unmatchedXero, unmatchedStatement,
            xeroClosingBalance, bankClosingBalance,
        }) => {
            const response = await api.post('/ca/xero/bank/manual-match', {
                xero_txn: xeroTxn,
                statement_row: statementRow,
                unmatched_xero: unmatchedXero,
                unmatched_statement: unmatchedStatement,
                xero_closing_balance: xeroClosingBalance ?? null,
                bank_closing_balance: bankClosingBalance ?? null,
            });
            return response.data;
        },

        // mode: 'mark_pending' | 'post_to_suspense'
        // post_to_suspense requires bankAccountId, suspenseAccountId, tenantId,
        // and statementRowIndices (defaults to all when omitted).
        resolvePending: async ({
            mode,
            unmatchedXero,
            unmatchedStatement,
            xeroClosingBalance,
            bankClosingBalance,
            tenantId,
            bankAccountId,
            bankAccountName,
            suspenseAccountId,
            suspenseAccountName,
            statementRowIndices,
        }) => {
            const response = await api.post('/ca/xero/bank/resolve-pending', {
                mode,
                unmatched_xero: unmatchedXero,
                unmatched_statement: unmatchedStatement,
                xero_closing_balance: xeroClosingBalance ?? null,
                bank_closing_balance: bankClosingBalance ?? null,
                tenant_id: tenantId,
                bank_account_id: bankAccountId,
                bank_account_name: bankAccountName,
                suspense_account_id: suspenseAccountId,
                suspense_account_name: suspenseAccountName,
                statement_row_indices: statementRowIndices,
            }, { timeout: 180000 });
            return response.data;
        },

        saveReport: async (payload) => {
            const response = await api.post('/ca/xero/bank/save', payload);
            return response.data;
        },

        listReports: async (tenantId) => {
            const response = await api.get('/ca/xero/bank/reports', {
                params: tenantId ? { tenant_id: tenantId } : {},
            });
            return response.data;
        },

        getReport: async (id) => {
            const response = await api.get(`/ca/xero/bank/reports/${id}`);
            return response.data;
        },

        deleteReport: async (id) => {
            const response = await api.delete(`/ca/xero/bank/reports/${id}`);
            return response.data;
        },
    },

    // Per-user drafts for in-progress work in Xero purchase / sales /
    // bank-entries flows. PDFs are not persisted — extracted state + edits only.
    drafts: {
        save: async ({ flow, payload, title, draftId, tenantId }) => {
            const response = await api.post('/ca/xero/drafts', {
                flow,
                payload,
                title,
                draft_id: draftId,
                tenant_id: tenantId,
            });
            return response.data;
        },
        list: async ({ flow, tenantId, status } = {}) => {
            const response = await api.get('/ca/xero/drafts', {
                params: { flow, tenant_id: tenantId, status },
            });
            return response.data;
        },
        get: async (draftId) => {
            const response = await api.get(`/ca/xero/drafts/${draftId}`);
            return response.data;
        },
        update: async (draftId, payload) => {
            const response = await api.put(`/ca/xero/drafts/${draftId}`, { payload });
            return response.data;
        },
        markPushed: async (draftId) => {
            const response = await api.patch(`/ca/xero/drafts/${draftId}/push`);
            return response.data;
        },
        // Pre-push gate: confirms Xero is connected and the draft's tenant still
        // resolves. Powers the bulk-push pre-flight check.
        validatePushable: async (draftId) => {
            const response = await api.post('/ca/xero/validate-draft-pushable', {
                draft_id: draftId,
            });
            return response.data;
        },
        delete: async (draftId) => {
            const response = await api.delete(`/ca/xero/drafts/${draftId}`);
            return response.data;
        },
    },

    // Xero Chat (NDJSON SSE stream)
    chatStream: async ({ message, tenantId, history, onEvent, signal }) => {
        const token = localStorage.getItem('jwt_token');
        const response = await fetch(`${API_URL}/ca/xero/chat`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                Authorization: `Bearer ${token}`,
            },
            body: JSON.stringify({
                message,
                tenant_id: tenantId,
                history,
            }),
            signal,
        });
        if (!response.ok) {
            const text = await response.text();
            throw new Error(text || `HTTP ${response.status}`);
        }
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        while (true) {
            const { value, done } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            // NDJSON: events separated by \n\n (matches backend emit format)
            const parts = buffer.split("\n\n");
            buffer = parts.pop();
            for (const raw of parts) {
                const line = raw.trim();
                if (!line) continue;
                try {
                    const evt = JSON.parse(line);
                    onEvent?.(evt);
                } catch {
                    // ignore non-JSON heartbeats
                }
            }
        }
        if (buffer.trim()) {
            try { onEvent?.(JSON.parse(buffer.trim())); } catch { /* ignore */ }
        }
    },

    // Bank entries — statement -> create Xero txns (Deposit / Purchase)
    bankEntries: {
        extract: async ({ file, accountId, accountName, tenantId }) => {
            const formData = new FormData();
            formData.append('file', file);
            formData.append('account_id', accountId);
            if (accountName) formData.append('account_name', accountName);
            if (tenantId) formData.append('tenant_id', tenantId);
            const response = await api.post('/ca/xero/bank-entries/extract', formData, {
                timeout: 180000,
                headers: { 'Content-Type': 'multipart/form-data' },
            });
            return response.data;
        },

        pushOne: async ({ entryType, payload, tenantId }) => {
            const response = await api.post('/ca/xero/bank-entries/push-one', {
                tenant_id: tenantId,
                entry_type: entryType,
                payload,
            });
            return response.data;
        },

        // Normalized-contract path (Tally parity). The frontend sends plain
        // vouchers; the backend builds every Xero payload via build_xero_bank_payload.
        validate: async ({ tenantId, bankAccountId, bankAccountName, vouchers }) => {
            const response = await api.post('/ca/xero/bank-entries/validate', {
                tenant_id: tenantId,
                bank_account_id: bankAccountId,
                bank_account_name: bankAccountName,
                vouchers,
            });
            return response.data;
        },

        pushBatch: async ({ tenantId, bankAccountId, bankAccountName, vouchers, autoCreateMasters = false }) => {
            const response = await api.post('/ca/xero/bank-entries/push-batch', {
                tenant_id: tenantId,
                bank_account_id: bankAccountId,
                bank_account_name: bankAccountName,
                vouchers,
                auto_create_masters: autoCreateMasters,
            }, { timeout: 180000 });
            return response.data;
        },
    },
};

// ── Xero OAuth ──

export const xeroOAuthApi = {
    /** Get the Xero consent URL — frontend then sets window.location to it. */
    getAuthUrl: async () => {
        const response = await api.get('/ca/xero/oauth/connect');
        return response.data;
    },

    /** Returns { connected: bool, tenants: [...] }. */
    status: async () => {
        const response = await api.get('/ca/xero/oauth/status');
        return response.data;
    },

    disconnect: async (tenantId) => {
        const response = await api.post('/ca/xero/oauth/disconnect', null, {
            params: tenantId ? { tenant_id: tenantId } : {},
        });
        return response.data;
    },
};

// ── Xero stateful Bank Reconciliation ──
//
// Parallel copy of qbBankReconApi for the Xero ERP path. All routes
// namespaced /ca/xero/bank-recon/*. The company key is `tenant_id`
// everywhere qbBankReconApi used `qb_company`.

export const xeroBankReconApi = {

    // Bank accounts — backend filters Xero chart-of-accounts to bank accounts
    getBankAccounts: async (tenantId) => {
        const response = await api.get('/ca/xero/bank-recon/accounts', {
            params: tenantId ? { tenant_id: tenantId } : {},
        });
        return response.data;
    },

    // Auto-generated serial name for new report
    getNextReportName: async () => {
        const response = await api.get('/ca/xero/bank-recon/next-report-name');
        return response.data;
    },

    // List reports for a bank account (within a tenant)
    listReports: async (accountId, tenantId) => {
        const response = await api.get('/ca/xero/bank-recon/reports', {
            params: { account_id: accountId, tenant_id: tenantId },
        });
        return response.data;
    },

    // Create report + upload bank statement
    createReport: async (formData) => {
        const response = await api.post('/ca/xero/bank-recon/reports', formData, {
            headers: { 'Content-Type': 'multipart/form-data' },
            timeout: 60000,
        });
        return response.data;
    },

    // Get single report details
    getReport: async (reportId) => {
        const response = await api.get(`/ca/xero/bank-recon/reports/${reportId}`);
        return response.data;
    },

    // Update report (name, status)
    updateReport: async (reportId, data) => {
        const response = await api.patch(`/ca/xero/bank-recon/reports/${reportId}`, data);
        return response.data;
    },

    // Delete report
    deleteReport: async (reportId) => {
        const response = await api.delete(`/ca/xero/bank-recon/reports/${reportId}`);
        return response.data;
    },

    // Trigger backend processing (fetch Xero book txns + run auto-match).
    // In-flight dedupe: if caller fires twice for same reportId, second call
    // reuses the first promise instead of hitting the backend again.
    // Backend is also idempotent, but this avoids needless network round-trips.
    _inflightProcess: new Map(),
    processReport: async function (reportId) {
        const key = String(reportId);
        if (this._inflightProcess.has(key)) return this._inflightProcess.get(key);
        const p = (async () => {
            try {
                const response = await api.post(`/ca/xero/bank-recon/reports/${reportId}/process`, {}, {
                    timeout: 600000, // 10 min — large Xero fetches + matching can exceed 2 min
                });
                return response.data;
            } finally {
                this._inflightProcess.delete(key);
            }
        })();
        this._inflightProcess.set(key, p);
        return p;
    },

    // Get all transactions for a report
    getTransactions: async (reportId, params = {}) => {
        const response = await api.get(`/ca/xero/bank-recon/reports/${reportId}/transactions`, {
            params,
        });
        return response.data;
    },

    // Get matches (optionally filtered by status)
    getMatches: async (reportId, status = null) => {
        const response = await api.get(`/ca/xero/bank-recon/reports/${reportId}/matches`, {
            params: status ? { status } : {},
        });
        return response.data;
    },

    // Accept or reject a single match
    updateMatchStatus: async (matchId, status) => {
        const response = await api.patch(`/ca/xero/bank-recon/matches/${matchId}`, { status });
        return response.data;
    },

    // Bulk accept or reject matches
    bulkUpdateMatches: async (matchIds, status) => {
        const response = await api.post('/ca/xero/bank-recon/matches/bulk', {
            match_ids: matchIds,
            status,
        });
        return response.data;
    },

    // Create a manual match (book side + bank side)
    createManualMatch: async (reportId, bookTxnId, bankTxnId, notes = '') => {
        const response = await api.post(`/ca/xero/bank-recon/reports/${reportId}/manual-match`, {
            book_txn_id: bookTxnId,
            bank_txn_id: bankTxnId,
            notes,
        });
        return response.data;
    },

    // Remove a manual match (returns both txns to unmatched)
    deleteMatch: async (matchId) => {
        const response = await api.delete(`/ca/xero/bank-recon/matches/${matchId}`);
        return response.data;
    },

    // Mark-pending triage: set aside unmatched txns for later (no Xero writes)
    markPending: async (reportId, txnIds) => {
        const response = await api.post(`/ca/xero/bank-recon/reports/${reportId}/mark-pending`, {
            txn_ids: txnIds,
        });
        return response.data;
    },

    // Return pending txns to the active unmatched list
    unmarkPending: async (reportId, txnIds) => {
        const response = await api.post(`/ca/xero/bank-recon/reports/${reportId}/unmark-pending`, {
            txn_ids: txnIds,
        });
        return response.data;
    },

    // Get reconciliation summary
    getSummary: async (reportId) => {
        const response = await api.get(`/ca/xero/bank-recon/reports/${reportId}/summary`);
        return response.data;
    },

    // Finalize and save report
    saveReport: async (reportId) => {
        const response = await api.post(`/ca/xero/bank-recon/reports/${reportId}/save`);
        return response.data;
    },

    // Download Excel report
    downloadReport: async (reportId, reportName) => {
        const response = await api.get(`/ca/xero/bank-recon/reports/${reportId}/download`, {
            responseType: 'blob',
        });
        const url = window.URL.createObjectURL(new Blob([response.data]));
        const link = document.createElement('a');
        link.href = url;
        link.setAttribute('download', `${reportName || 'xero_bank_recon'}.xlsx`);
        document.body.appendChild(link);
        link.click();
        link.remove();
        window.URL.revokeObjectURL(url);
    },
};

// Parallel copy of zohoApi for the SAP S/4HANA (cloud) ERP path. All routes
// namespaced /ca/sap/*. The company key is `company_code` (string)
// everywhere zohoApi used `organization_id`. SAP push endpoints take a
// `sap_payload` (vs zoho_payload). OAuth may run in either authorization_code
// or client_credentials mode — getAuthUrl surfaces whichever the backend uses.

export const sapApi = {
    // Connection
    checkConnection: async () => {
        const response = await api.get('/ca/sap/check-connection');
        return response.data;
    },

    checkDirectConnection: async () => {
        const response = await api.get('/ca/sap/status');
        return response.data;
    },

    // Master reads
    getCompanies: async () => {
        const response = await api.get('/ca/sap/get-companies');
        return response.data;
    },

    getAccounts: async (company) => {
        const response = await api.get('/ca/sap/get-accounts', {
            params: company ? { company_code: company } : {},
        });
        return response.data;
    },

    getCustomers: async (company) => {
        const response = await api.get('/ca/sap/get-customers', {
            params: company ? { company_code: company } : {},
        });
        return response.data;
    },

    getVendors: async (company) => {
        const response = await api.get('/ca/sap/get-vendors', {
            params: company ? { company_code: company } : {},
        });
        return response.data;
    },

    getItems: async (company) => {
        const response = await api.get('/ca/sap/get-items', {
            params: company ? { company_code: company } : {},
        });
        return response.data;
    },

    // Master writes
    createCustomer: async (data) => {
        const response = await api.post('/ca/sap/create-customer', data);
        return response.data;
    },

    createVendor: async (data) => {
        const response = await api.post('/ca/sap/create-vendor', data);
        return response.data;
    },

    createAccount: async (data) => {
        const response = await api.post('/ca/sap/create-account', data);
        return response.data;
    },

    // Tier-1 / Tier-5 master creation helpers.
    // `createItem` wraps SAP's Item requirements so callers can pass just
    // {name, company_code} for the common Service case.
    createItem: async (data) => {
        const response = await api.post('/ca/sap/create-item', data);
        return response.data;
    },

    // Tier-5: bulk-create a batch of master entities in one round-trip.
    // `items` is [{entity_type: 'customer'|'vendor'|'item'|'account', name, ...}].
    createBatch: async ({ items, company_code }) => {
        const response = await api.post('/ca/sap/create-batch', { items, company_code });
        return response.data;
    },

    // Tier-4: persist a user-confirmed (alias text) -> (sap entity id) mapping
    // so future extractions of the same variant text auto-resolve. Called when
    // the user picks an existing candidate from MatchPicker suggestions.
    saveAlias: async ({ entity_type, alias_text, sap_entity_id, sap_entity_name, company_code }) => {
        // Backend AliasSaveRequest expects entity_id / entity_name (not sap_-prefixed).
        const response = await api.post('/ca/sap/alias', {
            entity_type, alias_text, entity_id: sap_entity_id, entity_name: sap_entity_name, company_code,
        });
        return response.data;
    },

    // Phase 2: surface near-duplicate accounts before creating a new one so
    // names stay clean ("Stationary" vs "Stationery" etc.).
    previewCreateAccount: async ({ proposed_name, account_type, company_code }) => {
        const response = await api.post('/ca/sap/preview-create-account', {
            proposed_name, account_type, company_code,
        });
        return response.data;
    },

    // Document push
    pushInvoice: async ({ sap_payload, company_code, extracted_total }) => {
        const response = await api.post('/ca/sap/push-invoice', { sap_payload, company_code, extracted_total }, {
            timeout: 180000,
        });
        return response.data;
    },

    pushBill: async ({ sap_payload, company_code, extracted_total }) => {
        const response = await api.post('/ca/sap/push-bill', { sap_payload, company_code, extracted_total }, {
            timeout: 180000,
        });
        return response.data;
    },

    pushInvoiceSap: async ({ sap_payload, company_code, extracted_total, skip_duplicate_check }) => {
        const response = await api.post('/ca/sap/push-invoice', {
            sap_payload, company_code, extracted_total, skip_duplicate_check,
        });
        return response.data;
    },

    pushBillSap: async ({ sap_payload, company_code, extracted_total, skip_duplicate_check }) => {
        const response = await api.post('/ca/sap/push-bill', {
            sap_payload, company_code, extracted_total, skip_duplicate_check,
        });
        return response.data;
    },

    extractInvoice: async (file, companyCode) => {
        const formData = new FormData();
        formData.append('file', file);
        if (companyCode) formData.append('company_code', companyCode);
        const response = await api.post('/ca/sap/extract-invoice', formData, {
            timeout: 120000,
            headers: { 'Content-Type': 'multipart/form-data' },
        });
        return response.data;
    },

    extractBill: async (file, companyCode) => {
        const formData = new FormData();
        formData.append('file', file);
        if (companyCode) formData.append('company_code', companyCode);
        const response = await api.post('/ca/sap/extract-bill', formData, {
            timeout: 120000,
            headers: { 'Content-Type': 'multipart/form-data' },
        });
        return response.data;
    },

    // Bank reconciliation
    bank: {
        listAccounts: async (companyCode) => {
            const response = await api.get('/ca/sap/bank/list-accounts', {
                params: companyCode ? { company_code: companyCode } : {},
            });
            return response.data;
        },

        extractAndMatch: async ({ file, accountId, companyCode, periodStart, periodEnd, sapClosingBalance }) => {
            const formData = new FormData();
            formData.append('file', file);
            formData.append('account_id', accountId);
            if (companyCode) formData.append('company_code', companyCode);
            if (periodStart) formData.append('period_start', periodStart);
            if (periodEnd) formData.append('period_end', periodEnd);
            if (sapClosingBalance !== undefined && sapClosingBalance !== null && sapClosingBalance !== '') {
                formData.append('sap_closing_balance', sapClosingBalance);
            }
            const response = await api.post('/ca/sap/bank/extract-and-match', formData, {
                timeout: 180000,
                headers: { 'Content-Type': 'multipart/form-data' },
            });
            return response.data;
        },

        // Pass the current unmatched lists + closing balances to get a fresh
        // summary back in the same round-trip. Backwards-compatible: if the
        // optional fields are omitted, the response still includes match fields
        // at the top level.
        manualMatch: async ({
            sapTxn, statementRow,
            unmatchedSap, unmatchedStatement,
            sapClosingBalance, bankClosingBalance,
        }) => {
            const response = await api.post('/ca/sap/bank/manual-match', {
                sap_txn: sapTxn,
                statement_row: statementRow,
                unmatched_sap: unmatchedSap,
                unmatched_statement: unmatchedStatement,
                sap_closing_balance: sapClosingBalance ?? null,
                bank_closing_balance: bankClosingBalance ?? null,
            });
            return response.data;
        },

        // mode: 'mark_pending' | 'post_to_suspense'
        // post_to_suspense requires bankAccountId, suspenseAccountId, companyCode,
        // and statementRowIndices (defaults to all when omitted).
        resolvePending: async ({
            mode,
            unmatchedSap,
            unmatchedStatement,
            sapClosingBalance,
            bankClosingBalance,
            companyCode,
            bankAccountId,
            bankAccountName,
            suspenseAccountId,
            suspenseAccountName,
            statementRowIndices,
        }) => {
            const response = await api.post('/ca/sap/bank/resolve-pending', {
                mode,
                unmatched_sap: unmatchedSap,
                unmatched_statement: unmatchedStatement,
                sap_closing_balance: sapClosingBalance ?? null,
                bank_closing_balance: bankClosingBalance ?? null,
                company_code: companyCode,
                bank_account_id: bankAccountId,
                bank_account_name: bankAccountName,
                suspense_account_id: suspenseAccountId,
                suspense_account_name: suspenseAccountName,
                statement_row_indices: statementRowIndices,
            }, { timeout: 180000 });
            return response.data;
        },

        saveReport: async (payload) => {
            const response = await api.post('/ca/sap/bank/save', payload);
            return response.data;
        },

        listReports: async (companyCode) => {
            const response = await api.get('/ca/sap/bank/reports', {
                params: companyCode ? { company_code: companyCode } : {},
            });
            return response.data;
        },

        getReport: async (id) => {
            const response = await api.get(`/ca/sap/bank/reports/${id}`);
            return response.data;
        },

        deleteReport: async (id) => {
            const response = await api.delete(`/ca/sap/bank/reports/${id}`);
            return response.data;
        },
    },

    // Per-user drafts for in-progress work in SAP purchase / sales /
    // bank-entries flows. PDFs are not persisted — extracted state + edits only.
    drafts: {
        save: async ({ flow, payload, title, draftId, companyCode }) => {
            const response = await api.post('/ca/sap/drafts', {
                flow,
                payload,
                title,
                draft_id: draftId,
                company_code: companyCode,
            });
            return response.data;
        },
        list: async ({ flow, companyCode, status } = {}) => {
            const response = await api.get('/ca/sap/drafts', {
                params: { flow, company_code: companyCode, status },
            });
            return response.data;
        },
        get: async (draftId) => {
            const response = await api.get(`/ca/sap/drafts/${draftId}`);
            return response.data;
        },
        update: async (draftId, payload) => {
            const response = await api.put(`/ca/sap/drafts/${draftId}`, { payload });
            return response.data;
        },
        markPushed: async (draftId) => {
            const response = await api.patch(`/ca/sap/drafts/${draftId}/push`);
            return response.data;
        },
        // Pre-push gate: confirms SAP is connected and the draft's company_code
        // still resolves. Powers the bulk-push pre-flight check.
        validatePushable: async (draftId) => {
            const response = await api.post('/ca/sap/validate-draft-pushable', {
                draft_id: draftId,
            });
            return response.data;
        },
        delete: async (draftId) => {
            const response = await api.delete(`/ca/sap/drafts/${draftId}`);
            return response.data;
        },
    },

    // SAP Chat (NDJSON SSE stream)
    chatStream: async ({ message, companyCode, history, onEvent, signal }) => {
        const token = localStorage.getItem('jwt_token');
        const response = await fetch(`${API_URL}/ca/sap/chat`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                Authorization: `Bearer ${token}`,
            },
            body: JSON.stringify({
                message,
                company_code: companyCode,
                history,
            }),
            signal,
        });
        if (!response.ok) {
            const text = await response.text();
            throw new Error(text || `HTTP ${response.status}`);
        }
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        while (true) {
            const { value, done } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            // NDJSON: events separated by \n\n (matches backend emit format)
            const parts = buffer.split("\n\n");
            buffer = parts.pop();
            for (const raw of parts) {
                const line = raw.trim();
                if (!line) continue;
                try {
                    const evt = JSON.parse(line);
                    onEvent?.(evt);
                } catch {
                    // ignore non-JSON heartbeats
                }
            }
        }
        if (buffer.trim()) {
            try { onEvent?.(JSON.parse(buffer.trim())); } catch { /* ignore */ }
        }
    },

    // Chat history + financial brief (per backend contract).
    chatHistory: async () => {
        const response = await api.get('/ca/sap/chat/history');
        return response.data;
    },

    clearChat: async () => {
        const response = await api.delete('/ca/sap/chat/history');
        return response.data;
    },

    refreshBrief: async () => {
        const response = await api.post('/ca/sap/brief/refresh');
        return response.data;
    },

    getBrief: async () => {
        const response = await api.get('/ca/sap/brief');
        return response.data;
    },

    // Bank entries — statement -> create SAP txns (Deposit / Purchase)
    bankEntries: {
        extract: async ({ file, accountId, accountName, companyCode }) => {
            const formData = new FormData();
            formData.append('file', file);
            formData.append('account_id', accountId);
            if (accountName) formData.append('account_name', accountName);
            if (companyCode) formData.append('company_code', companyCode);
            const response = await api.post('/ca/sap/bank-entries/extract', formData, {
                timeout: 180000,
                headers: { 'Content-Type': 'multipart/form-data' },
            });
            return response.data;
        },

        pushOne: async ({ entryType, payload, companyCode }) => {
            const response = await api.post('/ca/sap/bank-entries/push-one', {
                company_code: companyCode,
                entry_type: entryType,
                payload,
            });
            return response.data;
        },

        openBills: async (companyCode) => {
            const response = await api.get('/ca/sap/bank-entries/open-bills', {
                params: companyCode ? { company_code: companyCode } : {},
            });
            return response.data;
        },

        openInvoices: async (companyCode) => {
            const response = await api.get('/ca/sap/bank-entries/open-invoices', {
                params: companyCode ? { company_code: companyCode } : {},
            });
            return response.data;
        },
    },
};

// ── SAP S/4HANA OAuth ──

export const sapOAuthApi = {
    /**
     * Get the SAP connect descriptor. Returns EITHER
     *   { mode: 'authorization_code', auth_url, state }  → redirect window to auth_url, or
     *   { mode: 'client_credentials', connected: true, api_base }  → already connected.
     */
    getAuthUrl: async () => {
        const response = await api.get('/ca/sap/oauth/connect');
        return response.data;
    },

    /** Returns { connected: bool, organizations: [...] }. */
    status: async () => {
        const response = await api.get('/ca/sap/oauth/status');
        return response.data;
    },

    disconnect: async (companyCode) => {
        const response = await api.post('/ca/sap/oauth/disconnect', null, {
            params: companyCode ? { company_code: companyCode } : {},
        });
        return response.data;
    },
};

// ──────────────────────────────────────────────────────────────────────
// Dashboard — unified analytics across all connected platforms
// ──────────────────────────────────────────────────────────────────────

export const dashboardApi = {
    /** Returns { tally: {...}, quickbooks: {...}, zoho: {...}, xero: {...} }
     *  Each entry has { status, lastSyncedAt, error }. */
    getConnections: async () => {
        const response = await api.get('/ca/dashboard/connections');
        return response.data;
    },

    /** Live aggregated dashboard payload.
     *  range: "month" | "quarter" | "year" | "custom"
     *  start/end: ISO dates (YYYY-MM-DD), required when range === "custom".
     *  sources: optional array; omit to include every connected platform. */
    getSummary: async ({ range = 'month', start, end, sources } = {}) => {
        const params = { range };
        if (range === 'custom' && start && end) {
            params.start = start;
            params.end = end;
        }
        if (Array.isArray(sources) && sources.length) {
            params.sources = sources.join(',');
        }
        const response = await api.get('/ca/dashboard/summary', { params });
        return response.data;
    },

    /** Revoke tokens for one platform. */
    disconnect: async (platform) => {
        const response = await api.post(`/ca/dashboard/disconnect/${platform}`);
        return response.data;
    },
};

// ──────────────────────────────────────────────────────────────────────
// Tally Analytics — reads synced Tally data from OUR DB, scoped to
// (current user, selected company). Never live-queries Tally.
// ──────────────────────────────────────────────────────────────────────

export const tallyAnalyticsApi = {
    /** Companies this user has synced (+ per-company sync status). */
    getCompanies: async () => {
        const response = await api.get('/ca/tally/analytics/companies');
        return response.data;
    },

    /** Per-(user, company) sync cursor + honest status. */
    getSyncStatus: async (company) => {
        const response = await api.get('/ca/tally/analytics/sync-status', {
            params: { company },
        });
        return response.data;
    },

    /** Full analytics payload for one company.
     *  range: "month" | "quarter" | "year" | "custom".
     *  For range="custom", pass start/end as "YYYY-MM-DD" to window the period
     *  figures (P&L, breakdowns, voucher activity). */
    getSummary: async ({ company, range = 'month', start, end } = {}) => {
        const params = { company, range };
        if (range === 'custom' && start && end) {
            params.start = start;
            params.end = end;
        }
        const response = await api.get('/ca/tally/analytics/summary', { params });
        return response.data;
    },

    /** Ask the desktop bridge to sync one company.
     *  mode: "incremental" (default) | "full" (reconcile). */
    triggerSync: async ({ company, mode = 'incremental' } = {}) => {
        const response = await api.post('/ca/tally/analytics/sync', null, {
            params: { company, mode },
        });
        return response.data;
    },
};

// ──────────────────────────────────────────────────────────────────────
// RBAC — users, roles, permissions, audit
// ──────────────────────────────────────────────────────────────────────
export const rbacApi = {
    // Identity
    me: async () => (await api.get('/ca/rbac/me')).data,
    overview: async () => (await api.get('/ca/rbac/overview')).data,
    listSettings: async () => (await api.get('/ca/rbac/settings')).data,
    updateSetting: async (key, body) =>
        (await api.put(`/ca/rbac/settings/${encodeURIComponent(key)}`, body)).data,
    listClients: async (params = {}) =>
        (await api.get('/ca/rbac/clients', { params })).data,
    listChats: async (params = {}) =>
        (await api.get('/ca/rbac/chats', { params })).data,
    listFiles: async (params = {}) =>
        (await api.get('/ca/rbac/files', { params })).data,
    listData: async (table, params = {}) =>
        (await api.get(`/ca/rbac/data/${encodeURIComponent(table)}`, { params })).data,

    // Permissions
    listPermissions: async () => (await api.get('/ca/rbac/permissions')).data,
    createPermission: async (body) =>
        (await api.post('/ca/rbac/permissions', body)).data,

    // Roles
    listRoles: async () => (await api.get('/ca/rbac/roles')).data,
    getRole: async (roleId) => (await api.get(`/ca/rbac/roles/${roleId}`)).data,
    createRole: async (body) => (await api.post('/ca/rbac/roles', body)).data,
    updateRole: async (roleId, body) =>
        (await api.patch(`/ca/rbac/roles/${roleId}`, body)).data,
    deleteRole: async (roleId) =>
        (await api.delete(`/ca/rbac/roles/${roleId}`)).data,
    cloneRole: async (roleId, body) =>
        (await api.post(`/ca/rbac/roles/${roleId}/clone`, body)).data,
    setRolePermissions: async (roleId, permissionKeys) =>
        (await api.put(`/ca/rbac/roles/${roleId}/permissions`, {
            permission_keys: permissionKeys,
        })).data,

    // Users
    listUsers: async ({ search, limit = 50, offset = 0 } = {}) => {
        const params = { limit, offset };
        if (search) params.search = search;
        return (await api.get('/ca/rbac/users', { params })).data;
    },
    getUser: async (userId) => (await api.get(`/ca/rbac/users/${userId}`)).data,
    createUser: async (body) => (await api.post('/ca/rbac/users', body)).data,
    updateUser: async (userId, body) =>
        (await api.patch(`/ca/rbac/users/${userId}`, body)).data,
    deactivateUser: async (userId) =>
        (await api.delete(`/ca/rbac/users/${userId}`)).data,
    setUserRoles: async (userId, roleKeys) =>
        (await api.put(`/ca/rbac/users/${userId}/roles`, {
            role_keys: roleKeys,
        })).data,
    resetUserPassword: async (userId, newPassword) =>
        (await api.post(`/ca/rbac/users/${userId}/reset-password`, {
            new_password: newPassword || null,
        })).data,

    // Direct user permissions (grants / denies overriding role-derived perms)
    listUserDirectPermissions: async (userId) =>
        (await api.get(`/ca/rbac/users/${userId}/direct-permissions`)).data,
    setUserDirectPermissions: async (userId, entries) =>
        (await api.put(`/ca/rbac/users/${userId}/direct-permissions`, {
            entries,
        })).data,

    // Audit
    listAudit: async ({
        actor_user_id,
        target_type,
        target_id,
        action,
        start_at,
        end_at,
        limit = 100,
        offset = 0,
    } = {}) => {
        const params = { limit, offset };
        if (actor_user_id) params.actor_user_id = actor_user_id;
        if (target_type) params.target_type = target_type;
        if (target_id) params.target_id = target_id;
        if (action) params.action = action;
        if (start_at) params.start_at = start_at;
        if (end_at) params.end_at = end_at;
        return (await api.get('/ca/rbac/audit', { params })).data;
    },
    listAuditActors: async (limit = 200) =>
        (await api.get('/ca/rbac/audit/actors', { params: { limit } })).data,
    auditExportUrl: ({
        actor_user_id,
        target_type,
        target_id,
        action,
        start_at,
        end_at,
        max_rows = 10000,
    } = {}) => {
        // Build the URL the CSV download anchor points at. We use a direct GET
        // (with the JWT attached) rather than going through the axios layer
        // so the browser handles streaming + Content-Disposition naturally.
        const params = new URLSearchParams({ max_rows: String(max_rows) });
        if (actor_user_id) params.set('actor_user_id', actor_user_id);
        if (target_type) params.set('target_type', target_type);
        if (target_id) params.set('target_id', target_id);
        if (action) params.set('action', action);
        if (start_at) params.set('start_at', start_at);
        if (end_at) params.set('end_at', end_at);
        return `${API_URL}/ca/rbac/audit/export.csv?${params.toString()}`;
    },
    /**
     * Download the audit log as a CSV file. Uses fetch + blob so the JWT
     * auth header travels with the request — we can't rely on a plain
     * <a download> here because the endpoint requires auth.
     */
    downloadAuditCsv: async (filters = {}) => {
        const url = rbacApi.auditExportUrl(filters);
        const token = localStorage.getItem('jwt_token');
        const res = await fetch(url, {
            headers: token ? { Authorization: `Bearer ${token}` } : {},
        });
        if (!res.ok) {
            const text = await res.text().catch(() => '');
            throw new Error(text || `audit export failed (${res.status})`);
        }
        const blob = await res.blob();
        const filename = `audit-log-${new Date().toISOString().slice(0, 10)}.csv`;
        const downloadUrl = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = downloadUrl;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(downloadUrl);
    },
};

export default api;
