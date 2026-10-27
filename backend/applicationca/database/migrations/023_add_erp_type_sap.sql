-- Migration: 023_add_erp_type_sap.sql
-- Description: Extend the erp_type discriminator to recognise SAP S/4HANA Cloud
--              ('sap') alongside Tally ('tally'), QuickBooks ('qb'), Zoho ('zoho')
--              and Xero ('xero'). Mirrors 017_add_erp_type_zoho_xero.sql — drops
--              and recreates the CHECK constraints to add the new value.
--              Idempotent.
-- Date: 2026-06-04

-- ============================================
-- 1. lockr.erp_type
-- ============================================
ALTER TABLE lockr DROP CONSTRAINT IF EXISTS lockr_erp_type_check;
ALTER TABLE lockr
    ADD CONSTRAINT lockr_erp_type_check
        CHECK (erp_type IN ('tally', 'qb', 'zoho', 'xero', 'sap'));

-- ============================================
-- 2. mapping_rules.erp_type
-- ============================================
ALTER TABLE mapping_rules DROP CONSTRAINT IF EXISTS mapping_rules_erp_type_check;
ALTER TABLE mapping_rules
    ADD CONSTRAINT mapping_rules_erp_type_check
        CHECK (erp_type IN ('tally', 'qb', 'zoho', 'xero', 'sap'));

-- ============================================
-- 3. imports.erp_type (only if the table exists)
-- ============================================
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'imports') THEN
        ALTER TABLE imports DROP CONSTRAINT IF EXISTS imports_erp_type_check;
        ALTER TABLE imports
            ADD CONSTRAINT imports_erp_type_check
                CHECK (erp_type IN ('tally', 'qb', 'zoho', 'xero', 'sap'));
    END IF;
END $$;
