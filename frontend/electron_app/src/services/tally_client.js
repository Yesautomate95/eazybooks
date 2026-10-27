/**
 * Tally XML HTTP API client — reusable primitives.
 *
 * Captures the patterns we learned the hard way:
 *   • TYPE=COLLECTION (not TYPE=Data) for clean structured XML
 *   • Explicit FETCH fields (no wildcards) to control payload size
 *   • Parse via xml2js, then unwrap attributed {_: "text", $: {attrs}} nodes
 *   • Serialize requests — Tally is single-threaded
 *   • Tally's UI-active period is a HARD LOCK; API cannot widen it
 *
 * Public surface:
 *   fetchCollection({ company, type, from, to, fetch, timeout })  → {records, meta, raw}
 *   fetchObject({ company, subtype, id, fetch, from, to, timeout }) → {object, raw}
 *   unwrap(v)            — xml2js attribute-wrapper helper
 *   clean(node)          — deep unwrap parsed xml2js into plain JS
 *   toTallyDate(yyyymmdd) — "20240401" → "1-Apr-2024"
 *   detectPeriodShortfall(requestedFrom, requestedTo, records, dateField)
 *     → null | { returnedMonths, requestedMonths, message }
 */
const axios = require('axios');
const xml2js = require('xml2js');

const TALLY_URL = 'http://127.0.0.1:9000';
const DEFAULT_TIMEOUT_MS = 300_000;

// ── serial queue — Tally handles one HTTP request at a time ─────────────────
let _chain = Promise.resolve();
function _enqueue(fn) {
    const next = _chain.then(fn, fn);
    _chain = next.catch(() => {});
    return next;
}

// ── xml2js helpers ───────────────────────────────────────────────────────────
function unwrap(v) {
    const first = Array.isArray(v) ? v[0] : v;
    if (first == null) return '';
    if (typeof first === 'object') return first._ != null ? String(first._) : '';
    return String(first);
}

/**
 * Recursively simplify a parsed xml2js tree:
 *   - single-element arrays collapse to their scalar
 *   - { _: "x", $: {attrs} } nodes collapse to "x"
 *   - attributes ($) are dropped (keep only data)
 *   - deeper arrays and objects are recursed into
 */
function clean(node) {
    if (node == null) return '';
    if (Array.isArray(node)) {
        if (node.length === 0) return '';
        if (node.length === 1) return clean(node[0]);
        return node.map(clean);
    }
    if (typeof node !== 'object') return node;
    const keys = Object.keys(node);
    if ('_' in node && keys.every(k => k === '_' || k === '$')) {
        return node._ != null ? String(node._) : '';
    }
    const out = {};
    for (const k of keys) {
        if (k === '$') continue;
        out[k] = clean(node[k]);
    }
    return out;
}

// ── date conversion ──────────────────────────────────────────────────────────
const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
function toTallyDate(yyyymmdd) {
    if (!yyyymmdd || String(yyyymmdd).length !== 8) return '';
    const s = String(yyyymmdd);
    const y = s.slice(0, 4);
    const m = parseInt(s.slice(4, 6), 10) - 1;
    const d = parseInt(s.slice(6, 8), 10);
    return `${d}-${MONTHS[m]}-${y}`;
}

// ── HTTP + parse ─────────────────────────────────────────────────────────────
async function _post(xml, timeoutMs) {
    return _enqueue(async () => {
        try {
            const resp = await axios.post(TALLY_URL, xml, {
                headers: { 'Content-Type': 'text/xml' },
                timeout: timeoutMs || DEFAULT_TIMEOUT_MS,
                maxContentLength: Infinity,
                maxBodyLength: Infinity,
            });
            return resp.data;
        } catch (err) {
            // axios throws AggregateError with empty .message on ECONNREFUSED/IPv6-race.
            // Unwrap to something diagnosable.
            const inner = err?.errors?.[0];
            const code  = inner?.code || err?.code || err?.cause?.code || '';
            const base  = err?.message || inner?.message || 'unknown network error';
            const msg   = code ? `Tally HTTP error [${code}] ${base}` : `Tally HTTP error: ${base}`;
            const wrapped = new Error(msg + ` (url=${TALLY_URL})`);
            wrapped.cause = err;
            throw wrapped;
        }
    });
}

async function _parse(xml) {
    const parser = new xml2js.Parser({ explicitArray: true });
    return new Promise((resolve, reject) => {
        parser.parseString(xml, (err, res) => err ? reject(err) : resolve(res));
    });
}

function _detectTopLevelError(raw) {
    const head = String(raw).slice(0, 2000);
    if (head.includes('Unknown Request, cannot be processed')) {
        return new Error('Tally rejected request: "Unknown Request, cannot be processed" (check XML structure)');
    }
    if (!head.includes('<VOUCHER') && !head.includes('<ENVELOPE')) {
        return new Error(`Tally returned unexpected response: ${head.slice(0, 200)}`);
    }
    if (head.includes('<ERRORMSG>') && !head.includes('<VOUCHER')) {
        const m = head.match(/<ERRORMSG>([^<]+)<\/ERRORMSG>/);
        return new Error(`Tally error: ${m?.[1] || 'unknown'}`);
    }
    return null;
}

// ── public: fetchCollection ──────────────────────────────────────────────────
/**
 * Fetch a Tally COLLECTION as plain JS records.
 *
 * @param {Object}   opts
 * @param {string}   opts.company  - SVCURRENTCOMPANY (exact name)
 * @param {string}   opts.type     - TDL collection TYPE (Voucher, Ledger, StockItem, Group, ...)
 * @param {string[]} opts.fetch    - FETCH field names (e.g. ['Date','AllLedgerEntries.LedgerName'])
 * @param {string}  [opts.from]    - yyyymmdd (e.g. "20240401")
 * @param {string}  [opts.to]      - yyyymmdd
 * @param {number}  [opts.timeout] - ms (default 300000)
 * @returns {Promise<{records: any[], meta: {count:number, type:string}, raw: string}>}
 */
async function fetchCollection({ company, type, fetch, from, to, timeout }) {
    if (!company) throw new Error('fetchCollection: company required');
    if (!type)    throw new Error('fetchCollection: type required');
    if (!Array.isArray(fetch) || fetch.length === 0) {
        throw new Error('fetchCollection: fetch must be a non-empty array of field names');
    }

    const staticVars = [
        `<SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>`,
        `<SVCURRENTCOMPANY>${_escape(company)}</SVCURRENTCOMPANY>`,
        from ? `<SVFROMDATE>${toTallyDate(from)}</SVFROMDATE>` : '',
        to   ? `<SVTODATE>${toTallyDate(to)}</SVTODATE>`       : '',
    ].filter(Boolean).join('\n      ');

    const collName = `Coll_${Math.random().toString(36).slice(2, 10)}`;
    const xml = `<?xml version="1.0" ?>
<ENVELOPE>
  <HEADER>
    <VERSION>1</VERSION>
    <TALLYREQUEST>Export</TALLYREQUEST>
    <TYPE>COLLECTION</TYPE>
    <ID>${collName}</ID>
  </HEADER>
  <BODY><DESC>
    <STATICVARIABLES>
      ${staticVars}
    </STATICVARIABLES>
    <TDL><TDLMESSAGE>
      <COLLECTION NAME="${collName}" ISINITIALIZE="Yes">
        <TYPE>${type}</TYPE>
        <FETCH>${fetch.join(', ')}</FETCH>
      </COLLECTION>
    </TDLMESSAGE></TDL>
  </DESC></BODY>
</ENVELOPE>`;

    const raw = await _post(xml, timeout);

    const err = _detectTopLevelError(raw);
    if (err) throw err;

    const parsed = await _parse(raw);
    const collNode = parsed?.ENVELOPE?.BODY?.[0]?.DATA?.[0]?.COLLECTION?.[0];
    if (!collNode) return { records: [], meta: { count: 0, type }, raw };

    const records = [];
    for (const [tag, val] of Object.entries(collNode)) {
        if (tag === '$') continue;
        const items = Array.isArray(val) ? val : [val];
        for (const item of items) {
            const c = clean(item);
            if (c && typeof c === 'object') c.$$tag = tag;
            records.push(c);
        }
    }
    return { records, meta: { count: records.length, type }, raw };
}

// ── public: fetchObject ──────────────────────────────────────────────────────
/**
 * Fetch a single Tally OBJECT (Ledger, StockItem, Group, ...).
 *
 * @param {Object}   opts
 * @param {string}   opts.company
 * @param {string}   opts.subtype - e.g. "Ledger"
 * @param {string}   opts.id      - object name
 * @param {string[]} opts.fetch   - field list
 * @param {string}  [opts.from]
 * @param {string}  [opts.to]
 * @param {number}  [opts.timeout]
 * @returns {Promise<{object: any, raw: string}>}
 */
async function fetchObject({ company, subtype, id, fetch, from, to, timeout }) {
    if (!company) throw new Error('fetchObject: company required');
    if (!subtype) throw new Error('fetchObject: subtype required');
    if (!id)      throw new Error('fetchObject: id required');
    if (!Array.isArray(fetch) || fetch.length === 0) {
        throw new Error('fetchObject: fetch must be a non-empty array');
    }

    const staticVars = [
        `<SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>`,
        `<SVCURRENTCOMPANY>${_escape(company)}</SVCURRENTCOMPANY>`,
        from ? `<SVFROMDATE>${toTallyDate(from)}</SVFROMDATE>` : '',
        to   ? `<SVTODATE>${toTallyDate(to)}</SVTODATE>`       : '',
    ].filter(Boolean).join('\n      ');
    const fetchList = fetch.map(f => `<FETCH>${f}</FETCH>`).join('\n      ');

    const xml = `<?xml version="1.0" ?>
<ENVELOPE>
  <HEADER>
    <VERSION>1</VERSION>
    <TALLYREQUEST>Export</TALLYREQUEST>
    <TYPE>Object</TYPE>
    <SUBTYPE>${subtype}</SUBTYPE>
    <ID TYPE="Name">${_escape(id)}</ID>
  </HEADER>
  <BODY><DESC>
    <STATICVARIABLES>
      ${staticVars}
    </STATICVARIABLES>
    <FETCHLIST>
      ${fetchList}
    </FETCHLIST>
  </DESC></BODY>
</ENVELOPE>`;

    const raw = await _post(xml, timeout || 60_000);
    const parsed = await _parse(raw);
    const upperSub = subtype.toUpperCase();
    const msg = parsed?.ENVELOPE?.BODY?.[0]?.DATA?.[0]?.TALLYMESSAGE?.[0];
    const objNode = msg?.[upperSub]?.[0];
    return { object: objNode ? clean(objNode) : null, raw };
}

// ── public: period shortfall detection ───────────────────────────────────────
/**
 * Detect the Tally "UI active period is narrower than requested" scenario.
 * Returns a warning object if detected, else null.
 *
 * @param {string}   from         - yyyymmdd requested from
 * @param {string}   to           - yyyymmdd requested to
 * @param {any[]}    records      - output of fetchCollection
 * @param {string}  [dateField='Date'] - which field on each record holds the voucher date
 */
function detectPeriodShortfall(from, to, records, dateField = 'Date') {
    if (!from || !to) return null;
    const reqMonths = _monthSpan(from, to);
    const dates = records
        .map(r => (r && r[dateField]) ? String(r[dateField]).slice(0, 6) : null)
        .filter(Boolean);
    const uniq = [...new Set(dates)];
    if (reqMonths >= 3 && uniq.length <= 2 && records.length < 200) {
        return {
            requestedMonths: reqMonths,
            returnedMonths: uniq.sort(),
            recordCount: records.length,
            message: `Tally returned only ${records.length} records across ${uniq.length} month(s) for a ${reqMonths}-month request. `
                   + `Tally's UI-active period likely does not cover the requested range. `
                   + `In TallyPrime, press Alt+F2 and set the period to cover ${toTallyDate(from)} → ${toTallyDate(to)}.`,
        };
    }
    return null;
}

// ── helpers ──────────────────────────────────────────────────────────────────
function _monthSpan(fromYyyymmdd, toYyyymmdd) {
    const yf = parseInt(fromYyyymmdd.slice(0,4),10), mf = parseInt(fromYyyymmdd.slice(4,6),10);
    const yt = parseInt(toYyyymmdd.slice(0,4),10),   mt = parseInt(toYyyymmdd.slice(4,6),10);
    return (yt - yf) * 12 + (mt - mf) + 1;
}

function _escape(s) {
    return String(s)
        .replace(/&/g,'&amp;')
        .replace(/</g,'&lt;')
        .replace(/>/g,'&gt;');
}

/**
 * Quick health check — sends a minimal request, expects a fast response.
 * Use before heavy queries to fail fast if Tally is busy/hung.
 *
 * @param {number} [timeoutMs=3000]
 * @returns {Promise<{ok:boolean, latencyMs:number, reason?:string}>}
 */
async function healthCheck(timeoutMs = 3000) {
    const t0 = Date.now();
    const xml = `<?xml version="1.0" ?>
<ENVELOPE>
  <HEADER>
    <VERSION>1</VERSION>
    <TALLYREQUEST>Export</TALLYREQUEST>
    <TYPE>Data</TYPE>
    <ID>List of Companies</ID>
  </HEADER>
</ENVELOPE>`;
    try {
        await axios.post(TALLY_URL, xml, {
            headers: { 'Content-Type': 'text/xml' },
            timeout: timeoutMs,
        });
        return { ok: true, latencyMs: Date.now() - t0 };
    } catch (err) {
        const code = err?.code || err?.errors?.[0]?.code || '';
        return {
            ok: false,
            latencyMs: Date.now() - t0,
            reason: code === 'ECONNREFUSED' ? 'Tally not running'
                  : code === 'ECONNABORTED' ? 'Tally busy / unresponsive (modal dialog open? heavy query in progress?)'
                  : `${code || 'unknown'}: ${err?.message || ''}`,
        };
    }
}

/**
 * Like fetchCollection, but verifies Tally is responsive first.
 * Throws a clear error instead of hanging for minutes if Tally is busy.
 */
async function fetchCollectionSafe(opts) {
    const h = await healthCheck(3000);
    if (!h.ok) {
        throw new Error(`Tally precheck failed (${h.latencyMs}ms): ${h.reason}. `
            + `Close any open TallyPrime dialogs (Esc), ensure only one client (Electron app OR test) queries Tally at a time, then retry.`);
    }
    return fetchCollection(opts);
}

module.exports = {
    fetchCollection,
    fetchCollectionSafe,
    fetchObject,
    healthCheck,
    unwrap,
    clean,
    toTallyDate,
    detectPeriodShortfall,
    // Exposed for advanced use / testing
    _post,
    _parse,
};
