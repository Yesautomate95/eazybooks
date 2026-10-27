-- Migration 024: Draft-first lifecycle for SAP drafts.
--
-- Brings sap_drafts up to parity with qbo_drafts / zoho_drafts / xero_drafts
-- (migration 021): the status state-machine plus push-bookkeeping columns, so
-- the shared document-library / bulk-push workflow treats SAP uniformly.
-- sap_drafts was created in migration 022 (after 021) without these columns.
-- Status vocabulary kept identical to the other platforms so frontend logic is shared.

ALTER TABLE sap_drafts
  ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'draft',
  ADD COLUMN IF NOT EXISTS pushed_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS error_message TEXT;

ALTER TABLE sap_drafts DROP CONSTRAINT IF EXISTS sap_drafts_status_check;
ALTER TABLE sap_drafts ADD CONSTRAINT sap_drafts_status_check
  CHECK (status IN ('uploaded', 'extracting', 'extracted', 'draft', 'pushing', 'pushed', 'error'));

CREATE INDEX IF NOT EXISTS idx_sap_drafts_status
  ON sap_drafts (user_id, status, updated_at DESC);
