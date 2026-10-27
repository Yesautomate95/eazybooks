-- Migration: 014_create_zoho_tables.sql
-- Description: All Zoho Books integration tables, mirroring the QuickBooks Online
--              schema (migrations 003-011) one-for-one. Differences vs QBO:
--                * Company key is `organization_id` (Zoho org) instead of `realm_id`.
--                * OAuth tokens are keyed per-user (NOT per-company): one Zoho
--                  token grants access to every organization the user owns, and
--                  the active org is chosen per API call via ?organization_id=.
--                  So `zoho_tokens` is UNIQUE(user_id) and stores the resolved
--                  api_domain + data-center for that user's account.
--                * Alias / history tables use platform-neutral `entity_id` /
--                  `entity_name` columns (the shared qbo_matcher consumes them).
-- Date: 2026-05-30


-- ============================================================
-- 1. OAuth tokens — one row per user (org-agnostic).
--    Zoho access tokens last ~1h; refresh tokens are long-lived and do NOT
--    rotate, so refresh_token is only overwritten when Zoho returns a new one.
-- ============================================================
CREATE TABLE IF NOT EXISTS zoho_tokens (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         TEXT NOT NULL,
    access_token    TEXT NOT NULL,
    refresh_token   TEXT NOT NULL,
    token_type      TEXT NOT NULL DEFAULT 'Bearer',
    expires_at      TIMESTAMP NOT NULL,            -- access_token expiry (UTC)
    api_domain      TEXT NOT NULL DEFAULT 'https://www.zohoapis.com',  -- per-DC API host
    dc              TEXT NOT NULL DEFAULT 'com',   -- data center: com|in|eu|com.au|jp|ca|sa
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    UNIQUE (user_id)
);

CREATE INDEX IF NOT EXISTS idx_zoho_tokens_user ON zoho_tokens(user_id);
CREATE INDEX IF NOT EXISTS idx_zoho_tokens_expires ON zoho_tokens(expires_at);


-- ============================================================
-- 2. Aliases — learned name → Zoho-entity-id mappings (mirrors qbo_aliases).
--    entity_id / entity_name are platform-neutral; the shared matcher reads them.
-- ============================================================
CREATE TABLE IF NOT EXISTS zoho_aliases (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             TEXT NOT NULL,
    organization_id     TEXT NOT NULL,
    entity_type         TEXT NOT NULL CHECK (entity_type IN ('customer', 'vendor', 'item', 'account')),
    alias_normalized    TEXT NOT NULL,
    entity_id           TEXT NOT NULL,            -- Zoho contact_id / item_id / account_id
    entity_name         TEXT NOT NULL,
    created_from_ui     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    UNIQUE (user_id, organization_id, entity_type, alias_normalized)
);

CREATE INDEX IF NOT EXISTS idx_zoho_aliases_lookup
    ON zoho_aliases(user_id, organization_id, entity_type);


-- ============================================================
-- 3. Vendor → account history (mirrors qbo_vendor_account_history).
-- ============================================================
CREATE TABLE IF NOT EXISTS zoho_vendor_account_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL,
    organization_id TEXT NOT NULL,
    vendor_name_canonical TEXT NOT NULL,
    vendor_list_id TEXT,
    account_list_id TEXT NOT NULL,
    account_name TEXT NOT NULL,
    usage_count INTEGER NOT NULL DEFAULT 1,
    last_used_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (user_id, organization_id, vendor_name_canonical, account_list_id)
);

CREATE INDEX IF NOT EXISTS idx_zoho_vah_lookup
    ON zoho_vendor_account_history
    (user_id, organization_id, vendor_name_canonical, usage_count DESC, last_used_at DESC);


-- ============================================================
-- 4. Item / bank-narration → account history (mirrors qbo_mapping_history).
-- ============================================================
CREATE TABLE IF NOT EXISTS zoho_mapping_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL,
    organization_id TEXT NOT NULL,
    mapping_type TEXT NOT NULL CHECK (mapping_type IN ('item_account', 'bank_description_ledger')),
    source_identifier TEXT NOT NULL,
    target_list_id TEXT NOT NULL,
    target_name TEXT NOT NULL,
    metadata JSONB DEFAULT '{}',
    usage_count INTEGER NOT NULL DEFAULT 1,
    last_used_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (user_id, organization_id, mapping_type, source_identifier, target_list_id)
);

CREATE INDEX IF NOT EXISTS idx_zoho_mh_lookup
    ON zoho_mapping_history
    (user_id, organization_id, mapping_type, source_identifier, usage_count DESC, last_used_at DESC);


-- ============================================================
-- 5. Bank reconciliation reports + matches (mirrors qb_recon_reports / qb_recon_matches).
-- ============================================================
CREATE TABLE IF NOT EXISTS zoho_recon_reports (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         TEXT NOT NULL,
    organization_id TEXT NOT NULL,
    account_id      TEXT NOT NULL,
    account_name    TEXT,
    period_start    DATE NOT NULL,
    period_end      DATE NOT NULL,
    statement_json  JSONB NOT NULL,
    zoho_txns_json  JSONB NOT NULL,
    summary_json    JSONB,
    zoho_closing_balance NUMERIC(18, 2),
    needs_review    BOOLEAN DEFAULT FALSE,
    status          TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'completed')),
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_zoho_recon_reports_user
    ON zoho_recon_reports(user_id, organization_id, account_id, period_end DESC);

CREATE TABLE IF NOT EXISTS zoho_recon_matches (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    report_id       UUID NOT NULL REFERENCES zoho_recon_reports(id) ON DELETE CASCADE,
    zoho_txn_id     TEXT,
    zoho_txn_type   TEXT,
    statement_idx   INT,
    amount          NUMERIC(18, 2),
    confidence      NUMERIC(4, 3),
    reason          TEXT,
    manual          BOOLEAN DEFAULT FALSE,
    zoho_txn_json   JSONB,
    statement_row_json JSONB,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_zoho_recon_matches_report
    ON zoho_recon_matches(report_id);


-- ============================================================
-- 6. Drafts (mirrors qbo_drafts).
-- ============================================================
CREATE TABLE IF NOT EXISTS zoho_drafts (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     TEXT NOT NULL,
    organization_id TEXT NOT NULL,
    flow        TEXT NOT NULL CHECK (flow IN ('purchase', 'sales', 'bank_entries')),
    title       TEXT,
    payload     JSONB NOT NULL,
    created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_zoho_drafts_user
    ON zoho_drafts (user_id, organization_id, flow, updated_at DESC);


-- ============================================================
-- 7. Chat messages (mirrors qb_chat_messages).
-- ============================================================
CREATE TABLE IF NOT EXISTS zoho_chat_messages (
    id           BIGSERIAL PRIMARY KEY,
    user_id      TEXT NOT NULL,
    organization_id TEXT NOT NULL DEFAULT '',
    thread_id    TEXT NOT NULL,
    role         TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content      TEXT NOT NULL,
    metadata     JSONB,
    created_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_zoho_chat_messages_thread
    ON zoho_chat_messages (user_id, organization_id, thread_id, created_at);

CREATE INDEX IF NOT EXISTS idx_zoho_chat_messages_user_recent
    ON zoho_chat_messages (user_id, organization_id, created_at DESC);


-- ============================================================
-- 8. Company profile / brief cache (mirrors qbo_company_profile).
-- ============================================================
CREATE TABLE IF NOT EXISTS zoho_company_profile (
    user_id         TEXT NOT NULL,
    organization_id TEXT NOT NULL,
    brief_md        TEXT NOT NULL,
    refreshed_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, organization_id)
);
