-- Migration 013: Extend tally_drafts with lifecycle status and Cloudflare R2 file reference
-- Enables the Tally Document Library feature (All / Drafts / Pushed to Tally tabs)

ALTER TABLE tally_drafts
  ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'uploaded'
    CHECK (status IN ('uploaded', 'extracting', 'draft', 'pushing', 'pushed', 'error')),
  ADD COLUMN IF NOT EXISTS file_url TEXT,
  ADD COLUMN IF NOT EXISTS file_name TEXT,
  ADD COLUMN IF NOT EXISTS r2_key TEXT,
  ADD COLUMN IF NOT EXISTS pushed_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS error_message TEXT;

-- Index for fast tab queries (All / Drafts / Pushed)
CREATE INDEX IF NOT EXISTS idx_tally_drafts_status
  ON tally_drafts (user_id, status, updated_at DESC);

-- Backfill existing rows to 'draft' status (they already have extracted payloads)
UPDATE tally_drafts SET status = 'draft' WHERE status = 'uploaded' AND payload != '{}'::jsonb;
