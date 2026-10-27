-- Migration: 004_create_qb_recon_reports.sql
-- Description: Persisted QBO bank-reconciliation reports + matched pairs.
--              Mirrors the Tally bank-recon storage (one report per period
--              per bank account, many match rows underneath). Stores the
--              full statement + QBO snapshot as JSON so users can reopen
--              past reports without re-fetching.
-- Date: 2026-04-30

CREATE TABLE IF NOT EXISTS qb_recon_reports (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         TEXT NOT NULL,
    realm_id        TEXT NOT NULL,
    account_id      TEXT NOT NULL,
    account_name    TEXT,
    period_start    DATE NOT NULL,
    period_end      DATE NOT NULL,
    statement_json  JSONB NOT NULL,             -- {opening_balance, closing_balance, transactions[]}
    qbo_txns_json   JSONB NOT NULL,             -- normalized QBO txns at recon time
    summary_json    JSONB,                      -- compute_reconciliation_summary output
    qbo_closing_balance NUMERIC(18, 2),
    needs_review    BOOLEAN DEFAULT FALSE,
    status          TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'completed')),
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_qb_recon_reports_user
    ON qb_recon_reports(user_id, realm_id, account_id, period_end DESC);


CREATE TABLE IF NOT EXISTS qb_recon_matches (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    report_id       UUID NOT NULL REFERENCES qb_recon_reports(id) ON DELETE CASCADE,
    qbo_txn_id      TEXT,
    qbo_txn_type    TEXT,
    statement_idx   INT,                        -- index into statement_json.transactions
    amount          NUMERIC(18, 2),
    confidence      NUMERIC(4, 3),
    reason          TEXT,
    manual          BOOLEAN DEFAULT FALSE,
    qbo_txn_json    JSONB,
    statement_row_json JSONB,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_qb_recon_matches_report
    ON qb_recon_matches(report_id);
