import { qbApi, zohoApi, xeroApi, sapApi } from "../../../../services/api";

/**
 * Per-platform bindings for the shared cloud draft library + bulk pusher.
 *
 * QuickBooks, Zoho and Xero share an identical draft + push model; they differ
 * only in a few names:
 *   - the company-id field stored on a draft row (realm_id / organization_id / tenant_id)
 *   - the native push-payload key inside an extracted result (qbo_payload / zoho_payload / xero_payload)
 *   - the push endpoints + their company param name
 *
 * Each adapter normalizes those so CloudDraftLibrary + cloudPushers stay
 * platform-agnostic. The draft `payload` shapes are the same across platforms:
 *   sales         → { result: { <payloadKey>, extracted_total, ... }, filename, pushResult }
 *   purchase      → { bills: [{ id, filename, status, result: { <payloadKey>, ... }, pushStatus }], activeId }
 *   bank_entries  → { result: { entries: [{ type, <payloadKey>, ... }] }, rowState, accountId }
 */

export const QUICKBOOKS_ADAPTER = {
  id: "quickbooks",
  label: "QuickBooks",
  accent: "#2CA01C",
  companyField: "realm_id",      // field on a draft row identifying its company
  companyNoun: "company",
  payloadKey: "qbo_payload",     // native push payload key inside result/bill/entry
  viewBase: "/dashboard/qb",
  drafts: qbApi.drafts,
  getCompanies: () => qbApi.getCompanies(),
  companyKeyOf: (c) => c.realm_id || c.name,
  pushSales: ({ payload, company }) =>
    qbApi.pushInvoiceQBO({ payload, qb_company: company }),
  pushPurchase: ({ payload, company }) =>
    qbApi.pushBillQBO({ payload, qb_company: company }),
  pushBank: ({ entryType, payload, company }) =>
    qbApi.bankEntries.pushOne({ entryType, payload, qbCompany: company }),
};

export const ZOHO_ADAPTER = {
  id: "zoho",
  label: "Zoho Books",
  accent: "#E42527",
  companyField: "organization_id",
  companyNoun: "organization",
  payloadKey: "zoho_payload",
  viewBase: "/dashboard/zoho",
  drafts: zohoApi.drafts,
  getCompanies: () => zohoApi.getCompanies(),
  companyKeyOf: (c) => c.organization_id,
  pushSales: ({ payload, company, extractedTotal }) =>
    zohoApi.pushInvoiceZoho({ payload, organization_id: company, extracted_total: extractedTotal }),
  pushPurchase: ({ payload, company, extractedTotal }) =>
    zohoApi.pushBillZoho({ payload, organization_id: company, extracted_total: extractedTotal }),
  pushBank: ({ entryType, payload, company }) =>
    zohoApi.bankEntries.pushOne({ entryType, payload, organizationId: company }),
};

export const XERO_ADAPTER = {
  id: "xero",
  label: "Xero",
  accent: "#13B5EA",
  companyField: "tenant_id",
  companyNoun: "organisation",
  payloadKey: "xero_payload",
  viewBase: "/dashboard/xero",
  drafts: xeroApi.drafts,
  getCompanies: () => xeroApi.getCompanies(),
  companyKeyOf: (c) => c.tenant_id,
  pushSales: ({ payload, company, extractedTotal }) =>
    xeroApi.pushInvoiceXero({ payload, tenant_id: company, extracted_total: extractedTotal }),
  pushPurchase: ({ payload, company, extractedTotal }) =>
    xeroApi.pushBillXero({ payload, tenant_id: company, extracted_total: extractedTotal }),
  pushBank: ({ entryType, payload, company }) =>
    xeroApi.bankEntries.pushOne({ entryType, payload, tenantId: company }),
};

export const SAP_ADAPTER = {
  id: "sap",
  label: "SAP S/4HANA",
  accent: "#0FAAFF",
  companyField: "company_code",
  companyNoun: "company code",
  payloadKey: "sap_payload",
  viewBase: "/dashboard/sap",
  drafts: sapApi.drafts,
  getCompanies: () => sapApi.getCompanies(),
  companyKeyOf: (c) => c.company_code,
  pushSales: ({ payload, company, extractedTotal }) =>
    sapApi.pushInvoiceSap({ sap_payload: payload, company_code: company, extracted_total: extractedTotal }),
  pushPurchase: ({ payload, company, extractedTotal }) =>
    sapApi.pushBillSap({ sap_payload: payload, company_code: company, extracted_total: extractedTotal }),
  pushBank: ({ entryType, payload, company }) =>
    sapApi.bankEntries.pushOne({ entryType, payload, companyCode: company }),
};

export const CLOUD_ADAPTERS = {
  quickbooks: QUICKBOOKS_ADAPTER,
  zoho: ZOHO_ADAPTER,
  xero: XERO_ADAPTER,
  sap: SAP_ADAPTER,
};
