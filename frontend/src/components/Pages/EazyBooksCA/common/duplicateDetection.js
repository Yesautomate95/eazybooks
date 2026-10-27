/**
 * Duplicate detection for in-batch invoices.
 *
 * Two invoices in the same upload batch are considered duplicates of each
 * other when, for the same selected company, they have:
 *   - same vendor/party GSTIN + same invoice number, OR
 *   - same vendor/party NAME + same invoice number   (fallback when no GSTIN)
 *
 * The GSTIN match is preferred because vendor names can collide. The
 * fallback exists because some invoices (e.g. unregistered suppliers) won't
 * have a GSTIN at all.
 *
 * Returns a Map keyed by invoice id, value:
 *   { ofId, matchKey, matchedBy: "gstin" | "name" }
 *
 * Only invoices with status === "done" (extraction complete) are considered.
 * Invoices whose user explicitly chose "Keep both" carry
 * `duplicateOverridden: true` and are filtered out before matching.
 */
export function findInBatchDuplicates(invoices) {
  const seen = new Map(); // normalizedKey → first invoice id
  const dupes = new Map();

  for (const inv of invoices || []) {
    if (!inv || inv.status !== "done") continue;
    if (inv.duplicateOverridden) continue;
    const header = inv.data?.header || {};
    const invoiceNum = String(header.invoice_number || "").trim().toLowerCase();
    if (!invoiceNum) continue;

    const gstin = String(header.party_gstin || "").trim().toUpperCase();
    const party = String(header.party_name || "").trim().toLowerCase();

    let key = null;
    let matchedBy = null;
    if (gstin) {
      key = `g:${gstin}|i:${invoiceNum}`;
      matchedBy = "gstin";
    } else if (party) {
      key = `n:${party}|i:${invoiceNum}`;
      matchedBy = "name";
    }
    if (!key) continue;

    if (seen.has(key)) {
      dupes.set(inv.id, { ofId: seen.get(key), matchKey: key, matchedBy });
    } else {
      seen.set(key, inv.id);
    }
  }

  return dupes;
}

/**
 * Convenience helper: returns true if the invoice should be skipped at push
 * time because it's a flagged-but-not-overridden duplicate of another invoice
 * in the same batch.
 */
export function isPushBlockedByBatchDuplicate(invoice, batchDuplicates) {
  if (!invoice || invoice.duplicateOverridden) return false;
  return batchDuplicates?.has?.(invoice.id) || false;
}
