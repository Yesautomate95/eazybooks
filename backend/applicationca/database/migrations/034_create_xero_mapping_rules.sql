-- Migration 034: Xero mapping-rules engine (Phase 3).
--
-- Xero analog of Tally's/QBO's mapping_rules. User-authorable, priority-ordered
-- rules that map a transaction (by keyword / pattern / vendor / amount /
-- composite AND-OR tree) to a target Xero account or party. Additive: with no
-- rows, the suggestion pipeline behaves exactly as before. Clones
-- 026_create_qbo_mapping_rules.sql + the ledger_slot column from
-- 025_mapping_rule_ledger_slot.sql.

CREATE TABLE IF NOT EXISTS xero_mapping_rules (
    rule_id            SERIAL PRIMARY KEY,
    user_id            TEXT NOT NULL,
    tenant_id          TEXT NOT NULL DEFAULT '',
    rule_name          TEXT,
    rule_type          TEXT NOT NULL DEFAULT 'keyword'
                       CHECK (rule_type IN ('keyword','pattern','vendor','amount_range','composite')),
    document_type      TEXT,                 -- xero_sales | xero_purchase | xero_bank
    account_slot       TEXT CHECK (account_slot IN ('party','account')),
    target_account_id   TEXT,
    target_account_name TEXT,
    keywords           JSONB,
    pattern            TEXT,
    vendor_name        TEXT,
    amount_min         NUMERIC,
    amount_max         NUMERIC,
    conditions         JSONB,                -- composite AND/OR tree
    -- Slot-aware evaluation (party vs expense). NULL keeps legacy slot-inference.
    ledger_slot        TEXT
                       CHECK (ledger_slot IS NULL OR ledger_slot IN ('party', 'expense')),
    priority           INTEGER NOT NULL DEFAULT 0,
    match_count        INTEGER NOT NULL DEFAULT 0,
    is_active          BOOLEAN NOT NULL DEFAULT TRUE,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_xero_mapping_rules_lookup
    ON xero_mapping_rules (user_id, tenant_id, document_type, account_slot, is_active);

CREATE INDEX IF NOT EXISTS idx_xero_mapping_rules_slot
    ON xero_mapping_rules (user_id, tenant_id, document_type, ledger_slot);
