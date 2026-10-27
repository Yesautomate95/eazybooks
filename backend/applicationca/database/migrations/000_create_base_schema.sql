-- Base schema reconstruction for fresh deployments.
-- The original production database had users/clients/lockr/chats/files
-- created out-of-band (no creation script ever existed in this repo);
-- every other migration assumes these already exist. This file recreates
-- them so `run_all_migrations()` succeeds against an empty database.
-- Reconstructed from README_DB_STRUCTURE.md + actual column usage in
-- applicationca/contoller/auth_controller.py and applicationca/rbac/*.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS users (
    user_id       VARCHAR(255) PRIMARY KEY DEFAULT gen_random_uuid()::text,
    username      TEXT NOT NULL,
    email         TEXT UNIQUE,
    password_hash TEXT
);

CREATE TABLE IF NOT EXISTS clients (
    client_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id   VARCHAR(255) REFERENCES users(user_id),
    name      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS lockr (
    lockr_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id  VARCHAR(255) REFERENCES users(user_id),
    name     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chats (
    chat_id           VARCHAR(255) PRIMARY KEY,
    client_id         UUID REFERENCES clients(client_id),
    lockr_id          UUID REFERENCES lockr(lockr_id),
    user_id           VARCHAR(255) REFERENCES users(user_id),
    chat_description  TEXT,
    chat_date         TIMESTAMP,
    chat_name         TEXT,
    chat_type         TEXT
);

CREATE TABLE IF NOT EXISTS files (
    file_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id           UUID REFERENCES clients(client_id),
    lockr_id            UUID REFERENCES lockr(lockr_id),
    user_id             VARCHAR(255) REFERENCES users(user_id),
    chat_id             VARCHAR(255) REFERENCES chats(chat_id),
    file_name           TEXT,
    file_type           TEXT,
    file_last_modified  TIMESTAMP,
    file_link           TEXT,
    file_location       TEXT,
    file_size           BIGINT
);

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id          TEXT PRIMARY KEY,
    document_id       TEXT,
    folder_id         TEXT,
    user_id           TEXT,
    content           TEXT,
    embedding         BYTEA,
    content_tsvector  TSVECTOR
);

-- Bank reconciliation tables. This is the authored content of
-- applicationca/database/create_bank_recon_tables.sql, which exists in the
-- repo but lives outside applicationca/database/migrations/ and is never
-- run automatically by run_all_migrations() — included here verbatim so
-- migration 015_add_awaiting_tally_status.sql (which ALTERs
-- bank_recon_reports) has something to alter on a fresh database.

CREATE TABLE IF NOT EXISTS bank_recon_reports (
    report_id               SERIAL PRIMARY KEY,
    user_id                 VARCHAR(255) NOT NULL,
    company_name            VARCHAR(500) NOT NULL,
    bank_ledger_name        VARCHAR(500) NOT NULL,
    report_name              VARCHAR(255) NOT NULL,
    period_from              DATE NOT NULL,
    period_to                DATE NOT NULL,
    financial_year           VARCHAR(10),

    status                   VARCHAR(50) NOT NULL DEFAULT 'draft',

    ledger_opening_balance          DECIMAL(15,2),
    ledger_closing_balance          DECIMAL(15,2),
    bank_opening_balance            DECIMAL(15,2),
    bank_closing_balance            DECIMAL(15,2),
    ledger_closing_balance_override DECIMAL(15,2),

    ledger_credits_not_in_bank      DECIMAL(15,2) DEFAULT 0,
    ledger_debits_not_in_bank       DECIMAL(15,2) DEFAULT 0,
    bank_credits_not_in_ledger      DECIMAL(15,2) DEFAULT 0,
    bank_debits_not_in_ledger       DECIMAL(15,2) DEFAULT 0,
    reconciliation_difference       DECIMAL(15,2),

    total_ledger_transactions       INT DEFAULT 0,
    total_bank_transactions         INT DEFAULT 0,
    matched_count                   INT DEFAULT 0,
    unmatched_ledger_count          INT DEFAULT 0,
    unmatched_bank_count            INT DEFAULT 0,

    bank_statement_file_url         VARCHAR(2000),
    bank_statement_file_name        VARCHAR(500),

    created_at  TIMESTAMP DEFAULT NOW(),
    updated_at  TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_bank_recon_reports_user
    ON bank_recon_reports(user_id, company_name, bank_ledger_name);

CREATE INDEX IF NOT EXISTS idx_bank_recon_reports_status
    ON bank_recon_reports(user_id, status);

CREATE TABLE IF NOT EXISTS bank_recon_transactions (
    txn_id              SERIAL PRIMARY KEY,
    report_id           INT NOT NULL REFERENCES bank_recon_reports(report_id) ON DELETE CASCADE,
    source               VARCHAR(20) NOT NULL,
    transaction_date     DATE NOT NULL,
    description          TEXT,
    reference            VARCHAR(500),
    debit_amount         DECIMAL(15,2),
    credit_amount        DECIMAL(15,2),
    recon_status         VARCHAR(30) NOT NULL DEFAULT 'unmatched',
    match_id             INT,
    voucher_type         VARCHAR(100),
    voucher_number       VARCHAR(100),
    tally_guid           VARCHAR(500),
    raw_data             JSONB,
    is_opening_balance   BOOLEAN DEFAULT FALSE,
    created_at           TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_bank_recon_txns_report_source
    ON bank_recon_transactions(report_id, source);

CREATE INDEX IF NOT EXISTS idx_bank_recon_txns_status
    ON bank_recon_transactions(report_id, recon_status);

CREATE INDEX IF NOT EXISTS idx_bank_recon_txns_date
    ON bank_recon_transactions(report_id, transaction_date);

CREATE TABLE IF NOT EXISTS bank_recon_matches (
    match_id            SERIAL PRIMARY KEY,
    report_id           INT NOT NULL REFERENCES bank_recon_reports(report_id) ON DELETE CASCADE,
    match_type          VARCHAR(20) NOT NULL,
    status               VARCHAR(30) NOT NULL DEFAULT 'pending_review',
    tier                 VARCHAR(20),
    ledger_txn_id        INT REFERENCES bank_recon_transactions(txn_id) ON DELETE CASCADE,
    bank_txn_id          INT REFERENCES bank_recon_transactions(txn_id) ON DELETE CASCADE,
    confidence_score     DECIMAL(4,3),
    match_reason         VARCHAR(300),
    notes                TEXT,
    created_at           TIMESTAMP DEFAULT NOW(),
    reviewed_at          TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_bank_recon_matches_report_status
    ON bank_recon_matches(report_id, status);

CREATE INDEX IF NOT EXISTS idx_bank_recon_matches_ledger_txn
    ON bank_recon_matches(ledger_txn_id);

CREATE INDEX IF NOT EXISTS idx_bank_recon_matches_bank_txn
    ON bank_recon_matches(bank_txn_id);

CREATE TABLE IF NOT EXISTS bank_recon_match_members (
    id          SERIAL PRIMARY KEY,
    match_id    INT NOT NULL REFERENCES bank_recon_matches(match_id) ON DELETE CASCADE,
    txn_id      INT NOT NULL REFERENCES bank_recon_transactions(txn_id) ON DELETE CASCADE,
    side        VARCHAR(10) NOT NULL,
    created_at  TIMESTAMP DEFAULT NOW(),
    UNIQUE (match_id, txn_id)
);

CREATE INDEX IF NOT EXISTS idx_bank_recon_brm_members_match
    ON bank_recon_match_members(match_id);

CREATE INDEX IF NOT EXISTS idx_bank_recon_brm_members_txn
    ON bank_recon_match_members(txn_id);
