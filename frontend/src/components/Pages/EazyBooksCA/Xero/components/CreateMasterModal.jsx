import React, { useEffect, useState } from "react";
import { Plus, AlertTriangle } from "lucide-react";
import { xeroApi } from "../../../../../services/api";
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
 * CreateMasterModal — Tier-1 inline-create modal for Xero master data.
 *
 *   entityType:     'customer' | 'vendor' | 'item' | 'account'
 *   prefillName:    AI-extracted name to seed the form (user can edit)
 *   tenantId: Xero org id, threaded to the create endpoint
 *
 * On success calls `onCreated({list_id, name})` so the caller can:
 *   1. Patch its xero_payload to reference the new entity
 *   2. Append the entity to its in-memory master list
 *   3. Mark the corresponding row as matched
 */
export default function CreateMasterModal({
  open,
  entityType,
  prefillName = "",
  tenantId,
  onClose,
  onCreated,
}) {
  const [name, setName] = useState(prefillName);
  const [email, setEmail] = useState("");
  const [accountType, setAccountType] = useState("Expense");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (open) {
      setName(prefillName);
      setEmail("");
      setAccountType("Expense");
      setError("");
      setBusy(false);
    }
  }, [open, prefillName]);

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
        const res = await xeroApi.createCustomer({
          name: trimmed,
          email: email.trim() || undefined,
          tenant_id: tenantId,
        });
        const c = res.customer || res;
        created = {
          list_id: String(c.Id ?? c.list_id),
          name: c.DisplayName || c.name || trimmed,
        };
      } else if (entityType === "vendor") {
        const res = await xeroApi.createVendor({
          name: trimmed,
          email: email.trim() || undefined,
          tenant_id: tenantId,
        });
        const v = res.vendor || res;
        created = {
          list_id: String(v.Id ?? v.list_id),
          name: v.DisplayName || v.name || trimmed,
        };
      } else if (entityType === "item") {
        const res = await xeroApi.createItem({
          name: trimmed,
          tenant_id: tenantId,
        });
        const it = res.item || res;
        created = {
          list_id: String(it.Id ?? it.list_id),
          name: it.Name || it.name || trimmed,
        };
      } else if (entityType === "account") {
        const res = await xeroApi.createAccount({
          name: trimmed,
          account_type: accountType,
          tenant_id: tenantId,
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
      const detail = err?.response?.data?.detail;
      const msg =
        typeof detail === "string"
          ? detail
          : detail?.message || detail?.error || err.message || "Create failed";
      setError(msg);
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

  return (
    <Dialog open={open} onOpenChange={(v) => !v && !busy && onClose?.()}>
      <DialogContent size="sm" showClose={!busy}>
        <DialogHeader>
          <DialogTitle>
            <span className="inline-flex items-center gap-2">
              <Plus className="h-4 w-4 text-gilt-brass" />
              {titleByType[entityType] || "Create"}
            </span>
          </DialogTitle>
        </DialogHeader>
        <form onSubmit={onSubmit} className="px-6 py-3 space-y-4">
          <div>
            <Label>Name</Label>
            <Input
              autoFocus
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. AGC ASIA PACIFIC INDIA PVT LTD"
            />
            <p className="text-2xs text-gilt-faint mt-1.5 leading-snug">
              Tip: edit before saving if the AI mis-spelled it — Xero treats this as the
              canonical name.
            </p>
          </div>
          {(entityType === "customer" || entityType === "vendor") && (
            <div>
              <Label>
                Email <span className="text-gilt-faint font-normal">(optional)</span>
              </Label>
              <Input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="finance@example.com"
              />
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
              Change the income account in Xero if you need a different one.
            </p>
          )}
          {error && (
            <div className="flex items-start gap-2 bg-gilt-sunken border border-gilt-debit/40 text-gilt-debit px-3 py-2 rounded-lg text-xs">
              <AlertTriangle className="h-3.5 w-3.5 flex-shrink-0 mt-0.5 text-gilt-debit" />
              <span>{error}</span>
            </div>
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
            {busy ? "Creating…" : "Create in Xero"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
