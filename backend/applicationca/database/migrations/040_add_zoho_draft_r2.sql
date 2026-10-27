-- Migration 040: persist the source document for Zoho Books drafts (Phase 6).
--
-- Lets a reopened Zoho draft show its original PDF/image preview (Tally already
-- does this via R2). Mirrors 028_add_qbo_draft_r2.sql. Additive; columns are
-- null until the extract path uploads to R2 (which only happens when R2 is
-- configured).

ALTER TABLE zoho_drafts
  ADD COLUMN IF NOT EXISTS file_url  TEXT,
  ADD COLUMN IF NOT EXISTS file_name TEXT,
  ADD COLUMN IF NOT EXISTS r2_key    TEXT;
