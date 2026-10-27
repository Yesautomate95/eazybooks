-- Migration 035: Xero suggestion-feedback (Phase 4).
--
-- Records when the AI suggested an account/party and the user picked something
-- different (was_override = TRUE), plus confirmations. Mirrors Tally's
-- ledger_suggestion_feedback / QBO's qbo_suggestion_feedback. Feeds future
-- rule-mining and scoring. Additive. Clones 027_create_qbo_suggestion_feedback.sql.

CREATE TABLE IF NOT EXISTS xero_suggestion_feedback (
    id                 SERIAL PRIMARY KEY,
    user_id            TEXT NOT NULL,
    tenant_id          TEXT NOT NULL DEFAULT '',
    slot               TEXT,                 -- 'account' | 'party'
    context_text       TEXT,                 -- line description / vendor name
    vendor_name        TEXT,
    amount             NUMERIC,
    suggested_id       TEXT,
    suggested_name     TEXT,
    suggested_confidence NUMERIC,
    suggested_method   TEXT,                 -- history|fuzzy|llm_category|mapping_rule|...
    picked_id          TEXT,
    picked_name        TEXT,
    was_override       BOOLEAN NOT NULL DEFAULT FALSE,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_xero_suggestion_feedback_lookup
    ON xero_suggestion_feedback (user_id, tenant_id, slot, was_override);
