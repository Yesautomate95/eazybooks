-- Migration: Create Import & Mapping Tables
-- Run this script to create the required tables for bank statement import features

-- Enable UUID generation if not already enabled
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ============================================
-- 1. MAPPING RULES TABLE
-- ============================================
CREATE TABLE IF NOT EXISTS mapping_rules (
    rule_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL,
    company_name TEXT NOT NULL,
    rule_name TEXT NOT NULL,
    rule_type TEXT NOT NULL CHECK (rule_type IN ('keyword', 'pattern', 'vendor', 'amount_range')),
    document_type TEXT NOT NULL CHECK (document_type IN ('bank_statement', 'purchase_invoice')),
    keywords JSONB,              -- ["NEFT", "John Doe"]
    pattern TEXT,                -- Regex pattern
    vendor_name TEXT,            -- Vendor name for vendor rules
    amount_min DECIMAL(15,2),
    amount_max DECIMAL(15,2),
    target_ledger TEXT NOT NULL,
    target_voucher_type TEXT,    -- Receipt/Payment/Purchase
    priority INTEGER DEFAULT 0,
    is_active BOOLEAN DEFAULT true,
    match_count INTEGER DEFAULT 0,
    last_used_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, company_name, rule_name)
);

-- Index for faster rule lookups
CREATE INDEX IF NOT EXISTS idx_mapping_rules_lookup
ON mapping_rules(user_id, company_name, document_type, is_active);

CREATE INDEX IF NOT EXISTS idx_mapping_rules_priority
ON mapping_rules(user_id, company_name, document_type, priority DESC);


-- ============================================
-- 2. IMPORT BATCHES TABLE
-- ============================================
CREATE TABLE IF NOT EXISTS import_batches (
    batch_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL,
    company_name TEXT NOT NULL,
    document_type TEXT NOT NULL CHECK (document_type IN ('bank_statement', 'purchase_invoice')),
    files JSONB,                 -- [{filename, file_hash, transaction_count}, ...]
    total_transactions INTEGER DEFAULT 0,
    processed_count INTEGER DEFAULT 0,
    duplicate_count INTEGER DEFAULT 0,
    error_count INTEGER DEFAULT 0,
    pushed_count INTEGER DEFAULT 0,
    status TEXT DEFAULT 'processing' CHECK (status IN ('processing', 'ready', 'pushing', 'completed', 'error')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP
);

-- Index for batch lookups
CREATE INDEX IF NOT EXISTS idx_import_batches_user
ON import_batches(user_id, company_name, created_at DESC);


-- ============================================
-- 3. TRANSACTION IMPORTS TABLE
-- ============================================
CREATE TABLE IF NOT EXISTS transaction_imports (
    import_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    batch_id UUID REFERENCES import_batches(batch_id) ON DELETE CASCADE,
    user_id TEXT NOT NULL,
    company_name TEXT NOT NULL,
    document_type TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    file_hash TEXT,              -- Hash of original file for duplicate detection
    transaction_date DATE NOT NULL,
    reference_number TEXT,
    amount DECIMAL(15,2) NOT NULL,
    description TEXT,
    party_name TEXT,
    transaction_hash TEXT NOT NULL,  -- Hash of (date + amount + ref + party) for duplicate detection
    extracted_data JSONB,        -- Full extracted data
    confidence_scores JSONB,     -- Per-field confidence scores
    debit_ledger TEXT,
    credit_ledger TEXT,
    rule_applied TEXT,           -- Name of mapping rule that was applied
    status TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'processed', 'skipped', 'duplicate', 'error', 'pushed')),
    tally_voucher_number TEXT,   -- Voucher number after push to Tally
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    processed_at TIMESTAMP,
    pushed_at TIMESTAMP,
    UNIQUE(user_id, company_name, transaction_hash)
);

-- Index for duplicate detection
CREATE INDEX IF NOT EXISTS idx_transaction_imports_hash
ON transaction_imports(user_id, company_name, transaction_hash);

CREATE INDEX IF NOT EXISTS idx_transaction_imports_batch
ON transaction_imports(batch_id);

CREATE INDEX IF NOT EXISTS idx_transaction_imports_date
ON transaction_imports(user_id, company_name, transaction_date);


-- ============================================
-- 4. AI EXTRACTION FEEDBACK TABLE
-- ============================================
CREATE TABLE IF NOT EXISTS ai_extraction_feedback (
    feedback_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL,
    company_name TEXT,
    document_type TEXT NOT NULL,
    import_id UUID REFERENCES transaction_imports(import_id) ON DELETE SET NULL,
    original_field TEXT NOT NULL,
    original_value TEXT,
    original_confidence DECIMAL(3,2),
    corrected_value TEXT NOT NULL,
    document_text_snippet TEXT,  -- Context around the field
    extraction_context JSONB,    -- Additional context for learning
    used_for_training BOOLEAN DEFAULT false,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Index for feedback analysis
CREATE INDEX IF NOT EXISTS idx_ai_feedback_field
ON ai_extraction_feedback(user_id, document_type, original_field);

CREATE INDEX IF NOT EXISTS idx_ai_feedback_company
ON ai_extraction_feedback(user_id, company_name, document_type);


-- ============================================
-- Grant permissions if needed (adjust as per your setup)
-- ============================================
-- GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO your_user;
-- GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO your_user;

-- Print confirmation
DO $$
BEGIN
    RAISE NOTICE 'Import tables created successfully!';
END $$;
