import React, { useEffect, useMemo, useState } from "react";
import { Layers, Plus, AlertTriangle, Info } from "lucide-react";
import { sapApi } from "../../../../../services/api";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "../../../../ui/dialog";
import { Button } from "../../../../ui/button";
import { Input, Select } from "../../../../ui/input";
import { Badge } from "../../../../ui/badge";
import { cn } from "../../../../../lib/utils";

const ENTITY_BADGE = {
  customer: "info",
  vendor: "warning",
  item: "brand",
  account: "success",
};

// SAP FI can't create GL accounts or materials via the API (the backend returns
// 501). Only customers and vendors create cleanly in a batch.
const UNSUPPORTED_TYPES = new Set(["account", "item"]);
const UNSUPPORTED_MSG =
  "SAP GL accounts and materials can't be created via the API — create them in the SAP Fiori app, then re-run matching.";

const amountFormatter = new Intl.NumberFormat("en-IN", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

function fmtDate(iso) {
  if (!iso || typeof iso !== "string") return "";
  const d = new Date(iso.length === 10 ? `${iso}T00:00:00` : iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });
}

function fmtAmount(value) {
  if (value === undefined || value === null || value === "") return "";
  const n = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(n)) return "";
  return `₹${amountFormatter.format(n)}`;
}

/**
 * BulkCreateModal — Tier-5 "create all missing master data" flow.
 *
 *   items: [{ key, entity_type, name, account_type? }] gathered by the parent
 *          from all unmatched rows across the review table.
 *   companyCode: SAP company code, threaded to the batch endpoint
 *   onClose(): dismiss
 *   onComplete(results): caller patches its sap_payloads + master lists.
 *
 * The CA can edit names (fix AI typos), pick types for accounts, or skip rows
 * before submitting. Only checked rows go into the batch — protects against
 * accidental creates the matcher would have caught on the next run anyway.
 *
 * SAP note: GL accounts and materials can't be created via the API. Rows of
 * those types come back as failures in the batch result (or the whole call may
 * 501); we surface an informational message instead of crashing.
 */
export default function BulkCreateModal({
  open,
  items: initialItems = [],
  companyCode,
  onClose,
  onComplete,
}) {
  const [rows, setRows] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (open) {
      setRows(
        initialItems.map((it, i) => ({
          key: it.key ?? `row_${i}`,
          entity_type: it.entity_type,
          name: it.name || "",
          account_type:
            it.account_type || (it.entity_type === "account" ? "Expense" : undefined),
          date: it.date || "",
          amount: it.amount,
          // GL accounts / materials can't be created — leave them unselected.
          selected: !UNSUPPORTED_TYPES.has(it.entity_type),
        }))
      );
      setError("");
      setBusy(false);
    }
  }, [open, initialItems]);

  const hasContext = useMemo(
    () =>
      rows.some(
        (r) => r.date || (r.amount !== undefined && r.amount !== null && r.amount !== "")
      ),
    [rows]
  );

  // Whether any unsupported (account/material) rows are present so we can show
  // the SAP-specific guidance banner.
  const hasUnsupported = useMemo(
    () => rows.some((r) => UNSUPPORTED_TYPES.has(r.entity_type)),
    [rows]
  );

  const updateRow = (i, patch) => {
    setRows((prev) => prev.map((r, idx) => (idx === i ? { ...r, ...patch } : r)));
  };

  const submit = async () => {
    // Only customers/vendors can actually be created in SAP — drop unsupported
    // types from the batch so the call doesn't 501 on them.
    const toCreate = rows.filter(
      (r) => r.selected && r.name.trim() && !UNSUPPORTED_TYPES.has(r.entity_type)
    );
    if (toCreate.length === 0) {
      setError(
        hasUnsupported
          ? "Only customers and vendors can be created from here. " + UNSUPPORTED_MSG
          : "Pick at least one row to create."
      );
      return;
    }
    setBusy(true);
    setError("");
    try {
      const payload = {
        items: toCreate.map((r) => ({
          entity_type: r.entity_type,
          name: r.name.trim(),
          ...(r.entity_type === "account" && r.account_type
            ? { account_type: r.account_type }
            : {}),
        })),
        company_code: companyCode,
      };
      const res = await sapApi.createBatch(payload);
      onComplete?.(res?.results || []);
      onClose?.();
    } catch (err) {
      // A 501 (or any error referencing accounts/materials) gets the friendly
      // SAP message; everything else surfaces the server detail.
      if (err?.response?.status === 501) {
        setError(UNSUPPORTED_MSG);
      } else {
        const detail = err?.response?.data?.detail;
        setError(
          typeof detail === "string"
            ? detail
            : detail?.message || err.message || "Batch create failed"
        );
      }
    } finally {
      setBusy(false);
    }
  };

  // Count only the creatable (customer/vendor) selected rows for the CTA.
  const creatableSelectedCount = rows.filter(
    (r) => r.selected && !UNSUPPORTED_TYPES.has(r.entity_type)
  ).length;
  const selectedCount = rows.filter((r) => r.selected).length;
  const allSelected = rows.length > 0 && selectedCount === rows.length;
  const someSelected = selectedCount > 0 && !allSelected;
  const toggleAll = (checked) => {
    // Never auto-select unsupported types — they can't be created.
    setRows((prev) =>
      prev.map((r) => ({
        ...r,
        selected: checked && !UNSUPPORTED_TYPES.has(r.entity_type),
      }))
    );
  };

  const dialogSize = hasContext ? "xl" : "lg";

  return (
    <Dialog open={open} onOpenChange={(v) => !v && !busy && onClose?.()}>
      <DialogContent size={dialogSize} showClose={!busy} className="flex flex-col max-h-[90vh]">
        <DialogHeader>
          <DialogTitle>
            <span className="inline-flex items-center gap-2">
              <Layers className="h-4 w-4 text-success-600" />
              Create missing master data
              <span className="text-ink-400 font-normal text-sm tabular-nums">
                ({selectedCount}/{rows.length})
              </span>
            </span>
          </DialogTitle>
        </DialogHeader>
        <div className="px-6 py-3 flex-1 overflow-hidden flex flex-col">
          <p className="text-xs text-ink-600 mb-3 leading-relaxed">
            Review each name — edit AI typos, deselect anything you don't want created, then
            submit. SAP S/4HANA treats the saved name as canonical, so it's worth a quick check.
            {hasContext && (
              <span className="text-ink-500">
                {" "}
                The date and amount columns show the originating transaction so you can confirm
                context.
              </span>
            )}
          </p>
          {hasUnsupported && (
            <div className="mb-3 flex items-start gap-2 bg-ink-50 border border-ink-200 text-ink-700 px-3 py-2 rounded-lg text-xs leading-relaxed">
              <Info className="h-3.5 w-3.5 flex-shrink-0 mt-0.5 text-ink-500" />
              <span>{UNSUPPORTED_MSG}</span>
            </div>
          )}
          <div className="flex-1 overflow-auto border border-ink-200 rounded-lg">
            <table className="w-full text-sm">
              <thead className="bg-ink-50 sticky top-0 z-10">
                <tr>
                  <th className="px-3 py-2.5 text-left text-2xs font-semibold text-ink-500 uppercase tracking-wider w-12">
                    <input
                      type="checkbox"
                      checked={allSelected}
                      ref={(el) => {
                        if (el) el.indeterminate = someSelected;
                      }}
                      onChange={(e) => toggleAll(e.target.checked)}
                      className="h-4 w-4 rounded border-ink-300 text-brand-600 focus:ring-brand-500"
                      aria-label="Toggle all"
                    />
                  </th>
                  <th className="px-3 py-2.5 text-left text-2xs font-semibold text-ink-500 uppercase tracking-wider w-24">
                    Type
                  </th>
                  {hasContext && (
                    <>
                      <th className="px-3 py-2.5 text-left text-2xs font-semibold text-ink-500 uppercase tracking-wider w-28">
                        Date
                      </th>
                      <th className="px-3 py-2.5 text-right text-2xs font-semibold text-ink-500 uppercase tracking-wider w-32">
                        Amount
                      </th>
                    </>
                  )}
                  <th className="px-3 py-2.5 text-left text-2xs font-semibold text-ink-500 uppercase tracking-wider">
                    Name
                  </th>
                  <th className="px-3 py-2.5 text-left text-2xs font-semibold text-ink-500 uppercase tracking-wider w-40">
                    Account type
                  </th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => {
                  const isUnsupported = UNSUPPORTED_TYPES.has(r.entity_type);
                  const muted = !r.selected || isUnsupported ? "opacity-50" : "";
                  return (
                    <tr
                      key={r.key}
                      className="border-t border-ink-100 hover:bg-ink-50/60 transition-colors"
                    >
                      <td className="px-3 py-2 align-middle">
                        <input
                          type="checkbox"
                          checked={r.selected}
                          disabled={isUnsupported}
                          onChange={(e) => updateRow(i, { selected: e.target.checked })}
                          className="h-4 w-4 rounded border-ink-300 text-brand-600 focus:ring-brand-500 disabled:opacity-40"
                          title={isUnsupported ? UNSUPPORTED_MSG : undefined}
                        />
                      </td>
                      <td className={cn("px-3 py-2 align-middle", muted)}>
                        <Badge
                          variant={ENTITY_BADGE[r.entity_type] || "neutral"}
                          size="sm"
                          className="capitalize"
                        >
                          {r.entity_type}
                        </Badge>
                      </td>
                      {hasContext && (
                        <>
                          <td
                            className={cn(
                              "px-3 py-2 align-middle text-xs text-ink-700 whitespace-nowrap",
                              muted
                            )}
                          >
                            {fmtDate(r.date) || <span className="text-ink-300">—</span>}
                          </td>
                          <td
                            className={cn(
                              "px-3 py-2 align-middle text-xs text-ink-900 text-right tabular-nums whitespace-nowrap",
                              muted
                            )}
                          >
                            {fmtAmount(r.amount) || <span className="text-ink-300">—</span>}
                          </td>
                        </>
                      )}
                      <td className="px-3 py-2 align-middle">
                        <Input
                          size="sm"
                          value={r.name}
                          onChange={(e) => updateRow(i, { name: e.target.value })}
                          disabled={!r.selected || isUnsupported}
                        />
                      </td>
                      <td className="px-3 py-2 align-middle">
                        {r.entity_type === "account" ? (
                          <Select
                            size="sm"
                            value={r.account_type || "Expense"}
                            onChange={(e) => updateRow(i, { account_type: e.target.value })}
                            disabled={!r.selected || isUnsupported}
                          >
                            <option value="Expense">Expense</option>
                            <option value="Income">Income</option>
                            <option value="CostOfGoodsSold">COGS</option>
                            <option value="OtherExpense">Other Expense</option>
                            <option value="OtherIncome">Other Income</option>
                          </Select>
                        ) : (
                          <span className="text-xs text-ink-300">—</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {error && (
            <div className="mt-3 flex items-start gap-2 bg-danger-50 border border-danger-200 text-danger-700 px-3 py-2 rounded-lg text-xs">
              <AlertTriangle className="h-3.5 w-3.5 flex-shrink-0 mt-0.5 text-danger-600" />
              <span>{error}</span>
            </div>
          )}
        </div>
        <DialogFooter>
          <Button variant="secondary" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button
            variant="primary"
            onClick={submit}
            disabled={creatableSelectedCount === 0}
            loading={busy}
            leftIcon={!busy ? <Plus /> : undefined}
          >
            {busy ? "Creating…" : `Create ${creatableSelectedCount} in SAP S/4HANA`}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
