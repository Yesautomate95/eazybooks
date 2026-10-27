-- Migration: 030_bank_recon_match_groups.sql
-- Description: Bring the Tally bank-recon match model to parity with QBO (migration
--              029). Generalize a "match" from a rigid 1 ledger + 1 bank pairing
--              into a GROUP that can hold N bank + M ledger transactions, unlocking:
--                * one-to-many aggregation (1 bank txn  <->  sum of many ledger txns)
--                * many-to-many manual reconciliation
--                * contra / offset groupings (e.g. 2 ledger entries that net to 0)
--
--              The match row (bank_recon_matches) becomes the GROUP HEADER;
--              membership lives in the new bank_recon_match_members table, which is
--              the single source of truth for "which txns are in a match".
--
--              Also adds a `tier` (auto / suggest / manual) so the engine can
--              auto-reconcile high-confidence groups and only surface uncertain ones
--              for review — powering the "Automatic reconciliation %" headline — and
--              a user-supplied closing-balance override (parity with QBO).
--
--              Idempotent: safe to re-run. Requires the base bank_recon_* tables
--              (create_bank_recon_tables.sql) to already exist.
-- Date: 2026-06-12

-- 1. The old 1:1 columns can no longer be mandatory: an aggregate match has many
--    txns per side, and a contra group has zero on one side. Make them nullable.
--    (Dropping NOT NULL on an already-nullable column is a no-op, so this is safe
--    to re-run.) They are kept, populated only for the trivial 1+1 case, as a
--    debugging convenience; the members table is authoritative.
ALTER TABLE bank_recon_matches ALTER COLUMN ledger_txn_id DROP NOT NULL;
ALTER TABLE bank_recon_matches ALTER COLUMN bank_txn_id DROP NOT NULL;

-- 2. Confidence tier of the match.
--    'auto'    -> auto-reconciled (high confidence), counts toward Automatic %
--    'suggest' -> proposed, needs human accept/reject (the Suggested screen)
--    'manual'  -> created by the user in manual reconciliation
ALTER TABLE bank_recon_matches ADD COLUMN IF NOT EXISTS tier VARCHAR(20);

-- 3. Optional user-supplied override of the Tally ledger closing balance. When set,
--    it wins over the auto-derived figure (parity with QBO's
--    qbo_closing_balance_override). Adjusting entries shift this so the
--    reconciliation difference stays invariant.
ALTER TABLE bank_recon_reports ADD COLUMN IF NOT EXISTS ledger_closing_balance_override DECIMAL(15,2);

-- 4. Membership: which transactions belong to a match, and on which side.
CREATE TABLE IF NOT EXISTS bank_recon_match_members (
    id          SERIAL PRIMARY KEY,
    match_id    INT NOT NULL REFERENCES bank_recon_matches(match_id) ON DELETE CASCADE,
    txn_id      INT NOT NULL REFERENCES bank_recon_transactions(txn_id) ON DELETE CASCADE,
    side        VARCHAR(10) NOT NULL,   -- 'ledger' | 'bank'
    created_at  TIMESTAMP DEFAULT NOW(),
    UNIQUE (match_id, txn_id)
);

CREATE INDEX IF NOT EXISTS idx_bank_recon_brm_members_match
    ON bank_recon_match_members(match_id);

CREATE INDEX IF NOT EXISTS idx_bank_recon_brm_members_txn
    ON bank_recon_match_members(txn_id);

-- 5. Backfill: every existing match becomes a 2-member group (its ledger + bank txn).
INSERT INTO bank_recon_match_members (match_id, txn_id, side)
SELECT m.match_id, m.ledger_txn_id, 'ledger'
FROM bank_recon_matches m
WHERE m.ledger_txn_id IS NOT NULL
  AND NOT EXISTS (
      SELECT 1 FROM bank_recon_match_members mm
      WHERE mm.match_id = m.match_id AND mm.txn_id = m.ledger_txn_id
  );

INSERT INTO bank_recon_match_members (match_id, txn_id, side)
SELECT m.match_id, m.bank_txn_id, 'bank'
FROM bank_recon_matches m
WHERE m.bank_txn_id IS NOT NULL
  AND NOT EXISTS (
      SELECT 1 FROM bank_recon_match_members mm
      WHERE mm.match_id = m.match_id AND mm.txn_id = m.bank_txn_id
  );

-- 6. Backfill tier for existing matches from their type + confidence.
UPDATE bank_recon_matches
SET tier = CASE
    WHEN match_type = 'manual'        THEN 'manual'
    WHEN confidence_score >= 0.95     THEN 'auto'
    ELSE 'suggest'
END
WHERE tier IS NULL;
