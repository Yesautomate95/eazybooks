-- Migration 026: QuickBooks mapping-rules engine (Phase 3).
--
-- QB analog of Tally's mapping_rules. User-authorable, priority-ordered rules
-- that map a transaction (by keyword / pattern / vendor / amount / composite
-- AND-OR tree) to a target QBO account or party. Additive: with no rows, the
-- suggestion pipeline behaves exactly as before.

CREATE TABLE IF NOT EXISTS qbo_mapping_rules (
    rule_id            SERIAL PRIMARY KEY,
    user_id            TEXT NOT NULL,
    realm_id           TEXT NOT NULL DEFAULT '',
    rule_name          TEXT,
    rule_type          TEXT NOT NULL DEFAULT 'keyword'
                       CHECK (rule_type IN ('keyword','pattern','vendor','amount_range','composite')),
    document_type      TEXT,                 -- qb_sales | qb_purchase | qb_bank
    account_slot       TEXT CHECK (account_slot IN ('party','account')),
    target_account_id   TEXT,
    target_account_name TEXT,
    keywords           JSONB,
    pattern            TEXT,
    vendor_name        TEXT,
    amount_min         NUMERIC,
    amount_max         NUMERIC,
    conditions         JSONB,                -- composite AND/OR tree
    priority           INTEGER NOT NULL DEFAULT 0,
    match_count        INTEGER NOT NULL DEFAULT 0,
    is_active          BOOLEAN NOT NULL DEFAULT TRUE,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_qbo_mapping_rules_lookup
    ON qbo_mapping_rules (user_id, realm_id, document_type, account_slot, is_active);
