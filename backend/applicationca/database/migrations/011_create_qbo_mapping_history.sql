-- Migration: 011_create_qbo_mapping_history.sql
-- Description: Extends QBO history beyond vendor→account (which lives in
--              qbo_vendor_account_history) to also cover item-level account
--              mappings and bank narration→ledger associations.

CREATE TABLE IF NOT EXISTS qbo_mapping_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL,
    realm_id TEXT NOT NULL,
    mapping_type TEXT NOT NULL CHECK (mapping_type IN ('item_account', 'bank_description_ledger')),
    source_identifier TEXT NOT NULL,
    target_list_id TEXT NOT NULL,
    target_name TEXT NOT NULL,
    metadata JSONB DEFAULT '{}',
    usage_count INTEGER NOT NULL DEFAULT 1,
    last_used_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (user_id, realm_id, mapping_type, source_identifier, target_list_id)
);

CREATE INDEX IF NOT EXISTS idx_qbo_mh_lookup
    ON qbo_mapping_history
    (user_id, realm_id, mapping_type, source_identifier, usage_count DESC, last_used_at DESC);

COMMENT ON TABLE qbo_mapping_history IS
    'Extended QBO mapping history for item→account and bank-description→ledger. '
    'Complements qbo_vendor_account_history (which handles vendor→account).';
COMMENT ON COLUMN qbo_mapping_history.mapping_type IS
    'item_account: line-item description→expense account. bank_description_ledger: cleaned narration→account.';
COMMENT ON COLUMN qbo_mapping_history.source_identifier IS
    'Normalized key: item description or cleaned bank narration (via normalize_name / bank_narration_tokenizer).';
