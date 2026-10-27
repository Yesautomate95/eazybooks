-- Migration: 013_add_erp_type_zoho_xero.sql
-- Description: Extend the erp_type discriminator (added in 002) to recognise the
--              two new cloud accounting platforms — Zoho Books ('zoho') and
--              Xero ('xero') — alongside Tally ('tally') and QuickBooks ('qb').
--              The original CHECK constraints only allowed ('tally', 'qb'); we
--              drop and recreate them to add the new values. Idempotent.
-- Date: 2026-05-30

-- ============================================
-- 1. lockr.erp_type
-- ============================================
ALTER TABLE lockr DROP CONSTRAINT IF EXISTS lockr_erp_type_check;
ALTER TABLE lockr
    ADD CONSTRAINT lockr_erp_type_check
        CHECK (erp_type IN ('tally', 'qb', 'zoho', 'xero'));

-- ============================================
-- 2. mapping_rules.erp_type
-- ============================================
ALTER TABLE mapping_rules DROP CONSTRAINT IF EXISTS mapping_rules_erp_type_check;
ALTER TABLE mapping_rules
    ADD CONSTRAINT mapping_rules_erp_type_check
        CHECK (erp_type IN ('tally', 'qb', 'zoho', 'xero'));

-- ============================================
-- 3. imports.erp_type (only if the table exists)
-- ============================================
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'imports') THEN
        ALTER TABLE imports DROP CONSTRAINT IF EXISTS imports_erp_type_check;
        ALTER TABLE imports
            ADD CONSTRAINT imports_erp_type_check
                CHECK (erp_type IN ('tally', 'qb', 'zoho', 'xero'));
    END IF;
END $$;
