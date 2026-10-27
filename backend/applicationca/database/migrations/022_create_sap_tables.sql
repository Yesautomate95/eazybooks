-- Migration: 022_create_sap_tables.sql
-- Description: All SAP S/4HANA Cloud integration tables, mirroring the Xero
--              schema (019) one-for-one. Differences vs Xero:
--                * Company key is `company_code` (SAP Company Code, e.g. '1000').
--                  The tenant itself is identified by the API host (SAP_API_BASE);
--                  one OAuth credential covers a tenant, and Company Code selects
--                  the posting org per request.
--                * sap_tokens.refresh_token is NULLABLE — client_credentials mode
--                  has no refresh token (we mint a fresh bearer on expiry). It is
--                  populated (and rotates) only in authorization_code mode.
--                * sap_tokens carries `api_base` (OData host) + `auth_mode`.
-- Date: 2026-06-04


-- ============================================================
-- 1. OAuth tokens — one row per user.
--    client_credentials: refresh_token NULL, token re-minted on expiry.
--    authorization_code: refresh_token set and rotated on refresh.
-- ============================================================
CREATE TABLE IF NOT EXISTS sap_tokens (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         TEXT NOT NULL,
    access_token    TEXT NOT NULL,
    refresh_token   TEXT,                         -- NULL in client_credentials mode
    token_type      TEXT NOT NULL DEFAULT 'Bearer',
    expires_at      TIMESTAMP NOT NULL,           -- access_token expiry (UTC)
    api_base        TEXT NOT NULL DEFAULT '',     -- OData host (SAP_API_BASE)
    company_code    TEXT,                          -- default Company Code, if chosen at connect
    auth_mode       TEXT NOT NULL DEFAULT 'client_credentials'
                        CHECK (auth_mode IN ('client_credentials', 'authorization_code')),
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    UNIQUE (user_id)
);

CREATE INDEX IF NOT EXISTS idx_sap_tokens_user ON sap_tokens(user_id);
CREATE INDEX IF NOT EXISTS idx_sap_tokens_expires ON sap_tokens(expires_at);


-- ============================================================
-- 2. Aliases — learned name → SAP-entity mappings (mirrors xero_aliases).
--    entity_id holds the SAP BusinessPartner / GLAccount / Product id.
-- ============================================================
CREATE TABLE IF NOT EXISTS sap_aliases (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             TEXT NOT NULL,
    company_code        TEXT NOT NULL,
    entity_type         TEXT NOT NULL CHECK (entity_type IN ('customer', 'vendor', 'item', 'account')),
    alias_normalized    TEXT NOT NULL,
    entity_id           TEXT NOT NULL,            -- SAP BusinessPartner / GLAccount / Product
    entity_name         TEXT NOT NULL,
    entity_code         TEXT,                     -- optional secondary code
    created_from_ui     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    UNIQUE (user_id, company_code, entity_type, alias_normalized)
);

CREATE INDEX IF NOT EXISTS idx_sap_aliases_lookup
    ON sap_aliases(user_id, company_code, entity_type);


-- ============================================================
-- 3. Vendor → GL account history (mirrors xero_vendor_account_history).
-- ============================================================
CREATE TABLE IF NOT EXISTS sap_vendor_account_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL,
    company_code TEXT NOT NULL,
    vendor_name_canonical TEXT NOT NULL,
    vendor_list_id TEXT,
    account_list_id TEXT NOT NULL,
    account_name TEXT NOT NULL,
    usage_count INTEGER NOT NULL DEFAULT 1,
    last_used_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (user_id, company_code, vendor_name_canonical, account_list_id)
);

CREATE INDEX IF NOT EXISTS idx_sap_vah_lookup
    ON sap_vendor_account_history
    (user_id, company_code, vendor_name_canonical, usage_count DESC, last_used_at DESC);


-- ============================================================
-- 4. Item / bank-narration → account history (mirrors xero_mapping_history).
-- ============================================================
CREATE TABLE IF NOT EXISTS sap_mapping_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL,
    company_code TEXT NOT NULL,
    mapping_type TEXT NOT NULL CHECK (mapping_type IN ('item_account', 'bank_description_ledger')),
    source_identifier TEXT NOT NULL,
    target_list_id TEXT NOT NULL,
    target_name TEXT NOT NULL,
    metadata JSONB DEFAULT '{}',
    usage_count INTEGER NOT NULL DEFAULT 1,
    last_used_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (user_id, company_code, mapping_type, source_identifier, target_list_id)
);

CREATE INDEX IF NOT EXISTS idx_sap_mh_lookup
    ON sap_mapping_history
    (user_id, company_code, mapping_type, source_identifier, usage_count DESC, last_used_at DESC);


-- ============================================================
-- 5. Bank reconciliation reports + matches (mirrors xero_recon_*).
-- ============================================================
CREATE TABLE IF NOT EXISTS sap_recon_reports (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         TEXT NOT NULL,
    company_code    TEXT NOT NULL,
    account_id      TEXT NOT NULL,
    account_name    TEXT,
    period_start    DATE NOT NULL,
    period_end      DATE NOT NULL,
    statement_json  JSONB NOT NULL,
    sap_txns_json   JSONB NOT NULL,
    summary_json    JSONB,
    sap_closing_balance NUMERIC(18, 2),
    needs_review    BOOLEAN DEFAULT FALSE,
    status          TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'completed')),
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_sap_recon_reports_user
    ON sap_recon_reports(user_id, company_code, account_id, period_end DESC);

CREATE TABLE IF NOT EXISTS sap_recon_matches (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    report_id       UUID NOT NULL REFERENCES sap_recon_reports(id) ON DELETE CASCADE,
    sap_txn_id      TEXT,
    sap_txn_type    TEXT,
    statement_idx   INT,
    amount          NUMERIC(18, 2),
    confidence      NUMERIC(4, 3),
    reason          TEXT,
    manual          BOOLEAN DEFAULT FALSE,
    sap_txn_json    JSONB,
    statement_row_json JSONB,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_sap_recon_matches_report
    ON sap_recon_matches(report_id);


-- ============================================================
-- 6. Drafts (mirrors xero_drafts).
-- ============================================================
CREATE TABLE IF NOT EXISTS sap_drafts (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     TEXT NOT NULL,
    company_code TEXT NOT NULL,
    flow        TEXT NOT NULL CHECK (flow IN ('purchase', 'sales', 'bank_entries')),
    title       TEXT,
    payload     JSONB NOT NULL,
    created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_sap_drafts_user
    ON sap_drafts (user_id, company_code, flow, updated_at DESC);


-- ============================================================
-- 7. Chat messages (mirrors xero_chat_messages).
-- ============================================================
CREATE TABLE IF NOT EXISTS sap_chat_messages (
    id           BIGSERIAL PRIMARY KEY,
    user_id      TEXT NOT NULL,
    company_code TEXT NOT NULL DEFAULT '',
    thread_id    TEXT NOT NULL,
    role         TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content      TEXT NOT NULL,
    metadata     JSONB,
    created_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_sap_chat_messages_thread
    ON sap_chat_messages (user_id, company_code, thread_id, created_at);

CREATE INDEX IF NOT EXISTS idx_sap_chat_messages_user_recent
    ON sap_chat_messages (user_id, company_code, created_at DESC);


-- ============================================================
-- 8. Company profile / brief cache (mirrors xero_company_profile).
-- ============================================================
CREATE TABLE IF NOT EXISTS sap_company_profile (
    user_id      TEXT NOT NULL,
    company_code TEXT NOT NULL,
    brief_md     TEXT NOT NULL,
    refreshed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, company_code)
);
