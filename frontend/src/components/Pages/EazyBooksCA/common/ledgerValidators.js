// Lightweight, non-blocking validators for the Create Ledger form.
// All validators return null on valid / empty input, or a string warning
// message on failure. The UI treats messages as warnings (not blockers) —
// Tally is the final arbiter on whether the value is accepted.

export const PAN_REGEX = /^[A-Z]{5}[0-9]{4}[A-Z]$/;
export const GSTIN_REGEX = /^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$/;
export const IFSC_REGEX = /^[A-Z]{4}0[A-Z0-9]{6}$/;
export const PINCODE_REGEX = /^[1-9][0-9]{5}$/;
export const MOBILE_REGEX = /^[6-9][0-9]{9}$/;            // Indian mobile
export const EMAIL_REGEX = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export const GST_REGISTRATION_TYPES = [
  "Regular",
  "Composition",
  "Composite Dealer",
  "Consumer",
  "Unregistered",
  "Unknown",
];

const empty = (v) => v === undefined || v === null || String(v).trim() === "";

export const validators = {
  name: (v) => (empty(v) ? "Ledger name is required" : null),
  parent_group: (v) => (empty(v) ? "Parent group is required" : null),

  pan: (v) => (empty(v) ? null : (PAN_REGEX.test(v) ? null : "PAN format looks invalid (e.g. AAFCS1234F)")),
  gstin: (v) => (empty(v) ? null : (GSTIN_REGEX.test(v) ? null : "GSTIN format looks invalid")),
  ifsc_code: (v) => (empty(v) ? null : (IFSC_REGEX.test(v) ? null : "IFSC format looks invalid (e.g. HDFC0001234)")),
  pincode: (v) => (empty(v) ? null : (PINCODE_REGEX.test(v) ? null : "PIN should be 6 digits, not starting with 0")),
  mobile: (v) => (empty(v) ? null : (MOBILE_REGEX.test(v) ? null : "Mobile should be 10 digits starting with 6–9")),
  email: (v) => (empty(v) ? null : (EMAIL_REGEX.test(v) ? null : "Email format looks invalid")),
  email_cc: (v) => (empty(v) ? null : (EMAIL_REGEX.test(v) ? null : "CC email format looks invalid")),
  gst_registration_type: (v) =>
    empty(v) || GST_REGISTRATION_TYPES.includes(v)
      ? null
      : `Unknown registration type — Tally typically accepts: ${GST_REGISTRATION_TYPES.join(", ")}`,
};

// Parent-group → ledger-type inference. Used so callers can pass either
// the group name directly or a ledger type code.
export const PARENT_GROUP_BY_TYPE = {
  party_debtor:  "Sundry Debtors",
  party_creditor: "Sundry Creditors",
  sales:         "Sales Accounts",
  purchase:      "Purchase Accounts",
  tax:           "Duties & Taxes",
  bank:          "Bank Accounts",
  cash:          "Cash-in-Hand",
  direct_expense:   "Direct Expenses",
  indirect_expense: "Indirect Expenses",
  direct_income:    "Direct Incomes",
  indirect_income:  "Indirect Incomes",
};

// Section visibility per ledger type. The shared modal uses these flags to
// hide blocks that don't apply (e.g. PAYMENTDETAILS only for bank ledgers).
export const SECTIONS_FOR_TYPE = {
  party:    { statutory: true,  billwise: true,  bank: false, behaviour: false },
  sales:    { statutory: true,  billwise: false, bank: false, behaviour: true  },
  purchase: { statutory: true,  billwise: false, bank: false, behaviour: true  },
  tax:      { statutory: false, billwise: false, bank: false, behaviour: false },
  bank:     { statutory: false, billwise: false, bank: true,  behaviour: false },
  cash:     { statutory: false, billwise: false, bank: false, behaviour: false },
  expense:  { statutory: true,  billwise: false, bank: false, behaviour: true  },
  income:   { statutory: true,  billwise: false, bank: false, behaviour: true  },
  other:    { statutory: true,  billwise: true,  bank: true,  behaviour: true  },
};
