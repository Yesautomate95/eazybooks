-- Migration: 003_create_qbo_tokens.sql
-- Description: Per-user QuickBooks Online OAuth2 tokens.
--              QBO access tokens last 1 hour; refresh tokens last 100 days
--              (rotated on every refresh). One row per (user_id, realm_id).
-- Date: 2026-04-25

CREATE TABLE IF NOT EXISTS qbo_tokens (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         TEXT NOT NULL,
    realm_id        TEXT NOT NULL,                 -- QBO company id
    access_token    TEXT NOT NULL,
    refresh_token   TEXT NOT NULL,
    token_type      TEXT NOT NULL DEFAULT 'bearer',
    expires_at      TIMESTAMP NOT NULL,            -- access_token expiry (UTC)
    refresh_expires_at TIMESTAMP,                  -- refresh_token expiry (UTC, ~100 days)
    qbo_env         TEXT NOT NULL DEFAULT 'sandbox' CHECK (qbo_env IN ('sandbox', 'production')),
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    UNIQUE (user_id, realm_id)
);

CREATE INDEX IF NOT EXISTS idx_qbo_tokens_user ON qbo_tokens(user_id);
CREATE INDEX IF NOT EXISTS idx_qbo_tokens_expires ON qbo_tokens(expires_at);
