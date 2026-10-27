-- Migration: 015_create_xero_tables.sql
-- Description: All Xero integration tables, mirroring the Zoho schema (014)
--              one-for-one. Differences vs Zoho:
--                * Company key is `tenant_id` (Xero tenant/organisation GUID).
--                * OAuth tokens are per-user (one token set covers every tenant
--                  the user authorized; the active tenant is chosen per call via
--                  the Xero-tenant-id header). Xero refresh tokens ROTATE on
--                  every refresh, so refresh_token is always overwritten.
--                * No api_domain/dc — the Xero API host is fixed.
-- Date: 2026-05-30


-- ============================================================
-- 1. OAuth tokens — one row per user (tenant-agnostic).
--    Xero access tokens last 30 min; refresh tokens rotate on each refresh and
--    expire after 60 days of inactivity.
-- ============================================================
CREATE TABLE IF NOT EXISTS xero_tokens (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         TEXT NOT NULL,
    access_token    TEXT NOT NULL,
    refresh_token   TEXT NOT NULL,
    token_type      TEXT NOT NULL DEFAULT 'Bearer',
    expires_at      TIMESTAMP NOT NULL,            -- access_token expiry (UTC)
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    UNIQUE (user_id)
);

CREATE INDEX IF NOT EXISTS idx_xero_tokens_user ON xero_tokens(user_id);
CREATE INDEX IF NOT EXISTS idx_xero_tokens_expires ON xero_tokens(expires_at);


-- ============================================================
-- 2. Aliases — learned name → Xero-entity mappings (mirrors zoho_aliases).
--    entity_id holds the Xero ContactID / ItemID / AccountID; entity_code holds
--    the Xero Code (line items reference accounts/items by Code, not GUID).
-- ============================================================
CREATE TABLE IF NOT EXISTS xero_aliases (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             TEXT NOT NULL,
    tenant_id           TEXT NOT NULL,
    entity_type         TEXT NOT NULL CHECK (entity_type IN ('customer', 'vendor', 'item', 'account')),
    alias_normalized    TEXT NOT NULL,
    entity_id           TEXT NOT NULL,            -- Xero ContactID / ItemID / AccountID
    entity_name         TEXT NOT NULL,
    entity_code         TEXT,                     -- Xero Code (for item/account line refs)
    created_from_ui     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    UNIQUE (user_id, tenant_id, entity_type, alias_normalized)
);

CREATE INDEX IF NOT EXISTS idx_xero_aliases_lookup
    ON xero_aliases(user_id, tenant_id, entity_type);


-- ============================================================
-- 3. Vendor → account history (mirrors zoho_vendor_account_history).
-- ============================================================
CREATE TABLE IF NOT EXISTS xero_vendor_account_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    vendor_name_canonical TEXT NOT NULL,
    vendor_list_id TEXT,
    account_list_id TEXT NOT NULL,
    account_name TEXT NOT NULL,
    usage_count INTEGER NOT NULL DEFAULT 1,
    last_used_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (user_id, tenant_id, vendor_name_canonical, account_list_id)
);

CREATE INDEX IF NOT EXISTS idx_xero_vah_lookup
    ON xero_vendor_account_history
    (user_id, tenant_id, vendor_name_canonical, usage_count DESC, last_used_at DESC);


-- ============================================================
-- 4. Item / bank-narration → account history (mirrors zoho_mapping_history).
-- ============================================================
CREATE TABLE IF NOT EXISTS xero_mapping_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    mapping_type TEXT NOT NULL CHECK (mapping_type IN ('item_account', 'bank_description_ledger')),
    source_identifier TEXT NOT NULL,
    target_list_id TEXT NOT NULL,
    target_name TEXT NOT NULL,
    metadata JSONB DEFAULT '{}',
    usage_count INTEGER NOT NULL DEFAULT 1,
    last_used_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (user_id, tenant_id, mapping_type, source_identifier, target_list_id)
);

CREATE INDEX IF NOT EXISTS idx_xero_mh_lookup
    ON xero_mapping_history
    (user_id, tenant_id, mapping_type, source_identifier, usage_count DESC, last_used_at DESC);


-- ============================================================
-- 5. Bank reconciliation reports + matches (mirrors zoho_recon_*).
-- ============================================================
CREATE TABLE IF NOT EXISTS xero_recon_reports (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         TEXT NOT NULL,
    tenant_id       TEXT NOT NULL,
    account_id      TEXT NOT NULL,
    account_name    TEXT,
    period_start    DATE NOT NULL,
    period_end      DATE NOT NULL,
    statement_json  JSONB NOT NULL,
    xero_txns_json  JSONB NOT NULL,
    summary_json    JSONB,
    xero_closing_balance NUMERIC(18, 2),
    needs_review    BOOLEAN DEFAULT FALSE,
    status          TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'completed')),
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_xero_recon_reports_user
    ON xero_recon_reports(user_id, tenant_id, account_id, period_end DESC);

CREATE TABLE IF NOT EXISTS xero_recon_matches (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    report_id       UUID NOT NULL REFERENCES xero_recon_reports(id) ON DELETE CASCADE,
    xero_txn_id     TEXT,
    xero_txn_type   TEXT,
    statement_idx   INT,
    amount          NUMERIC(18, 2),
    confidence      NUMERIC(4, 3),
    reason          TEXT,
    manual          BOOLEAN DEFAULT FALSE,
    xero_txn_json   JSONB,
    statement_row_json JSONB,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_xero_recon_matches_report
    ON xero_recon_matches(report_id);


-- ============================================================
-- 6. Drafts (mirrors zoho_drafts).
-- ============================================================
CREATE TABLE IF NOT EXISTS xero_drafts (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     TEXT NOT NULL,
    tenant_id   TEXT NOT NULL,
    flow        TEXT NOT NULL CHECK (flow IN ('purchase', 'sales', 'bank_entries')),
    title       TEXT,
    payload     JSONB NOT NULL,
    created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_xero_drafts_user
    ON xero_drafts (user_id, tenant_id, flow, updated_at DESC);


-- ============================================================
-- 7. Chat messages (mirrors zoho_chat_messages).
-- ============================================================
CREATE TABLE IF NOT EXISTS xero_chat_messages (
    id           BIGSERIAL PRIMARY KEY,
    user_id      TEXT NOT NULL,
    tenant_id    TEXT NOT NULL DEFAULT '',
    thread_id    TEXT NOT NULL,
    role         TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content      TEXT NOT NULL,
    metadata     JSONB,
    created_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_xero_chat_messages_thread
    ON xero_chat_messages (user_id, tenant_id, thread_id, created_at);

CREATE INDEX IF NOT EXISTS idx_xero_chat_messages_user_recent
    ON xero_chat_messages (user_id, tenant_id, created_at DESC);


-- ============================================================
-- 8. Company profile / brief cache (mirrors zoho_company_profile).
-- ============================================================
CREATE TABLE IF NOT EXISTS xero_company_profile (
    user_id     TEXT NOT NULL,
    tenant_id   TEXT NOT NULL,
    brief_md    TEXT NOT NULL,
    refreshed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, tenant_id)
);
