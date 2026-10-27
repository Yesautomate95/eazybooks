import { useEffect, useRef } from "react";
import { useSearchParams } from "react-router-dom";

/**
 * Opens a draft referenced by a `?draftId=` URL param, once, after mount.
 *
 * The cloud draft library (CloudDraftLibrary) links each row to its flow's
 * view with `?draftId=<id>`; this hook lets that view auto-load the draft so
 * "Open" lands the user straight in the editor instead of an empty form.
 *
 * Params:
 *   getDraft:     (id) => Promise<draft>   e.g. qbApi.drafts.get
 *   companyField: "realm_id" | "organization_id" | "tenant_id"
 *   setCompany:   setter for the active company id (so masters load)
 *   onOpen:       (draft) => void          the view's handleOpenDraft
 */
export function useDraftDeepLink({ getDraft, companyField, setCompany, onOpen }) {
  const [searchParams] = useSearchParams();
  const draftId = searchParams.get("draftId");
  const handled = useRef(false);

  useEffect(() => {
    if (!draftId || handled.current) return;
    handled.current = true;
    (async () => {
      try {
        const draft = await getDraft(draftId);
        if (!draft) return;
        const companyId = draft[companyField];
        if (companyId && setCompany) setCompany(companyId);
        if (onOpen) onOpen(draft);
      } catch {
        /* Non-fatal: the user can still open the draft from the in-view panel. */
      }
    })();
    // Only the draftId from the URL should retrigger this.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draftId]);
}
