-- Migration: 001_create_import_tables.sql
-- Description: Create tables for bank statement and purchase invoice import features
-- Author: Claude Code
-- Date: 2026-03-05

-- ============================================
-- 1. MAPPING_RULES TABLE
-- Stores reusable ledger mapping rules for auto-suggestion
-- ============================================
CREATE TABLE IF NOT EXISTS mapping_rules (
    rule_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL,
    company_name TEXT NOT NULL,
    rule_name TEXT NOT NULL,
    rule_type TEXT NOT NULL CHECK (rule_type IN ('keyword', 'pattern', 'vendor', 'amount_range')),
    document_type TEXT NOT NULL CHECK (document_type IN ('bank_statement', 'purchase_invoice', 'sales_invoice')),

    -- Matching criteria (JSON for flexibility)
    keywords JSONB,                    -- Array of keywords to match: ["NEFT", "John Doe"]
    pattern TEXT,                      -- Regex pattern for advanced matching
    vendor_name TEXT,                  -- Specific vendor name match
    amount_min DECIMAL(15,2),          -- Amount range lower bound
    amount_max DECIMAL(15,2),          -- Amount range upper bound

    -- Target mappings
    target_ledger TEXT NOT NULL,       -- The ledger to assign when rule matches
    target_voucher_type TEXT,          -- Payment/Receipt/Purchase

    -- Metadata
    priority INTEGER DEFAULT 0,        -- Higher priority rules evaluated first
    is_active BOOLEAN DEFAULT true,
    match_count INTEGER DEFAULT 0,     -- Track how often rule is used
    last_used_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- Ensure unique rule names per user/company
    UNIQUE(user_id, company_name, rule_name)
);

-- Indexes for efficient querying
CREATE INDEX IF NOT EXISTS idx_mapping_rules_user_company ON mapping_rules(user_id, company_name);
CREATE INDEX IF NOT EXISTS idx_mapping_rules_type ON mapping_rules(document_type, rule_type);
CREATE INDEX IF NOT EXISTS idx_mapping_rules_keywords ON mapping_rules USING GIN(keywords);
CREATE INDEX IF NOT EXISTS idx_mapping_rules_active ON mapping_rules(is_active) WHERE is_active = true;

-- ============================================
-- 2. IMPORT_BATCHES TABLE
-- Tracks import sessions/batches for grouping transactions
-- ============================================
CREATE TABLE IF NOT EXISTS import_batches (
    batch_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL,
    company_name TEXT NOT NULL,
    document_type TEXT NOT NULL CHECK (document_type IN ('bank_statement', 'purchase_invoice', 'sales_invoice')),

    -- Batch statistics
    total_transactions INTEGER DEFAULT 0,
    processed_count INTEGER DEFAULT 0,
    duplicate_count INTEGER DEFAULT 0,
    error_count INTEGER DEFAULT 0,
    pushed_count INTEGER DEFAULT 0,

    -- Status tracking
    status TEXT DEFAULT 'processing' CHECK (status IN ('processing', 'ready', 'pushed', 'partial', 'error')),

    -- Files in this batch (JSON array)
    files JSONB,                       -- Array of {filename, file_hash, transaction_count}

    -- Timestamps
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_import_batches_user ON import_batches(user_id, company_name);
CREATE INDEX IF NOT EXISTS idx_import_batches_status ON import_batches(status);
CREATE INDEX IF NOT EXISTS idx_import_batches_created ON import_batches(created_at DESC);

-- ============================================
-- 3. TRANSACTION_IMPORTS TABLE
-- Tracks imported transactions for duplicate detection
-- ============================================
CREATE TABLE IF NOT EXISTS transaction_imports (
    import_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    batch_id UUID REFERENCES import_batches(batch_id) ON DELETE CASCADE,
    user_id TEXT NOT NULL,
    company_name TEXT NOT NULL,
    document_type TEXT NOT NULL CHECK (document_type IN ('bank_statement', 'purchase_invoice', 'sales_invoice')),

    -- Source file info
    original_filename TEXT NOT NULL,
    file_hash TEXT,                    -- SHA256 hash for file-level duplicate detection

    -- Transaction identifiers for duplicate detection
    transaction_date DATE NOT NULL,
    reference_number TEXT,             -- Invoice/transaction number
    amount DECIMAL(15,2) NOT NULL,
    description TEXT,
    party_name TEXT,

    -- Composite hash for duplicate detection
    transaction_hash TEXT NOT NULL,    -- Hash of (date + amount + reference + party)

    -- Processing status
    status TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'processed', 'duplicate', 'error', 'pushed', 'skipped')),
    tally_voucher_number TEXT,         -- Voucher number after push to Tally

    -- Extracted data and confidence
    extracted_data JSONB,              -- Full extracted transaction data
    confidence_scores JSONB,           -- AI confidence scores per field: {"party_name": 0.85, "amount": 0.95}

    -- Ledger mappings
    debit_ledger TEXT,
    credit_ledger TEXT,
    rule_applied TEXT,                 -- Which mapping rule was applied (if any)

    -- Timestamps
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    processed_at TIMESTAMP,
    pushed_at TIMESTAMP,

    -- Unique constraint for duplicate detection (same transaction can't be imported twice)
    UNIQUE(user_id, company_name, transaction_hash)
);

-- Indexes for efficient querying
CREATE INDEX IF NOT EXISTS idx_transaction_imports_user ON transaction_imports(user_id, company_name);
CREATE INDEX IF NOT EXISTS idx_transaction_imports_batch ON transaction_imports(batch_id);
CREATE INDEX IF NOT EXISTS idx_transaction_imports_hash ON transaction_imports(transaction_hash);
CREATE INDEX IF NOT EXISTS idx_transaction_imports_status ON transaction_imports(status);
CREATE INDEX IF NOT EXISTS idx_transaction_imports_date ON transaction_imports(transaction_date);
CREATE INDEX IF NOT EXISTS idx_transaction_imports_party ON transaction_imports(party_name);

-- ============================================
-- 4. AI_EXTRACTION_FEEDBACK TABLE
-- Store user corrections for AI learning and improvement
-- ============================================
CREATE TABLE IF NOT EXISTS ai_extraction_feedback (
    feedback_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL,
    company_name TEXT NOT NULL,
    document_type TEXT NOT NULL CHECK (document_type IN ('bank_statement', 'purchase_invoice', 'sales_invoice')),

    -- Import reference
    import_id UUID REFERENCES transaction_imports(import_id) ON DELETE SET NULL,

    -- Original extraction
    original_field TEXT NOT NULL,      -- Field name: party_name, amount, date, etc.
    original_value TEXT,
    original_confidence DECIMAL(3,2),

    -- User correction
    corrected_value TEXT NOT NULL,

    -- Context for learning
    document_text_snippet TEXT,        -- Surrounding text for context (truncated)
    extraction_context JSONB,          -- Additional context: {file_type, page_number, etc.}

    -- Timestamps
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_ai_feedback_user ON ai_extraction_feedback(user_id, company_name);
CREATE INDEX IF NOT EXISTS idx_ai_feedback_field ON ai_extraction_feedback(document_type, original_field);
CREATE INDEX IF NOT EXISTS idx_ai_feedback_created ON ai_extraction_feedback(created_at DESC);

-- ============================================
-- HELPER FUNCTION: Update updated_at timestamp
-- ============================================
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Trigger for mapping_rules
DROP TRIGGER IF EXISTS update_mapping_rules_updated_at ON mapping_rules;
CREATE TRIGGER update_mapping_rules_updated_at
    BEFORE UPDATE ON mapping_rules
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- ============================================
-- COMMENTS for documentation
-- ============================================
COMMENT ON TABLE mapping_rules IS 'Stores reusable ledger mapping rules for auto-suggestion in bank statement and invoice imports';
COMMENT ON TABLE import_batches IS 'Tracks import sessions/batches for grouping transactions and tracking progress';
COMMENT ON TABLE transaction_imports IS 'Tracks individual imported transactions for duplicate detection and audit trail';
COMMENT ON TABLE ai_extraction_feedback IS 'Stores user corrections to AI extractions for continuous learning';

COMMENT ON COLUMN mapping_rules.keywords IS 'JSON array of keywords to match in transaction descriptions';
COMMENT ON COLUMN mapping_rules.pattern IS 'Regex pattern for advanced matching (optional)';
COMMENT ON COLUMN mapping_rules.priority IS 'Higher values = higher priority. Rules with same priority evaluated in order of specificity';
COMMENT ON COLUMN transaction_imports.transaction_hash IS 'SHA256 hash of normalized (date+amount+reference+party) for duplicate detection';
COMMENT ON COLUMN transaction_imports.confidence_scores IS 'AI confidence scores per field: {"party_name": 0.85, "amount": 0.95, "date": 0.99}';
