/**
 * Shared bulk-push helpers for the cloud draft library (QuickBooks / Zoho /
 * Xero). Each pusher takes a hydrated draft (the shape returned by
 * <platform>Api.drafts.get) plus the platform adapter, and returns:
 *   { ok, pushed, failed, total, errors: [{ index, message }] }
 * mirroring the Tally common/pushers.js contract, so the library's bulk-push
 * orchestration is identical across every platform.
 *
 * Unlike Tally (which generates + pushes XML), the cloud platforms already hold
 * a native push payload (qbo_payload / zoho_payload / xero_payload) on each
 * extracted document, so a pusher just forwards it to the platform's push
 * endpoint — no XML step.
 */

const pushErrorMessage = (err) => {
  const detail = err?.response?.data?.detail;
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object") {
    return (
      detail.qbo_error ||
      detail.qbo_detail ||
      detail.zoho_error ||
      detail.xero_error ||
      detail.message ||
      JSON.stringify(detail)
    );
  }
  return err?.response?.data?.error || err?.message || "Push failed";
};

const summarize = (pushed, failed, total, errors) => ({
  ok: errors.length === 0 && pushed > 0,
  pushed,
  failed,
  total,
  errors,
});

// Sales draft → a single invoice in payload.result.<payloadKey>
async function pushSalesDraft(draft, adapter) {
  const company = draft?.[adapter.companyField] || "";
  const res = draft?.payload?.result;
  const native = res?.[adapter.payloadKey];
  if (!native) {
    return summarize(0, 1, 1, [{ index: 0, message: "Draft has no push payload" }]);
  }
  try {
    await adapter.pushSales({ payload: native, company, extractedTotal: res?.extracted_total });
    return summarize(1, 0, 1, []);
  } catch (err) {
    return summarize(0, 1, 1, [{ index: 0, message: pushErrorMessage(err) }]);
  }
}

// Purchase draft → N bills in payload.bills[].result.<payloadKey>
async function pushPurchaseDraft(draft, adapter) {
  const company = draft?.[adapter.companyField] || "";
  const bills = draft?.payload?.bills || [];
  const pushable = bills.filter(
    (b) => b?.result?.[adapter.payloadKey] && b.pushStatus !== "pushed"
  );
  if (pushable.length === 0) {
    return summarize(0, 0, 0, [{ index: -1, message: "No bills ready to push" }]);
  }
  const errors = [];
  let pushed = 0;
  for (let i = 0; i < pushable.length; i += 1) {
    const bill = pushable[i];
    try {
      await adapter.pushPurchase({
        payload: bill.result[adapter.payloadKey],
        company,
        extractedTotal: bill.result.extracted_total,
      });
      pushed += 1;
    } catch (err) {
      errors.push({ index: i, message: `${bill.filename || `Bill ${i + 1}`}: ${pushErrorMessage(err)}` });
    }
  }
  return summarize(pushed, errors.length, pushable.length, errors);
}

// Bank-entries draft → N entries in payload.result.entries[].<payloadKey>
async function pushBankEntriesDraft(draft, adapter) {
  const company = draft?.[adapter.companyField] || "";
  const entries = draft?.payload?.result?.entries || [];
  const pushable = entries.filter((e) => e?.[adapter.payloadKey]);
  if (pushable.length === 0) {
    return summarize(0, 0, 0, [{ index: -1, message: "No entries ready to push" }]);
  }
  const errors = [];
  let pushed = 0;
  for (let i = 0; i < pushable.length; i += 1) {
    const e = pushable[i];
    try {
      await adapter.pushBank({ entryType: e.type, payload: e[adapter.payloadKey], company });
      pushed += 1;
    } catch (err) {
      errors.push({ index: i, message: pushErrorMessage(err) });
    }
  }
  return summarize(pushed, errors.length, pushable.length, errors);
}

/**
 * Build a `pushDraft(draft)` bound to a platform adapter. Routes by draft.flow.
 */
export function makePushDraft(adapter) {
  return async function pushDraft(draft) {
    switch (draft?.flow) {
      case "sales":
        return pushSalesDraft(draft, adapter);
      case "purchase":
        return pushPurchaseDraft(draft, adapter);
      case "bank_entries":
        return pushBankEntriesDraft(draft, adapter);
      default:
        return summarize(0, 0, 0, [{ index: -1, message: `Unsupported flow: ${draft?.flow}` }]);
    }
  };
}
