-- Migration 031: Tally analytics — tenant scoping + per-tenant sync state
--
-- Background
-- ----------
-- Tally master/transaction data is written into the shared Postgres by the
-- desktop tally-database-loader (electron_app/tally-loader). The loader's own
-- bundled DDL (config/database-structure-incremental.sql) does NOT define the
-- user_id / company_name columns even though the forked loader inserts them
-- (dist/database.mjs). Depending on which DDL created a given deployment, the
-- columns may be missing. This migration guarantees they exist and are indexed
-- so EVERY analytics read can filter on (user_id, company_name) — the hard
-- data-isolation requirement. A user may own several companies; companies
-- across users must never mix.
--
-- It also introduces tally_sync_state: the backend-owned, per-(user, company)
-- replacement for the loader's GLOBAL `config`-table AlterID watermark. The
-- global watermark let two companies clobber each other's cursor; this fixes
-- that and gives us an honest, surfaced sync status.

-- ---------------------------------------------------------------------------
-- 1. Ensure tenant columns exist on every table analytics reads from.
--    Added nullable: rows written by an older (non-tenant) loader stay NULL
--    and therefore match NO tenant — orphaned, never leaked. A full reconcile
--    repopulates them correctly.
-- ---------------------------------------------------------------------------
DO $$
DECLARE
    t TEXT;
    read_tables TEXT[] := ARRAY[
        'mst_group', 'mst_ledger', 'mst_stock_item', 'mst_stock_group',
        'mst_vouchertype', 'mst_uom', 'mst_godown',
        'trn_voucher', 'trn_accounting', 'trn_inventory',
        'trn_bill', 'trn_bank', 'trn_batch', 'trn_cost_centre'
    ];
BEGIN
    FOREACH t IN ARRAY read_tables LOOP
        -- Skip tables that don't exist in this deployment yet (loader creates
        -- them lazily); the loader will add them with the right shape.
        IF to_regclass(t) IS NOT NULL THEN
            EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS user_id varchar(255)', t);
            EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS company_name varchar(255)', t);
            EXECUTE format(
                'CREATE INDEX IF NOT EXISTS %I ON %I (user_id, company_name)',
                'idx_' || t || '_tenant', t
            );
        END IF;
    END LOOP;

    -- Time-series reads (cash flow, monthly P&L) scan trn_voucher by date
    -- within a tenant — give them a covering composite.
    IF to_regclass('trn_voucher') IS NOT NULL THEN
        CREATE INDEX IF NOT EXISTS idx_trn_voucher_tenant_date
            ON trn_voucher (user_id, company_name, date);
    END IF;

    -- Ledger-amount rollups join trn_accounting on guid then group by ledger.
    IF to_regclass('trn_accounting') IS NOT NULL THEN
        CREATE INDEX IF NOT EXISTS idx_trn_accounting_tenant_ledger
            ON trn_accounting (user_id, company_name, ledger);
    END IF;
END $$;

-- ---------------------------------------------------------------------------
-- 2. Per-(user, company) sync state — cursor + honest status.
--    Replaces the loader's global `config` AlterID watermark.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tally_sync_state (
    user_id                  varchar(255) NOT NULL,
    company_name             varchar(255) NOT NULL,
    -- AlterID watermarks: the highest AlterID we have durably stored for this
    -- tenant. Incremental syncs pull only rows with AlterID > these.
    last_alterid_master      bigint       NOT NULL DEFAULT 0,
    last_alterid_transaction bigint       NOT NULL DEFAULT 0,
    -- Honest status surfaced in the UI.
    --   idle      — never synced / between runs
    --   syncing   — a run is in flight (coin-edge dial)
    --   completed — last run finished cleanly
    --   failed    — last run errored (UI: brass/amber, never red)
    status                   varchar(16)  NOT NULL DEFAULT 'idle'
                              CHECK (status IN ('idle', 'syncing', 'completed', 'failed')),
    -- 'incremental' | 'full' — what the in-flight / last run was.
    sync_mode                varchar(16)  NOT NULL DEFAULT 'incremental',
    last_synced_at           timestamptz,
    last_full_reconcile_at   timestamptz,
    sync_started_at          timestamptz,
    last_error               text,
    -- Per-table row counts from the last successful run, for diagnostics.
    row_counts               jsonb        NOT NULL DEFAULT '{}'::jsonb,
    created_at               timestamptz  NOT NULL DEFAULT NOW(),
    updated_at               timestamptz  NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, company_name)
);

CREATE INDEX IF NOT EXISTS idx_tally_sync_state_user
    ON tally_sync_state (user_id, last_synced_at DESC);

-- Reuse the shared updated_at trigger if the project's helper exists; create a
-- minimal one otherwise so updated_at stays honest.
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS update_tally_sync_state_updated_at ON tally_sync_state;
CREATE TRIGGER update_tally_sync_state_updated_at
    BEFORE UPDATE ON tally_sync_state
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

COMMENT ON TABLE tally_sync_state IS
    'Per-(user, company) Tally incremental-sync cursor and status. Replaces the loader''s global config-table AlterID watermark so companies cannot clobber each other''s cursor.';

-- ---------------------------------------------------------------------------
-- 3. Tenant-scoped incremental scratch tables.
--    The loader's bulkLoad() injects user_id/company_name into EVERY insert,
--    including the _diff/_delete/_vchnumber scratch tables — so they must carry
--    those columns. They are also process-shared across all desktops writing to
--    this DB, so the loader now scopes every scratch read/write/delete by
--    (user_id, company_name) instead of TRUNCATE. That makes concurrent syncs
--    from different tenants safe (one tenant's scratch rows can never cause
--    another tenant's rows to be diffed out and deleted).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS _diff (
    guid         varchar(64) NOT NULL,
    alterid      bigint      NOT NULL DEFAULT 0,
    user_id      varchar(255),
    company_name varchar(255)
);
CREATE TABLE IF NOT EXISTS _delete (
    guid         varchar(64) NOT NULL,
    user_id      varchar(255),
    company_name varchar(255)
);
CREATE TABLE IF NOT EXISTS _vchnumber (
    guid           varchar(64)  NOT NULL,
    voucher_number varchar(256) NOT NULL DEFAULT '',
    user_id        varchar(255),
    company_name   varchar(255)
);

DO $$
DECLARE
    s TEXT;
    scratch_tables TEXT[] := ARRAY['_diff', '_delete', '_vchnumber'];
BEGIN
    FOREACH s IN ARRAY scratch_tables LOOP
        EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS user_id varchar(255)', s);
        EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS company_name varchar(255)', s);
        EXECUTE format(
            'CREATE INDEX IF NOT EXISTS %I ON %I (user_id, company_name)',
            'idx_' || ltrim(s, '_') || '_scratch_tenant', s
        );
    END LOOP;
END $$;
