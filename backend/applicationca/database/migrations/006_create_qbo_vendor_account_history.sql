-- Migration: 006_create_qbo_vendor_account_history.sql
-- Description: Per-vendor, per-realm history of expense-account assignments
--              for QuickBooks Online. Drives "remember this vendor → account"
--              auto-fill in bill and bank-entry flows.

CREATE TABLE IF NOT EXISTS qbo_vendor_account_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL,
    realm_id TEXT NOT NULL,
    vendor_name_canonical TEXT NOT NULL,
    vendor_list_id TEXT,
    account_list_id TEXT NOT NULL,
    account_name TEXT NOT NULL,
    usage_count INTEGER NOT NULL DEFAULT 1,
    last_used_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (user_id, realm_id, vendor_name_canonical, account_list_id)
);

CREATE INDEX IF NOT EXISTS idx_qbo_vah_lookup
    ON qbo_vendor_account_history
    (user_id, realm_id, vendor_name_canonical, usage_count DESC, last_used_at DESC);

COMMENT ON TABLE qbo_vendor_account_history IS
    'Per-vendor, per-realm history of expense-account assignments for QBO. '
    'Drives vendor->account auto-fill suggestions.';
COMMENT ON COLUMN qbo_vendor_account_history.vendor_name_canonical IS
    'Vendor name after normalize_name() (suffix-stripped, lowercased, whitespace-collapsed).';
COMMENT ON COLUMN qbo_vendor_account_history.usage_count IS
    'Times this vendor has been booked to this account. Higher => stronger suggestion.';
