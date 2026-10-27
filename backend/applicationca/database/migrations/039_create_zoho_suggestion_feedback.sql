-- Migration 039: Zoho Books suggestion-feedback (Phase 4).
--
-- Records when the AI suggested an account/party and the user picked something
-- different (was_override = TRUE), plus confirmations. Mirrors QBO's
-- qbo_suggestion_feedback / Tally's ledger_suggestion_feedback. Feeds future
-- rule-mining and scoring. Additive.

CREATE TABLE IF NOT EXISTS zoho_suggestion_feedback (
    id                 SERIAL PRIMARY KEY,
    user_id            TEXT NOT NULL,
    organization_id    TEXT NOT NULL DEFAULT '',
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

CREATE INDEX IF NOT EXISTS idx_zoho_suggestion_feedback_lookup
    ON zoho_suggestion_feedback (user_id, organization_id, slot, was_override);
