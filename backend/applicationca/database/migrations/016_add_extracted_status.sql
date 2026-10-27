-- Migration 016: Introduce 'extracted' status for tally_drafts.
-- Cleanly separates "the system finished extracting" from "the user
-- explicitly clicked Save Draft". 'extracted' means the row is ready
-- for review but the user has not yet committed to it as a draft.

ALTER TABLE tally_drafts DROP CONSTRAINT IF EXISTS tally_drafts_status_check;
ALTER TABLE tally_drafts ADD CONSTRAINT tally_drafts_status_check
  CHECK (status IN ('uploaded', 'extracting', 'extracted', 'draft', 'pushing', 'pushed', 'error'));
