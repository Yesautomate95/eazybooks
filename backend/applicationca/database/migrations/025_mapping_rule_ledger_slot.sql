-- Migration 025: Make mapping_rules slot-aware (party vs expense).
--
-- Before this change, the suggestion service called Layer 1 twice — once
-- with the party-ledger universe and once with the expense-ledger universe.
-- A rule fired for whichever call's `available_names` happened to contain
-- the rule's target_ledger. This worked in the common case (a ledger name
-- belongs to exactly one universe) but had three problems:
--
--   1. Implicit. Users picking a target ledger didn't know which slot
--      they were targeting.
--   2. Brittle. If a name happened to exist in both universes the rule
--      misfired.
--   3. No way to express "this rule fills the party slot specifically",
--      independent of which target ledger is picked.
--
-- The new ledger_slot column makes the intent explicit. NULL is allowed
-- so existing rules keep working (they're still slot-inferred from
-- target_ledger membership). New rules created via the updated UI carry
-- 'party' or 'expense'; evaluation filters by slot.

ALTER TABLE mapping_rules
    ADD COLUMN IF NOT EXISTS ledger_slot TEXT
        CHECK (ledger_slot IS NULL OR ledger_slot IN ('party', 'expense'));

CREATE INDEX IF NOT EXISTS idx_mapping_rules_slot
    ON mapping_rules (user_id, company_name, document_type, ledger_slot);
