-- Migration: 007_create_drafts_tables.sql
-- Description: Per-platform drafts tables for in-progress purchase / sales /
--              bank-entries work. Each row is one draft of one session's
--              extracted-state + edits, stored as JSONB so the frontend can
--              rehydrate without re-extracting. PDFs are intentionally NOT
--              persisted — extracted state + edits only.

CREATE TABLE IF NOT EXISTS qbo_drafts (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     TEXT NOT NULL,
    realm_id    TEXT NOT NULL,
    flow        TEXT NOT NULL CHECK (flow IN ('purchase', 'sales', 'bank_entries')),
    title       TEXT,                            -- short label derived from filenames / first vendor
    payload     JSONB NOT NULL,                  -- in-memory state to restore
    created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_qbo_drafts_user
    ON qbo_drafts (user_id, realm_id, flow, updated_at DESC);


CREATE TABLE IF NOT EXISTS tally_drafts (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       TEXT NOT NULL,
    company_name  TEXT NOT NULL,
    flow          TEXT NOT NULL CHECK (flow IN ('purchase', 'sales', 'bank_entries')),
    title         TEXT,
    payload       JSONB NOT NULL,
    created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_tally_drafts_user
    ON tally_drafts (user_id, company_name, flow, updated_at DESC);
