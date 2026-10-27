import React, { useState, useMemo, useCallback } from "react";
import { X, ChevronDown, ChevronRight } from "lucide-react";
import {
  validators,
  GST_REGISTRATION_TYPES,
  PARENT_GROUP_BY_TYPE,
  SECTIONS_FOR_TYPE,
} from "./ledgerValidators";

// ── Default field state ──────────────────────────────────────────────
const BLANK_LEDGER = {
  name: "",
  parent_group: "",
  opening_balance: "",
  alias: "",
  mailing_name: "",
  address_lines: ["", "", ""],
  pincode: "",
  state: "",
  country: "India",
  contact_person: "",
  phone: "",
  mobile: "",
  fax: "",
  email: "",
  email_cc: "",
  pan: "",
  gstin: "",
  gst_registration_type: "",
  gst_applicable_from: "",
  bill_wise: undefined,
  bill_credit_period: "",
  credit_limit: "",
  bank_name: "",
  account_number: "",
  ifsc_code: "",
  payment_favouring: "",
  affects_stock: undefined,
  cost_centres_applicable: undefined,
  narration: "",
  description: "",
};

const inputStyle =
  "w-full border border-gilt-hairline rounded-lg px-3 py-2 text-sm text-gilt-ink bg-gilt-paper focus:border-gilt-brass placeholder:text-gilt-faint outline-none transition-colors duration-150";
const labelStyle = "block font-mono text-[10px] uppercase tracking-[0.14em] text-gilt-soft mb-1.5";

// ── Collapsible section ─────────────────────────────────────────────
function Section({ title, defaultOpen = false, children, badge }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="border border-gilt-hairline rounded-lg overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center justify-between px-3 py-2 hover:bg-gilt-sunken transition-colors duration-150 text-left"
      >
        <span className="font-mono text-[10px] font-semibold text-gilt-soft uppercase tracking-[0.14em]">
          {title}
          {badge && (
            <span className="ml-2 text-[10px] font-normal text-gilt-faint">{badge}</span>
          )}
        </span>
        {open ? <ChevronDown className="h-4 w-4 text-gilt-faint" /> : <ChevronRight className="h-4 w-4 text-gilt-faint" />}
      </button>
      {open && <div className="p-3 space-y-3">{children}</div>}
    </div>
  );
}

function FieldWarning({ message }) {
  if (!message) return null;
  return <p className="text-xs text-gilt-debit mt-1">{message}</p>;
}

// ── Component ──────────────────────────────────────────────────────────
/**
 * @param {Object} props
 * @param {boolean} props.open
 * @param {() => void} props.onClose
 * @param {(payload: object) => Promise<void>} props.onCreate
 * @param {boolean} props.busy
 * @param {"party"|"sales"|"purchase"|"tax"|"bank"|"cash"|"expense"|"income"|"other"} [props.ledgerType="other"]
 * @param {string} [props.defaultParentGroup]  override the parent group suggested by ledgerType
 * @param {string[]} [props.parentGroups]      full list of parent groups available in Tally (for the dropdown)
 * @param {Object} [props.initialValues]       prefill (e.g. AI-suggested GSTIN/state from the invoice)
 * @param {string} [props.title]               override the modal title
 */
export default function LedgerCreateModal({
  open,
  onClose,
  onCreate,
  busy = false,
  ledgerType = "other",
  defaultParentGroup,
  parentGroups,
  initialValues,
  title,
}) {
  const sections = SECTIONS_FOR_TYPE[ledgerType] || SECTIONS_FOR_TYPE.other;

  // Default parent group: caller override > type default > "" (forces user pick)
  const computedDefaultParent = useMemo(() => {
    if (defaultParentGroup) return defaultParentGroup;
    if (ledgerType === "party") return "Sundry Debtors";
    if (ledgerType === "sales") return "Sales Accounts";
    if (ledgerType === "purchase") return "Purchase Accounts";
    if (ledgerType === "tax") return "Duties & Taxes";
    if (ledgerType === "bank") return "Bank Accounts";
    if (ledgerType === "cash") return "Cash-in-Hand";
    if (ledgerType === "expense") return "Indirect Expenses";
    if (ledgerType === "income") return "Indirect Incomes";
    return "";
  }, [defaultParentGroup, ledgerType]);

  const [data, setData] = useState(() => ({
    ...BLANK_LEDGER,
    parent_group: computedDefaultParent,
    ...(initialValues || {}),
    address_lines:
      initialValues?.address_lines && initialValues.address_lines.length > 0
        ? initialValues.address_lines
        : BLANK_LEDGER.address_lines,
  }));
  const [touched, setTouched] = useState({});

  const setField = useCallback((field, value) => {
    setData((prev) => ({ ...prev, [field]: value }));
  }, []);

  const setAddressLine = (idx, value) =>
    setData((prev) => {
      const lines = [...prev.address_lines];
      lines[idx] = value;
      return { ...prev, address_lines: lines };
    });

  const warning = (field) => (touched[field] ? validators[field]?.(data[field]) : null);

  const blockingError = validators.name(data.name) || validators.parent_group(data.parent_group);

  const handleSubmit = async () => {
    setTouched({ name: true, parent_group: true });
    if (blockingError) return;
    // Strip empty values from address_lines before sending
    const payload = {
      ...data,
      address_lines: data.address_lines.filter((s) => s && s.trim() !== ""),
    };
    // Don't send the `address` legacy field if we already have lines
    if (payload.address_lines.length > 0) {
      delete payload.address;
    }
    await onCreate(payload);
  };

  if (!open) return null;

  const parentGroupOptions =
    parentGroups && parentGroups.length > 0
      ? parentGroups
      : Array.from(new Set(Object.values(PARENT_GROUP_BY_TYPE)));

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-gilt-ink/40 p-4 overflow-y-auto">
      <div className="bg-gilt-card border border-gilt-hairline rounded-xl w-full max-w-2xl my-8">
        <div className="flex items-center justify-between px-6 py-4 border-b border-gilt-hairline">
          <h3 className="text-lg font-semibold text-gilt-ink">
            {title || `Create New ${capitalize(ledgerType)} Ledger`}
          </h3>
          <button onClick={onClose} className="p-1 hover:bg-gilt-sunken rounded-lg transition-colors">
            <X className="h-5 w-5 text-gilt-soft" />
          </button>
        </div>

        <div className="px-6 py-4 space-y-4 max-h-[70vh] overflow-y-auto">
          {/* ── Identity (always open) ── */}
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <div className="col-span-2">
                <label className={labelStyle}>
                  Ledger Name <span className="text-gilt-debit">*</span>
                </label>
                <input
                  type="text"
                  value={data.name}
                  onChange={(e) => setField("name", e.target.value)}
                  onBlur={() => setTouched((t) => ({ ...t, name: true }))}
                  className={inputStyle}
                  placeholder="e.g. Sharma Traders"
                  autoFocus
                />
                <FieldWarning message={touched.name ? validators.name(data.name) : null} />
              </div>

              <div>
                <label className={labelStyle}>
                  Parent Group <span className="text-gilt-debit">*</span>
                </label>
                <select
                  value={data.parent_group}
                  onChange={(e) => setField("parent_group", e.target.value)}
                  className={inputStyle}
                >
                  <option value="">Select group…</option>
                  {parentGroupOptions.map((g) => (
                    <option key={g} value={g}>
                      {g}
                    </option>
                  ))}
                </select>
                <FieldWarning message={touched.parent_group ? validators.parent_group(data.parent_group) : null} />
              </div>

              <div>
                <label className={labelStyle}>Opening Balance</label>
                <input
                  type="number"
                  value={data.opening_balance}
                  onChange={(e) => setField("opening_balance", e.target.value)}
                  className={inputStyle}
                  placeholder="0.00"
                  step="any"
                />
                <p className="text-[10px] text-gilt-faint mt-1">Positive = Dr, negative = Cr</p>
              </div>

              <div>
                <label className={labelStyle}>Alias / Additional Name</label>
                <input
                  type="text"
                  value={data.alias}
                  onChange={(e) => setField("alias", e.target.value)}
                  className={inputStyle}
                  placeholder="e.g. Sharma & Co."
                />
              </div>

              <div>
                <label className={labelStyle}>Mailing Name</label>
                <input
                  type="text"
                  value={data.mailing_name}
                  onChange={(e) => setField("mailing_name", e.target.value)}
                  className={inputStyle}
                  placeholder="Name printed on invoices"
                />
              </div>
            </div>
          </div>

          {/* ── Address & Contact ── */}
          <Section title="Address & Contact" defaultOpen={false}>
            <div>
              <label className={labelStyle}>Address (up to 3 lines)</label>
              {data.address_lines.map((line, idx) => (
                <input
                  key={idx}
                  type="text"
                  value={line}
                  onChange={(e) => setAddressLine(idx, e.target.value)}
                  className={`${inputStyle} ${idx > 0 ? "mt-1.5" : ""}`}
                  placeholder={idx === 0 ? "Street / building" : idx === 1 ? "Area / locality" : "City"}
                />
              ))}
            </div>
            <div className="grid grid-cols-3 gap-3">
              <div>
                <label className={labelStyle}>PIN code</label>
                <input
                  type="text"
                  value={data.pincode}
                  onChange={(e) => setField("pincode", e.target.value)}
                  onBlur={() => setTouched((t) => ({ ...t, pincode: true }))}
                  className={inputStyle}
                  maxLength={6}
                  placeholder="560038"
                />
                <FieldWarning message={warning("pincode")} />
              </div>
              <div>
                <label className={labelStyle}>State</label>
                <input
                  type="text"
                  value={data.state}
                  onChange={(e) => setField("state", e.target.value)}
                  className={inputStyle}
                  placeholder="Karnataka"
                />
              </div>
              <div>
                <label className={labelStyle}>Country</label>
                <input
                  type="text"
                  value={data.country}
                  onChange={(e) => setField("country", e.target.value)}
                  className={inputStyle}
                  placeholder="India"
                />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className={labelStyle}>Contact Person</label>
                <input
                  type="text"
                  value={data.contact_person}
                  onChange={(e) => setField("contact_person", e.target.value)}
                  className={inputStyle}
                  placeholder="Name"
                />
              </div>
              <div>
                <label className={labelStyle}>Phone</label>
                <input
                  type="text"
                  value={data.phone}
                  onChange={(e) => setField("phone", e.target.value)}
                  className={inputStyle}
                  placeholder="Landline"
                />
              </div>
              <div>
                <label className={labelStyle}>Mobile</label>
                <input
                  type="text"
                  value={data.mobile}
                  onChange={(e) => setField("mobile", e.target.value)}
                  onBlur={() => setTouched((t) => ({ ...t, mobile: true }))}
                  className={inputStyle}
                  maxLength={10}
                  placeholder="9876543210"
                />
                <FieldWarning message={warning("mobile")} />
              </div>
              <div>
                <label className={labelStyle}>Fax</label>
                <input
                  type="text"
                  value={data.fax}
                  onChange={(e) => setField("fax", e.target.value)}
                  className={inputStyle}
                />
              </div>
              <div>
                <label className={labelStyle}>Email</label>
                <input
                  type="email"
                  value={data.email}
                  onChange={(e) => setField("email", e.target.value)}
                  onBlur={() => setTouched((t) => ({ ...t, email: true }))}
                  className={inputStyle}
                  placeholder="name@company.com"
                />
                <FieldWarning message={warning("email")} />
              </div>
              <div>
                <label className={labelStyle}>Email CC</label>
                <input
                  type="email"
                  value={data.email_cc}
                  onChange={(e) => setField("email_cc", e.target.value)}
                  onBlur={() => setTouched((t) => ({ ...t, email_cc: true }))}
                  className={inputStyle}
                />
                <FieldWarning message={warning("email_cc")} />
              </div>
            </div>
          </Section>

          {/* ── Statutory (PAN, GST) ── */}
          {sections.statutory && (
            <Section title="Statutory — PAN & GST" defaultOpen={ledgerType === "party"}>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className={labelStyle}>PAN</label>
                  <input
                    type="text"
                    value={data.pan}
                    onChange={(e) => setField("pan", e.target.value.toUpperCase())}
                    onBlur={() => setTouched((t) => ({ ...t, pan: true }))}
                    className={inputStyle}
                    maxLength={10}
                    placeholder="AAFCS1234F"
                  />
                  <FieldWarning message={warning("pan")} />
                </div>
                <div>
                  <label className={labelStyle}>GSTIN</label>
                  <input
                    type="text"
                    value={data.gstin}
                    onChange={(e) => setField("gstin", e.target.value.toUpperCase())}
                    onBlur={() => setTouched((t) => ({ ...t, gstin: true }))}
                    className={inputStyle}
                    maxLength={15}
                    placeholder="29AAFCG1234A1ZF"
                  />
                  <FieldWarning message={warning("gstin")} />
                </div>
                <div>
                  <label className={labelStyle}>GST Registration Type</label>
                  <select
                    value={data.gst_registration_type}
                    onChange={(e) => setField("gst_registration_type", e.target.value)}
                    className={inputStyle}
                  >
                    <option value="">Not specified</option>
                    {GST_REGISTRATION_TYPES.map((t) => (
                      <option key={t} value={t}>
                        {t}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className={labelStyle}>GST applicable from</label>
                  <input
                    type="date"
                    value={data.gst_applicable_from ? toDateInput(data.gst_applicable_from) : ""}
                    onChange={(e) => setField("gst_applicable_from", e.target.value ? e.target.value.replace(/-/g, "") : "")}
                    className={inputStyle}
                  />
                </div>
              </div>
            </Section>
          )}

          {/* ── Bill-wise & Credit (party only) ── */}
          {sections.billwise && (
            <Section title="Bill-wise & Credit" defaultOpen={false}>
              <div className="flex items-center gap-2">
                <input
                  type="checkbox"
                  id="bill_wise"
                  checked={data.bill_wise === true}
                  onChange={(e) => setField("bill_wise", e.target.checked ? true : false)}
                  className="rounded"
                  style={{ accentColor: "rgb(var(--gl-brass))" }}
                />
                <label htmlFor="bill_wise" className="text-sm text-gilt-soft">
                  Maintain balances bill-by-bill
                </label>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className={labelStyle}>Default credit period</label>
                  <input
                    type="text"
                    value={data.bill_credit_period}
                    onChange={(e) => setField("bill_credit_period", e.target.value)}
                    className={inputStyle}
                    placeholder="30 Days"
                  />
                  <p className="text-[10px] text-gilt-faint mt-1">e.g. "30 Days", "1 Month"</p>
                </div>
                <div>
                  <label className={labelStyle}>Credit limit</label>
                  <input
                    type="number"
                    value={data.credit_limit}
                    onChange={(e) => setField("credit_limit", e.target.value)}
                    className={inputStyle}
                    placeholder="0"
                    step="any"
                  />
                </div>
              </div>
            </Section>
          )}

          {/* ── Bank details (bank only) ── */}
          {sections.bank && (
            <Section title="Bank Account Details" defaultOpen={ledgerType === "bank"}>
              <div className="grid grid-cols-2 gap-3">
                <div className="col-span-2">
                  <label className={labelStyle}>Bank Name</label>
                  <input
                    type="text"
                    value={data.bank_name}
                    onChange={(e) => setField("bank_name", e.target.value)}
                    className={inputStyle}
                    placeholder="HDFC Bank"
                  />
                </div>
                <div>
                  <label className={labelStyle}>Account Number</label>
                  <input
                    type="text"
                    value={data.account_number}
                    onChange={(e) => setField("account_number", e.target.value)}
                    className={inputStyle}
                    placeholder="50100123456789"
                  />
                </div>
                <div>
                  <label className={labelStyle}>IFSC</label>
                  <input
                    type="text"
                    value={data.ifsc_code}
                    onChange={(e) => setField("ifsc_code", e.target.value.toUpperCase())}
                    onBlur={() => setTouched((t) => ({ ...t, ifsc_code: true }))}
                    className={inputStyle}
                    maxLength={11}
                    placeholder="HDFC0001234"
                  />
                  <FieldWarning message={warning("ifsc_code")} />
                </div>
                <div className="col-span-2">
                  <label className={labelStyle}>Cheques favouring</label>
                  <input
                    type="text"
                    value={data.payment_favouring}
                    onChange={(e) => setField("payment_favouring", e.target.value)}
                    className={inputStyle}
                    placeholder="Beneficiary name on cheques"
                  />
                </div>
              </div>
            </Section>
          )}

          {/* ── Behaviour flags ── */}
          {sections.behaviour && (
            <Section title="Behaviour" defaultOpen={false}>
              <div className="flex items-center gap-2">
                <input
                  type="checkbox"
                  id="affects_stock"
                  checked={data.affects_stock === true}
                  onChange={(e) => setField("affects_stock", e.target.checked)}
                  className="rounded"
                  style={{ accentColor: "rgb(var(--gl-brass))" }}
                />
                <label htmlFor="affects_stock" className="text-sm text-gilt-soft">
                  Inventory values are affected
                </label>
              </div>
              <div className="flex items-center gap-2">
                <input
                  type="checkbox"
                  id="cost_centres_applicable"
                  checked={data.cost_centres_applicable === true}
                  onChange={(e) => setField("cost_centres_applicable", e.target.checked)}
                  className="rounded"
                  style={{ accentColor: "rgb(var(--gl-brass))" }}
                />
                <label htmlFor="cost_centres_applicable" className="text-sm text-gilt-soft">
                  Cost centres applicable
                </label>
              </div>
            </Section>
          )}

          {/* ── Notes ── */}
          <Section title="Notes" defaultOpen={false}>
            <div>
              <label className={labelStyle}>Description</label>
              <input
                type="text"
                value={data.description}
                onChange={(e) => setField("description", e.target.value)}
                className={inputStyle}
                placeholder="Internal note"
              />
            </div>
            <div>
              <label className={labelStyle}>Narration</label>
              <textarea
                value={data.narration}
                onChange={(e) => setField("narration", e.target.value)}
                className={`${inputStyle} resize-none`}
                rows={2}
                placeholder="Default narration on vouchers using this ledger"
              />
            </div>
          </Section>
        </div>

        <div className="flex justify-end gap-2 px-6 py-4 border-t border-gilt-hairline bg-gilt-sunken rounded-b-xl">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm text-gilt-soft bg-gilt-card border border-gilt-hairline rounded-md hover:border-gilt-brass hover:text-gilt-brass transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={busy || !!blockingError}
            className="px-4 py-2 text-sm font-medium text-gilt-paper bg-gilt-brass rounded-md hover:bg-gilt-brass/90 disabled:opacity-50 transition-colors"
          >
            {busy ? "Creating…" : "Create Ledger"}
          </button>
        </div>
      </div>
    </div>
  );
}

function capitalize(s) {
  if (!s) return "";
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function toDateInput(yyyymmdd) {
  if (!yyyymmdd || yyyymmdd.length !== 8) return "";
  return `${yyyymmdd.slice(0, 4)}-${yyyymmdd.slice(4, 6)}-${yyyymmdd.slice(6, 8)}`;
}
