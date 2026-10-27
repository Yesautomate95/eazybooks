/**
 * QuickBooks Desktop client for the Electron bridge.
 *
 * Production note:
 *   QB Desktop does not expose HTTP. Real production wiring is one of:
 *     a) QB Web Connector (SOAP-based, polled by QBWC.exe). The Electron app
 *        runs a small SOAP server that QBWC connects to and queues qbXML.
 *     b) qbXMLRP2.dll via win32 COM (Windows only). Direct in-process call.
 *
 * This module currently targets QB_URL (default http://127.0.0.1:9001) which
 * is mock_qb in dev. Swap _post() with the chosen production transport when
 * wiring real QB Desktop.
 */

const axios = require('axios');

const QB_URL = process.env.QB_URL || 'http://127.0.0.1:9001';
const REQUEST_TIMEOUT = 60000;

const QBXML_HEADER = '<?xml version="1.0" encoding="utf-8"?>\n<?qbxml version="13.0"?>\n';


function buildQBXML(operationXml, onError = 'stopOnError') {
    return (
        `${QBXML_HEADER}<QBXML>\n` +
        `  <QBXMLMsgsRq onError="${onError}">\n` +
        `    ${operationXml.trim()}\n` +
        `  </QBXMLMsgsRq>\n` +
        `</QBXML>`
    );
}


function escapeXml(s) {
    return String(s == null ? '' : s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');
}


async function _post(qbxml) {
    const resp = await axios.post(QB_URL, qbxml, {
        headers: { 'Content-Type': 'text/xml' },
        timeout: REQUEST_TIMEOUT,
    });
    return resp.data;
}


// ---------------------------------------------------------------------------
// Lightweight regex-based extractors. Mock_qb returns simple non-namespaced XML
// so we avoid pulling in xml2js for these calls. For production wiring with
// real QB the responses are richer - swap to xml2js then.
// ---------------------------------------------------------------------------

function extractAll(xml, tag) {
    const re = new RegExp(`<${tag}>([\\s\\S]*?)</${tag}>`, 'g');
    const out = [];
    let m;
    while ((m = re.exec(xml)) !== null) out.push(m[1]);
    return out;
}

function firstTag(block, tag) {
    const m = block.match(new RegExp(`<${tag}>([^<]*)</${tag}>`));
    return m ? m[1] : '';
}

function checkStatus(xml, op) {
    const m = xml.match(new RegExp(`<${op}\\s[^>]*statusCode="(\\d+)"[^>]*statusMessage="([^"]*)"`));
    if (m && m[1] !== '0') {
        throw new Error(`${op} failed (statusCode=${m[1]}): ${m[2]}`);
    }
}


// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

async function checkConnection() {
    try {
        const resp = await _post(buildQBXML('<HostQueryRq requestID="1"/>'));
        checkStatus(resp, 'HostQueryRs');
        return true;
    } catch (err) {
        console.error('[qb_client] checkConnection failed:', err.message);
        return false;
    }
}


async function getCompanies() {
    const resp = await _post(buildQBXML('<HostQueryRq requestID="1"/>'));
    checkStatus(resp, 'HostQueryRs');
    return extractAll(resp, 'CompanyFileName').map((file) => ({
        file,
        name: (file.split(/[\\/]/).pop() || file).replace(/\.[^.]+$/, ''),
    }));
}


async function getAccounts(_company) {
    const resp = await _post(buildQBXML('<AccountQueryRq requestID="1"/>'));
    checkStatus(resp, 'AccountQueryRs');
    return extractAll(resp, 'AccountRet').map((b) => ({
        list_id: firstTag(b, 'ListID'),
        name: firstTag(b, 'Name'),
        full_name: firstTag(b, 'FullName'),
        account_type: firstTag(b, 'AccountType'),
        balance: firstTag(b, 'Balance') || '0.00',
    }));
}


async function getCustomers(_company) {
    const resp = await _post(buildQBXML('<CustomerQueryRq requestID="1"/>'));
    checkStatus(resp, 'CustomerQueryRs');
    return extractAll(resp, 'CustomerRet').map((b) => ({
        list_id: firstTag(b, 'ListID'),
        name: firstTag(b, 'Name'),
        company: firstTag(b, 'CompanyName'),
        email: firstTag(b, 'Email'),
        balance: firstTag(b, 'Balance') || '0.00',
    }));
}


async function getVendors(_company) {
    const resp = await _post(buildQBXML('<VendorQueryRq requestID="1"/>'));
    checkStatus(resp, 'VendorQueryRs');
    return extractAll(resp, 'VendorRet').map((b) => ({
        list_id: firstTag(b, 'ListID'),
        name: firstTag(b, 'Name'),
        company: firstTag(b, 'CompanyName'),
        email: firstTag(b, 'Email'),
        balance: firstTag(b, 'Balance') || '0.00',
    }));
}


async function getItems(_company) {
    const resp = await _post(buildQBXML('<ItemQueryRq requestID="1"/>'));
    checkStatus(resp, 'ItemQueryRs');
    const out = [];
    for (const tag of ['ItemServiceRet', 'ItemInventoryRet', 'ItemNonInventoryRet', 'ItemOtherChargeRet']) {
        for (const b of extractAll(resp, tag)) {
            out.push({
                list_id: firstTag(b, 'ListID'),
                name: firstTag(b, 'Name'),
                type: tag.replace('Item', '').replace('Ret', ''),
                rate: firstTag(b, 'SalesPrice') || '0.00',
            });
        }
    }
    return out;
}


async function createCustomer({ name, company_name, email }) {
    let inner = '<CustomerAddRq requestID="1"><CustomerAdd>';
    inner += `<Name>${escapeXml(name)}</Name>`;
    if (company_name) inner += `<CompanyName>${escapeXml(company_name)}</CompanyName>`;
    if (email) inner += `<Email>${escapeXml(email)}</Email>`;
    inner += '</CustomerAdd></CustomerAddRq>';
    const resp = await _post(buildQBXML(inner));
    checkStatus(resp, 'CustomerAddRs');
    const block = extractAll(resp, 'CustomerRet')[0] || '';
    return { list_id: firstTag(block, 'ListID'), name: firstTag(block, 'Name') };
}


async function createVendor({ name, company_name, email }) {
    let inner = '<VendorAddRq requestID="1"><VendorAdd>';
    inner += `<Name>${escapeXml(name)}</Name>`;
    if (company_name) inner += `<CompanyName>${escapeXml(company_name)}</CompanyName>`;
    if (email) inner += `<Email>${escapeXml(email)}</Email>`;
    inner += '</VendorAdd></VendorAddRq>';
    const resp = await _post(buildQBXML(inner));
    checkStatus(resp, 'VendorAddRs');
    const block = extractAll(resp, 'VendorRet')[0] || '';
    return { list_id: firstTag(block, 'ListID'), name: firstTag(block, 'Name') };
}


async function createAccount({ name, account_type }) {
    const inner =
        '<AccountAddRq requestID="1"><AccountAdd>' +
        `<Name>${escapeXml(name)}</Name>` +
        `<AccountType>${escapeXml(account_type)}</AccountType>` +
        '</AccountAdd></AccountAddRq>';
    const resp = await _post(buildQBXML(inner));
    checkStatus(resp, 'AccountAddRs');
    const block = extractAll(resp, 'AccountRet')[0] || '';
    return { list_id: firstTag(block, 'ListID'), name: firstTag(block, 'Name') };
}


async function pushInvoice(qbxmlOrFragment) {
    const envelope = qbxmlOrFragment.includes('<QBXML>') ? qbxmlOrFragment : buildQBXML(qbxmlOrFragment);
    const resp = await _post(envelope);
    checkStatus(resp, 'InvoiceAddRs');
    const block = extractAll(resp, 'InvoiceRet')[0] || '';
    return { txn_id: firstTag(block, 'TxnID'), ref_number: firstTag(block, 'RefNumber'), raw: resp };
}


async function pushBill(qbxmlOrFragment) {
    const envelope = qbxmlOrFragment.includes('<QBXML>') ? qbxmlOrFragment : buildQBXML(qbxmlOrFragment);
    const resp = await _post(envelope);
    checkStatus(resp, 'BillAddRs');
    const block = extractAll(resp, 'BillRet')[0] || '';
    return { txn_id: firstTag(block, 'TxnID'), ref_number: firstTag(block, 'RefNumber'), raw: resp };
}


module.exports = {
    QB_URL,
    buildQBXML,
    checkConnection,
    getCompanies,
    getAccounts,
    getCustomers,
    getVendors,
    getItems,
    createCustomer,
    createVendor,
    createAccount,
    pushInvoice,
    pushBill,
};
