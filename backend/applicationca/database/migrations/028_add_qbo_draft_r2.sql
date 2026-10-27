-- Migration 028: persist the source document for QuickBooks drafts (Phase 6).
--
-- Lets a reopened QB draft show its original PDF/image preview (Tally already
-- does this via R2). Additive; columns are null until the extract path uploads
-- to R2 (which only happens when R2 is configured).

ALTER TABLE qbo_drafts
  ADD COLUMN IF NOT EXISTS file_url  TEXT,
  ADD COLUMN IF NOT EXISTS file_name TEXT,
  ADD COLUMN IF NOT EXISTS r2_key    TEXT;
