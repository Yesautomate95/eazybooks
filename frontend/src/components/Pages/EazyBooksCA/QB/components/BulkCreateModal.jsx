import React, { useEffect, useMemo, useState } from "react";
import { Layers, Plus } from "lucide-react";
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
import { Input, Select } from "../../../../ui/input";
import { Badge } from "../../../../ui/badge";
import { cn } from "../../../../../lib/utils";

const ENTITY_BADGE = {
  customer: "info",
  vendor: "warning",
  item: "brand",
  account: "success",
};

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
 *   qbCompany: realm id, threaded to the batch endpoint
 *   onClose(): dismiss
 *   onComplete(results): caller patches its qbo_payloads + master lists.
 *                       Results shape mirrors backend:
 *                       [{ok, entity_type, name, qbo_entity_id, qbo_entity_name, error?}]
 *
 * The CA can edit names (fix AI typos), pick types for accounts, or skip rows
 * before submitting. Only checked rows go into the batch — protects against
 * accidental creates the matcher would have caught on the next run anyway.
 */
export default function BulkCreateModal({
  open,
  items: initialItems = [],
  qbCompany,
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
          selected: true,
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

  const updateRow = (i, patch) => {
    setRows((prev) => prev.map((r, idx) => (idx === i ? { ...r, ...patch } : r)));
  };

  const submit = async () => {
    const toCreate = rows.filter((r) => r.selected && r.name.trim());
    if (toCreate.length === 0) {
      setError("Pick at least one row to create.");
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
        qb_company: qbCompany,
      };
      const res = await qbApi.createBatch(payload);
      onComplete?.(res?.results || []);
      onClose?.();
    } catch (err) {
      setError(err);  // raw — ErrorMessage extracts structure.
    } finally {
      setBusy(false);
    }
  };

  const selectedCount = rows.filter((r) => r.selected).length;
  const allSelected = rows.length > 0 && selectedCount === rows.length;
  const someSelected = selectedCount > 0 && !allSelected;
  const toggleAll = (checked) => {
    setRows((prev) => prev.map((r) => ({ ...r, selected: checked })));
  };

  const dialogSize = hasContext ? "xl" : "lg";

  return (
    <Dialog open={open} onOpenChange={(v) => !v && !busy && onClose?.()}>
      <DialogContent size={dialogSize} showClose={!busy} className="flex flex-col max-h-[90vh]">
        <DialogHeader>
          <DialogTitle>
            <span className="inline-flex items-center gap-2">
              <Layers className="h-4 w-4 text-gilt-credit" />
              Create missing master data
              <span className="text-gilt-faint font-normal text-sm font-mono tabular-nums">
                ({selectedCount}/{rows.length})
              </span>
            </span>
          </DialogTitle>
        </DialogHeader>
        <div className="px-6 py-3 flex-1 overflow-hidden flex flex-col">
          <p className="text-xs text-gilt-soft mb-3 leading-relaxed">
            Review each name — edit AI typos, deselect anything you don't want created, then
            submit. QuickBooks treats the saved name as canonical, so it's worth a quick check.
            {hasContext && (
              <span className="text-gilt-soft">
                {" "}
                The date and amount columns show the originating transaction so you can confirm
                context.
              </span>
            )}
          </p>
          <div className="flex-1 overflow-auto border border-gilt-hairline rounded-lg">
            <table className="w-full text-sm">
              <thead className="bg-gilt-sunken sticky top-0 z-10">
                <tr className="border-b border-gilt-hairline">
                  <th className="px-3 py-2.5 text-left font-mono text-[10px] font-medium text-gilt-soft uppercase tracking-wider w-12">
                    <input
                      type="checkbox"
                      checked={allSelected}
                      ref={(el) => {
                        if (el) el.indeterminate = someSelected;
                      }}
                      onChange={(e) => toggleAll(e.target.checked)}
                      className="h-4 w-4 rounded accent-gilt-brass"
                      aria-label="Toggle all"
                    />
                  </th>
                  <th className="px-3 py-2.5 text-left font-mono text-[10px] font-medium text-gilt-soft uppercase tracking-wider w-24">
                    Type
                  </th>
                  {hasContext && (
                    <>
                      <th className="px-3 py-2.5 text-left font-mono text-[10px] font-medium text-gilt-soft uppercase tracking-wider w-28">
                        Date
                      </th>
                      <th className="px-3 py-2.5 text-right font-mono text-[10px] font-medium text-gilt-soft uppercase tracking-wider w-32">
                        Amount
                      </th>
                    </>
                  )}
                  <th className="px-3 py-2.5 text-left font-mono text-[10px] font-medium text-gilt-soft uppercase tracking-wider">
                    Name
                  </th>
                  <th className="px-3 py-2.5 text-left font-mono text-[10px] font-medium text-gilt-soft uppercase tracking-wider w-40">
                    Account type
                  </th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => {
                  const muted = !r.selected ? "opacity-50" : "";
                  return (
                    <tr
                      key={r.key}
                      className="border-t border-gilt-hairline hover:bg-gilt-sunken transition-colors duration-150"
                    >
                      <td className="px-3 py-2 align-middle">
                        <input
                          type="checkbox"
                          checked={r.selected}
                          onChange={(e) => updateRow(i, { selected: e.target.checked })}
                          className="h-4 w-4 rounded accent-gilt-brass"
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
                              "px-3 py-2 align-middle text-xs text-gilt-soft font-mono tabular-nums whitespace-nowrap",
                              muted
                            )}
                          >
                            {fmtDate(r.date) || <span className="text-gilt-faint">—</span>}
                          </td>
                          <td
                            className={cn(
                              "px-3 py-2 align-middle text-xs text-gilt-ink text-right font-mono tabular-nums whitespace-nowrap",
                              muted
                            )}
                          >
                            {fmtAmount(r.amount) || <span className="text-gilt-faint">—</span>}
                          </td>
                        </>
                      )}
                      <td className="px-3 py-2 align-middle">
                        <Input
                          size="sm"
                          value={r.name}
                          onChange={(e) => updateRow(i, { name: e.target.value })}
                          disabled={!r.selected}
                        />
                      </td>
                      <td className="px-3 py-2 align-middle">
                        {r.entity_type === "account" ? (
                          <Select
                            size="sm"
                            value={r.account_type || "Expense"}
                            onChange={(e) => updateRow(i, { account_type: e.target.value })}
                            disabled={!r.selected}
                          >
                            <option value="Expense">Expense</option>
                            <option value="Income">Income</option>
                            <option value="CostOfGoodsSold">COGS</option>
                            <option value="OtherExpense">Other Expense</option>
                            <option value="OtherIncome">Other Income</option>
                          </Select>
                        ) : (
                          <span className="text-xs text-gilt-faint">—</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {error && (
            <div className="mt-3">
              <ErrorMessage error={error} prefix="Batch create failed" />
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
            disabled={selectedCount === 0}
            loading={busy}
            leftIcon={!busy ? <Plus /> : undefined}
          >
            {busy ? "Creating…" : `Create ${selectedCount} in QuickBooks`}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
