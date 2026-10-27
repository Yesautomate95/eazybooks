const WebSocket = require('ws');
const { v4: uuidv4 } = require('uuid');
const EventEmitter = require('events');
const axios = require('axios');
const qbClient = require('./qb_client');

const TALLY_URL = 'http://127.0.0.1:9000';

// Module-level cache for Ledger Vouchers query — Tally takes ~12s per fetch
// for 8k rows, so one BRS session reuses the data across repeat opens.
const LV_CACHE = new Map();              // key: `${company}|${ledger}`  →  { rows, fetchedAt }
const LV_TTL_MS = 15 * 60 * 1000;        // 15 min

// Tally voucher type short-code → canonical name
const VCH_TYPE_MAP = {
    'Pymt': 'Payment',
    'Rcpt': 'Receipt',
    'Ctra': 'Contra',
    'Jrnl': 'Journal',
    'Sale': 'Sales',
    'Purc': 'Purchase',
    'CrNt': 'Credit Note',
    'DrNt': 'Debit Note',
};

// ── Ledger XML body builder ────────────────────────────────────────────
// Mirrors the Python tally_connector helper. Emits only tags whose values
// are non-empty (or, for booleans, non-undefined). Tag set + naming match
// TallyPrime's master import schema (see ShwetaSoftwares/excel-to-tally-
// templates Master-LedgerMaster-xml-tags.xml and help.tallysolutions.com).
//
// Backward compat: keeps legacy LEDSTATENAME / COUNTRYOFRESIDENCE alongside
// canonical STATENAME / COUNTRYNAME.

const GST_REG_TYPES = new Set([
    'Regular', 'Composition', 'Composite Dealer', 'Consumer', 'Unregistered', 'Unknown',
]);

function xmlEscape(value) {
    if (value === undefined || value === null) return '';
    return String(value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&apos;');
}

function hasValue(v) {
    if (v === undefined || v === null) return false;
    if (typeof v === 'string') return v.trim() !== '';
    return true;
}

function yesNo(v) {
    if (v === undefined || v === null) return null;
    return v ? 'Yes' : 'No';
}

function buildLedgerXmlBody(action, data) {
    const out = [];
    const push = (line) => out.push(`                                ${line}`);

    const primaryName = action === 'Alter'
        ? (data.name || data.current_name)
        : data.name;

    push(`<LEDGER NAME="${xmlEscape(primaryName)}" Action="${action}">`);

    if (action === 'Alter' && hasValue(data.new_name) && data.new_name !== primaryName) {
        push(`  <NAME.LIST>`);
        push(`    <NAME>${xmlEscape(data.new_name)}</NAME>`);
        push(`  </NAME.LIST>`);
    } else if (action === 'Create') {
        push(`  <NAME>${xmlEscape(primaryName)}</NAME>`);
    }

    if (hasValue(data.parent_group)) push(`  <PARENT>${xmlEscape(data.parent_group)}</PARENT>`);
    if (hasValue(data.alias))         push(`  <ADDITIONALNAME>${xmlEscape(data.alias)}</ADDITIONALNAME>`);
    if (hasValue(data.opening_balance)) push(`  <OPENINGBALANCE>${xmlEscape(data.opening_balance)}</OPENINGBALANCE>`);
    if (hasValue(data.description))   push(`  <DESCRIPTION>${xmlEscape(data.description)}</DESCRIPTION>`);
    if (hasValue(data.narration))     push(`  <NARRATION>${xmlEscape(data.narration)}</NARRATION>`);

    // Address — multi-line list takes precedence over single-line `address`
    const lines = (Array.isArray(data.address_lines) ? data.address_lines : [])
        .filter((s) => typeof s === 'string' && s.trim() !== '');
    const finalLines = lines.length > 0 ? lines : (hasValue(data.address) ? [data.address] : []);
    if (finalLines.length > 0) {
        push(`  <ADDRESS.LIST>`);
        finalLines.forEach((line) => push(`    <ADDRESS>${xmlEscape(line)}</ADDRESS>`));
        push(`  </ADDRESS.LIST>`);
    }

    if (hasValue(data.pincode)) push(`  <PINCODE>${xmlEscape(data.pincode)}</PINCODE>`);
    if (hasValue(data.state)) {
        push(`  <STATENAME>${xmlEscape(data.state)}</STATENAME>`);
        push(`  <LEDSTATENAME>${xmlEscape(data.state)}</LEDSTATENAME>`);
    }
    if (hasValue(data.country)) {
        push(`  <COUNTRYNAME>${xmlEscape(data.country)}</COUNTRYNAME>`);
        push(`  <COUNTRYOFRESIDENCE>${xmlEscape(data.country)}</COUNTRYOFRESIDENCE>`);
    }

    // Mailing details container — canonical place for printed name + address-on-record
    if (hasValue(data.mailing_name) || finalLines.length > 0) {
        push(`  <LEDMAILINGDETAILS.LIST>`);
        if (hasValue(data.gst_applicable_from)) {
            push(`    <APPLICABLEFROM>${xmlEscape(data.gst_applicable_from)}</APPLICABLEFROM>`);
        }
        if (hasValue(data.mailing_name)) push(`    <MAILINGNAME>${xmlEscape(data.mailing_name)}</MAILINGNAME>`);
        if (finalLines.length > 0) {
            push(`    <ADDRESS.LIST>`);
            finalLines.forEach((line) => push(`      <ADDRESS>${xmlEscape(line)}</ADDRESS>`));
            push(`    </ADDRESS.LIST>`);
        }
        if (hasValue(data.state))   push(`    <STATE>${xmlEscape(data.state)}</STATE>`);
        if (hasValue(data.country)) push(`    <COUNTRY>${xmlEscape(data.country)}</COUNTRY>`);
        if (hasValue(data.pincode)) push(`    <PINCODE>${xmlEscape(data.pincode)}</PINCODE>`);
        push(`  </LEDMAILINGDETAILS.LIST>`);
    }

    if (hasValue(data.contact_person)) push(`  <LEDGERCONTACT>${xmlEscape(data.contact_person)}</LEDGERCONTACT>`);
    if (hasValue(data.phone))          push(`  <LEDGERPHONE>${xmlEscape(data.phone)}</LEDGERPHONE>`);
    if (hasValue(data.mobile))         push(`  <LEDGERMOBILE>${xmlEscape(data.mobile)}</LEDGERMOBILE>`);
    if (hasValue(data.fax))            push(`  <LEDGERFAX>${xmlEscape(data.fax)}</LEDGERFAX>`);
    if (hasValue(data.email))          push(`  <EMAIL>${xmlEscape(data.email)}</EMAIL>`);
    if (hasValue(data.email_cc))       push(`  <EMAILCC>${xmlEscape(data.email_cc)}</EMAILCC>`);

    if (hasValue(data.pan))   push(`  <INCOMETAXNUMBER>${xmlEscape(data.pan)}</INCOMETAXNUMBER>`);
    if (hasValue(data.gstin)) push(`  <PARTYGSTIN>${xmlEscape(data.gstin)}</PARTYGSTIN>`);
    if (hasValue(data.gst_registration_type)) {
        if (!GST_REG_TYPES.has(data.gst_registration_type)) {
            console.warn(`[buildLedgerXmlBody] Unknown GST registration type ${data.gst_registration_type} — sending as-is`);
        }
        push(`  <GSTREGISTRATIONTYPE>${xmlEscape(data.gst_registration_type)}</GSTREGISTRATIONTYPE>`);
    }
    if (hasValue(data.gstin) || hasValue(data.gst_registration_type)) {
        push(`  <LEDGSTREGDETAILS.LIST>`);
        if (hasValue(data.gst_applicable_from)) push(`    <APPLICABLEFROM>${xmlEscape(data.gst_applicable_from)}</APPLICABLEFROM>`);
        if (hasValue(data.gst_registration_type)) push(`    <GSTREGISTRATIONTYPE>${xmlEscape(data.gst_registration_type)}</GSTREGISTRATIONTYPE>`);
        if (hasValue(data.state)) push(`    <STATE>${xmlEscape(data.state)}</STATE>`);
        if (hasValue(data.gstin)) push(`    <GSTIN>${xmlEscape(data.gstin)}</GSTIN>`);
        push(`  </LEDGSTREGDETAILS.LIST>`);
    }

    const bw = yesNo(data.bill_wise);
    if (bw !== null) push(`  <ISBILLWISEON>${bw}</ISBILLWISEON>`);
    if (hasValue(data.bill_credit_period)) push(`  <BILLCREDITPERIOD>${xmlEscape(data.bill_credit_period)}</BILLCREDITPERIOD>`);
    if (hasValue(data.credit_limit))       push(`  <CREDITLIMIT>${xmlEscape(data.credit_limit)}</CREDITLIMIT>`);

    if (hasValue(data.bank_name) || hasValue(data.account_number) || hasValue(data.ifsc_code)) {
        push(`  <PAYMENTDETAILS.LIST>`);
        if (hasValue(data.bank_name))       push(`    <BANKNAME>${xmlEscape(data.bank_name)}</BANKNAME>`);
        if (hasValue(data.account_number))  push(`    <ACCOUNTNUMBER>${xmlEscape(data.account_number)}</ACCOUNTNUMBER>`);
        if (hasValue(data.ifsc_code))       push(`    <IFSCODE>${xmlEscape(data.ifsc_code)}</IFSCODE>`);
        if (hasValue(data.payment_favouring)) push(`    <PAYMENTFAVOURING>${xmlEscape(data.payment_favouring)}</PAYMENTFAVOURING>`);
        push(`  </PAYMENTDETAILS.LIST>`);
    }

    const aff = yesNo(data.affects_stock);
    if (aff !== null) push(`  <AFFECTSSTOCK>${aff}</AFFECTSSTOCK>`);
    const cc = yesNo(data.cost_centres_applicable);
    if (cc !== null) push(`  <ISCOSTCENTRESON>${cc}</ISCOSTCENTRESON>`);

    push(`</LEDGER>`);
    return out.join('\n');
}

// Get WebSocket URL from API URL environment variable
function getWebSocketUrl() {
    const apiUrl = process.env.REACT_APP_API_URL || 'http://127.0.0.1:8000';
    // Convert http(s):// to ws(s)://
    if (apiUrl.startsWith('https://')) {
        return apiUrl.replace('https://', 'wss://');
    } else if (apiUrl.startsWith('http://')) {
        return apiUrl.replace('http://', 'ws://');
    }
    return 'ws://127.0.0.1:8000';
}

class WebSocketService extends EventEmitter {
    constructor(baseUrl = null) {
        super();
        this.baseUrl = baseUrl || getWebSocketUrl();
        this.ws = null;
        this.isConnected = false;
        this.reconnectInterval = 5000;        // base backoff
        this.maxReconnectDelay = 30000;       // backoff cap
        this.maxReconnectAttempts = 50;
        this.reconnectAttempts = 0;
        this.reconnectTimer = null;
        this.intentionalDisconnect = false;
        this.clientId = uuidv4(); // Generate unique client ID
        this.token = null;
        // Injected by electron.js — lets the 'run-tally-sync' bridge command kick
        // off a tally-database-loader run on demand from the web app.
        this.tallyLoader = null;
        this.tallyUrl = TALLY_URL;

        // ── Heartbeat (RC1) ────────────────────────────────────────────
        // We ping the backend every heartbeatInterval and expect a pong before
        // the next beat. A missed pong means the socket is half-open (common
        // behind cloud proxies that silently drop idle connections) — we then
        // force a reconnect instead of sitting on a dead socket forever.
        this.heartbeatInterval = 20000;
        this.heartbeatTimer = null;
        this.pendingPong = false;
        // Last-known Tally reachability, piggy-backed on each heartbeat so the
        // web app can show both legs without a round-trip (RC4). Fed both by the
        // heartbeat's own probe and by electron.js via updateTallyStatus().
        this.tallyStatus = { connected: false, company: null };
        
        this.callbacks = {  // Use callbacks instead of events
            message: null,
            connected: null,
            disconnected: null,
            reconnected: null,
            'reconnect-failed': null,
            error: null
        };

    }

    setTallyUrl(url) {
        if (typeof url === 'string' && url.trim()) this.tallyUrl = url.trim();
    }

    async connect(authData) {
        try {
            if (!authData || !authData.token) {
                throw new Error('No authentication token provided');
            }

            this.intentionalDisconnect = false;
            this.reconnectAttempts = 0;
            this.token = authData.token;
            
            // Get UID from auth data or current user
            if (authData.uid) {
                this.uid = authData.uid;
            } else {
                const user = this.auth.currentUser;
                if (!user) {
                    throw new Error('No authenticated user');
                }
                this.uid = user.uid;
            }

            console.log('Connecting with Firebase UID:', this.uid);

            // Create WebSocket URL with Firebase UID
            const wsUrl = new URL(`${this.baseUrl}/ca/ws/electron/${this.uid}`);
            wsUrl.searchParams.append('token', this.token);

            await this.establishConnection(wsUrl);

        } catch (error) {
            console.error('Connection error:', error);
            if (this.callbacks.error) {
                this.callbacks.error(error);
            }
            throw error;
        }
    }

    async establishConnection(wsUrl) {
        console.log('Attempting WebSocket connection to:', 
            wsUrl.toString().replace(/token=[^&]+/, 'token=***'));
        
        if (this.ws) {
            console.log('Closing existing connection...');
            this.ws.close();
        }

        return new Promise((resolve, reject) => {
            try {
                this.ws = new WebSocket(wsUrl);

                this.ws.on('open', () => {
                    console.log('WebSocket connection established');
                    this.isConnected = true;
                    this.reconnectAttempts = 0;
                    this.pendingPong = false;

                    // Send initial auth message with Firebase UID
                    this.send({
                        type: 'auth',
                        token: this.token,
                        uid: this.uid
                    });

                    // Start heartbeat and fire an immediate resync ping so the
                    // backend + web app reflect current state right away — this
                    // is the resync after any (re)connect (RC5).
                    this._startHeartbeat();
                    this._sendHeartbeat();

                    this.emit('connected');
                    resolve();
                });

                this.ws.on('error', (error) => {
                    console.error('WebSocket error details:', {
                        message: error.message,
                        code: error.code,
                        address: error.address,
                        port: error.port
                    });
                    this.emit('error', error);
                    reject(error);
                });

                this.setupEventListeners();
            } catch (error) {
                console.error('Error creating WebSocket:', error);
                reject(error);
            }
        });
    }

    setupEventListeners() {
        if (!this.ws) return;

        this.ws.on('message', async (data) => {
            try {
                const message = JSON.parse(data);

                if (message.type === "pong") {
                    // Heartbeat ack — socket is alive (RC1).
                    this.pendingPong = false;
                    return;
                }
                else if (message.type === "push-to-tally") {
                    try {
                        console.log('Received:', message.data);
                        console.log('Received company:', message.company);
                        
                        // Make POST request to Tally
                        const response = await axios.post(this.tallyUrl, message.data, {
                            headers: {
                                'Content-Type': 'application/xml'
                            },
                            timeout: 120000 // 2 minutes for large batch imports
                        });

                        // Log Tally response
                        console.log('Tally Response:', response.data);

                        // Parse XML response to check for errors
                        const xml2js = require('xml2js');
                        const parser = new xml2js.Parser({ explicitArray: false });
                        
                        const result = await new Promise((resolve, reject) => {
                            parser.parseString(response.data, (err, result) => {
                                if (err) {
                                    console.error('XML parsing error:', err);
                                    reject(err);
                                } else {
                                    resolve(result);
                                }
                            });
                        });

                        // Extract the required values from the response
                        // Try ENVELOPE format (Tally Prime)
                        const importResult = result?.ENVELOPE?.BODY?.DATA?.IMPORTRESULT || {};
                        // Try flat RESPONSE format (Tally ERP 9 / direct import)
                        const responseFlat = result?.RESPONSE || {};

                        const lastVoucherId = result?.ENVELOPE?.BODY?.DESC?.CMPINFOEX?.IDINFO?.LASTCREATEDVCHID
                            || responseFlat.LASTVCHID || '0';

                        const tallyResponse = {
                            created: parseInt(importResult.CREATED || responseFlat.CREATED || '0'),
                            altered: parseInt(importResult.ALTERED || responseFlat.ALTERED || '0'),
                            deleted: parseInt(importResult.DELETED || responseFlat.DELETED || '0'),
                            exception: parseInt(importResult.EXCEPTIONS || responseFlat.EXCEPTIONS || '0'),
                            lastVoucherNumber: parseInt(lastVoucherId),
                            error: importResult.LINEERROR || responseFlat.LINEERROR || null,
                            raw: response.data
                        };

                        // Send response back through websocket
                        this.send({
                            type: 'push-to-tally-response',
                            request_id: message.request_id,
                            data: tallyResponse
                        });

                        if (this.callbacks.message) {
                            this.callbacks.message(tallyResponse);
                        }

                    } catch (error) {
                        console.error('Error processing message:', error);
                        
                        // Send error response back through websocket
                        this.send({
                            type: 'push-to-tally-response',
                            request_id: message.request_id,
                            error: error.message,
                            data: {
                                created: 0,
                                altered: 0,
                                deleted: 0,
                                exception: 0,
                                lastVoucherNumber: 0,
                                error: error.message
                            }
                        });
                    }
                }
                else if (message.type === "check-connection") {
                    console.log('check-connection request received:', message);
                    const requestId = message.request_id;
                    
                    this.checkTallyConnection()
                        .then(connected => {
                            this.send({
                                type: 'connection-response',
                                request_id: requestId,
                                connected: connected
                            });
                        })
                        .catch(error => {
                            console.error('Error checking Tally connection:', error);
                            this.send({
                                type: 'connection-response',
                                request_id: requestId,
                                error: error.message,
                                connected: false
                            });
                        });
                }
                else if (message.type === "get-companies") {
                    console.log('get-companies request received:', message);
                    const requestId = message.request_id;
                    
                    this.getCompanies()
                        .then(companies => {
                            this.send({
                                type: 'companies-response',
                                request_id: requestId,
                                data: companies
                            });
                        })
                        .catch(error => {
                            console.error('Error fetching companies:', error);
                            this.send({
                                type: 'companies-response',
                                request_id: requestId,
                                error: error.message,
                                data: []  // Empty array if error
                            });
                        });
                }
                else if (message.type === "get-ledgers") {
                    console.log('get-ledgers request received:', message);
                    const requestId = message.request_id; // Get the request ID
                    
                    this.getLedgers(message.company)
                        .then(ledgers => {
                            this.send({
                                type: 'ledgers-response',
                                request_id: requestId, // Include the request ID in response
                                data: ledgers
                            });
                        })
                        .catch(error => {
                            console.error('Error fetching ledgers:', error);
                            this.send({
                                type: 'ledgers-response',
                                request_id: requestId, // Include the request ID in response
                                error: error.message,
                                data: this.getDefaultLedgers()
                            });
                        });
                }
                else if (message.type === "get-grouped-ledgers") {
                    console.log('get-grouped-ledgers request received:', message);
                    const requestId = message.request_id;

                    this.getGroupedLedgers(message.company)
                        .then(grouped => {
                            this.send({
                                type: 'grouped-ledgers-response',
                                request_id: requestId,
                                data: grouped
                            });
                        })
                        .catch(error => {
                            console.error('Error fetching grouped ledgers:', error);
                            this.send({
                                type: 'grouped-ledgers-response',
                                request_id: requestId,
                                error: error.message,
                                data: { party: {}, purchase: {}, sales: {} }
                            });
                        });
                }
                else if (message.type === "get-inventory") {
                    console.log('get-inventory request received:', message);
                    const requestId = message.request_id;
                    
                    this.getInventory(message.company)
                        .then(inventory => {
                            this.send({
                                type: 'inventory-response',
                                request_id: requestId,
                                data: inventory
                            });
                        })
                        .catch(error => {
                            console.error('Error fetching inventory:', error);
                            this.send({
                                type: 'inventory-response',
                                request_id: requestId,
                                error: error.message,
                                data: []  // Empty array if error
                            });
                        });
                }
                else if (message.type === "get-godowns") {
                    console.log('get-godowns request received:', message);
                    const requestId = message.request_id;
                    
                    this.getGodowns(message.company)
                        .then(godowns => {
                            this.send({
                                type: 'godowns-response',
                                request_id: requestId,
                                data: godowns
                            });
                        })
                        .catch(error => {
                            console.error('Error fetching godowns:', error);
                            this.send({
                                type: 'godowns-response',
                                request_id: requestId,
                                error: error.message,
                                data: []  // Empty array if error
                            });
                        });
                }
                else if (message.type === "get-groups") {
                    console.log('get-groups request received:', message);
                    const requestId = message.request_id;
                    
                    // First check if Tally is connected
                    this.checkTallyConnection()
                        .then(connected => {
                            if (!connected) {
                                throw new Error('Tally is not connected');
                            }
                            return this.getGroups();
                        })
                        .then(groups => {
                            console.log('Successfully fetched groups:', groups);
                            this.send({
                                type: 'groups-response',
                                request_id: requestId,
                                data: groups
                            });
                            console.log('Sent groups response:', {
                                type: 'groups-response',
                                request_id: requestId,
                                dataLength: groups.length
                            });
                        })
                        .catch(error => {
                            console.error('Error in get-groups flow:', error);
                            this.send({
                                type: 'groups-response',
                                request_id: requestId,
                                error: error.message,
                                data: []  // Empty array if error
                            });
                            console.log('Sent error response for groups');
                        });
                }
                else if (message.type === "create-ledger") {
                    console.log('create-ledger request received:', message);
                    const requestId = message.request_id;
                    
                    this.createLedger(message.data)
                        .then(() => {
                            this.send({
                                type: 'create-ledger-response',
                                request_id: requestId,
                                success: true
                            });
                        })
                        .catch(error => {
                            console.error('Error creating ledger:', error);
                            this.send({
                                type: 'create-ledger-response',
                                request_id: requestId,
                                error: error.message,
                                success: false
                            });
                        });
                }
                else if (message.type === "create-group") {
                    console.log('create-group request received:', message);
                    const requestId = message.request_id;
                    
                    this.createGroup(message.data)
                        .then(() => {
                            this.send({
                                type: 'create-group-response',
                                request_id: requestId,
                                success: true
                            });
                        })
                        .catch(error => {
                            console.error('Error creating group:', error);
                            this.send({
                                type: 'create-group-response',
                                request_id: requestId,
                                error: error.message,
                                success: false
                            });
                        });
                }
                else if (message.type === "get-ledger-details") {
                    console.log('get-ledger-details request received:', message);
                    const requestId = message.request_id;
                    const { ledger_name, company } = message.data;

                    if (!ledger_name || !company) {
                        this.send({
                            type: 'get-ledger-details-response',
                            request_id: requestId,
                            error: 'Ledger name and company are required',
                            data: null
                        });
                        return;
                    }

                    try {
                        const ledgerDetails = await this.getLedgerDetails(ledger_name, company);
                        this.send({
                            type: 'get-ledger-details-response',
                            request_id: requestId,
                            data: ledgerDetails
                        });
                    } catch (error) {
                        console.error('Error fetching ledger details:', error);
                        this.send({
                            type: 'get-ledger-details-response',
                            request_id: requestId,
                            error: error.message,
                            data: null
                        });
                    }
                }
                else if (message.type === "get-ledger-transactions") {
                    console.log('get-ledger-transactions request received:', message);
                    const requestId = message.request_id;
                    const { company, ledger_name, from_date, to_date } = message;

                    this.getLedgerTransactions(company, ledger_name, from_date, to_date)
                        .then(data => {
                            this.send({
                                type: 'ledger-transactions-response',
                                request_id: requestId,
                                data
                            });
                        })
                        .catch(error => {
                            console.error('Error fetching ledger transactions:', error);
                            this.send({
                                type: 'ledger-transactions-response',
                                request_id: requestId,
                                error: error.message,
                                data: { opening_balance: null, closing_balance: null, transactions: [] }
                            });
                        });
                }
                else if (message.type === "edit-ledger") {
                    console.log('edit-ledger request received:', message);
                    const requestId = message.request_id;
                    const ledgerData = message.data;

                    this.editLedger(ledgerData)
                        .then(result => {
                            this.send({
                                type: 'edit-ledger-response',
                                request_id: requestId,
                                data: result
                            });
                        })
                        .catch(error => {
                            console.error('Error editing ledger:', error);
                            this.send({
                                type: 'edit-ledger-response',
                                request_id: requestId,
                                error: error.message
                            });
                        });
                }
                else if (message.type === "run-tally-sync") {
                    // The web app asked us to sync this company's Tally data into the
                    // shared DB. Ack "started" IMMEDIATELY (the run can take minutes);
                    // the loader updates tally_sync_state, which the web app polls.
                    const company = message.company;
                    const mode = message.mode === 'full' ? 'full' : 'incremental';
                    if (!this.tallyLoader) {
                        this.send({ type: 'run-tally-sync-response', request_id: message.request_id,
                            error: 'Tally loader not available on this desktop client' });
                    } else if (!company) {
                        this.send({ type: 'run-tally-sync-response', request_id: message.request_id,
                            error: 'company is required' });
                    } else if (this.tallyLoader.isRunning) {
                        // A sync is already in flight — that's fine, report it as started.
                        this.send({ type: 'run-tally-sync-response', request_id: message.request_id,
                            data: { started: true, alreadyRunning: true, company, mode } });
                    } else {
                        this.send({ type: 'run-tally-sync-response', request_id: message.request_id,
                            data: { started: true, company, mode } });
                        if (mode === 'full') {
                            // One-off reconcile (full re-pull + tenant-scoped delete).
                            this.tallyLoader.startTallyExport({ company, mode: 'full' })
                                .then((r) => console.log('run-tally-sync (full) finished:', r?.success, company))
                                .catch((e) => console.error('run-tally-sync (full) error:', e?.error || e?.message || e));
                        } else {
                            // Incremental: run now AND arm the background scheduler so
                            // subsequent syncs happen automatically on an interval.
                            this.tallyLoader.startBackgroundSync(company, 5);
                        }
                    }
                }
                else if (typeof message.type === 'string' && message.type.startsWith('qb-')) {
                    // QuickBooks Desktop bridge. All `qb-*` messages route through
                    // qb_client. Each handler sends `<msg.type>-response` back.
                    const requestId = message.request_id;
                    const respond = (data, error) => {
                        const out = { type: `${message.type}-response`, request_id: requestId };
                        if (error) out.error = error.message || String(error);
                        if (data !== undefined) out.data = data;
                        this.send(out);
                    };

                    const dispatch = async () => {
                        switch (message.type) {
                            case 'qb-check-connection':
                                return { connected: await qbClient.checkConnection() };
                            case 'qb-get-companies':
                                return await qbClient.getCompanies();
                            case 'qb-get-accounts':
                                return await qbClient.getAccounts(message.company);
                            case 'qb-get-customers':
                                return await qbClient.getCustomers(message.company);
                            case 'qb-get-vendors':
                                return await qbClient.getVendors(message.company);
                            case 'qb-get-items':
                                return await qbClient.getItems(message.company);
                            case 'qb-create-customer':
                                return await qbClient.createCustomer(message.data || {});
                            case 'qb-create-vendor':
                                return await qbClient.createVendor(message.data || {});
                            case 'qb-create-account':
                                return await qbClient.createAccount(message.data || {});
                            case 'qb-push-invoice':
                                return await qbClient.pushInvoice(message.qbxml || '');
                            case 'qb-push-bill':
                                return await qbClient.pushBill(message.qbxml || '');
                            default:
                                throw new Error(`Unknown QB message type: ${message.type}`);
                        }
                    };

                    dispatch()
                        .then((data) => respond(data))
                        .catch((err) => {
                            console.error(`[qb] ${message.type} failed:`, err.message);
                            respond(null, err);
                        });
                }
            } catch (error) {
                console.error('Error processing message:', error);
            }
        });

        this.ws.on('close', (code, reasonBuf) => {
            const reason = reasonBuf ? reasonBuf.toString() : '';
            console.log(`WebSocket connection closed (code=${code} reason="${reason}")`);
            this.isConnected = false;
            this._stopHeartbeat();
            this.emit('disconnected', { code, reason });

            // 4003 = auth rejected by the backend (token expired/invalid).
            // Blindly reconnecting just hammers the server with the same dead
            // token, so surface it for a token refresh instead (RC5).
            if (code === 4003) {
                console.warn('WS closed with 4003 (auth) — requesting token refresh, not blind reconnect');
                this.emit('auth-failed', { code, reason });
                return;
            }

            this.attemptReconnect();
        });
    }

    // ── Heartbeat helpers (RC1) ────────────────────────────────────────

    _startHeartbeat() {
        this._stopHeartbeat();
        this.pendingPong = false;
        this.heartbeatTimer = setInterval(() => this._sendHeartbeat(), this.heartbeatInterval);
    }

    _stopHeartbeat() {
        if (this.heartbeatTimer) {
            clearInterval(this.heartbeatTimer);
            this.heartbeatTimer = null;
        }
        this.pendingPong = false;
    }

    async _sendHeartbeat() {
        if (!this.ws || !this.isConnected) return;

        // If we never got a pong for the previous beat, the socket is half-open
        // — terminate it so the close handler triggers a reconnect.
        if (this.pendingPong) {
            console.warn('No pong since last heartbeat — socket is half-open, forcing reconnect');
            this.pendingPong = false;
            try {
                if (typeof this.ws.terminate === 'function') this.ws.terminate();
                else this.ws.close();
            } catch (_) { /* ignore */ }
            return;
        }

        // The Tally leg is piggy-backed from electron.js's existing 30s poll
        // (via updateTallyStatus) — we deliberately do NOT probe Tally here, to
        // avoid adding serial load on Tally during long imports. Flips are
        // pushed immediately by updateTallyStatus, so this stays accurate.
        this.pendingPong = true;
        this.send({
            type: 'ping',
            ts: Date.now(),
            tally_connected: !!this.tallyStatus.connected,
            company: this.tallyStatus.company || null,
        });
    }

    /**
     * Push an out-of-band Tally status update (e.g. when electron.js's own 30s
     * poll flips) so the web app updates immediately instead of waiting for the
     * next heartbeat.
     */
    updateTallyStatus(connected, company = null) {
        const changed = this.tallyStatus.connected !== !!connected;
        this.tallyStatus = {
            connected: !!connected,
            company: company || this.tallyStatus.company || null,
        };
        if (changed && this.ws && this.isConnected) {
            this.pendingPong = true;
            this.send({
                type: 'ping',
                ts: Date.now(),
                tally_connected: !!connected,
                company: this.tallyStatus.company,
            });
        }
    }

    on(eventName, callback) {
        if (eventName in this.callbacks) {
            this.callbacks[eventName] = callback;
        }
        // Always register with EventEmitter so emit() works
        super.on(eventName, callback);
    }

    setTallyLoader(loader) {
        this.tallyLoader = loader;
    }

    send(message) {
        if (this.ws && this.isConnected) {
            const stringMessage = JSON.stringify(message);
            console.log('Sending:', message);
            this.ws.send(stringMessage);
        } else {
            console.error('Cannot send message: WebSocket is not connected');
        }
    }

    disconnect() {
        this.intentionalDisconnect = true;
        this._stopHeartbeat();
        if (this.reconnectTimer) {
            clearTimeout(this.reconnectTimer);
            this.reconnectTimer = null;
        }
        if (this.ws) {
            // Clean, intentional close (1000) so the backend clears status
            // promptly rather than waiting for the reaper (RC5).
            try { this.ws.close(1000, 'client disconnect'); } catch (_) { /* ignore */ }
            this.ws = null;
            this.isConnected = false;
        }
    }

    attemptReconnect() {
        // Don't reconnect if intentionally disconnected or no credentials
        if (this.intentionalDisconnect || !this.token || !this.uid) {
            return;
        }

        if (this.reconnectAttempts >= this.maxReconnectAttempts) {
            console.log(`Max reconnect attempts (${this.maxReconnectAttempts}) reached. Giving up.`);
            this.emit('reconnect-failed');
            return;
        }

        this.reconnectAttempts++;
        // True exponential backoff with a cap, plus jitter to avoid a
        // thundering-herd reconnect when many clients drop at once (RC5).
        const expo = Math.min(
            this.reconnectInterval * 2 ** (this.reconnectAttempts - 1),
            this.maxReconnectDelay
        );
        const jitter = Math.floor(Math.random() * 1000);
        const delay = expo + jitter;
        console.log(`Reconnecting in ${(delay / 1000).toFixed(1)}s (attempt ${this.reconnectAttempts}/${this.maxReconnectAttempts})...`);

        this.reconnectTimer = setTimeout(async () => {
            try {
                const wsUrl = new URL(`${this.baseUrl}/ca/ws/electron/${this.uid}`);
                wsUrl.searchParams.append('token', this.token);
                await this.establishConnection(wsUrl);
                console.log('Reconnected successfully');
                this.emit('reconnected');
            } catch (error) {
                console.error('Reconnect attempt failed:', error.message);
                // The close handler will trigger another attempt
            }
        }, delay);
    }

    getDefaultLedgers() {
        return [
            {
                "name": "Cash",
                "parent_group": "Cash in Hand",
                "opening_balance": "0"
            },
            {
                "name": "Sales",
                "parent_group": "Sales Accounts",
                "opening_balance": "0"
            },
            {
                "name": "Purchase",
                "parent_group": "Purchase Accounts",
                "opening_balance": "0"
            }
        ];
    }

    async getInventory(company = null) {
        try {
            // If no company is provided, get the first available company
            if (!company) {
                const companies = await this.getCompanies();
                if (companies && companies.length > 0) {
                    company = companies[0].name;
                } else {
                    throw new Error('No companies found in Tally');
                }
            }

            const xmlQuery = `<?xml version="1.0" ?>
            <ENVELOPE>
            <HEADER>
                <VERSION>1</VERSION>
                <TALLYREQUEST>Export</TALLYREQUEST>
                <TYPE>Collection</TYPE>
                <ID>AllStockItems</ID>
            </HEADER>
            <BODY>
                <DESC>
                <STATICVARIABLES>
                    <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
                    <SVCURRENTCOMPANY>${this.cleanString(company)}</SVCURRENTCOMPANY>
                </STATICVARIABLES>
                <TDL>
                    <TDLMESSAGE>
                    <COLLECTION NAME="AllStockItems">
                        <TYPE>Stock Item</TYPE>
                        <FETCH>NAME,PARENT,CATEGORY,BASEUNITS,OPENINGBALANCE,OPENINGVALUE,CLOSINGBALANCE,CLOSINGVALUE</FETCH>
                    </COLLECTION>
                    </TDLMESSAGE>
                </TDL>
                </DESC>
            </BODY>
            </ENVELOPE>`;

            // Make POST request to Tally
            const response = await axios.post(TALLY_URL, xmlQuery, {
                headers: {
                    'Content-Type': 'text/xml'
                },
                timeout: 30000 // 30 seconds timeout
            });

            // Parse XML response
            const xml2js = require('xml2js');
            const parser = new xml2js.Parser({ explicitArray: false });
            
            const result = await new Promise((resolve, reject) => {
                parser.parseString(response.data, (err, result) => {
                    if (err) reject(err);
                    else resolve(result);
                });
            });

            // Extract inventory items from the response
            const inventory = [];
            if (result?.ENVELOPE?.BODY?.DATA?.COLLECTION?.STOCKITEM) {
                const stockItems = Array.isArray(result.ENVELOPE.BODY.DATA.COLLECTION.STOCKITEM) 
                    ? result.ENVELOPE.BODY.DATA.COLLECTION.STOCKITEM 
                    : [result.ENVELOPE.BODY.DATA.COLLECTION.STOCKITEM];

                for (const item of stockItems) {
                    // Name from attribute
                    const name = this.cleanString(item.$?.NAME || '');

                    // Parent, category, baseUnits from child tags
                    const parent = this.cleanString(item.PARENT?._ || item.PARENT || '');
                    const category = this.cleanString(item.CATEGORY?._ || item.CATEGORY || '');
                    const baseUnits = this.cleanString(item.BASEUNITS?._ || item.BASEUNITS || '');

                    // Quantities
                    const closingBalance = this.extractQuantity(item.CLOSINGBALANCE?._ || item.CLOSINGBALANCE || '');
                    const openingBalance = this.extractQuantity(item.OPENINGBALANCE?._ || item.OPENINGBALANCE || '');

                    // Values
                    const openingValue = parseFloat(this.cleanString(item.OPENINGVALUE?._ || item.OPENINGVALUE || '')) || 0;
                    const closingValue = parseFloat(this.cleanString(item.CLOSINGVALUE?._ || item.CLOSINGVALUE || '')) || 0;

                    inventory.push({
                        name,
                        parent,
                        category,
                        baseUnits,
                        openingBalance,
                        openingValue,
                        closingBalance,
                        closingValue,
                        quantity: closingBalance // This is your main quantity
                    });
                }
            }

            return inventory;

        } catch (error) {
            console.error('Error fetching inventory from Tally:', error);
            if (error.response) {
                console.error('Tally response error:', error.response.data);
            }
            throw error;
        }
    }

    async getLedgers(company = null) {
        try {
            if (!company) {
                const companies = await this.getCompanies();
                if (companies && companies.length > 0) {
                    company = companies[0].name;
                } else {
                    throw new Error('No companies found in Tally');
                }
            }

            const xmlQuery = `<?xml version="1.0" ?>
            <ENVELOPE>
                <HEADER>
                    <VERSION>1</VERSION>
                    <TALLYREQUEST>Export</TALLYREQUEST>
                    <TYPE>Collection</TYPE>
                    <ID>AllLedgers</ID>
                </HEADER>
                <BODY>
                    <DESC>
                        <STATICVARIABLES>
                            <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
                            <SVCURRENTCOMPANY>${this.cleanString(company)}</SVCURRENTCOMPANY>
                        </STATICVARIABLES>
                        <TDL>
                            <TDLMESSAGE>
                                <COLLECTION NAME="AllLedgers">
                                    <TYPE>Ledger</TYPE>
                                    <FETCH>NAME,PARENT,OPENINGBALANCE</FETCH>
                                </COLLECTION>
                            </TDLMESSAGE>
                        </TDL>
                    </DESC>
                </BODY>
            </ENVELOPE>`;

            const response = await axios.post(TALLY_URL, xmlQuery, {
                headers: { 'Content-Type': 'text/xml' },
                timeout: 30000
            });

            const xml2js = require('xml2js');
            const parser = new xml2js.Parser({ explicitArray: false });
            const result = await new Promise((resolve, reject) => {
                parser.parseString(response.data, (err, res) => {
                    if (err) reject(err);
                    else resolve(res);
                });
            });

            const ledgers = [];
            if (result?.ENVELOPE?.BODY?.DATA?.COLLECTION?.LEDGER) {
                const items = Array.isArray(result.ENVELOPE.BODY.DATA.COLLECTION.LEDGER)
                    ? result.ENVELOPE.BODY.DATA.COLLECTION.LEDGER
                    : [result.ENVELOPE.BODY.DATA.COLLECTION.LEDGER];
                for (const item of items) {
                    const name = this.cleanString(item.$?.NAME || item.NAME || '');
                    const parent = this.cleanString(item.PARENT?._ || item.PARENT || 'Primary');
                    const balance = this.cleanString(item.OPENINGBALANCE?._ || item.OPENINGBALANCE || '0');
                    if (name) {
                        ledgers.push({ name, parent_group: parent, opening_balance: balance });
                    }
                }
            }

            // Add default ledgers if missing
            const existingNames = new Set(ledgers.map(l => l.name));
            for (const dl of this.getDefaultLedgers()) {
                if (!existingNames.has(dl.name)) ledgers.push(dl);
            }

            return ledgers;

        } catch (error) {
            console.error('Error fetching ledgers from Tally:', error.message);
            return this.getDefaultLedgers();
        }
    }

    // ── Grouped Ledger Fetching ──────────────────────────────────

    /**
     * Fetch all ledgers under a specific Tally parent group recursively.
     *
     * Uses `<ADD>CHILD OF : GroupName</ADD>` with `ISMODIFY="Yes"` which
     * returns only ledger objects from the entire sub-tree — Tally handles
     * the recursion, so no manual tree-walking is needed.
     *
     * @param {string} groupName - Exact Tally group name (case-sensitive)
     * @param {string|null} company - Company name (auto-detected if null)
     * @returns {Promise<Array<{name: string, parent: string, guid: string}>>}
     */
    async getLedgersByGroup(groupName, company = null) {
        try {
            if (!company) {
                const companies = await this.getCompanies();
                if (companies && companies.length > 0) {
                    company = companies[0].name;
                } else {
                    throw new Error('No companies found in Tally');
                }
            }

            const xmlQuery = `<?xml version="1.0" ?>
            <ENVELOPE>
                <HEADER>
                    <VERSION>1</VERSION>
                    <TALLYREQUEST>EXPORT</TALLYREQUEST>
                    <TYPE>COLLECTION</TYPE>
                    <ID>LedgersUnderGroup</ID>
                </HEADER>
                <BODY>
                    <DESC>
                        <STATICVARIABLES>
                            <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
                            <SVCURRENTCOMPANY>${this.cleanString(company)}</SVCURRENTCOMPANY>
                        </STATICVARIABLES>
                        <TDL>
                            <TDLMESSAGE>
                                <COLLECTION NAME="LedgersUnderGroup">
                                    <TYPE>Ledger</TYPE>
                                    <CHILDOF>${this.cleanString(groupName)}</CHILDOF>
                                    <BELONGSTO>Yes</BELONGSTO>
                                    <NATIVEMETHOD>Name</NATIVEMETHOD>
                                    <NATIVEMETHOD>Parent</NATIVEMETHOD>
                                    <NATIVEMETHOD>GUID</NATIVEMETHOD>
                                    <NATIVEMETHOD>ClosingBalance</NATIVEMETHOD>
                                    <NATIVEMETHOD>PartyGSTIN</NATIVEMETHOD>
                                </COLLECTION>
                            </TDLMESSAGE>
                        </TDL>
                    </DESC>
                </BODY>
            </ENVELOPE>`;

            const response = await axios.post(TALLY_URL, xmlQuery, {
                headers: { 'Content-Type': 'text/xml' },
                timeout: 30000
            });

            const xml2js = require('xml2js');
            const parser = new xml2js.Parser({ explicitArray: false });
            const result = await new Promise((resolve, reject) => {
                parser.parseString(response.data, (err, res) => {
                    if (err) reject(err);
                    else resolve(res);
                });
            });

            const ledgers = [];
            if (result?.ENVELOPE?.BODY?.DATA?.COLLECTION?.LEDGER) {
                const items = Array.isArray(result.ENVELOPE.BODY.DATA.COLLECTION.LEDGER)
                    ? result.ENVELOPE.BODY.DATA.COLLECTION.LEDGER
                    : [result.ENVELOPE.BODY.DATA.COLLECTION.LEDGER];
                for (const item of items) {
                    const name = this.cleanString(item.$?.NAME || item.NAME || '');
                    const parent = this.cleanString(item.PARENT?._ || item.PARENT || '');
                    const guid = this.cleanString(item.GUID?._ || item.GUID || '');
                    const closingBalance = this.cleanString(item.CLOSINGBALANCE?._ || item.CLOSINGBALANCE || '0');
                    const gstin = this.cleanString(item.PARTYGSTIN?._ || item.PARTYGSTIN || '');
                    if (name) {
                        ledgers.push({ name, parent, guid, closing_balance: closingBalance, gstin });
                    }
                }
            }

            console.log(`Fetched ${ledgers.length} ledgers from group "${groupName}"`);
            return ledgers;

        } catch (error) {
            console.error(`Error fetching ledgers for group "${groupName}":`, error.message);
            return []; // One group failing should not break all
        }
    }

    /**
     * Fetch ledgers grouped into Party, Purchase, and Sales categories.
     *
     * Fires all group requests in parallel using Promise.allSettled so that
     * one group failing does not break the others.
     *
     * @param {string|null} company - Company name (auto-detected if null)
     * @returns {Promise<{party: object, purchase: object, sales: object}>}
     */
    async getGroupedLedgers(company = null) {
        if (!company) {
            const companies = await this.getCompanies();
            if (companies && companies.length > 0) {
                company = companies[0].name;
            } else {
                throw new Error('No companies found in Tally');
            }
        }

        const PARTY_GROUPS = ['Sundry Debtors', 'Sundry Creditors'];
        const PURCHASE_GROUPS = ['Direct Expenses', 'Indirect Expenses', 'Purchase Accounts'];
        const SALES_GROUPS = ['Direct Incomes', 'Indirect Incomes', 'Sales Accounts'];

        const allGroups = [...PARTY_GROUPS, ...PURCHASE_GROUPS, ...SALES_GROUPS];

        // Fire all requests in parallel — allSettled so one failure doesn't break all
        const results = await Promise.allSettled(
            allGroups.map(g => this.getLedgersByGroup(g, company))
        );

        // Map results back to group names
        const groupData = {};
        allGroups.forEach((groupName, i) => {
            groupData[groupName] = results[i].status === 'fulfilled'
                ? results[i].value
                : [];
        });

        // Build categorized response
        const buildCategory = (groups) => {
            const cat = {};
            for (const g of groups) {
                cat[g] = groupData[g] || [];
            }
            return cat;
        };

        const grouped = {
            party: buildCategory(PARTY_GROUPS),
            purchase: buildCategory(PURCHASE_GROUPS),
            sales: buildCategory(SALES_GROUPS),
        };

        console.log('Grouped ledger fetch complete:', {
            party: Object.entries(grouped.party).map(([k, v]) => `${k}: ${v.length}`).join(', '),
            purchase: Object.entries(grouped.purchase).map(([k, v]) => `${k}: ${v.length}`).join(', '),
            sales: Object.entries(grouped.sales).map(([k, v]) => `${k}: ${v.length}`).join(', '),
        });

        return grouped;
    }

    async getCompanies() {
        try {
            const xmlQuery = `<?xml version="1.0" ?>
            <ENVELOPE>
                <HEADER>
                    <VERSION>1</VERSION>
                    <TALLYREQUEST>Export</TALLYREQUEST>
                    <TYPE>Collection</TYPE>
                    <ID>CompanyList</ID>
                </HEADER>
                <BODY>
                    <DESC>
                        <STATICVARIABLES>
                            <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
                        </STATICVARIABLES>
                        <TDL>
                            <TDLMESSAGE>
                                <COLLECTION NAME="CompanyList">
                                    <TYPE>Company</TYPE>
                                    <FETCH>NAME</FETCH>
                                </COLLECTION>
                            </TDLMESSAGE>
                        </TDL>
                    </DESC>
                </BODY>
            </ENVELOPE>`;

            const response = await axios.post(TALLY_URL, xmlQuery, {
                headers: { 'Content-Type': 'text/xml' },
                timeout: 30000
            });

            const xml2js = require('xml2js');
            const parser = new xml2js.Parser({ explicitArray: false });
            const result = await new Promise((resolve, reject) => {
                parser.parseString(response.data, (err, res) => {
                    if (err) reject(err);
                    else resolve(res);
                });
            });

            const companies = [];
            if (result?.ENVELOPE?.BODY?.DATA?.COLLECTION?.COMPANY) {
                const items = Array.isArray(result.ENVELOPE.BODY.DATA.COLLECTION.COMPANY)
                    ? result.ENVELOPE.BODY.DATA.COLLECTION.COMPANY
                    : [result.ENVELOPE.BODY.DATA.COLLECTION.COMPANY];
                for (const item of items) {
                    const name = this.cleanString(item.$?.NAME || item.NAME || '');
                    if (name) companies.push({ name });
                }
            }

            console.log('Companies from Tally:', companies);
            return companies;

        } catch (error) {
            console.error('Error fetching companies from Tally:', error.message);
            throw error;
        }
    }

    async checkTallyConnection() {
        try {
            // Try to connect to Tally using HTTP XML API
            const xmlQuery = `<?xml version="1.0" ?>
            <ENVELOPE>
                <HEADER>
                    <VERSION>1</VERSION>
                    <TALLYREQUEST>Export</TALLYREQUEST>
                    <TYPE>Collection</TYPE>
                    <ID>CompanyList</ID>
                </HEADER>
                <BODY>
                    <DESC>
                        <STATICVARIABLES>
                            <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
                        </STATICVARIABLES>
                        <TDL>
                            <TDLMESSAGE>
                                <COLLECTION NAME="CompanyList">
                                    <TYPE>Company</TYPE>
                                    <FETCH>NAME</FETCH>
                                </COLLECTION>
                            </TDLMESSAGE>
                        </TDL>
                    </DESC>
                </BODY>
            </ENVELOPE>`;

            await axios.post(TALLY_URL, xmlQuery, {
                headers: { 'Content-Type': 'text/xml' },
                timeout: 5000
            });
            return true;
        } catch (error) {
            console.error('Tally connection check failed:', error.message);
            return false;
        }
    }

    async getGodowns(company = null) {
        try {
            // If no company is provided, get the first available company
            if (!company) {
                const companies = await this.getCompanies();
                if (companies && companies.length > 0) {
                    company = companies[0].name;
                } else {
                    throw new Error('No companies found in Tally');
                }
            }

            const xmlQuery = `<?xml version="1.0" ?>
            <ENVELOPE>
            <HEADER>
                <VERSION>1</VERSION>
                <TALLYREQUEST>Export</TALLYREQUEST>
                <TYPE>Collection</TYPE>
                <ID>AllGodowns</ID>
            </HEADER>
            <BODY>
                <DESC>
                <STATICVARIABLES>
                    <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
                    <SVCURRENTCOMPANY>${this.cleanString(company)}</SVCURRENTCOMPANY>
                </STATICVARIABLES>
                <TDL>
                    <TDLMESSAGE>
                    <COLLECTION NAME="AllGodowns">
                        <TYPE>Godown</TYPE>
                        <FETCH>NAME,PARENT,ADDRESS,PINCODE</FETCH>
                    </COLLECTION>
                    </TDLMESSAGE>
                </TDL>
                </DESC>
            </BODY>
            </ENVELOPE>`;

            // Make POST request to Tally
            const response = await axios.post(TALLY_URL, xmlQuery, {
                headers: {
                    'Content-Type': 'text/xml'
                },
                timeout: 30000 // 30 seconds timeout
            });

            // Parse XML response
            const xml2js = require('xml2js');
            const parser = new xml2js.Parser({ explicitArray: false });
            
            const result = await new Promise((resolve, reject) => {
                parser.parseString(response.data, (err, result) => {
                    if (err) reject(err);
                    else resolve(result);
                });
            });

            // Extract godown locations from the response
            const godowns = [];
            if (result?.ENVELOPE?.BODY?.DATA?.COLLECTION?.GODOWN) {
                const godownItems = Array.isArray(result.ENVELOPE.BODY.DATA.COLLECTION.GODOWN) 
                    ? result.ENVELOPE.BODY.DATA.COLLECTION.GODOWN 
                    : [result.ENVELOPE.BODY.DATA.COLLECTION.GODOWN];

                for (const item of godownItems) {
                    // Name from attribute
                    const name = this.cleanString(item.$?.NAME || '');

                    // Parent and other details from child tags
                    const parent = this.cleanString(item.PARENT?._ || item.PARENT || '');
                    const address = this.cleanString(item.ADDRESS?._ || item.ADDRESS || '');
                    const pincode = this.cleanString(item.PINCODE?._ || item.PINCODE || '');

                    godowns.push({
                        name,
                        parent,
                        address,
                        pincode
                    });
                }
            }

            return godowns;

        } catch (error) {
            console.error('Error fetching godowns from Tally:', error);
            if (error.response) {
                console.error('Tally response error:', error.response.data);
            }
            throw error;
        }
    }

    async getGroups(company = null) {
        try {
            if (!company) {
                const companies = await this.getCompanies();
                if (companies && companies.length > 0) {
                    company = companies[0].name;
                } else {
                    throw new Error('No companies found in Tally');
                }
            }

            const xmlQuery = `<?xml version="1.0" ?>
            <ENVELOPE>
                <HEADER>
                    <VERSION>1</VERSION>
                    <TALLYREQUEST>Export</TALLYREQUEST>
                    <TYPE>Collection</TYPE>
                    <ID>AllGroups</ID>
                </HEADER>
                <BODY>
                    <DESC>
                        <STATICVARIABLES>
                            <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
                            <SVCURRENTCOMPANY>${this.cleanString(company)}</SVCURRENTCOMPANY>
                        </STATICVARIABLES>
                        <TDL>
                            <TDLMESSAGE>
                                <COLLECTION NAME="AllGroups">
                                    <TYPE>Group</TYPE>
                                    <FETCH>NAME,PARENT,ISPRIMARYGROUP</FETCH>
                                </COLLECTION>
                            </TDLMESSAGE>
                        </TDL>
                    </DESC>
                </BODY>
            </ENVELOPE>`;

            const response = await axios.post(TALLY_URL, xmlQuery, {
                headers: { 'Content-Type': 'text/xml' },
                timeout: 30000
            });

            const xml2js = require('xml2js');
            const parser = new xml2js.Parser({ explicitArray: false });
            const result = await new Promise((resolve, reject) => {
                parser.parseString(response.data, (err, res) => {
                    if (err) reject(err);
                    else resolve(res);
                });
            });

            const groups = [];
            if (result?.ENVELOPE?.BODY?.DATA?.COLLECTION?.GROUP) {
                const items = Array.isArray(result.ENVELOPE.BODY.DATA.COLLECTION.GROUP)
                    ? result.ENVELOPE.BODY.DATA.COLLECTION.GROUP
                    : [result.ENVELOPE.BODY.DATA.COLLECTION.GROUP];
                for (const item of items) {
                    const name = this.cleanString(item.$?.NAME || item.NAME || '');
                    const parent = this.cleanString(item.PARENT?._ || item.PARENT || 'Primary');
                    const isPrimary = (item.ISPRIMARYGROUP?._ || item.ISPRIMARYGROUP || '') === 'Yes';
                    if (name) groups.push({ name, parent, isPrimary });
                }
            }

            console.log(`Fetched ${groups.length} groups from Tally`);
            return groups;

        } catch (error) {
            console.error('Error fetching groups from Tally:', error.message);
            throw new Error(`Failed to fetch groups: ${error.message}`);
        }
    }

    debugString(str) {
        if (!str) return 'empty string';
        return `"${str}" (length: ${str.length}, hex: ${Array.from(str).map(c => c.charCodeAt(0).toString(16).padStart(2, '0')).join(' ')})`;
    }

    async getLedgerDetails(ledgerName, company) {
        try {
            const cleanedLedgerName = this.cleanString(ledgerName);
            console.log('Fetching ledger details for:', cleanedLedgerName, 'in company:', company);

            const xmlQuery = `<?xml version="1.0" ?>
            <ENVELOPE>
                <HEADER>
                    <VERSION>1</VERSION>
                    <TALLYREQUEST>Export</TALLYREQUEST>
                    <TYPE>Collection</TYPE>
                    <ID>LedgerDetails</ID>
                </HEADER>
                <BODY>
                    <DESC>
                        <STATICVARIABLES>
                            <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
                            <SVCURRENTCOMPANY>${this.cleanString(company)}</SVCURRENTCOMPANY>
                        </STATICVARIABLES>
                        <TDL>
                            <TDLMESSAGE>
                                <COLLECTION NAME="LedgerDetails">
                                    <TYPE>Ledger</TYPE>
                                    <FETCH>NAME,PARENT,OPENINGBALANCE,ADDRESS,COUNTRYOFRESIDENCE,LEDSTATENAME,LEDGERMOBILE,PARTYGSTIN</FETCH>
                                    <FILTER>NameFilter</FILTER>
                                </COLLECTION>
                                <SYSTEM TYPE="Formulae" NAME="NameFilter">$$FilterByName:$Name:"${cleanedLedgerName}"</SYSTEM>
                            </TDLMESSAGE>
                        </TDL>
                    </DESC>
                </BODY>
            </ENVELOPE>`;

            const response = await axios.post(TALLY_URL, xmlQuery, {
                headers: { 'Content-Type': 'text/xml' },
                timeout: 30000
            });

            const xml2js = require('xml2js');
            const parser = new xml2js.Parser({ explicitArray: false });
            const result = await new Promise((resolve, reject) => {
                parser.parseString(response.data, (err, res) => {
                    if (err) reject(err);
                    else resolve(res);
                });
            });

            let ledgerItem = null;
            if (result?.ENVELOPE?.BODY?.DATA?.COLLECTION?.LEDGER) {
                const items = Array.isArray(result.ENVELOPE.BODY.DATA.COLLECTION.LEDGER)
                    ? result.ENVELOPE.BODY.DATA.COLLECTION.LEDGER
                    : [result.ENVELOPE.BODY.DATA.COLLECTION.LEDGER];
                // Find exact or closest match
                ledgerItem = items.find(i => {
                    const n = this.cleanString(i.$?.NAME || i.NAME || '');
                    return n.toLowerCase().includes(cleanedLedgerName.toLowerCase());
                }) || items[0];
            }

            if (!ledgerItem) {
                throw new Error(`Ledger "${cleanedLedgerName}" not found in company "${company}"`);
            }

            const mappedData = {
                name: this.cleanString(ledgerItem.$?.NAME || ledgerItem.NAME || ledgerName),
                parent_group: this.cleanString(ledgerItem.PARENT?._ || ledgerItem.PARENT || 'Primary'),
                opening_balance: this.cleanString(ledgerItem.OPENINGBALANCE?._ || ledgerItem.OPENINGBALANCE || '0'),
                address: this.cleanString(ledgerItem.ADDRESS?._ || ledgerItem.ADDRESS || ''),
                state: this.cleanString(ledgerItem.LEDSTATENAME?._ || ledgerItem.LEDSTATENAME || ''),
                country: this.cleanString(ledgerItem.COUNTRYOFRESIDENCE?._ || ledgerItem.COUNTRYOFRESIDENCE || ''),
                mobile: this.cleanString(ledgerItem.LEDGERMOBILE?._ || ledgerItem.LEDGERMOBILE || ''),
                gstin: this.cleanString(ledgerItem.PARTYGSTIN?._ || ledgerItem.PARTYGSTIN || '')
            };

            console.log('Mapped ledger data:', mappedData);
            return mappedData;

        } catch (error) {
            console.error('Error fetching ledger details:', error.message);
            throw error;
        }
    }

    async createLedger(ledgerData) {
        try {
            console.log('Creating ledger with data:', ledgerData);

            const ledgerBody = buildLedgerXmlBody('Create', ledgerData);
            const xmlRequest = `<?xml version="1.0" ?>
            <ENVELOPE>
                <HEADER>
                    <TALLYREQUEST>Import Data</TALLYREQUEST>
                </HEADER>
                <BODY>
                    <IMPORTDATA>
                        <REQUESTDESC>
                            <REPORTNAME>All Masters</REPORTNAME>
                        </REQUESTDESC>
                        <REQUESTDATA>
                            <TALLYMESSAGE xmlns:UDF="TallyUDF">
${ledgerBody}
                            </TALLYMESSAGE>
                        </REQUESTDATA>
                    </IMPORTDATA>
                </BODY>
            </ENVELOPE>`;

            console.log('Sending XML request to Tally:', xmlRequest);

            // Make POST request to Tally
            const response = await axios.post(TALLY_URL, xmlRequest, {
                headers: {
                    'Content-Type': 'text/xml'
                },
                timeout: 30000 // 30 seconds timeout
            });

            // Parse XML response
            const xml2js = require('xml2js');
            const parser = new xml2js.Parser({ explicitArray: false });
            
            const result = await new Promise((resolve, reject) => {
                parser.parseString(response.data, (err, result) => {
                    if (err) {
                        console.error('XML parsing error:', err);
                        reject(err);
                    } else {
                        resolve(result);
                    }
                });
            });

            console.log('Parsed Tally response:', result);

            // Check for errors in response
            if (result?.RESPONSE?.EXCEPTIONS === '1') {
                // Try to get more details about the exception
                const errorDetails = [];
                if (result?.RESPONSE?.LINEERROR) errorDetails.push(result.RESPONSE.LINEERROR);
                if (result?.RESPONSE?.ERRORS) errorDetails.push(result.RESPONSE.ERRORS);
                
                // Log the full response for debugging
                console.error('Full error response:', result);
                
                throw new Error(`Tally Exception: ${errorDetails.join(', ') || 'Please verify Tally is running and the company is open'}`);
            }

            return true;
            
        } catch (error) {
            console.error('Complete error details:', {
                message: error.message,
                code: error.code,
                type: error.type,
                stack: error.stack,
                response: error.response?.data
            });
            throw error;
        }
    }

    async createGroup(groupData) {
        try {
            console.log('Creating group with data:', groupData);

            // Construct XML request for creating a group
            const xmlRequest = `<?xml version="1.0" ?>
            <ENVELOPE>
                <HEADER>
                    <TALLYREQUEST>Import Data</TALLYREQUEST>
                </HEADER>
                <BODY>
                    <IMPORTDATA>
                        <REQUESTDESC>
                            <REPORTNAME>All Masters</REPORTNAME>
                        </REQUESTDESC>
                        <REQUESTDATA>
                            <TALLYMESSAGE xmlns:UDF="TallyUDF">
                                <GROUP Action="Create">
                                    <NAME>${groupData.name}</NAME>
                                    ${groupData.parent ? `<PARENT>${groupData.parent}</PARENT>` : ''}
                                    <NATURE>${groupData.nature || 'Primary'}</NATURE>
                                    <ISSUBLEDGER>No</ISSUBLEDGER>
                                    <NETSIGNDISPLAYTYPE>NoSign</NETSIGNDISPLAYTYPE>
                                </GROUP>
                            </TALLYMESSAGE>
                        </REQUESTDATA>
                    </IMPORTDATA>
                </BODY>
            </ENVELOPE>`;

            console.log('Sending XML request to Tally:', xmlRequest);

            // Make POST request to Tally
            const response = await axios.post(TALLY_URL, xmlRequest, {
                headers: {
                    'Content-Type': 'text/xml'
                },
                timeout: 30000 // 30 seconds timeout
            });

            // Parse XML response
            const xml2js = require('xml2js');
            const parser = new xml2js.Parser({ explicitArray: false });
            
            const result = await new Promise((resolve, reject) => {
                parser.parseString(response.data, (err, result) => {
                    if (err) {
                        console.error('XML parsing error:', err);
                        reject(err);
                    } else {
                        resolve(result);
                    }
                });
            });

            console.log('Parsed Tally response:', result);

            // Check for errors in response
            if (result?.RESPONSE?.EXCEPTIONS === '1') {
                // Try to get more details about the exception
                const errorDetails = [];
                if (result?.RESPONSE?.LINEERROR) errorDetails.push(result.RESPONSE.LINEERROR);
                if (result?.RESPONSE?.ERRORS) errorDetails.push(result.RESPONSE.ERRORS);
                
                // Log the full response for debugging
                console.error('Full error response:', result);
                
                throw new Error(`Tally Exception: ${errorDetails.join(', ') || 'Please verify Tally is running and the company is open'}`);
            }

            return true;
            
        } catch (error) {
            console.error('Complete error details:', {
                message: error.message,
                code: error.code,
                type: error.type,
                stack: error.stack,
                response: error.response?.data
            });
            throw error;
        }
    }

    /**
     * Fetch voucher transactions for a specific ledger from Tally.
     * @param {string} company
     * @param {string} ledgerName
     * @param {string} fromDate  YYYYMMDD
     * @param {string} toDate    YYYYMMDD
     * @returns {Promise<{opening_balance: number|null, closing_balance: number|null, transactions: Array}>}
     */
    async getLedgerTransactions(company, ledgerName, fromDate, toDate) {
        console.log('[BRS-DIAG] getLedgerTransactions called:', { company, ledgerName, fromDate, toDate });
        const cleanCompany = this.cleanString(company);
        const cleanLedger  = this.cleanString(ledgerName);
        const cacheKey     = `${cleanCompany}|${cleanLedger}`;

        // Kick off balance fetch in parallel (always fresh, ~1s)
        const balancePromise = this._fetchLedgerBalance(cleanCompany, cleanLedger, fromDate, toDate);

        try {
            // Cache: avoid re-running the 12s Ledger Vouchers query every BRS iteration.
            let entry = LV_CACHE.get(cacheKey);
            if (entry && (Date.now() - entry.fetchedAt) < LV_TTL_MS) {
                console.log(`[BRS] LV cache HIT for ${cleanLedger} (${entry.rows.length} rows, age=${((Date.now()-entry.fetchedAt)/1000).toFixed(0)}s)`);
            } else {
                console.log(`[BRS] LV cache MISS → fetching Ledger Vouchers for ${cleanLedger}`);
                const rows = await this._fetchLedgerVouchers(cleanCompany, cleanLedger);
                entry = { rows, fetchedAt: Date.now() };
                LV_CACHE.set(cacheKey, entry);
            }

            // Client-side date filter
            const transactions = this._filterLedgerRowsByDate(entry.rows, fromDate, toDate);
            console.log(`[BRS] ${entry.rows.length} total ledger rows → ${transactions.length} after date filter [${fromDate}-${toDate}]`);

            const { opening, closing } = await balancePromise;
            console.log(`[BRS] balance: opening=${opening}, closing=${closing}`);
            return { opening_balance: opening, closing_balance: closing, transactions };
        } catch (err) {
            console.error('[BRS] FATAL getLedgerTransactions:', err.message);
            console.error('[BRS] stack:', err.stack);
            return { opening_balance: null, closing_balance: null, transactions: [] };
        }
    }

    /**
     * Run the `Ledger Vouchers` report for a specific ledger.
     * Pre-filters server-side by LedgerName → only ~8k rows for HDFC Bank
     * instead of 11k full voucher dump. Response is 1.5 MB / ~12 s.
     * Uses inline <SET> on REPORT to unlock historical data (without it
     * Tally shows only the current month view, ~733 rows).
     */
    async _fetchLedgerVouchers(company, ledger) {
        const xml = `<?xml version="1.0" ?>
<ENVELOPE>
  <HEADER><VERSION>1</VERSION><TALLYREQUEST>Export</TALLYREQUEST><TYPE>Data</TYPE><ID>Ledger Vouchers</ID></HEADER>
  <BODY><DESC>
    <STATICVARIABLES>
      <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
      <SVCURRENTCOMPANY>${company}</SVCURRENTCOMPANY>
      <LedgerName>${ledger}</LedgerName>
    </STATICVARIABLES>
    <TDL><TDLMESSAGE>
      <REPORT NAME="Ledger Vouchers" ISMODIFY="Yes" ISFIXED="No" ISINITIALIZE="No" ISOPTION="No" ISINTERNAL="No">
        <SET>SVFromDate : $$Date:"1-Apr-2000"</SET>
        <SET>SVToDate : $$Date:"31-Mar-2099"</SET>
      </REPORT>
    </TDLMESSAGE></TDL>
  </DESC></BODY>
</ENVELOPE>`;

        const t0 = Date.now();
        const resp = await axios.post(TALLY_URL, xml, {
            headers: { 'Content-Type': 'text/xml' },
            timeout: 300000,
            maxContentLength: Infinity,
            maxBodyLength: Infinity,
        });
        console.log(`[BRS] Ledger Vouchers fetched in ${Date.now()-t0}ms, ${resp.data.length} bytes`);

        const body = resp.data;
        if (body.includes('Unknown Request') || /LINEERROR/i.test(body.slice(0, 500))) {
            throw new Error('Tally rejected Ledger Vouchers request — is company loaded?');
        }

        // Parse DSPVCH* fields — each voucher is a tuple of 5 consecutive tags.
        // Some records also have extra NAMEFIELD / INFOFIELD between them (always empty here).
        const rows = [];
        const re = /<DSPVCHDATE>([^<]*)<\/DSPVCHDATE>[\s\S]*?<DSPVCHLEDACCOUNT>([^<]*)<\/DSPVCHLEDACCOUNT>[\s\S]*?<DSPVCHTYPE>([^<]*)<\/DSPVCHTYPE>\s*<DSPVCHDRAMT>([^<]*)<\/DSPVCHDRAMT>\s*<DSPVCHCRAMT>([^<]*)<\/DSPVCHCRAMT>/g;
        let m;
        while ((m = re.exec(body)) !== null) {
            const [, dateStr, contra, typeShort, drRaw, crRaw] = m;
            const isoDate = this._parseDMonYY(this.cleanString(dateStr));
            if (!isoDate) continue;
            const dr = parseFloat(String(drRaw || '').replace(/[^0-9.\-]/g, '')) || 0;
            const cr = parseFloat(String(crRaw || '').replace(/[^0-9.\-]/g, '')) || 0;
            const absAmt = Math.abs(dr) || Math.abs(cr);
            rows.push({
                date: isoDate,
                narration: this.cleanString(contra),                       // use contra ledger as narration stand-in
                voucher_type: VCH_TYPE_MAP[this.cleanString(typeShort)] || this.cleanString(typeShort),
                voucher_number: '',                                        // not exposed by Ledger Vouchers
                guid: '',
                amount: absAmt,
                is_debit: dr !== 0,                                        // true = bank debited (money in)
                contra_ledger: this.cleanString(contra),
            });
        }
        console.log(`[BRS] parsed ${rows.length} Ledger Vouchers rows`);
        return rows;
    }

    /**
     * Fetch the ledger object for opening/closing balance (small, ~1s).
     */
    async _fetchLedgerBalance(company, ledger, fromDate, toDate) {
        try {
            const tallyFrom = this._toTallyDate(fromDate);
            const tallyTo   = this._toTallyDate(toDate);
            const xml = `<?xml version="1.0" ?>
<ENVELOPE>
  <HEADER><VERSION>1</VERSION><TALLYREQUEST>Export</TALLYREQUEST><TYPE>Object</TYPE><SUBTYPE>Ledger</SUBTYPE>
    <ID TYPE="Name">${ledger}</ID></HEADER>
  <BODY><DESC>
    <STATICVARIABLES>
      <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
      <SVCURRENTCOMPANY>${company}</SVCURRENTCOMPANY>
      <SVFROMDATE>${tallyFrom}</SVFROMDATE>
      <SVTODATE>${tallyTo}</SVTODATE>
    </STATICVARIABLES>
    <FETCHLIST>
      <FETCH>Name</FETCH><FETCH>OpeningBalance</FETCH><FETCH>ClosingBalance</FETCH>
    </FETCHLIST>
  </DESC></BODY>
</ENVELOPE>`;
            const resp = await axios.post(TALLY_URL, xml, {
                headers: { 'Content-Type': 'text/xml' },
                timeout: 60000,
            });
            const xml2js = require('xml2js');
            const parser = new xml2js.Parser({ explicitArray: true });
            const parsed = await new Promise((resolve, reject) =>
                parser.parseString(resp.data, (e, r) => e ? reject(e) : resolve(r)));
            const ledgerNode = parsed?.ENVELOPE?.BODY?.[0]?.DATA?.[0]?.TALLYMESSAGE?.[0]?.LEDGER;
            const l = Array.isArray(ledgerNode) ? ledgerNode[0] : ledgerNode;
            const parseNum = (val) => {
                if (!val) return null;
                const raw = Array.isArray(val) ? val[0] : val;
                const s = String(raw?._ || raw).replace(/[^0-9.\-]/g, '');
                const n = parseFloat(s);
                return isNaN(n) ? null : n;
            };
            return {
                opening: l ? parseNum(l.OPENINGBALANCE) : null,
                closing: l ? parseNum(l.CLOSINGBALANCE) : null,
            };
        } catch (e) {
            console.warn('[BRS] Balance fetch failed (non-fatal):', e.message);
            return { opening: null, closing: null };
        }
    }

    /** Filter parsed Ledger Vouchers rows by ISO date range (YYYYMMDD strings). */
    _filterLedgerRowsByDate(rows, fromYyyymmdd, toYyyymmdd) {
        if (!fromYyyymmdd && !toYyyymmdd) return rows;
        const from = fromYyyymmdd || '00000000';
        const to   = toYyyymmdd   || '99999999';
        return rows.filter(r => r.date >= from && r.date <= to);
    }

    /** "1-Apr-24" → "20240401" (ISO yyyymmdd for comparisons). */
    _parseDMonYY(s) {
        const m = /^(\d{1,2})-(\w+)-(\d{2,4})$/.exec(String(s || '').trim());
        if (!m) return null;
        const monthMap = { Jan:'01', Feb:'02', Mar:'03', Apr:'04', May:'05', Jun:'06', Jul:'07', Aug:'08', Sep:'09', Oct:'10', Nov:'11', Dec:'12' };
        const day = m[1].padStart(2, '0');
        const mon = monthMap[m[2]];
        if (!mon) return null;
        let yr = m[3];
        if (yr.length === 2) yr = (parseInt(yr, 10) >= 50 ? '19' : '20') + yr;
        return `${yr}${mon}${day}`;
    }

    /** "20240401" → "1-Apr-2024" for Tally STATICVARIABLES. */
    _toTallyDate(yyyymmdd) {
        if (!yyyymmdd || String(yyyymmdd).length !== 8) return '';
        const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
        const d = new Date(parseInt(yyyymmdd.slice(0,4)), parseInt(yyyymmdd.slice(4,6))-1, parseInt(yyyymmdd.slice(6,8)));
        return `${d.getDate()}-${months[d.getMonth()]}-${d.getFullYear()}`;
    }

    cleanString(str) {
        if (!str) return '';
        // Remove non-printable/control characters (including \u0004)
        return String(str)
            .replace(/[\x00-\x1F\x7F-\x9F]/g, '')  // Remove control characters
            .replace(/&#\d+;/g, '')                 // Remove HTML entities
            .replace(/\r?\n/g, '')                  // Remove newlines and carriage returns
            .trim();                                // Remove leading/trailing whitespace
    }

    extractQuantity(balance) {
        if (!balance) return 0;
        const match = String(balance).match(/-?\d*\.?\d+/);
        return match ? parseFloat(match[0]) : 0;
    }

    async getTableColumns(tableName) {
        // No longer uses ODBC - return empty array as this is primarily for debugging
        console.log(`getTableColumns called for ${tableName} - not available without ODBC`);
        return [];
    }

    async editLedger(ledgerData) {
        try {
            console.log('Editing ledger with data:', ledgerData);

            // Use the current name directly (cleaned)
            const exactCurrentName = this.cleanString(ledgerData.current_name || ledgerData.name);
            console.log('Current name for edit:', exactCurrentName);

            // Use new name if provided, otherwise keep the current name
            const newName = ledgerData.new_name || ledgerData.name || exactCurrentName;
            console.log('New name to be used:', this.debugString(newName));
            console.log('Exact current name:', this.debugString(exactCurrentName));
            const ledgerBody = buildLedgerXmlBody('Alter', {
                ...ledgerData,
                name: exactCurrentName,
                new_name: newName !== exactCurrentName ? newName : undefined,
            });
            const xmlRequest = `<?xml version="1.0" ?>
            <ENVELOPE>
                <HEADER>
                    <TALLYREQUEST>Import Data</TALLYREQUEST>
                </HEADER>
                <BODY>
                    <IMPORTDATA>
                        <REQUESTDESC>
                            <REPORTNAME>All Masters</REPORTNAME>
                        </REQUESTDESC>
                        <REQUESTDATA>
                            <TALLYMESSAGE xmlns:UDF="TallyUDF">
${ledgerBody}
                            </TALLYMESSAGE>
                        </REQUESTDATA>
                    </IMPORTDATA>
                </BODY>
            </ENVELOPE>`;

            console.log('Sending XML request to Tally:', xmlRequest);

            // Make POST request to Tally
            const response = await axios.post(TALLY_URL, xmlRequest, {
                headers: {
                    'Content-Type': 'text/xml'
                },
                timeout: 30000 // 30 seconds timeout
            });

            // Parse XML response
            const xml2js = require('xml2js');
            const parser = new xml2js.Parser({ explicitArray: false });
            
            const result = await new Promise((resolve, reject) => {
                parser.parseString(response.data, (err, result) => {
                    if (err) {
                        console.error('XML parsing error:', err);
                        reject(err);
                    } else {
                        resolve(result);
                    }
                });
            });

            console.log('Parsed Tally response:', result);

            // Check for errors in response
            if (result?.RESPONSE?.EXCEPTIONS === '1') {
                const errorDetails = [];
                if (result?.RESPONSE?.LINEERROR) errorDetails.push(result.RESPONSE.LINEERROR);
                if (result?.RESPONSE?.ERRORS) errorDetails.push(result.RESPONSE.ERRORS);
                
                console.error('Full error response:', result);
                throw new Error(`Tally Exception: ${errorDetails.join(', ') || 'Please verify Tally is running and the company is open'}`);
            }
            
            return {
                success: true,
                message: 'Ledger updated successfully',
                data: {
                    name: newName,
                    parent_group: ledgerData.parent_group,
                    opening_balance: ledgerData.opening_balance,
                    address: ledgerData.address,
                    country: ledgerData.country,
                    state: ledgerData.state,
                    mobile: ledgerData.mobile,
                    gstin: ledgerData.gstin
                }
            };
        } catch (error) {
            console.error('Complete error details:', {
                message: error.message,
                code: error.code,
                type: error.type,
                stack: error.stack,
                response: error.response?.data
            });
            throw error;
        }
    }
}

module.exports = WebSocketService; 