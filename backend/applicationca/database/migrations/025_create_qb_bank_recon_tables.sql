-- Migration: 025_create_qb_bank_recon_tables.sql
-- Description: Normalized QBO bank-reconciliation storage, cloning the Tally
--              bank_recon_* model (stateful report + per-row transactions +
--              reviewable matches). Replaces the legacy JSON-blob qb_recon_*
--              tables so QuickBooks walks the same flow as Tally:
--                create report -> process -> review matches -> summary -> save.
--
--              The "book" side here is the set of QBO transactions that hit the
--              chosen bank/credit-card account in the period (the equivalent of
--              Tally's "ledger" side).
-- Date: 2026-06-05

-- Main reconciliation report record
CREATE TABLE IF NOT EXISTS qb_bank_recon_reports (
    report_id               SERIAL PRIMARY KEY,
    user_id                 VARCHAR(255) NOT NULL,
    realm_id                VARCHAR(255) NOT NULL,
    account_id              VARCHAR(255) NOT NULL,
    account_name            VARCHAR(500),
    report_name             VARCHAR(255) NOT NULL,
    period_from             DATE NOT NULL,
    period_to               DATE NOT NULL,
    financial_year          VARCHAR(10),

    -- Report lifecycle status
    status                  VARCHAR(50) NOT NULL DEFAULT 'draft',
    -- draft | processing | in_progress | completed | awaiting_source | error

    -- Balances (populated after processing)
    book_opening_balance            DECIMAL(15,2),
    book_closing_balance            DECIMAL(15,2),
    bank_opening_balance            DECIMAL(15,2),
    bank_closing_balance            DECIMAL(15,2),

    -- Optional user-supplied override of the QBO book closing balance. When set,
    -- it wins over the auto-derived figure (parity with the old QB field).
    qbo_closing_balance_override    DECIMAL(15,2),

    -- Unreconciled category amounts (recomputed on demand)
    book_credits_not_in_bank        DECIMAL(15,2) DEFAULT 0,
    book_debits_not_in_bank         DECIMAL(15,2) DEFAULT 0,
    bank_credits_not_in_book        DECIMAL(15,2) DEFAULT 0,
    bank_debits_not_in_book         DECIMAL(15,2) DEFAULT 0,
    reconciliation_difference       DECIMAL(15,2),

    -- Transaction counts
    total_book_transactions         INT DEFAULT 0,
    total_bank_transactions         INT DEFAULT 0,
    matched_count                   INT DEFAULT 0,
    unmatched_book_count            INT DEFAULT 0,
    unmatched_bank_count            INT DEFAULT 0,

    -- Uploaded bank statement file
    bank_statement_file_url         VARCHAR(2000),
    bank_statement_file_name        VARCHAR(500),

    created_at  TIMESTAMP DEFAULT NOW(),
    updated_at  TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_qb_bank_recon_reports_user
    ON qb_bank_recon_reports(user_id, realm_id, account_id, period_to DESC);

CREATE INDEX IF NOT EXISTS idx_qb_bank_recon_reports_status
    ON qb_bank_recon_reports(user_id, status);


-- All transactions from both the QBO book and the bank statement
-- (snapshotted at processing time)
CREATE TABLE IF NOT EXISTS qb_bank_recon_transactions (
    txn_id              SERIAL PRIMARY KEY,
    report_id           INT NOT NULL REFERENCES qb_bank_recon_reports(report_id) ON DELETE CASCADE,

    source              VARCHAR(20) NOT NULL,
    -- 'book' | 'bank_statement'

    transaction_date    DATE NOT NULL,
    description         TEXT,
    reference           VARCHAR(500),
    debit_amount        DECIMAL(15,2),   -- money received (bank credit / book debit / inflow)
    credit_amount       DECIMAL(15,2),   -- money paid out (bank debit / book credit / outflow)

    -- Reconciliation status
    recon_status        VARCHAR(30) NOT NULL DEFAULT 'unmatched',
    -- unmatched | auto_matched | manual_matched | ignored

    match_id            INT,             -- back-ref to qb_bank_recon_matches (no FK to avoid circular dep)

    -- QBO-specific fields (for book source transactions)
    qbo_txn_id          VARCHAR(255),
    qbo_txn_type        VARCHAR(100),

    -- Raw parsed/normalized data (statement row or QBO txn)
    raw_data            JSONB,

    is_opening_balance  BOOLEAN DEFAULT FALSE,

    created_at          TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_qb_bank_recon_txns_report_source
    ON qb_bank_recon_transactions(report_id, source);

CREATE INDEX IF NOT EXISTS idx_qb_bank_recon_txns_status
    ON qb_bank_recon_transactions(report_id, recon_status);

CREATE INDEX IF NOT EXISTS idx_qb_bank_recon_txns_date
    ON qb_bank_recon_transactions(report_id, transaction_date);


-- Matches between QBO book transactions and bank statement transactions
CREATE TABLE IF NOT EXISTS qb_bank_recon_matches (
    match_id            SERIAL PRIMARY KEY,
    report_id           INT NOT NULL REFERENCES qb_bank_recon_reports(report_id) ON DELETE CASCADE,

    match_type          VARCHAR(20) NOT NULL,
    -- 'automatic' | 'manual'

    status              VARCHAR(30) NOT NULL DEFAULT 'pending_review',
    -- pending_review | accepted | rejected

    book_txn_id         INT NOT NULL REFERENCES qb_bank_recon_transactions(txn_id) ON DELETE CASCADE,
    bank_txn_id         INT NOT NULL REFERENCES qb_bank_recon_transactions(txn_id) ON DELETE CASCADE,

    confidence_score    DECIMAL(4,3),
    match_reason        VARCHAR(300),
    notes               TEXT,

    created_at          TIMESTAMP DEFAULT NOW(),
    reviewed_at         TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_qb_bank_recon_matches_report_status
    ON qb_bank_recon_matches(report_id, status);

CREATE INDEX IF NOT EXISTS idx_qb_bank_recon_matches_book_txn
    ON qb_bank_recon_matches(book_txn_id);

CREATE INDEX IF NOT EXISTS idx_qb_bank_recon_matches_bank_txn
    ON qb_bank_recon_matches(bank_txn_id);
