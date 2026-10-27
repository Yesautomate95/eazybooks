-- Migration: 005_create_qbo_aliases.sql
-- Description: Persistent name-to-entity mappings learned from user confirmations
--              in the QBO review screens. When a CA picks an existing customer
--              from Tier-2 fuzzy suggestions (instead of creating a duplicate),
--              we save that mapping so future runs hit it on exact lookup.
--              Compounds matcher quality over time; impossible for stock QBO to
--              offer because it lacks the AI-extraction loop that surfaces the
--              variant names in the first place.
-- Date: 2026-05-12

CREATE TABLE IF NOT EXISTS qbo_aliases (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             TEXT NOT NULL,
    realm_id            TEXT NOT NULL,
    entity_type         TEXT NOT NULL CHECK (entity_type IN ('customer', 'vendor', 'item', 'account')),
    alias_normalized    TEXT NOT NULL,                    -- canonicalized form (lowercase, suffix-stripped, etc.)
    qbo_entity_id       TEXT NOT NULL,                    -- QBO Id the alias resolves to
    qbo_entity_name     TEXT NOT NULL,                    -- canonical QBO display name at save time (for audit)
    created_from_ui     BOOLEAN NOT NULL DEFAULT TRUE,    -- TRUE = explicit user pick; reserved for future auto-learning
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    UNIQUE (user_id, realm_id, entity_type, alias_normalized)
);

CREATE INDEX IF NOT EXISTS idx_qbo_aliases_lookup
    ON qbo_aliases(user_id, realm_id, entity_type);
