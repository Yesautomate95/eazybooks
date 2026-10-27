-- Migration 023: Composite mapping rules.
--
-- mapping_rules originally supported a single-condition match per rule
-- (keyword OR pattern OR vendor OR amount_range). Real CA cases need
-- combined logic — e.g. "GSTIN starts with 27 AND amount > 50000 AND
-- voucher_type is purchase_bill". Adds a new rule_type 'composite' that
-- stores its logic in a JSONB condition tree, while keeping all existing
-- flat-field rules working as-is.

ALTER TABLE mapping_rules
  ADD COLUMN IF NOT EXISTS conditions JSONB,
  ADD COLUMN IF NOT EXISTS rule_schema_version INTEGER NOT NULL DEFAULT 1;

ALTER TABLE mapping_rules DROP CONSTRAINT IF EXISTS mapping_rules_rule_type_check;
ALTER TABLE mapping_rules ADD CONSTRAINT mapping_rules_rule_type_check
  CHECK (rule_type IN ('keyword', 'pattern', 'vendor', 'amount_range', 'composite'));

COMMENT ON COLUMN mapping_rules.conditions IS
  'Composite condition tree for rule_type=composite. Shape: '
  '{op: "AND"|"OR", clauses: [<tree>|{field, operator, value}]}. '
  'Fields: vendor_name, vendor_gstin, gstin_prefix, amount, item_description, '
  'hsn_code, voucher_type, narration, description, any_text. '
  'Operators: equals, iequals, contains, starts_with, ends_with, regex, '
  'in, between, gt, gte, lt, lte.';
