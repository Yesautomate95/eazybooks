"""
Mapping Service
===============
Manages ledger mapping rules for auto-suggestion in document imports.
"""

import re
import json
import logging
from typing import Dict, List, Any, Optional, Tuple

from applicationca.database.database_retrieval import (
    get_mapping_rules,
    get_mapping_rule_by_id,
    get_ai_feedback_for_learning
)
from applicationca.database.database_update import (
    create_mapping_rule,
    update_mapping_rule,
    delete_mapping_rule,
    increment_rule_match_count
)

logger = logging.getLogger(__name__)


class MappingService:
    """
    Service for managing and applying ledger mapping rules.

    Features:
    - CRUD operations for mapping rules
    - Priority-based rule evaluation
    - Keyword, pattern, vendor, and amount-based matching
    - Learning from user corrections
    - Rule suggestions based on historical data
    """

    def __init__(self, user_id: str, company_name: str):
        """
        Initialize the MappingService.

        Args:
            user_id: User ID
            company_name: Company name
        """
        self.user_id = user_id
        self.company_name = company_name
        self._rules_cache = None

    def get_rules(self, document_type: str = None, active_only: bool = True,
                  force_refresh: bool = False, ledger_slot: str = None) -> List[Dict[str, Any]]:
        """
        Get all mapping rules for the user/company.

        Args:
            document_type: Optional filter by document type
            active_only: If True, only return active rules
            force_refresh: If True, bypass cache
            ledger_slot: Optional slot filter ('party' | 'expense'). When
                         set, also includes NULL-slot rules (legacy).

        Returns:
            List of mapping rule dicts
        """
        # Slot-filtered reads bypass the cache — the cache is keyed by
        # (user, company) only and would conflate the two slots.
        if ledger_slot:
            return get_mapping_rules(
                self.user_id, self.company_name, document_type, active_only,
                ledger_slot=ledger_slot,
            ) or []

        if force_refresh or self._rules_cache is None:
            self._rules_cache = get_mapping_rules(
                self.user_id, self.company_name, document_type, active_only
            ) or []

        return self._rules_cache

    def get_rule(self, rule_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific rule by ID."""
        return get_mapping_rule_by_id(rule_id)

    def create_rule(self, rule_name: str, rule_type: str, document_type: str,
                    target_ledger: str, **kwargs) -> Optional[str]:
        """
        Create a new mapping rule.

        Args:
            rule_name: Unique name for the rule
            rule_type: Type of rule (keyword, pattern, vendor, amount_range)
            document_type: Document type (bank_statement, purchase_invoice)
            target_ledger: The ledger to assign when rule matches
            **kwargs: Additional fields (keywords, pattern, vendor_name, amount_min/max, priority)

        Returns:
            The created rule_id or None on error
        """
        rule_id = create_mapping_rule(
            user_id=self.user_id,
            company_name=self.company_name,
            rule_name=rule_name,
            rule_type=rule_type,
            document_type=document_type,
            target_ledger=target_ledger,
            **kwargs
        )

        if rule_id:
            # Invalidate cache
            self._rules_cache = None

        return rule_id

    def update_rule(self, rule_id: str, **kwargs) -> bool:
        """
        Update an existing mapping rule.

        Args:
            rule_id: The rule ID to update
            **kwargs: Fields to update

        Returns:
            True on success, False on error
        """
        success = update_mapping_rule(rule_id, **kwargs)
        if success:
            self._rules_cache = None
        return success

    def delete_rule(self, rule_id: str) -> bool:
        """
        Delete a mapping rule.

        Args:
            rule_id: The rule ID to delete

        Returns:
            True on success, False on error
        """
        success = delete_mapping_rule(rule_id)
        if success:
            self._rules_cache = None
        return success

    def apply_rules(self, transactions: List[Dict[str, Any]],
                    document_type: str, ledger_slot: str = None) -> List[Dict[str, Any]]:
        """
        Apply mapping rules to a list of transactions.

        Rules are evaluated in priority order. The first matching rule wins.

        Args:
            transactions: List of transaction dicts
            document_type: Document type for filtering rules
            ledger_slot: Optional slot filter ('party' | 'expense'). When
                         set, only rules with matching slot (or NULL slot,
                         which means "either") are considered.

        Returns:
            List of transactions with suggested ledgers and rule info added
        """
        rules = self.get_rules(
            document_type=document_type, active_only=True, ledger_slot=ledger_slot,
        )

        if not rules:
            logger.info(
                "No active rules found for document type=%s slot=%s",
                document_type, ledger_slot,
            )
            return transactions

        # Sort by priority (higher first), then by match_count (more used first)
        sorted_rules = sorted(rules, key=lambda r: (r.get('priority', 0), r.get('match_count', 0)), reverse=True)

        results = []
        for transaction in transactions:
            matched_rule = None

            for rule in sorted_rules:
                if self._rule_matches(rule, transaction):
                    matched_rule = rule
                    break

            if matched_rule:
                # Add suggestion to transaction
                transaction['suggested_ledger'] = matched_rule['target_ledger']
                transaction['suggested_voucher_type'] = matched_rule.get('target_voucher_type')
                transaction['rule_applied'] = matched_rule['rule_name']
                transaction['rule_id'] = str(matched_rule['rule_id'])

                # Increment match count (async would be better but keeping simple)
                increment_rule_match_count(str(matched_rule['rule_id']))
            else:
                transaction['suggested_ledger'] = None
                transaction['rule_applied'] = None

            results.append(transaction)

        return results

    def _rule_matches(self, rule: Dict[str, Any], transaction: Dict[str, Any]) -> bool:
        """
        Check if a rule matches a transaction.

        Args:
            rule: The mapping rule
            transaction: The transaction to check

        Returns:
            True if the rule matches, False otherwise
        """
        rule_type = rule.get('rule_type')

        if rule_type == 'keyword':
            return self._keyword_match(rule, transaction)
        elif rule_type == 'pattern':
            return self._pattern_match(rule, transaction)
        elif rule_type == 'vendor':
            return self._vendor_match(rule, transaction)
        elif rule_type == 'amount_range':
            return self._amount_range_match(rule, transaction)
        elif rule_type == 'composite':
            return self._composite_match(rule, transaction)
        else:
            logger.warning(f"Unknown rule type: {rule_type}")
            return False

    # ──────────────────────────────────────────────────────────────────
    # Composite rules — AND/OR trees of field/operator/value clauses
    # ──────────────────────────────────────────────────────────────────

    COMPOSITE_FIELDS = {
        "vendor_name", "vendor_gstin", "gstin_prefix", "amount",
        "item_description", "hsn_code", "voucher_type",
        "narration", "description", "any_text",
    }
    COMPOSITE_OPERATORS = {
        "equals", "iequals", "contains", "starts_with", "ends_with",
        "regex", "in", "between", "gt", "gte", "lt", "lte",
    }

    def _composite_match(self, rule: Dict[str, Any], transaction: Dict[str, Any]) -> bool:
        tree = rule.get("conditions")
        if isinstance(tree, str):
            try:
                tree = json.loads(tree)
            except json.JSONDecodeError:
                logger.error("Composite rule %s has invalid JSON conditions", rule.get("rule_id"))
                return False
        if not isinstance(tree, dict) or not tree:
            return False
        try:
            return self._evaluate_conditions(tree, transaction)
        except Exception as exc:  # noqa: BLE001 — never let a bad rule break the suggest flow
            logger.warning("Composite rule %s evaluation failed: %s", rule.get("rule_id"), exc)
            return False

    def _evaluate_conditions(self, node: Any, txn: Dict[str, Any]) -> bool:
        """Recursive evaluator. A node is either a group {op, clauses} or a leaf clause."""
        if not isinstance(node, dict):
            return False

        op = (node.get("op") or "").upper()
        if op in ("AND", "OR"):
            clauses = node.get("clauses") or []
            if not isinstance(clauses, list) or not clauses:
                return False
            if op == "AND":
                return all(self._evaluate_conditions(c, txn) for c in clauses)
            return any(self._evaluate_conditions(c, txn) for c in clauses)

        # Leaf clause: {field, operator, value}
        return self._evaluate_clause(node, txn)

    def _evaluate_clause(self, clause: Dict[str, Any], txn: Dict[str, Any]) -> bool:
        field = clause.get("field")
        operator = clause.get("operator")
        value = clause.get("value")
        if field not in self.COMPOSITE_FIELDS or operator not in self.COMPOSITE_OPERATORS:
            return False

        actuals = self._extract_field_values(field, txn)
        if not actuals:
            return False

        # Numeric operators short-circuit through the first parseable float.
        if operator in ("gt", "gte", "lt", "lte", "between"):
            for actual in actuals:
                if self._numeric_compare(actual, operator, value):
                    return True
            return False

        if operator == "in":
            values = value if isinstance(value, (list, tuple)) else [value]
            normalized = [str(v).strip().lower() for v in values if v is not None]
            for actual in actuals:
                if str(actual).strip().lower() in normalized:
                    return True
            return False

        if operator == "regex":
            try:
                rx = re.compile(str(value), re.IGNORECASE)
            except re.error:
                return False
            return any(rx.search(str(a)) for a in actuals)

        target = str(value or "")
        target_l = target.lower()
        for actual in actuals:
            a = str(actual or "")
            a_l = a.lower()
            if operator == "equals" and a == target:
                return True
            if operator == "iequals" and a_l == target_l:
                return True
            if operator == "contains" and target_l in a_l:
                return True
            if operator == "starts_with" and a_l.startswith(target_l):
                return True
            if operator == "ends_with" and a_l.endswith(target_l):
                return True
        return False

    def _extract_field_values(self, field: str, txn: Dict[str, Any]) -> List[Any]:
        """Pull all values for `field` from the transaction. Returns a list because
        item_description / hsn_code can have multiple values (one per line item)."""
        if field == "vendor_name":
            return [txn.get("vendor_name") or txn.get("party_name") or txn.get("party") or ""]
        if field == "vendor_gstin":
            return [txn.get("vendor_gstin") or (txn.get("gstin") or "")]
        if field == "gstin_prefix":
            gstin = txn.get("vendor_gstin") or txn.get("gstin") or ""
            return [gstin[:2]] if gstin else []
        if field == "amount":
            return [txn.get("amount") or txn.get("total_amount") or txn.get("subtotal") or 0]
        if field == "voucher_type":
            return [txn.get("voucher_type") or ""]
        if field == "narration":
            return [txn.get("narration") or ""]
        if field == "description":
            return [txn.get("description") or ""]
        if field == "any_text":
            return [self._get_searchable_text(txn)]
        if field == "item_description":
            return [
                (it.get("description") or it.get("name") or "")
                for it in (txn.get("line_items") or txn.get("items") or [])
                if isinstance(it, dict)
            ]
        if field == "hsn_code":
            codes: List[str] = []
            for it in (txn.get("line_items") or txn.get("items") or []):
                if not isinstance(it, dict):
                    continue
                code = it.get("hsn_code") or it.get("hsn") or it.get("sac_code") or it.get("sac")
                if code:
                    codes.append(str(code))
            for code in (txn.get("hsn_codes") or []):
                if code:
                    codes.append(str(code))
            return codes
        return []

    @staticmethod
    def _numeric_compare(actual: Any, operator: str, value: Any) -> bool:
        try:
            a = float(actual)
        except (TypeError, ValueError):
            return False
        try:
            if operator == "between":
                lo, hi = value if isinstance(value, (list, tuple)) and len(value) == 2 else (None, None)
                if lo is None or hi is None:
                    return False
                return float(lo) <= a <= float(hi)
            v = float(value)
        except (TypeError, ValueError):
            return False
        if operator == "gt":
            return a > v
        if operator == "gte":
            return a >= v
        if operator == "lt":
            return a < v
        if operator == "lte":
            return a <= v
        return False

    def _keyword_match(self, rule: Dict[str, Any], transaction: Dict[str, Any]) -> bool:
        """Check if any keyword in the rule matches the transaction description."""
        keywords = rule.get('keywords', [])

        # Handle JSON string if not already parsed
        if isinstance(keywords, str):
            try:
                keywords = json.loads(keywords)
            except json.JSONDecodeError:
                keywords = [keywords]

        if not keywords:
            return False

        # Get searchable text from transaction
        search_text = self._get_searchable_text(transaction).lower()

        # Check if any keyword matches
        for keyword in keywords:
            if keyword.lower() in search_text:
                return True

        return False

    def _pattern_match(self, rule: Dict[str, Any], transaction: Dict[str, Any]) -> bool:
        """Check if the regex pattern matches the transaction description."""
        pattern = rule.get('pattern')
        if not pattern:
            return False

        search_text = self._get_searchable_text(transaction)

        try:
            return bool(re.search(pattern, search_text, re.IGNORECASE))
        except re.error as e:
            logger.error(f"Invalid regex pattern in rule {rule.get('rule_id')}: {e}")
            return False

    def _vendor_match(self, rule: Dict[str, Any], transaction: Dict[str, Any]) -> bool:
        """Check if the vendor name matches."""
        vendor_name = rule.get('vendor_name', '').lower().strip()
        if not vendor_name:
            return False

        # Get party/vendor from transaction
        party = (
            transaction.get('vendor_name') or
            transaction.get('party_name') or
            transaction.get('party') or
            ''
        ).lower().strip()

        if not party:
            return False

        # Exact match or contains
        return vendor_name in party or party in vendor_name

    def _amount_range_match(self, rule: Dict[str, Any], transaction: Dict[str, Any]) -> bool:
        """Check if the transaction amount falls within the rule's range."""
        try:
            amount = float(transaction.get('amount', 0))
            amount_min = rule.get('amount_min')
            amount_max = rule.get('amount_max')

            # Handle None values
            min_ok = amount_min is None or amount >= float(amount_min)
            max_ok = amount_max is None or amount <= float(amount_max)

            return min_ok and max_ok
        except (ValueError, TypeError):
            return False

    def _get_searchable_text(self, transaction: Dict[str, Any]) -> str:
        """Get all searchable text from a transaction."""
        parts = [
            transaction.get('description', ''),
            transaction.get('narration', ''),
            transaction.get('party_name', ''),
            transaction.get('vendor_name', ''),
            transaction.get('reference_number', ''),
        ]
        return ' '.join(filter(None, parts))

    def suggest_rules_from_history(self, document_type: str,
                                    min_occurrences: int = 3) -> List[Dict[str, Any]]:
        """
        Analyze historical corrections and suggest new mapping rules.

        This looks at AI extraction feedback to find patterns in user corrections
        that could be turned into automatic rules.

        Args:
            document_type: Document type to analyze
            min_occurrences: Minimum times a correction pattern must appear

        Returns:
            List of suggested rule dicts (not yet created)
        """
        feedback = get_ai_feedback_for_learning(
            self.user_id, self.company_name, document_type,
            field='party_name', limit=500
        ) or []

        if not feedback:
            return []

        # Analyze correction patterns
        correction_patterns = {}
        for f in feedback:
            original = (f.get('original_value') or '').lower().strip()
            corrected = (f.get('corrected_value') or '').lower().strip()

            if original and corrected:
                key = (original, corrected)
                if key not in correction_patterns:
                    correction_patterns[key] = 0
                correction_patterns[key] += 1

        # Filter by minimum occurrences and generate rule suggestions
        suggestions = []
        for (original, corrected), count in correction_patterns.items():
            if count >= min_occurrences:
                # Generate a keyword-based rule suggestion
                suggestions.append({
                    'rule_name': f"Auto: {original[:30]} -> {corrected[:30]}",
                    'rule_type': 'keyword',
                    'document_type': document_type,
                    'keywords': [original],
                    'target_ledger': corrected,
                    'suggested_priority': count,  # Higher count = higher priority
                    'occurrences': count
                })

        # Sort by occurrences
        suggestions.sort(key=lambda x: x['occurrences'], reverse=True)

        return suggestions[:10]  # Return top 10 suggestions

    def learn_from_correction(self, transaction: Dict[str, Any],
                               original_ledger: str, corrected_ledger: str,
                               document_type: str, auto_create_rule: bool = False) -> Optional[str]:
        """
        Learn from a user correction and optionally create a new rule.

        Args:
            transaction: The transaction that was corrected
            original_ledger: The original (AI-suggested) ledger
            corrected_ledger: The corrected (user-selected) ledger
            document_type: Document type
            auto_create_rule: If True, automatically create a rule from the correction

        Returns:
            The created rule_id if auto_create_rule is True, None otherwise
        """
        description = self._get_searchable_text(transaction)

        if auto_create_rule and description:
            # Extract key terms from description for keyword rule
            keywords = self._extract_keywords(description)

            if keywords:
                # Check if similar rule already exists
                existing_rules = self.get_rules(document_type=document_type)
                for rule in existing_rules:
                    if rule.get('target_ledger', '').lower() == corrected_ledger.lower():
                        rule_keywords = rule.get('keywords', [])
                        if isinstance(rule_keywords, str):
                            try:
                                rule_keywords = json.loads(rule_keywords)
                            except:
                                rule_keywords = []

                        # If keywords overlap, update existing rule
                        if any(k in rule_keywords for k in keywords):
                            # Add new keywords to existing rule
                            updated_keywords = list(set(rule_keywords + keywords))
                            self.update_rule(str(rule['rule_id']), keywords=updated_keywords)
                            return str(rule['rule_id'])

                # Create new rule
                rule_name = f"Auto: {keywords[0][:20]} -> {corrected_ledger[:20]}"
                return self.create_rule(
                    rule_name=rule_name,
                    rule_type='keyword',
                    document_type=document_type,
                    target_ledger=corrected_ledger,
                    keywords=keywords,
                    priority=5  # Medium priority for auto-created rules
                )

        return None

    def _extract_keywords(self, text: str, max_keywords: int = 3) -> List[str]:
        """
        Extract significant keywords from text.

        Uses simple heuristics to identify meaningful terms.
        """
        # Common words to ignore
        stop_words = {
            'neft', 'rtgs', 'imps', 'upi', 'transfer', 'payment', 'credit', 'debit',
            'bank', 'account', 'a/c', 'ref', 'no', 'number', 'the', 'and', 'for',
            'from', 'to', 'of', 'in', 'on', 'at', 'by', 'with', 'inr', 'rs', 'rupees'
        }

        # Split and clean
        words = re.findall(r'\b[a-zA-Z]{3,}\b', text.lower())

        # Filter stop words and get unique
        keywords = []
        seen = set()
        for word in words:
            if word not in stop_words and word not in seen:
                keywords.append(word)
                seen.add(word)
                if len(keywords) >= max_keywords:
                    break

        return keywords

    def get_rule_statistics(self, document_type: str = None) -> Dict[str, Any]:
        """
        Get statistics about mapping rules.

        Returns:
            Dict with rule statistics
        """
        rules = self.get_rules(document_type=document_type, active_only=False)

        if not rules:
            return {
                'total_rules': 0,
                'active_rules': 0,
                'total_matches': 0,
                'by_type': {},
                'top_rules': []
            }

        active = [r for r in rules if r.get('is_active')]
        by_type = {}
        total_matches = 0

        for rule in rules:
            rule_type = rule.get('rule_type', 'unknown')
            by_type[rule_type] = by_type.get(rule_type, 0) + 1
            total_matches += rule.get('match_count', 0)

        # Top rules by match count
        top_rules = sorted(rules, key=lambda r: r.get('match_count', 0), reverse=True)[:5]

        return {
            'total_rules': len(rules),
            'active_rules': len(active),
            'total_matches': total_matches,
            'by_type': by_type,
            'top_rules': [
                {
                    'rule_name': r.get('rule_name'),
                    'target_ledger': r.get('target_ledger'),
                    'match_count': r.get('match_count', 0)
                }
                for r in top_rules
            ]
        }
