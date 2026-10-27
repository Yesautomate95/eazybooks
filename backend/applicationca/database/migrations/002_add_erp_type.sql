-- Migration: 002_add_erp_type.sql
-- Description: Add erp_type column for Tally / QuickBooks Desktop coexistence.
--              Each company-mapped folder ("lockr") records which ERP backs it
--              so the frontend can route to the correct view (Tally* vs QB*).
-- Date: 2026-04-25

-- ============================================
-- 1. lockr.erp_type
--    'tally' (default for back-compat) | 'qb' (QuickBooks Desktop)
-- ============================================
ALTER TABLE lockr
    ADD COLUMN IF NOT EXISTS erp_type TEXT NOT NULL DEFAULT 'tally'
        CHECK (erp_type IN ('tally', 'qb'));

CREATE INDEX IF NOT EXISTS idx_lockr_erp_type ON lockr(erp_type);


-- ============================================
-- 2. mapping_rules.erp_type
--    Mapping rules are ERP-specific. Tally targets ledgers, QB targets accounts
--    or customers/vendors. Existing rows default to 'tally'.
-- ============================================
ALTER TABLE mapping_rules
    ADD COLUMN IF NOT EXISTS erp_type TEXT NOT NULL DEFAULT 'tally'
        CHECK (erp_type IN ('tally', 'qb'));


-- ============================================
-- 3. imports.erp_type (if imports table exists)
-- ============================================
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'imports') THEN
        ALTER TABLE imports
            ADD COLUMN IF NOT EXISTS erp_type TEXT NOT NULL DEFAULT 'tally'
                CHECK (erp_type IN ('tally', 'qb'));
    END IF;
END $$;
