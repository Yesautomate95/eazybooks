import { tallyApi } from "../../../../services/api";

// Shared push helpers used by both the in-view "Push" buttons and the
// library's bulk-push action. Each pusher takes a hydrated draft (the shape
// returned by tallyApi.drafts.get) and returns:
//   { ok, pushed, failed, total, errors: [{ index, message }] }

const toTallyDate = (value) => {
  if (!value) return "";
  // Accept YYYY-MM-DD or DD-MM-YYYY; emit YYYYMMDD as Tally expects.
  const trimmed = String(value).trim();
  if (/^\d{8}$/.test(trimmed)) return trimmed;
  const dashIso = trimmed.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (dashIso) return `${dashIso[1]}${dashIso[2]}${dashIso[3]}`;
  const dashDmy = trimmed.match(/^(\d{2})-(\d{2})-(\d{4})$/);
  if (dashDmy) return `${dashDmy[3]}${dashDmy[2]}${dashDmy[1]}`;
  return trimmed.replace(/-/g, "");
};

const num = (v) => {
  const n = parseFloat(v);
  return Number.isFinite(n) ? n : 0;
};

const tallyError = (response) => {
  if (response?.success === false) {
    return response.message || response.error || "Push failed";
  }
  if (response?.data?.error) {
    const err = response.data.error;
    return typeof err === "string" ? err : JSON.stringify(err);
  }
  const exc = response?.data?.exception ?? response?.exception ?? 0;
  if (exc > 0) {
    return (
      response?.data?.line_error ||
      response?.line_error ||
      response?.data?.LINEERROR ||
      response?.LINEERROR ||
      response?.data?.description ||
      response?.description ||
      "Tally reported an exception."
    );
  }
  return null;
};

// Resolve a line item's discount as an (amount, percentage) pair. Accepts
// either field name. Amount wins when present; percentage is the fallback.
const resolveDiscount = (it, q, r) => {
  const gross = Math.max(q * r, 0);
  let amount = num(it.discount_amount);
  if (!isFinite(amount) || amount <= 0) {
    const pct = num(it.discount_percent || it.discount_percentage);
    amount = gross > 0 && pct > 0 ? Math.round((gross * pct / 100) * 100) / 100 : 0;
  }
  if (amount < 0) amount = 0;
  if (gross > 0 && amount > gross) amount = gross;
  const percentage = gross > 0 ? Math.round((amount / gross) * 10000) / 100 : 0;
  return { amount, percentage };
};

const buildSalesPayload = (inv, company) => {
  const data = inv.data || {};
  const header = data.header || {};
  const items = data.items || [];
  const taxes = data.taxes || [];
  const subtotal = items.reduce((s, it) => {
    const q = num(it.quantity);
    const r = num(it.rate);
    const { amount } = resolveDiscount(it, q, r);
    return s + Math.max(q * r - amount, 0);
  }, 0);
  const taxAmount = taxes.reduce((s, t) => s + num(t.amount), 0);
  const total = subtotal + taxAmount;
  const tdsLedger = inv.tds_ledger || "";
  const tdsPct = num(inv.tds_percent);
  const tdsAmount =
    tdsLedger && tdsPct > 0
      ? Math.round((subtotal * tdsPct) / 100 * 100) / 100
      : 0;
  const discountTotal = items.reduce((s, it) => {
    const q = num(it.quantity);
    const r = num(it.rate);
    const { amount } = resolveDiscount(it, q, r);
    return s + amount;
  }, 0);
  const discountLedger = inv.selectedDiscountLedger || "";
  return {
    header: {
      invoice_type: "Sales",
      voucher_date: toTallyDate(header.voucher_date),
      invoice_number: header.invoice_number || "",
      party_name: header.party_name || "",
      party_gstin: header.party_gstin || "",
      place_of_supply: header.place_of_supply || "",
    },
    items: items.map((it, idx) => {
      const q = num(it.quantity);
      const r = num(it.rate);
      const { amount: discAmount, percentage: discPct } = resolveDiscount(it, q, r);
      return {
        sr_no: idx + 1,
        item_name: it.item_name,
        quantity: q,
        rate: r,
        amount: num(it.amount),
        unit: it.unit || "Nos",
        discount_amount: discAmount,
        discount_percentage: discPct,
        batch_number: it.batch || "",
      };
    }),
    taxes: taxes.map((t, idx) => ({
      sr_no: idx + 1,
      ledger_name: t.ledger_name,
      description: t.description || "",
      amount: num(t.amount),
    })),
    summary: {
      subtotal,
      tax_amount: taxAmount,
      total_amount: total,
      narration: header.narration || data.summary?.narration || "",
    },
    company,
    sales_ledger: inv.selectedSalesLedger || "Sales",
    tds_ledger: tdsAmount > 0 ? tdsLedger : "",
    tds_amount: tdsAmount,
    discount_ledger: discountTotal > 0 ? discountLedger : "",
    discount_amount: Math.round(discountTotal * 100) / 100,
  };
};

const buildPurchasePayload = (inv) => {
  const data = inv.data || {};
  const header = data.header || {};
  const items = data.items || [];
  const taxes = data.taxes || [];
  const subtotal = items.reduce((s, it) => {
    const q = num(it.quantity);
    const r = num(it.rate);
    const { amount } = resolveDiscount(it, q, r);
    return s + Math.max(q * r - amount, 0);
  }, 0);
  const taxAmount = taxes.reduce((s, t) => s + num(t.amount), 0);
  const total = subtotal + taxAmount;
  const tdsLedger = inv.tds_ledger || "";
  const tdsPct = num(inv.tds_percent);
  const tdsAmount =
    tdsLedger && tdsPct > 0
      ? Math.round((subtotal * tdsPct) / 100 * 100) / 100
      : 0;
  const discountTotal = items.reduce(
    (s, it) => s + resolveDiscount(it, num(it.quantity), num(it.rate)).amount,
    0,
  );
  return {
    invoice_date: toTallyDate(header.voucher_date),
    vendor_ledger: header.party_name || "",
    purchase_ledger: inv.selectedPurchaseLedger || "Purchase",
    description: header.narration || data.summary?.narration || "",
    invoice_number: header.invoice_number || "",
    place_of_supply: header.place_of_supply || "",
    subtotal,
    amount: subtotal,
    gst_percentage: 0,
    gst_amount: taxAmount,
    tax_amount: taxAmount,
    tax_entries: taxes.map((t) => ({
      ledger_name: t.ledger_name,
      amount: num(t.amount),
    })),
    total_amount: total,
    tds_ledger: tdsAmount > 0 ? tdsLedger : "",
    tds_amount: tdsAmount,
    discount_ledger: discountTotal > 0 ? (inv.selectedDiscountLedger || "") : "",
    discount_amount: Math.round(discountTotal * 100) / 100,
  };
};

const pushOneInvoice = async ({ flow, company, invoice }) => {
  try {
    let xmlResponse;
    if (flow === "sales") {
      xmlResponse = await tallyApi.generateSalesVoucherXml(
        buildSalesPayload(invoice, company)
      );
    } else {
      xmlResponse = await tallyApi.generatePurchaseXml({
        invoices: [buildPurchasePayload(invoice)],
        company,
      });
    }
    if (xmlResponse?.success === false) {
      const errors = xmlResponse.validation_errors || [];
      return {
        ok: false,
        message: `XML generation failed: ${
          errors.join(", ") || xmlResponse.message || "Unknown error"
        }`,
      };
    }
    const xml = xmlResponse?.xml || xmlResponse?.data?.xml;
    if (!xml) return { ok: false, message: "No XML returned from backend" };
    const pushResponse = await tallyApi.pushToTally({ xml, company });
    const err = tallyError(pushResponse);
    if (err) return { ok: false, message: err };
    return { ok: true };
  } catch (err) {
    const msg =
      err?.response?.data?.detail ||
      err?.response?.data?.error ||
      err?.message ||
      "Failed to push to Tally";
    return { ok: false, message: msg };
  }
};

export const pushSalesDraft = async (draft) => {
  const company = draft?.company_name || "";
  const invoices = draft?.payload?.invoices || [];
  return runInvoiceLoop({ flow: "sales", company, invoices });
};

export const pushPurchaseDraft = async (draft) => {
  const company = draft?.company_name || "";
  const invoices = draft?.payload?.invoices || [];
  return runInvoiceLoop({ flow: "purchase", company, invoices });
};

const runInvoiceLoop = async ({ flow, company, invoices }) => {
  const errors = [];
  let pushed = 0;
  for (let i = 0; i < invoices.length; i += 1) {
    const result = await pushOneInvoice({ flow, company, invoice: invoices[i] });
    if (result.ok) pushed += 1;
    else errors.push({ index: i, message: result.message });
  }
  return {
    ok: errors.length === 0 && pushed > 0,
    pushed,
    failed: errors.length,
    total: invoices.length,
    errors,
  };
};

export const pushBankEntriesDraft = async (draft) => {
  const company = draft?.company_name || "";
  const payload = draft?.payload || {};
  const transactions = (payload.transactions || []).filter((t) => !t.isSkipped);
  const bankLedger =
    payload.bank_ledger ||
    transactions.find((t) => t.bank_ledger)?.bank_ledger ||
    "";
  if (!bankLedger) {
    return {
      ok: false,
      pushed: 0,
      failed: transactions.length,
      total: transactions.length,
      errors: [{ index: -1, message: "Bank ledger missing from draft" }],
    };
  }
  const vouchers = transactions
    .filter((t) => t.partyLedger && t.voucherType && t.date)
    .map((t) => ({
      date: t.date,
      voucher_type: t.voucherType,
      narration: t.narration || "",
      amount: Math.abs(num(t.amount)),
      party_ledger: t.partyLedger,
      bank_ledger: bankLedger,
      reference: t.reference || "",
      voucher_number: t.voucherNumber || "",
    }));
  if (vouchers.length === 0) {
    return {
      ok: false,
      pushed: 0,
      failed: transactions.length,
      total: transactions.length,
      errors: [{ index: -1, message: "No transactions ready to push (missing ledger/type/date)" }],
    };
  }
  try {
    const xmlResponse = await tallyApi.generateBankXml({
      company,
      bank_ledger: bankLedger,
      vouchers,
    });
    if (!xmlResponse?.xml) {
      return {
        ok: false,
        pushed: 0,
        failed: vouchers.length,
        total: vouchers.length,
        errors: [{ index: -1, message: "Failed to generate XML" }],
      };
    }
    const pushResponse = await tallyApi.pushToTally({
      company,
      xml: xmlResponse.xml,
    });
    const err = tallyError(pushResponse);
    if (err) {
      return {
        ok: false,
        pushed: 0,
        failed: vouchers.length,
        total: vouchers.length,
        errors: [{ index: -1, message: err }],
      };
    }
    return { ok: true, pushed: vouchers.length, failed: 0, total: vouchers.length, errors: [] };
  } catch (err) {
    const msg =
      err?.response?.data?.detail ||
      err?.response?.data?.error ||
      err?.message ||
      "Failed to push to Tally";
    return {
      ok: false,
      pushed: 0,
      failed: vouchers.length,
      total: vouchers.length,
      errors: [{ index: -1, message: msg }],
    };
  }
};

// Single entry point used by the library bulk-push. Routes by flow.
export const pushDraft = async (draft) => {
  switch (draft?.flow) {
    case "sales":
      return pushSalesDraft(draft);
    case "purchase":
      return pushPurchaseDraft(draft);
    case "bank_entries":
      return pushBankEntriesDraft(draft);
    default:
      return {
        ok: false,
        pushed: 0,
        failed: 0,
        total: 0,
        errors: [{ index: -1, message: `Unsupported flow: ${draft?.flow}` }],
      };
  }
};
