import React, { useEffect, useMemo, useState } from "react";
import { Plus, ChevronDown, ChevronUp } from "lucide-react";
import { qbApi } from "../../../../../services/api";
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
 * CreateMasterModal — Tier-1 inline-create modal for QBO master data.
 *
 *   entityType: 'customer' | 'vendor' | 'item' | 'account'
 *   prefillName: AI-extracted name to seed the form (user can edit)
 *   qbCompany:   realm id, threaded to the create endpoint
 *
 * On success calls `onCreated({list_id, name})` so the caller can:
 *   1. Patch its qbo_payload to reference the new entity
 *   2. Append the entity to its in-memory master list
 *   3. Mark the corresponding row as matched
 *
 * For vendor / customer we now expose the full QBO Vendor schema behind a
 * "More details" toggle — DisplayName alone still works (Tier-1 quick path),
 * but the CA can also capture name parts, contact channels, billing address,
 * and business identifiers (Tax ID / Account Number / Print-on-check name)
 * directly at create time so vendors don't need an immediate QBO edit pass.
 */

// Per QBO Vendor docs (Title/Suffix are tight at 16 chars — easy to miss).
const FIELD_MAX = {
  name: 500,
  title: 16,
  given: 100,
  middle: 100,
  family: 100,
  suffix: 16,
  company: 1024,
  printOnCheck: 110,
  taxIdentifier: 20,
  acctNum: 20,
  phone: 21,
  email: 100,
  uri: 1000,
  addrLine: 500,
  addrCity: 255,
  addrState: 255,
  addrPostal: 30,
  addrCountry: 255,
};

const emptyPartyFields = () => ({
  title: "",
  givenName: "",
  middleName: "",
  familyName: "",
  suffix: "",
  companyName: "",
  phone: "",
  mobile: "",
  fax: "",
  website: "",
  taxIdentifier: "",
  acctNum: "",
  printOnCheckName: "",
  addrLine1: "",
  addrLine2: "",
  addrLine3: "",
  addrCity: "",
  addrState: "",
  addrPostal: "",
  addrCountry: "",
});

export default function CreateMasterModal({
  open,
  entityType,
  prefillName = "",
  qbCompany,
  onClose,
  onCreated,
}) {
  const [name, setName] = useState(prefillName);
  const [email, setEmail] = useState("");
  const [accountType, setAccountType] = useState("Expense");  // for entityType=account
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
      setAccountType("Expense");
      setParty(emptyPartyFields());
      setShowAdvanced(false);
      setError("");
      setBusy(false);
    }
  }, [open, prefillName]);

  // Build the optional-fields payload that gets merged onto the base
  // {name, email, qb_company} body for vendor/customer creates. Empty strings
  // are stripped server-side too but we drop them here as well so the request
  // shape stays compact.
  const partyExtras = useMemo(() => {
    if (!isParty) return {};
    const extras = {};
    const trim = (s) => (s || "").trim();
    const setIf = (key, val) => {
      const v = trim(val);
      if (v) extras[key] = v;
    };
    setIf("title", party.title);
    setIf("given_name", party.givenName);
    setIf("middle_name", party.middleName);
    setIf("family_name", party.familyName);
    setIf("suffix", party.suffix);
    setIf("company_name", party.companyName);
    setIf("phone", party.phone);
    setIf("mobile", party.mobile);
    setIf("fax", party.fax);
    setIf("website", party.website);
    setIf("tax_identifier", party.taxIdentifier);
    setIf("acct_num", party.acctNum);
    setIf("print_on_check_name", party.printOnCheckName);
    const addr = {};
    if (trim(party.addrLine1)) addr.line1 = trim(party.addrLine1);
    if (trim(party.addrLine2)) addr.line2 = trim(party.addrLine2);
    if (trim(party.addrLine3)) addr.line3 = trim(party.addrLine3);
    if (trim(party.addrCity)) addr.city = trim(party.addrCity);
    if (trim(party.addrState)) addr.state = trim(party.addrState);
    if (trim(party.addrPostal)) addr.postal_code = trim(party.addrPostal);
    if (trim(party.addrCountry)) addr.country = trim(party.addrCountry);
    if (Object.keys(addr).length > 0) extras.bill_addr = addr;
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
        const res = await qbApi.createCustomer({
          name: trimmed,
          email: email.trim() || undefined,
          qb_company: qbCompany,
          ...partyExtras,
        });
        const c = res.customer || res;
        created = {
          list_id: String(c.Id ?? c.list_id),
          name: c.DisplayName || c.name || trimmed,
        };
      } else if (entityType === "vendor") {
        const res = await qbApi.createVendor({
          name: trimmed,
          email: email.trim() || undefined,
          qb_company: qbCompany,
          ...partyExtras,
        });
        const v = res.vendor || res;
        created = {
          list_id: String(v.Id ?? v.list_id),
          name: v.DisplayName || v.name || trimmed,
        };
      } else if (entityType === "item") {
        const res = await qbApi.createItem({
          name: trimmed,
          qb_company: qbCompany,
        });
        const it = res.item || res;
        created = {
          list_id: String(it.Id ?? it.list_id),
          name: it.Name || it.name || trimmed,
        };
      } else if (entityType === "account") {
        const res = await qbApi.createAccount({
          name: trimmed,
          account_type: accountType,
          qb_company: qbCompany,
        });
        const a = res.account || res;
        created = {
          list_id: String(a.Id ?? a.list_id),
          name: a.Name || a.name || trimmed,
          account_type: a.AccountType || accountType,
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
              maxLength={FIELD_MAX.name}
            />
            <p className="text-2xs text-gilt-soft mt-1.5 leading-snug">
              Tip: edit before saving if the AI mis-spelled it — QuickBooks treats this as the
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
                  maxLength={FIELD_MAX.email}
                />
              </div>
              <div>
                <Label>Phone <span className="text-gilt-faint font-normal">(optional)</span></Label>
                <Input
                  type="tel"
                  value={party.phone}
                  onChange={setPartyField("phone")}
                  placeholder="(650) 555-2342"
                  maxLength={FIELD_MAX.phone}
                />
              </div>
            </div>
          )}

          {entityType === "account" && (
            <div>
              <Label>Account type</Label>
              <Select value={accountType} onChange={(e) => setAccountType(e.target.value)}>
                <option value="Expense">Expense</option>
                <option value="Income">Income</option>
                <option value="CostOfGoodsSold">Cost of Goods Sold</option>
                <option value="OtherExpense">Other Expense</option>
                <option value="OtherIncome">Other Income</option>
                <option value="Bank">Bank</option>
              </Select>
            </div>
          )}

          {entityType === "item" && (
            <p className="text-xs text-gilt-soft bg-gilt-sunken border border-gilt-hairline rounded-lg p-3 leading-relaxed">
              Created as a Service item with your first Income account as the revenue bucket.
              Change the income account in QuickBooks if you need a different one.
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
                    Add more details (address, tax id, name parts…)
                  </>
                )}
              </button>

              {showAdvanced && (
                <div className="mt-4 space-y-5">
                  <PartySection title="Name parts">
                    <div className="grid grid-cols-5 gap-2">
                      <FieldShort label="Title" value={party.title} onChange={setPartyField("title")} maxLength={FIELD_MAX.title} placeholder="Ms." />
                      <FieldShort label="First" value={party.givenName} onChange={setPartyField("givenName")} maxLength={FIELD_MAX.given} placeholder="Dianne" />
                      <FieldShort label="Middle" value={party.middleName} onChange={setPartyField("middleName")} maxLength={FIELD_MAX.middle} placeholder="" />
                      <FieldShort label="Last" value={party.familyName} onChange={setPartyField("familyName")} maxLength={FIELD_MAX.family} placeholder="Bradley" />
                      <FieldShort label="Suffix" value={party.suffix} onChange={setPartyField("suffix")} maxLength={FIELD_MAX.suffix} placeholder="Jr." />
                    </div>
                  </PartySection>

                  <PartySection title="Contact">
                    <div className="grid grid-cols-2 gap-3">
                      <div>
                        <Label>Mobile</Label>
                        <Input type="tel" value={party.mobile} onChange={setPartyField("mobile")} placeholder="(650) 555-2000" maxLength={FIELD_MAX.phone} />
                      </div>
                      <div>
                        <Label>Fax</Label>
                        <Input type="tel" value={party.fax} onChange={setPartyField("fax")} placeholder="(650) 555-2099" maxLength={FIELD_MAX.phone} />
                      </div>
                      <div className="col-span-2">
                        <Label>Website</Label>
                        <Input type="url" value={party.website} onChange={setPartyField("website")} placeholder="https://example.com" maxLength={FIELD_MAX.uri} />
                      </div>
                    </div>
                  </PartySection>

                  <PartySection title="Billing address">
                    <div className="space-y-3">
                      <div>
                        <Label>Line 1</Label>
                        <Input value={party.addrLine1} onChange={setPartyField("addrLine1")} placeholder="Vendor name on bill" maxLength={FIELD_MAX.addrLine} />
                      </div>
                      <div>
                        <Label>Line 2</Label>
                        <Input value={party.addrLine2} onChange={setPartyField("addrLine2")} placeholder="Attention / contact person" maxLength={FIELD_MAX.addrLine} />
                      </div>
                      <div>
                        <Label>Line 3</Label>
                        <Input value={party.addrLine3} onChange={setPartyField("addrLine3")} placeholder="Street address" maxLength={FIELD_MAX.addrLine} />
                      </div>
                      <div className="grid grid-cols-3 gap-2">
                        <div>
                          <Label>City</Label>
                          <Input value={party.addrCity} onChange={setPartyField("addrCity")} placeholder="Millbrae" maxLength={FIELD_MAX.addrCity} />
                        </div>
                        <div>
                          <Label>State / Region</Label>
                          <Input value={party.addrState} onChange={setPartyField("addrState")} placeholder="CA" maxLength={FIELD_MAX.addrState} />
                        </div>
                        <div>
                          <Label>Postal code</Label>
                          <Input value={party.addrPostal} onChange={setPartyField("addrPostal")} placeholder="94030" maxLength={FIELD_MAX.addrPostal} />
                        </div>
                      </div>
                      <div>
                        <Label>Country</Label>
                        <Input value={party.addrCountry} onChange={setPartyField("addrCountry")} placeholder="U.S.A" maxLength={FIELD_MAX.addrCountry} />
                      </div>
                    </div>
                  </PartySection>

                  <PartySection title="Business details">
                    <div className="grid grid-cols-2 gap-3">
                      <div className="col-span-2">
                        <Label>Company name</Label>
                        <Input value={party.companyName} onChange={setPartyField("companyName")} placeholder="Dianne's Auto Shop" maxLength={FIELD_MAX.company} />
                      </div>
                      <div>
                        <Label>Tax identifier <span className="text-gilt-faint font-normal">(GSTIN / PAN / EIN)</span></Label>
                        <Input value={party.taxIdentifier} onChange={setPartyField("taxIdentifier")} placeholder="99-5688293" maxLength={FIELD_MAX.taxIdentifier} />
                      </div>
                      <div>
                        <Label>Account number</Label>
                        <Input value={party.acctNum} onChange={setPartyField("acctNum")} placeholder="35372649" maxLength={FIELD_MAX.acctNum} />
                      </div>
                      <div className="col-span-2">
                        <Label>Print-on-check name</Label>
                        <Input value={party.printOnCheckName} onChange={setPartyField("printOnCheckName")} placeholder="Falls back to display name when blank" maxLength={FIELD_MAX.printOnCheck} />
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
            {busy ? "Creating…" : "Create in QuickBooks"}
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

function FieldShort({ label, value, onChange, placeholder, maxLength }) {
  return (
    <div>
      <Label>{label}</Label>
      <Input value={value} onChange={onChange} placeholder={placeholder} maxLength={maxLength} />
    </div>
  );
}
