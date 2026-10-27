import React, { useEffect, useMemo, useState } from "react";
import { Plus, ChevronDown, ChevronUp } from "lucide-react";
import { zohoApi } from "../../../../../services/api";
import ErrorMessage from "./ErrorMessage";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "../../../../ui/dialog";
import { Button } from "../../../../ui/button";
import { Input, Select, Label } from "../../../../ui/input";

/**
 * CreateMasterModal — Tier-1 inline-create modal for Zoho Books master data.
 *
 *   entityType:     'customer' | 'vendor' | 'item' | 'account'
 *   prefillName:    AI-extracted name to seed the form (user can edit)
 *   organizationId: Zoho org id, threaded to the create endpoint
 *
 * On success calls `onCreated({list_id, name})` so the caller can:
 *   1. Patch its zoho_payload to reference the new entity
 *   2. Append the entity to its in-memory master list
 *   3. Mark the corresponding row as matched
 *
 * For vendor / customer we expose the full Zoho contact schema behind a "More
 * details" toggle — contact_name alone still works (Tier-1 quick path), but the
 * CA can also capture the primary contact person, channels, GST/PAN and a
 * billing address directly at create time so contacts don't need an immediate
 * Zoho edit pass. The Zoho twin of QB/components/CreateMasterModal.jsx, mapped
 * onto Zoho's contact schema (contact_persons[] for name parts, top-level
 * phone/mobile/website/gst_no/pan_no, billing_address{}).
 */

const emptyPartyFields = () => ({
  salutation: "",
  firstName: "",
  lastName: "",
  companyName: "",
  phone: "",
  mobile: "",
  website: "",
  gstNo: "",
  panNo: "",
  addrAttention: "",
  addrAddress: "",
  addrStreet2: "",
  addrCity: "",
  addrState: "",
  addrZip: "",
  addrCountry: "",
});

const ZOHO_ACCOUNT_TYPE_ALIASES = {
  Expense: "expense",
  Income: "income",
  CostOfGoodsSold: "cost_of_goods_sold",
  OtherExpense: "other_expense",
  OtherIncome: "other_income",
  Bank: "bank",
};

const normalizeZohoAccountType = (value) =>
  ZOHO_ACCOUNT_TYPE_ALIASES[value] || value || "expense";

export default function CreateMasterModal({
  open,
  entityType,
  prefillName = "",
  accountType: initialAccountType = "expense",
  organizationId,
  onClose,
  onCreated,
}) {
  const [name, setName] = useState(prefillName);
  const [email, setEmail] = useState("");
  const [accountType, setAccountType] = useState(normalizeZohoAccountType(initialAccountType));
  const [party, setParty] = useState(emptyPartyFields);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const isParty = entityType === "customer" || entityType === "vendor";

  // Re-sync the prefill whenever a new row triggers the modal — otherwise the
  // old name lingers and the CA edits the wrong entity.
  useEffect(() => {
    if (open) {
      setName(prefillName);
      setEmail("");
      setAccountType(normalizeZohoAccountType(initialAccountType));
      setParty(emptyPartyFields());
      setShowAdvanced(false);
      setError("");
      setBusy(false);
    }
  }, [open, prefillName, initialAccountType]);

  // Build the optional-fields payload merged onto the base
  // {name, email, organization_id} body for vendor/customer creates. Empty
  // strings are dropped here (and server-side) so the request stays compact and
  // Zoho never sees an empty field (which it treats as "set to empty").
  const partyExtras = useMemo(() => {
    if (!isParty) return {};
    const extras = {};
    const trim = (s) => (s || "").trim();
    const setIf = (key, val) => {
      const v = trim(val);
      if (v) extras[key] = v;
    };
    setIf("salutation", party.salutation);
    setIf("first_name", party.firstName);
    setIf("last_name", party.lastName);
    setIf("company_name", party.companyName);
    setIf("phone", party.phone);
    setIf("mobile", party.mobile);
    setIf("website", party.website);
    setIf("gst_no", party.gstNo);
    setIf("pan_no", party.panNo);
    const addr = {};
    if (trim(party.addrAttention)) addr.attention = trim(party.addrAttention);
    if (trim(party.addrAddress)) addr.address = trim(party.addrAddress);
    if (trim(party.addrStreet2)) addr.street2 = trim(party.addrStreet2);
    if (trim(party.addrCity)) addr.city = trim(party.addrCity);
    if (trim(party.addrState)) addr.state = trim(party.addrState);
    if (trim(party.addrZip)) addr.zip = trim(party.addrZip);
    if (trim(party.addrCountry)) addr.country = trim(party.addrCountry);
    if (Object.keys(addr).length > 0) extras.billing_address = addr;
    return extras;
  }, [isParty, party]);

  const setPartyField = (key) => (e) =>
    setParty((p) => ({ ...p, [key]: e.target.value }));

  const onSubmit = async (e) => {
    e?.preventDefault?.();
    if (!name.trim()) {
      setError("Name is required.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const trimmed = name.trim();
      let created = null;
      if (entityType === "customer") {
        const res = await zohoApi.createCustomer({
          name: trimmed,
          email: email.trim() || undefined,
          organization_id: organizationId,
          ...partyExtras,
        });
        const c = res.customer || res;
        created = {
          list_id: String(c.contact_id ?? c.list_id ?? c.Id),
          name: c.contact_name || c.name || trimmed,
        };
      } else if (entityType === "vendor") {
        const res = await zohoApi.createVendor({
          name: trimmed,
          email: email.trim() || undefined,
          organization_id: organizationId,
          ...partyExtras,
        });
        const v = res.vendor || res;
        created = {
          list_id: String(v.contact_id ?? v.list_id ?? v.Id),
          name: v.contact_name || v.name || trimmed,
        };
      } else if (entityType === "item") {
        const res = await zohoApi.createItem({
          name: trimmed,
          organization_id: organizationId,
        });
        const it = res.item || res;
        created = {
          list_id: String(it.item_id ?? it.list_id ?? it.Id),
          name: it.name || it.Name || trimmed,
        };
      } else if (entityType === "account") {
        const res = await zohoApi.createAccount({
          name: trimmed,
          account_type: normalizeZohoAccountType(accountType),
          organization_id: organizationId,
        });
        const a = res.account || res;
        created = {
          list_id: String(a.account_id ?? a.list_id ?? a.Id),
          name: a.account_name || a.name || trimmed,
          account_type: a.account_type || normalizeZohoAccountType(accountType),
        };
      } else {
        throw new Error(`Unsupported entityType: ${entityType}`);
      }
      onCreated?.(created);
      onClose?.();
    } catch (err) {
      setError(err);  // Store the raw error; ErrorMessage extracts structure.
    } finally {
      setBusy(false);
    }
  };

  const titleByType = {
    customer: "Create new customer",
    vendor: "Create new vendor",
    item: "Create new service item",
    account: "Create new account",
  };

  // Larger dialog for the rich party form so the address row doesn't squeeze.
  const dialogSize = isParty ? "lg" : "sm";

  return (
    <Dialog open={open} onOpenChange={(v) => !v && !busy && onClose?.()}>
      <DialogContent size={dialogSize} showClose={!busy}>
        <DialogHeader>
          <DialogTitle>
            <span className="inline-flex items-center gap-2">
              <Plus className="h-4 w-4 text-gilt-brass" />
              {titleByType[entityType] || "Create"}
            </span>
          </DialogTitle>
        </DialogHeader>
        <form onSubmit={onSubmit} className="px-6 py-3 space-y-4 max-h-[70vh] overflow-y-auto">
          <div>
            <Label required={isParty}>{isParty ? "Display name" : "Name"}</Label>
            <Input
              autoFocus
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. AGC ASIA PACIFIC INDIA PVT LTD"
            />
            <p className="text-2xs text-gilt-soft mt-1.5 leading-snug">
              Tip: edit before saving if the AI mis-spelled it — Zoho Books treats this as the
              canonical name.
            </p>
          </div>

          {isParty && (
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label>Email <span className="text-gilt-faint font-normal">(optional)</span></Label>
                <Input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="finance@example.com"
                />
              </div>
              <div>
                <Label>Phone <span className="text-gilt-faint font-normal">(optional)</span></Label>
                <Input
                  type="tel"
                  value={party.phone}
                  onChange={setPartyField("phone")}
                  placeholder="+91 80 1234 5678"
                />
              </div>
            </div>
          )}

          {entityType === "account" && (
            <div>
              <Label>Account type</Label>
              <Select value={accountType} onChange={(e) => setAccountType(e.target.value)}>
                <option value="expense">Expense</option>
                <option value="income">Income</option>
                <option value="cost_of_goods_sold">Cost of Goods Sold</option>
                <option value="other_expense">Other Expense</option>
                <option value="other_income">Other Income</option>
                <option value="bank">Bank</option>
              </Select>
            </div>
          )}

          {entityType === "item" && (
            <p className="text-xs text-gilt-soft bg-gilt-sunken border border-gilt-hairline rounded-lg p-3 leading-relaxed">
              Created as a Service item with your first Income account as the revenue bucket.
              Change the income account in Zoho Books if you need a different one.
            </p>
          )}

          {isParty && (
            <div className="border-t border-gilt-hairline pt-3">
              <button
                type="button"
                onClick={() => setShowAdvanced((v) => !v)}
                className="inline-flex items-center gap-1.5 text-xs font-medium text-gilt-brass hover:underline"
              >
                {showAdvanced ? (
                  <>
                    <ChevronUp className="h-3.5 w-3.5" />
                    Hide additional details
                  </>
                ) : (
                  <>
                    <ChevronDown className="h-3.5 w-3.5" />
                    Add more details (contact person, address, GST/PAN…)
                  </>
                )}
              </button>

              {showAdvanced && (
                <div className="mt-4 space-y-5">
                  <PartySection title="Primary contact">
                    <div className="grid grid-cols-3 gap-2">
                      <FieldShort label="Salutation" value={party.salutation} onChange={setPartyField("salutation")} placeholder="Ms." />
                      <FieldShort label="First name" value={party.firstName} onChange={setPartyField("firstName")} placeholder="Dianne" />
                      <FieldShort label="Last name" value={party.lastName} onChange={setPartyField("lastName")} placeholder="Bradley" />
                    </div>
                  </PartySection>

                  <PartySection title="Contact channels">
                    <div className="grid grid-cols-2 gap-3">
                      <div>
                        <Label>Mobile</Label>
                        <Input type="tel" value={party.mobile} onChange={setPartyField("mobile")} placeholder="+91 98765 43210" />
                      </div>
                      <div>
                        <Label>Website</Label>
                        <Input type="url" value={party.website} onChange={setPartyField("website")} placeholder="https://example.com" />
                      </div>
                    </div>
                  </PartySection>

                  <PartySection title="Billing address">
                    <div className="space-y-3">
                      <div>
                        <Label>Attention</Label>
                        <Input value={party.addrAttention} onChange={setPartyField("addrAttention")} placeholder="Contact person / department" />
                      </div>
                      <div>
                        <Label>Address</Label>
                        <Input value={party.addrAddress} onChange={setPartyField("addrAddress")} placeholder="Street address" />
                      </div>
                      <div>
                        <Label>Street 2</Label>
                        <Input value={party.addrStreet2} onChange={setPartyField("addrStreet2")} placeholder="Suite / floor / landmark" />
                      </div>
                      <div className="grid grid-cols-3 gap-2">
                        <div>
                          <Label>City</Label>
                          <Input value={party.addrCity} onChange={setPartyField("addrCity")} placeholder="Bengaluru" />
                        </div>
                        <div>
                          <Label>State</Label>
                          <Input value={party.addrState} onChange={setPartyField("addrState")} placeholder="Karnataka" />
                        </div>
                        <div>
                          <Label>ZIP / PIN</Label>
                          <Input value={party.addrZip} onChange={setPartyField("addrZip")} placeholder="560001" />
                        </div>
                      </div>
                      <div>
                        <Label>Country</Label>
                        <Input value={party.addrCountry} onChange={setPartyField("addrCountry")} placeholder="India" />
                      </div>
                    </div>
                  </PartySection>

                  <PartySection title="Tax details">
                    <div className="grid grid-cols-2 gap-3">
                      <div className="col-span-2">
                        <Label>Company name</Label>
                        <Input value={party.companyName} onChange={setPartyField("companyName")} placeholder="Dianne's Auto Shop Pvt Ltd" />
                      </div>
                      <div>
                        <Label>GSTIN</Label>
                        <Input value={party.gstNo} onChange={setPartyField("gstNo")} placeholder="29ABCDE1234F1Z5" />
                      </div>
                      <div>
                        <Label>PAN</Label>
                        <Input value={party.panNo} onChange={setPartyField("panNo")} placeholder="ABCDE1234F" />
                      </div>
                    </div>
                  </PartySection>
                </div>
              )}
            </div>
          )}

          {error && (
            <ErrorMessage error={error} prefix={`Could not ${({
              customer: "create customer",
              vendor: "create vendor",
              item: "create item",
              account: "create account",
            })[entityType] || "create"}`} />
          )}
        </form>
        <DialogFooter>
          <Button variant="secondary" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button
            variant="primary"
            onClick={onSubmit}
            disabled={!name.trim()}
            loading={busy}
            leftIcon={!busy ? <Plus /> : undefined}
          >
            {busy ? "Creating…" : "Create in Zoho Books"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function PartySection({ title, children }) {
  return (
    <div>
      <div className="gl-eyebrow mb-2">
        {title}
      </div>
      {children}
    </div>
  );
}

function FieldShort({ label, value, onChange, placeholder }) {
  return (
    <div>
      <Label>{label}</Label>
      <Input value={value} onChange={onChange} placeholder={placeholder} />
    </div>
  );
}
