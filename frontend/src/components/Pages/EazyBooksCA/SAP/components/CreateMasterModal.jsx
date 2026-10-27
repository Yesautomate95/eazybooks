import React, { useEffect, useState } from "react";
import { Plus, AlertTriangle, Info } from "lucide-react";
import { sapApi } from "../../../../../services/api";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "../../../../ui/dialog";
import { Button } from "../../../../ui/button";
import { Input, Label } from "../../../../ui/input";

// SAP FI cannot create GL accounts or item/material masters via the API — those
// endpoints return HTTP 501. Surface a clear message instead of a generic crash.
const UNSUPPORTED_TYPES = new Set(["account", "item"]);
const UNSUPPORTED_MSG =
  "SAP GL accounts must be created in the SAP Fiori app — they can't be created via the API. Pick an existing one instead.";

/**
 * CreateMasterModal — Tier-1 inline-create modal for SAP S/4HANA master data.
 *
 *   entityType:   'customer' | 'vendor' | 'item' | 'account'
 *   prefillName:  AI-extracted name to seed the form (user can edit)
 *   companyCode:  SAP company code, threaded to the create endpoint
 *
 * On success calls `onCreated({list_id, name})` so the caller can:
 *   1. Patch its sap_payload to reference the new entity
 *   2. Append the entity to its in-memory master list
 *   3. Mark the corresponding row as matched
 *
 * Note: customers and vendors create fine. 'account' and 'item' are not
 * creatable via the SAP API (the backend returns 501); for those types we show
 * an informational message and disable the create action.
 */
export default function CreateMasterModal({
  open,
  entityType,
  prefillName = "",
  companyCode,
  onClose,
  onCreated,
}) {
  const [name, setName] = useState(prefillName);
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const unsupported = UNSUPPORTED_TYPES.has(entityType);

  useEffect(() => {
    if (open) {
      setName(prefillName);
      setEmail("");
      setError("");
      setBusy(false);
    }
  }, [open, prefillName]);

  const onSubmit = async (e) => {
    e?.preventDefault?.();
    if (unsupported) {
      // Guard: SAP can't create these — nothing to submit.
      setError(UNSUPPORTED_MSG);
      return;
    }
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
        const res = await sapApi.createCustomer({
          name: trimmed,
          email: email.trim() || undefined,
          company_code: companyCode,
        });
        const c = res.customer || res;
        created = {
          list_id: String(c.Id ?? c.list_id),
          name: c.DisplayName || c.name || trimmed,
        };
      } else if (entityType === "vendor") {
        const res = await sapApi.createVendor({
          name: trimmed,
          email: email.trim() || undefined,
          company_code: companyCode,
        });
        const v = res.vendor || res;
        created = {
          list_id: String(v.Id ?? v.list_id),
          name: v.DisplayName || v.name || trimmed,
        };
      } else {
        throw new Error(`Unsupported entityType: ${entityType}`);
      }
      onCreated?.(created);
      onClose?.();
    } catch (err) {
      // SAP returns 501 for GL accounts / items — translate to a helpful note
      // rather than dumping a raw server error on the CA.
      if (err?.response?.status === 501 || UNSUPPORTED_TYPES.has(entityType)) {
        setError(UNSUPPORTED_MSG);
      } else {
        const detail = err?.response?.data?.detail;
        const msg =
          typeof detail === "string"
            ? detail
            : detail?.message || detail?.error || err.message || "Create failed";
        setError(msg);
      }
    } finally {
      setBusy(false);
    }
  };

  const titleByType = {
    customer: "Create new customer",
    vendor: "Create new vendor",
    item: "Create new material",
    account: "Create new GL account",
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
          {unsupported ? (
            <div className="flex items-start gap-2 bg-gilt-sunken border border-gilt-hairline text-gilt-soft px-3 py-3 rounded-lg text-xs leading-relaxed">
              <Info className="h-3.5 w-3.5 flex-shrink-0 mt-0.5 text-gilt-soft" />
              <span>{UNSUPPORTED_MSG}</span>
            </div>
          ) : (
            <>
              <div>
                <Label>Name</Label>
                <Input
                  autoFocus
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="e.g. AGC ASIA PACIFIC INDIA PVT LTD"
                />
                <p className="text-2xs text-gilt-faint mt-1.5 leading-snug">
                  Tip: edit before saving if the AI mis-spelled it — SAP S/4HANA treats this as the
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
            </>
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
            {unsupported ? "Close" : "Cancel"}
          </Button>
          {!unsupported && (
            <Button
              variant="primary"
              onClick={onSubmit}
              disabled={!name.trim()}
              loading={busy}
              leftIcon={!busy ? <Plus /> : undefined}
            >
              {busy ? "Creating…" : "Create in SAP S/4HANA"}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
