-- Migration 014: Cache last-seen Tally companies per user
-- Enables the draft-first workflow: users can pick a company and start uploading
-- even when their Tally desktop bridge isn't online right now. The cache is
-- refreshed every time the WebSocket bridge successfully returns companies.

CREATE TABLE IF NOT EXISTS tally_cached_companies (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL,
    company_name TEXT NOT NULL,
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ledgers_synced_at TIMESTAMPTZ,
    UNIQUE (user_id, company_name)
);

CREATE INDEX IF NOT EXISTS idx_tally_cached_companies_user
    ON tally_cached_companies (user_id, last_seen_at DESC);

-- Per-company ledger cache, populated on every successful live fetch.
-- Stored as JSONB so we keep whatever shape Tally returns without flattening.
CREATE TABLE IF NOT EXISTS tally_cached_ledgers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL,
    company_name TEXT NOT NULL,
    ledgers JSONB NOT NULL DEFAULT '[]'::jsonb,
    refreshed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (user_id, company_name)
);

CREATE INDEX IF NOT EXISTS idx_tally_cached_ledgers_lookup
    ON tally_cached_ledgers (user_id, company_name);
