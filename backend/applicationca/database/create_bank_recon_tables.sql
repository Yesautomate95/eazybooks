-- ============================================================
-- Bank Reconciliation Tables Migration
-- Run with: python -m applicationca.database.run_bank_recon_migration
-- ============================================================

-- Main reconciliation report record
CREATE TABLE IF NOT EXISTS bank_recon_reports (
    report_id               SERIAL PRIMARY KEY,
    user_id                 VARCHAR(255) NOT NULL,
    company_name            VARCHAR(500) NOT NULL,
    bank_ledger_name        VARCHAR(500) NOT NULL,
    report_name             VARCHAR(255) NOT NULL,
    period_from             DATE NOT NULL,
    period_to               DATE NOT NULL,
    financial_year          VARCHAR(10),

    -- Report lifecycle status
    status                  VARCHAR(50) NOT NULL DEFAULT 'draft',
    -- draft | processing | in_progress | completed | awaiting_tally | error

    -- Balances (populated after processing)
    ledger_opening_balance          DECIMAL(15,2),
    ledger_closing_balance          DECIMAL(15,2),
    bank_opening_balance            DECIMAL(15,2),
    bank_closing_balance            DECIMAL(15,2),

    -- Optional user-supplied override of the Tally ledger closing balance. When
    -- set, it wins over the auto-derived figure (parity with QBO). Adjusting
    -- entries shift it so the reconciliation difference stays invariant.
    ledger_closing_balance_override DECIMAL(15,2),

    -- Unreconciled category amounts (recomputed on demand)
    ledger_credits_not_in_bank      DECIMAL(15,2) DEFAULT 0,
    ledger_debits_not_in_bank       DECIMAL(15,2) DEFAULT 0,
    bank_credits_not_in_ledger      DECIMAL(15,2) DEFAULT 0,
    bank_debits_not_in_ledger       DECIMAL(15,2) DEFAULT 0,
    reconciliation_difference       DECIMAL(15,2),

    -- Transaction counts
    total_ledger_transactions       INT DEFAULT 0,
    total_bank_transactions         INT DEFAULT 0,
    matched_count                   INT DEFAULT 0,
    unmatched_ledger_count          INT DEFAULT 0,
    unmatched_bank_count            INT DEFAULT 0,

    -- Uploaded bank statement file
    bank_statement_file_url         VARCHAR(2000),
    bank_statement_file_name        VARCHAR(500),

    created_at  TIMESTAMP DEFAULT NOW(),
    updated_at  TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_bank_recon_reports_user
    ON bank_recon_reports(user_id, company_name, bank_ledger_name);

CREATE INDEX IF NOT EXISTS idx_bank_recon_reports_status
    ON bank_recon_reports(user_id, status);


-- All transactions from both ledger and bank statement (snapshotted at processing time)
CREATE TABLE IF NOT EXISTS bank_recon_transactions (
    txn_id              SERIAL PRIMARY KEY,
    report_id           INT NOT NULL REFERENCES bank_recon_reports(report_id) ON DELETE CASCADE,

    source              VARCHAR(20) NOT NULL,
    -- 'ledger' | 'bank_statement'

    transaction_date    DATE NOT NULL,
    description         TEXT,
    reference           VARCHAR(500),
    debit_amount        DECIMAL(15,2),   -- money received (bank credit / ledger debit)
    credit_amount       DECIMAL(15,2),   -- money paid out (bank debit / ledger credit)

    -- Reconciliation status
    recon_status        VARCHAR(30) NOT NULL DEFAULT 'unmatched',
    -- unmatched | auto_matched | manual_matched | pending | ignored

    match_id            INT,             -- back-ref to bank_recon_matches (no FK to avoid circular dep)

    -- Tally-specific fields (for ledger source transactions)
    voucher_type        VARCHAR(100),
    voucher_number      VARCHAR(100),
    tally_guid          VARCHAR(500),

    -- Raw parsed data (for bank statement source)
    raw_data            JSONB,

    is_opening_balance  BOOLEAN DEFAULT FALSE,

    created_at          TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_bank_recon_txns_report_source
    ON bank_recon_transactions(report_id, source);

CREATE INDEX IF NOT EXISTS idx_bank_recon_txns_status
    ON bank_recon_transactions(report_id, recon_status);

CREATE INDEX IF NOT EXISTS idx_bank_recon_txns_date
    ON bank_recon_transactions(report_id, transaction_date);


-- Matches between ledger transactions and bank statement transactions
CREATE TABLE IF NOT EXISTS bank_recon_matches (
    match_id            SERIAL PRIMARY KEY,
    report_id           INT NOT NULL REFERENCES bank_recon_reports(report_id) ON DELETE CASCADE,

    match_type          VARCHAR(20) NOT NULL,
    -- 'automatic' | 'manual'

    status              VARCHAR(30) NOT NULL DEFAULT 'pending_review',
    -- pending_review | accepted | rejected

    -- Confidence tier: 'auto' (auto-reconciled) | 'suggest' (needs review) | 'manual'
    tier                VARCHAR(20),

    -- Denormalized 1:1 columns — nullable, populated only for the trivial
    -- single-ledger + single-bank case. bank_recon_match_members is authoritative.
    ledger_txn_id       INT REFERENCES bank_recon_transactions(txn_id) ON DELETE CASCADE,
    bank_txn_id         INT REFERENCES bank_recon_transactions(txn_id) ON DELETE CASCADE,

    confidence_score    DECIMAL(4,3),
    match_reason        VARCHAR(300),
    notes               TEXT,

    created_at          TIMESTAMP DEFAULT NOW(),
    reviewed_at         TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_bank_recon_matches_report_status
    ON bank_recon_matches(report_id, status);

CREATE INDEX IF NOT EXISTS idx_bank_recon_matches_ledger_txn
    ON bank_recon_matches(ledger_txn_id);

CREATE INDEX IF NOT EXISTS idx_bank_recon_matches_bank_txn
    ON bank_recon_matches(bank_txn_id);


-- Match membership: which transactions belong to a match, and on which side.
-- A match (header) can hold N bank + M ledger members (one-to-many aggregation,
-- many-to-many manual matches, contra/offset groups that net to zero).
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
