-- Migration: 041_create_zoho_bank_recon_match_groups.sql
-- Description: Generalize a Zoho bank-recon "match" from a rigid 1 book + 1 bank
--              pairing into a GROUP that can hold N bank + M book transactions.
--              Zoho twin of 029_qb_bank_recon_match_groups.sql. This unblocks:
--                * one-to-many aggregation (1 bank txn  <->  sum of many book txns)
--                * many-to-many manual reconciliation
--                * contra / offset groupings (e.g. 2 book entries that net to 0)
--
--              The match row (zoho_bank_recon_matches) becomes the GROUP HEADER;
--              membership lives in the new zoho_bank_recon_match_members table,
--              which is the single source of truth for "which txns are in a match".
--
--              Also adds a `tier` to distinguish auto-reconciled matches from
--              suggested ones the user must review, powering the
--              "Automatic reconciliation %" headline.

-- 1. The old 1:1 columns can no longer be mandatory: an aggregate match has many
--    txns per side, and a contra group has zero on one side. Make them nullable.
--    (Dropping NOT NULL on an already-nullable column is a no-op, so this is safe
--    to re-run.) They are kept, populated only for the trivial 1+1 case, as a
--    debugging convenience; the members table is authoritative.
ALTER TABLE zoho_bank_recon_matches ALTER COLUMN book_txn_id DROP NOT NULL;
ALTER TABLE zoho_bank_recon_matches ALTER COLUMN bank_txn_id DROP NOT NULL;

-- 2. Confidence tier of the match.
--    'auto'    -> auto-reconciled (high confidence), counts toward Automatic %
--    'suggest' -> proposed, needs human accept/reject (the Suggested screen)
--    'manual'  -> created by the user in manual reconciliation
ALTER TABLE zoho_bank_recon_matches ADD COLUMN IF NOT EXISTS tier VARCHAR(20);

-- 3. Membership: which transactions belong to a match, and on which side.
CREATE TABLE IF NOT EXISTS zoho_bank_recon_match_members (
    id          SERIAL PRIMARY KEY,
    match_id    INT NOT NULL REFERENCES zoho_bank_recon_matches(match_id) ON DELETE CASCADE,
    txn_id      INT NOT NULL REFERENCES zoho_bank_recon_transactions(txn_id) ON DELETE CASCADE,
    side        VARCHAR(10) NOT NULL,   -- 'book' | 'bank'
    created_at  TIMESTAMP DEFAULT NOW(),
    UNIQUE (match_id, txn_id)
);

CREATE INDEX IF NOT EXISTS idx_zoho_brm_members_match
    ON zoho_bank_recon_match_members(match_id);

CREATE INDEX IF NOT EXISTS idx_zoho_brm_members_txn
    ON zoho_bank_recon_match_members(txn_id);

-- 4. Backfill: every existing match becomes a 2-member group (its book + bank txn).
INSERT INTO zoho_bank_recon_match_members (match_id, txn_id, side)
SELECT m.match_id, m.book_txn_id, 'book'
FROM zoho_bank_recon_matches m
WHERE m.book_txn_id IS NOT NULL
  AND NOT EXISTS (
      SELECT 1 FROM zoho_bank_recon_match_members mm
      WHERE mm.match_id = m.match_id AND mm.txn_id = m.book_txn_id
  );

INSERT INTO zoho_bank_recon_match_members (match_id, txn_id, side)
SELECT m.match_id, m.bank_txn_id, 'bank'
FROM zoho_bank_recon_matches m
WHERE m.bank_txn_id IS NOT NULL
  AND NOT EXISTS (
      SELECT 1 FROM zoho_bank_recon_match_members mm
      WHERE mm.match_id = m.match_id AND mm.txn_id = m.bank_txn_id
  );

-- 5. Backfill tier for existing matches from their type + confidence.
UPDATE zoho_bank_recon_matches
SET tier = CASE
    WHEN match_type = 'manual'        THEN 'manual'
    WHEN confidence_score >= 0.95     THEN 'auto'
    ELSE 'suggest'
END
WHERE tier IS NULL;
