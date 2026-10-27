-- Migration 015: Document the awaiting_tally status for bank_recon_reports
-- The status column is already VARCHAR(50) with no CHECK constraint, so this
-- migration is informational: it adds a column comment listing the new value
-- and is a no-op if re-run.

COMMENT ON COLUMN bank_recon_reports.status IS
    'Lifecycle: draft | awaiting_tally | processing | in_progress | completed | error. '
    'awaiting_tally means the bank statement has been parsed but ledger transactions '
    'have not been fetched from Tally yet (used when the user uploads offline).';
