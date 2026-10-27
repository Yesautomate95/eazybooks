"""
Confidence Scoring Module
=========================
Calculates confidence scores for AI-extracted data fields.
"""

import re
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class ConfidenceScorer:
    """
    Calculates confidence scores for AI-extracted document fields.

    Confidence is scored on a 0-1 scale:
    - 0.0-0.5: Low confidence (red) - likely needs manual review
    - 0.5-0.8: Medium confidence (yellow) - may need verification
    - 0.8-1.0: High confidence (green) - likely correct

    Factors affecting confidence:
    - Format validation (date, amount, GST format)
    - Completeness (all required fields present)
    - Consistency (values make sense together)
    - Source clarity (text quality indicators)
    """

    # Field-specific validation patterns
    DATE_PATTERNS = [
        r'\d{2}/\d{2}/\d{4}',      # DD/MM/YYYY
        r'\d{2}-\d{2}-\d{4}',      # DD-MM-YYYY
        r'\d{4}-\d{2}-\d{2}',      # YYYY-MM-DD
        r'\d{2}/\d{2}/\d{2}',      # DD/MM/YY
    ]

    GSTIN_PATTERN = r'^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$'

    AMOUNT_PATTERN = r'^-?[\d,]+\.?\d*$'

    def __init__(self, document_type: str = 'bank_statement'):
        """
        Initialize the ConfidenceScorer.

        Args:
            document_type: Type of document (bank_statement, purchase_invoice, sales_invoice)
        """
        self.document_type = document_type

    def score_transaction(self, transaction: Dict[str, Any],
                          original_text: str = None) -> Dict[str, float]:
        """
        Calculate confidence scores for all fields in a transaction.

        Args:
            transaction: The extracted transaction data
            original_text: Optional original document text for context matching

        Returns:
            Dict mapping field names to confidence scores (0-1)
        """
        scores = {}

        # Score each field based on document type
        if self.document_type == 'bank_statement':
            scores = self._score_bank_transaction(transaction, original_text)
        elif self.document_type == 'purchase_invoice':
            scores = self._score_purchase_invoice(transaction, original_text)
        elif self.document_type == 'sales_invoice':
            scores = self._score_sales_invoice(transaction, original_text)
        else:
            scores = self._score_generic(transaction, original_text)

        # Calculate overall confidence
        scores['_overall'] = self._calculate_overall(scores)

        return scores

    def _score_bank_transaction(self, transaction: Dict[str, Any],
                                 original_text: str = None) -> Dict[str, float]:
        """Score fields for a bank statement transaction."""
        scores = {}

        # Date confidence
        date_value = transaction.get('date') or transaction.get('transaction_date', '')
        scores['date'] = self._score_date_field(str(date_value))

        # Amount confidence
        amount_value = transaction.get('amount', '')
        scores['amount'] = self._score_amount_field(amount_value)

        # Description/narration confidence
        description = transaction.get('description') or transaction.get('narration', '')
        scores['description'] = self._score_description_field(description, original_text)

        # Party name confidence (derived from description)
        party = transaction.get('party_name', '')
        scores['party_name'] = self._score_party_name(party, description, original_text)

        # Transaction type confidence
        txn_type = transaction.get('type', '')
        scores['type'] = self._score_transaction_type(txn_type, amount_value)

        return scores

    def _score_purchase_invoice(self, transaction: Dict[str, Any],
                                 original_text: str = None) -> Dict[str, float]:
        """Score fields for a purchase invoice."""
        scores = {}

        # Vendor name confidence
        vendor = transaction.get('vendor_name') or transaction.get('party_name', '')
        scores['vendor_name'] = self._score_vendor_name(vendor, original_text)

        # Invoice number confidence
        invoice_num = transaction.get('invoice_number') or transaction.get('reference_number', '')
        scores['invoice_number'] = self._score_invoice_number(invoice_num, original_text)

        # Date confidence
        date_value = transaction.get('date') or transaction.get('invoice_date', '')
        scores['date'] = self._score_date_field(str(date_value))

        # Amount confidence
        amount = transaction.get('amount') or transaction.get('total', 0)
        scores['amount'] = self._score_amount_field(amount)

        # GSTIN confidence
        gstin = transaction.get('gstin') or transaction.get('vendor_gstin', '')
        scores['gstin'] = self._score_gstin_field(gstin)

        # Items confidence (if present)
        items = transaction.get('items', [])
        if items:
            scores['items'] = self._score_items_list(items)

        # Tax amounts confidence
        cgst = transaction.get('cgst', 0)
        sgst = transaction.get('sgst', 0)
        igst = transaction.get('igst', 0)
        scores['tax'] = self._score_tax_fields(cgst, sgst, igst, amount)

        return scores

    def _score_sales_invoice(self, transaction: Dict[str, Any],
                              original_text: str = None) -> Dict[str, float]:
        """Score fields for a sales invoice."""
        # Similar to purchase invoice
        return self._score_purchase_invoice(transaction, original_text)

    def _score_generic(self, transaction: Dict[str, Any],
                       original_text: str = None) -> Dict[str, float]:
        """Score fields for a generic transaction."""
        scores = {}

        for key, value in transaction.items():
            if key.startswith('_'):
                continue
            if 'date' in key.lower():
                scores[key] = self._score_date_field(str(value))
            elif 'amount' in key.lower() or 'total' in key.lower():
                scores[key] = self._score_amount_field(value)
            elif 'gst' in key.lower():
                scores[key] = self._score_gstin_field(str(value))
            else:
                scores[key] = self._score_text_field(str(value), original_text)

        return scores

    def _score_date_field(self, value: str) -> float:
        """Score a date field based on format validity."""
        if not value or value.lower() in ['null', 'none', '']:
            return 0.0

        # Check against known date patterns
        for pattern in self.DATE_PATTERNS:
            if re.match(pattern, value.strip()):
                return 0.95

        # Try parsing as datetime
        try:
            datetime.strptime(value, '%Y-%m-%d')
            return 0.95
        except:
            pass

        # Has some date-like structure
        if re.search(r'\d+[/-]\d+[/-]\d+', value):
            return 0.7

        # Contains digits that could be a date
        if re.search(r'\d{2,4}', value):
            return 0.4

        return 0.2

    def _score_amount_field(self, value) -> float:
        """Score an amount field based on format and reasonableness."""
        if value is None:
            return 0.0

        try:
            # Convert to float
            if isinstance(value, str):
                # Remove currency symbols and commas
                clean_value = re.sub(r'[₹$€,\s]', '', value)
                amount = float(clean_value)
            else:
                amount = float(value)

            # Check if it's a reasonable amount
            if amount == 0:
                return 0.5  # Could be intentional or extraction error

            if amount < 0:
                # Negative amounts are valid but less common
                return 0.85

            if amount > 0:
                return 0.95

        except (ValueError, TypeError):
            return 0.2

        return 0.5

    def _score_description_field(self, description: str, original_text: str = None) -> float:
        """Score a description/narration field."""
        if not description:
            return 0.3

        # Longer descriptions generally more reliable
        length = len(description.strip())

        if length < 5:
            return 0.4
        elif length < 20:
            return 0.7
        elif length < 100:
            return 0.85
        else:
            return 0.9

    def _score_party_name(self, party_name: str, description: str = None,
                          original_text: str = None) -> float:
        """Score a party/vendor name field."""
        if not party_name:
            return 0.3

        name = party_name.strip()

        # Very short names are suspicious
        if len(name) < 3:
            return 0.4

        # Check if it looks like a proper name (contains letters)
        if not re.search(r'[a-zA-Z]', name):
            return 0.5

        # Check if party name appears in description (good sign)
        if description and name.lower() in description.lower():
            return 0.95

        # Check if it appears in original text
        if original_text and name.lower() in original_text.lower():
            return 0.9

        # Reasonable length name
        if 3 <= len(name) <= 100:
            return 0.8

        return 0.7

    def _score_vendor_name(self, vendor: str, original_text: str = None) -> float:
        """Score a vendor name field for invoices."""
        return self._score_party_name(vendor, None, original_text)

    def _score_invoice_number(self, invoice_num: str, original_text: str = None) -> float:
        """Score an invoice/reference number field."""
        if not invoice_num:
            return 0.3

        num = str(invoice_num).strip()

        # Empty or placeholder values
        if num.lower() in ['null', 'none', 'n/a', '-']:
            return 0.2

        # Check if it appears in original text
        if original_text and num in original_text:
            return 0.95

        # Has alphanumeric pattern typical of invoice numbers
        if re.match(r'^[A-Z0-9/-]+$', num, re.IGNORECASE):
            return 0.85

        # Contains both letters and numbers (good sign)
        if re.search(r'[A-Z]', num, re.IGNORECASE) and re.search(r'\d', num):
            return 0.8

        # Just numbers
        if num.isdigit():
            return 0.75

        return 0.6

    def _score_gstin_field(self, gstin: str) -> float:
        """Score a GSTIN field based on format validation."""
        if not gstin:
            return 0.5  # GSTIN might not be present

        gstin_clean = gstin.strip().upper()

        # Full GSTIN format validation
        if re.match(self.GSTIN_PATTERN, gstin_clean):
            return 0.98

        # Partial match (right length, some correct structure)
        if len(gstin_clean) == 15:
            return 0.7

        # Has some GSTIN-like characteristics
        if len(gstin_clean) >= 10 and re.search(r'[A-Z0-9]{10,}', gstin_clean):
            return 0.5

        return 0.3

    def _score_items_list(self, items: List[Dict]) -> float:
        """Score a list of line items."""
        if not items:
            return 0.3

        total_score = 0
        for item in items:
            item_score = 0

            # Check required fields
            if item.get('description') or item.get('name'):
                item_score += 0.3
            if item.get('quantity') is not None:
                item_score += 0.25
            if item.get('rate') is not None:
                item_score += 0.25
            if item.get('amount') is not None:
                item_score += 0.2

            total_score += item_score

        return min(total_score / len(items), 1.0)

    def _score_tax_fields(self, cgst: float, sgst: float, igst: float, total: float) -> float:
        """Score tax field extraction based on consistency."""
        try:
            total_val = float(total) if total else 0
            cgst_val = float(cgst) if cgst else 0
            sgst_val = float(sgst) if sgst else 0
            igst_val = float(igst) if igst else 0

            # Either CGST+SGST or IGST should be present, not both
            if (cgst_val > 0 or sgst_val > 0) and igst_val > 0:
                return 0.6  # Unusual but possible

            # CGST and SGST should typically be equal
            if cgst_val > 0 and sgst_val > 0 and cgst_val != sgst_val:
                return 0.7

            # Tax should be reasonable percentage of total (typically 5-28%)
            tax_total = cgst_val + sgst_val + igst_val
            if total_val > 0:
                tax_percent = (tax_total / total_val) * 100
                if 0 < tax_percent <= 30:
                    return 0.9

            return 0.75

        except (ValueError, TypeError):
            return 0.5

    def _score_text_field(self, value: str, original_text: str = None) -> float:
        """Score a generic text field."""
        if not value:
            return 0.3

        value = str(value).strip()

        # Check if value appears in original text
        if original_text and value.lower() in original_text.lower():
            return 0.9

        # Reasonable length
        if 3 <= len(value) <= 200:
            return 0.8

        return 0.6

    def _score_transaction_type(self, txn_type: str, amount) -> float:
        """Score transaction type field."""
        if not txn_type:
            return 0.5

        txn_type_lower = txn_type.lower().strip()

        # Valid known types
        valid_types = ['receipt', 'payment', 'credit', 'debit', 'cr', 'dr']
        if txn_type_lower in valid_types:
            return 0.95

        return 0.6

    def _calculate_overall(self, field_scores: Dict[str, float]) -> float:
        """
        Calculate overall confidence from individual field scores.

        Uses weighted average with critical fields having higher weight.
        """
        if not field_scores:
            return 0.5

        # Define weights for different fields
        weights = {
            'date': 1.2,
            'amount': 1.5,
            'vendor_name': 1.3,
            'party_name': 1.2,
            'invoice_number': 1.0,
            'description': 0.8,
            'gstin': 0.7,
            'type': 0.6,
            'items': 1.0,
            'tax': 0.8,
        }

        total_weight = 0
        weighted_sum = 0

        for field, score in field_scores.items():
            if field.startswith('_'):
                continue
            weight = weights.get(field, 1.0)
            weighted_sum += score * weight
            total_weight += weight

        if total_weight == 0:
            return 0.5

        return weighted_sum / total_weight

    @staticmethod
    def get_confidence_level(score: float) -> str:
        """
        Convert a confidence score to a human-readable level.

        Args:
            score: Confidence score (0-1)

        Returns:
            'high', 'medium', or 'low'
        """
        if score >= 0.8:
            return 'high'
        elif score >= 0.5:
            return 'medium'
        else:
            return 'low'

    @staticmethod
    def get_confidence_color(score: float) -> str:
        """
        Get a color for displaying confidence score.

        Args:
            score: Confidence score (0-1)

        Returns:
            'green', 'yellow', or 'red'
        """
        if score >= 0.8:
            return 'green'
        elif score >= 0.5:
            return 'yellow'
        else:
            return 'red'


def score_batch(transactions: List[Dict[str, Any]], document_type: str) -> List[Dict[str, float]]:
    """
    Score a batch of transactions.

    Args:
        transactions: List of transaction dicts
        document_type: Type of document

    Returns:
        List of confidence score dicts (one per transaction)
    """
    scorer = ConfidenceScorer(document_type)
    return [scorer.score_transaction(t) for t in transactions]
