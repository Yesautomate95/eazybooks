-- 009_create_qbo_company_profile
--
-- Caches a compact markdown "company brief" per (user, realm). The QBO chat
-- agent injects this into its system prompt so it already knows the chart of
-- accounts, top customers/vendors, and transaction volume — saving 1-3 tool
-- calls per conversation.

CREATE TABLE IF NOT EXISTS qbo_company_profile (
    user_id       TEXT NOT NULL,
    realm_id      TEXT NOT NULL,
    brief_md      TEXT NOT NULL,
    refreshed_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, realm_id)
);
