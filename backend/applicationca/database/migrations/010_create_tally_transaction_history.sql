-- Migration: 010_create_tally_transaction_history.sql
-- Description: Per-vendor, per-company history of all mapping decisions
--              for Tally. Drives "Layer 0" auto-fill in sales, purchase,
--              and bank-entry flows. A single unified table with a
--              mapping_type discriminator covers all 6 mapping types.

CREATE TABLE IF NOT EXISTS tally_transaction_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL,
    company_name TEXT NOT NULL,
    mapping_type TEXT NOT NULL CHECK (mapping_type IN (
        'party_ledger',
        'expense_ledger',
        'item_mapping',
        'tax_treatment',
        'tds_section',
        'bank_description'
    )),
    source_identifier TEXT NOT NULL,
    target_value TEXT NOT NULL,
    metadata JSONB DEFAULT '{}',
    usage_count INTEGER NOT NULL DEFAULT 1,
    last_used_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (user_id, company_name, mapping_type, source_identifier, target_value)
);

CREATE INDEX IF NOT EXISTS idx_tth_lookup
    ON tally_transaction_history
    (user_id, company_name, mapping_type, source_identifier, usage_count DESC, last_used_at DESC);

COMMENT ON TABLE tally_transaction_history IS
    'Per-vendor, per-company history of mapping decisions for Tally. '
    'Drives Layer 0 (history-based) auto-fill suggestions that run before '
    'the 4-layer matching pipeline (mapping rules -> GSTIN -> fuzzy -> LLM).';
COMMENT ON COLUMN tally_transaction_history.mapping_type IS
    'Discriminator: party_ledger, expense_ledger, item_mapping, tax_treatment, tds_section, bank_description.';
COMMENT ON COLUMN tally_transaction_history.source_identifier IS
    'Normalized key (via normalize_name): vendor name, item description, or cleaned bank narration.';
COMMENT ON COLUMN tally_transaction_history.target_value IS
    'The matched value: ledger name, stock item name, TDS section, or "tax_config" sentinel for tax_treatment.';
COMMENT ON COLUMN tally_transaction_history.metadata IS
    'Type-specific extras as JSONB: gstin, parent_group, tax rates, payment_type, voucher_type, etc.';
COMMENT ON COLUMN tally_transaction_history.usage_count IS
    'Times this mapping has been confirmed. Higher => stronger suggestion. Confidence = 0.88 + 0.02 * count, max 0.99.';
