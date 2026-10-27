-- Migration 032: Row-Level Security for the TallyChat read path.
--
-- Background
-- ----------
-- TallyChat lets an LLM author arbitrary SELECT SQL against the shared Tally
-- tables. Tenant scoping previously relied on the model embedding
-- `WHERE user_id = '...' AND company_name = '...'` plus a soft substring check
-- in the agent. That is prompt discipline, not a guarantee: a forgotten filter
-- on a joined table could leak another tenant's financial data.
--
-- This migration moves the guarantee into Postgres. It introduces a
-- least-privilege role `tally_chat_ro` and enables Row-Level Security on every
-- Tally table that carries (user_id, company_name) (see migration 031), with a
-- SELECT policy keyed on per-transaction GUCs. The agent assumes this role via
-- `SET LOCAL ROLE` and publishes the tenant with set_config() inside its
-- READ ONLY transaction, so the database itself filters every read.
--
-- Why a role + SET LOCAL ROLE (not FORCE):
--   - The owner role (this DATABASE_URL) and superusers BYPASS RLS. The backend
--     analytics reads, sync_masters writes, and the Electron Tally loader all
--     run as the owner and must stay unaffected.
--   - tally_chat_ro is neither the owner nor a superuser, so RLS APPLIES to it
--     automatically. The agent enters it per-query; everything else bypasses.
--   - The policy is FOR SELECT TO tally_chat_ro only, so writes are never
--     touched even on tables where RLS is enabled.
--
-- Fail-closed: current_setting(..., true) returns NULL when the GUC is unset,
-- so a missing set_config() yields ZERO rows rather than a leak.
--
-- Idempotent / re-runnable: re-running picks up any new tenant-scoped tables
-- the loader has since created.

DO $$
DECLARE
    t TEXT;
BEGIN
    -- 1. Least-privilege read role + the membership that lets the owner
    --    (this migration's CURRENT_USER, == the backend's runtime role) SET ROLE
    --    into it. NOLOGIN: it is only ever entered via SET LOCAL ROLE, never
    --    connected to directly.
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tally_chat_ro') THEN
        CREATE ROLE tally_chat_ro NOLOGIN;
    END IF;
    GRANT USAGE ON SCHEMA public TO tally_chat_ro;
    EXECUTE format('GRANT tally_chat_ro TO %I', CURRENT_USER);

    -- 2. For every public mst_/trn_ table that has BOTH tenant columns: grant
    --    SELECT to the read role, enable RLS, and (re)create the tenant policy.
    FOR t IN
        SELECT c.relname
          FROM pg_class c
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = 'public'
           AND c.relkind = 'r'
           AND (c.relname LIKE 'mst_%' OR c.relname LIKE 'trn_%')
           AND EXISTS (
               SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = c.relname
                  AND column_name = 'user_id')
           AND EXISTS (
               SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = c.relname
                  AND column_name = 'company_name')
    LOOP
        EXECUTE format('GRANT SELECT ON %I TO tally_chat_ro', t);
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('DROP POLICY IF EXISTS tally_tenant_read ON %I', t);
        EXECUTE format($p$
            CREATE POLICY tally_tenant_read ON %I
                FOR SELECT TO tally_chat_ro
                USING (
                    user_id = current_setting('app.user_id', true)
                    AND company_name = current_setting('app.company_name', true)
                )
        $p$, t);
        RAISE NOTICE 'RLS tenant policy applied to %', t;
    END LOOP;
END $$;
