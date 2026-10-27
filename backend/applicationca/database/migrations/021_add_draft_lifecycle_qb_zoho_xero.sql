-- Migration 021: Draft-first lifecycle for QuickBooks / Zoho / Xero drafts.
--
-- Brings qbo_drafts / zoho_drafts / xero_drafts up to parity with tally_drafts
-- (migrations 013 + 016): a status state-machine plus push bookkeeping columns,
-- so the shared document-library / bulk-push workflow can treat every platform
-- uniformly instead of being Tally-only.
--
-- Note: cloud platforms have no Cloudflare-R2 document library (there is no
-- server-side upload -> extract flow — extraction stays client-driven), so we
-- deliberately skip the file_url / file_name / r2_key columns that tally_drafts
-- carries. Only the status + push-tracking columns are mirrored. The status
-- vocabulary is kept identical to tally_drafts so the frontend logic is shared.

-- ── QuickBooks Online ──────────────────────────────────────────────────────
ALTER TABLE qbo_drafts
  ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'draft',
  ADD COLUMN IF NOT EXISTS pushed_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS error_message TEXT;

ALTER TABLE qbo_drafts DROP CONSTRAINT IF EXISTS qbo_drafts_status_check;
ALTER TABLE qbo_drafts ADD CONSTRAINT qbo_drafts_status_check
  CHECK (status IN ('uploaded', 'extracting', 'extracted', 'draft', 'pushing', 'pushed', 'error'));

CREATE INDEX IF NOT EXISTS idx_qbo_drafts_status
  ON qbo_drafts (user_id, status, updated_at DESC);

-- ── Zoho Books ─────────────────────────────────────────────────────────────
ALTER TABLE zoho_drafts
  ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'draft',
  ADD COLUMN IF NOT EXISTS pushed_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS error_message TEXT;

ALTER TABLE zoho_drafts DROP CONSTRAINT IF EXISTS zoho_drafts_status_check;
ALTER TABLE zoho_drafts ADD CONSTRAINT zoho_drafts_status_check
  CHECK (status IN ('uploaded', 'extracting', 'extracted', 'draft', 'pushing', 'pushed', 'error'));

CREATE INDEX IF NOT EXISTS idx_zoho_drafts_status
  ON zoho_drafts (user_id, status, updated_at DESC);

-- ── Xero ───────────────────────────────────────────────────────────────────
ALTER TABLE xero_drafts
  ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'draft',
  ADD COLUMN IF NOT EXISTS pushed_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS error_message TEXT;

ALTER TABLE xero_drafts DROP CONSTRAINT IF EXISTS xero_drafts_status_check;
ALTER TABLE xero_drafts ADD CONSTRAINT xero_drafts_status_check
  CHECK (status IN ('uploaded', 'extracting', 'extracted', 'draft', 'pushing', 'pushed', 'error'));

CREATE INDEX IF NOT EXISTS idx_xero_drafts_status
  ON xero_drafts (user_id, status, updated_at DESC);
