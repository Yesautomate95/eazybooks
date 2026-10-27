-- Migration 022: Deeper learning for ledger suggestions.
--
-- Extends tally_transaction_history with the context fields the Layer 0
-- scorer needs to do recency- and context-aware ranking instead of the
-- naive (0.88 + 0.02 * usage_count) formula. Adds a sibling table that
-- captures every suggestion-vs-pick decision so we can learn from
-- overrides explicitly — not just from successful pushes.
--
-- New context columns on tally_transaction_history are nullable + indexed
-- where it pays off. The lookup_history scorer treats missing context as a
-- mild penalty (not a disqualifier), so older rows keep working.

-- ── tally_transaction_history: context columns ─────────────────────────────
ALTER TABLE tally_transaction_history
  ADD COLUMN IF NOT EXISTS voucher_type TEXT,
  ADD COLUMN IF NOT EXISTS amount_band TEXT,
  ADD COLUMN IF NOT EXISTS gstin TEXT,
  ADD COLUMN IF NOT EXISTS hsn_codes TEXT[],
  ADD COLUMN IF NOT EXISTS confirmation_source TEXT NOT NULL DEFAULT 'auto_pushed';

ALTER TABLE tally_transaction_history DROP CONSTRAINT IF EXISTS tth_confirmation_source_check;
ALTER TABLE tally_transaction_history ADD CONSTRAINT tth_confirmation_source_check
  CHECK (confirmation_source IN ('auto_pushed', 'user_corrected', 'user_picked_from_alts'));

-- Backfill voucher_type and gstin from existing metadata so older rows
-- still participate in scoring instead of being silently ignored.
UPDATE tally_transaction_history
   SET voucher_type = metadata->>'voucher_type'
 WHERE voucher_type IS NULL
   AND metadata ? 'voucher_type';

UPDATE tally_transaction_history
   SET gstin = metadata->>'gstin'
 WHERE gstin IS NULL
   AND metadata ? 'gstin';

CREATE INDEX IF NOT EXISTS idx_tth_gstin
  ON tally_transaction_history (user_id, company_name, mapping_type, gstin)
  WHERE gstin IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_tth_voucher_type
  ON tally_transaction_history (user_id, company_name, mapping_type, voucher_type, last_used_at DESC);

-- ── ledger_suggestion_feedback ────────────────────────────────────────────
-- Records every suggestion + user-pick pair, including the override flag,
-- so the rule-mining service can mine correction patterns and the Layer 0
-- scorer can weight user_corrected history rows above auto-pushed ones.
CREATE TABLE IF NOT EXISTS ledger_suggestion_feedback (
  feedback_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id TEXT NOT NULL,
  company_name TEXT NOT NULL,
  invoice_draft_id UUID,
  field TEXT NOT NULL CHECK (field IN ('party_ledger', 'expense_ledger')),
  vendor_name TEXT,
  vendor_gstin TEXT,
  voucher_type TEXT,
  amount DECIMAL(15,2),
  hsn_codes TEXT[],
  suggested_ledger TEXT,
  suggested_match_type TEXT,
  suggested_confidence DECIMAL(4,3),
  picked_ledger TEXT NOT NULL,
  was_override BOOLEAN NOT NULL DEFAULT FALSE,
  source TEXT NOT NULL DEFAULT 'push' CHECK (source IN ('push', 'live')),
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_lsf_vendor
  ON ledger_suggestion_feedback (user_id, company_name, vendor_name);

CREATE INDEX IF NOT EXISTS idx_lsf_override
  ON ledger_suggestion_feedback (user_id, company_name, was_override)
  WHERE was_override = TRUE;

CREATE INDEX IF NOT EXISTS idx_lsf_field
  ON ledger_suggestion_feedback (user_id, company_name, field, created_at DESC);

COMMENT ON TABLE ledger_suggestion_feedback IS
  'Records every AI ledger suggestion paired with the ledger the user '
  'actually picked. Drives correction-aware learning and rule mining.';

COMMENT ON COLUMN tally_transaction_history.amount_band IS
  'Discretized invoice amount: lt_5k, 5k_50k, 50k_500k, gt_500k. Used by '
  'Layer 0 context-aware scoring to prefer history rows in the same band.';

COMMENT ON COLUMN tally_transaction_history.confirmation_source IS
  'How the mapping was confirmed: auto_pushed (user did not override AI), '
  'user_corrected (user changed the AI pick before pushing), or '
  'user_picked_from_alts (user picked a different ledger via the dropdown).';
