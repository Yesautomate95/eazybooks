"""
Duplicate Detection Module
==========================
Detects duplicate transactions based on hash matching and fuzzy matching.
"""

import hashlib
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple
from decimal import Decimal

from applicationca.database.database_retrieval import (
    check_transaction_duplicate,
    find_similar_transactions
)

logger = logging.getLogger(__name__)


class DuplicateDetector:
    """
    Detects duplicate transactions using exact hash matching and fuzzy matching.

    Features:
    - Exact duplicate detection using transaction hash
    - Fuzzy duplicate detection with configurable tolerances
    - Batch duplicate checking for efficiency
    """

    def __init__(self, user_id: str, company_name: str,
                 tolerance_days: int = 3, tolerance_percent: float = 0.01):
        """
        Initialize the DuplicateDetector.

        Args:
            user_id: User ID for scoping duplicate checks
            company_name: Company name for scoping duplicate checks
            tolerance_days: Number of days tolerance for fuzzy date matching
            tolerance_percent: Percentage tolerance for fuzzy amount matching (0.01 = 1%)
        """
        self.user_id = user_id
        self.company_name = company_name
        self.tolerance_days = tolerance_days
        self.tolerance_percent = tolerance_percent

    @staticmethod
    def generate_transaction_hash(transaction_date: str, amount: float,
                                   reference_number: str = None, party_name: str = None) -> str:
        """
        Generate a unique hash for a transaction based on its key attributes.

        The hash is generated from normalized values:
        - Date: Converted to YYYY-MM-DD format
        - Amount: Rounded to 2 decimal places, absolute value
        - Reference: Lowercased and stripped, or empty string
        - Party: Lowercased and stripped, or empty string

        Args:
            transaction_date: Transaction date (various formats accepted)
            amount: Transaction amount
            reference_number: Reference/invoice number (optional)
            party_name: Party/vendor name (optional)

        Returns:
            SHA256 hash string
        """
        # Normalize date
        date_str = DuplicateDetector._normalize_date(transaction_date)

        # Normalize amount (absolute value, 2 decimal places)
        amount_str = f"{abs(float(amount)):.2f}"

        # Normalize reference and party
        ref_str = (reference_number or "").lower().strip()
        party_str = (party_name or "").lower().strip()

        # Create composite string
        composite = f"{date_str}|{amount_str}|{ref_str}|{party_str}"

        # Generate hash
        return hashlib.sha256(composite.encode('utf-8')).hexdigest()

    @staticmethod
    def _normalize_date(date_input) -> str:
        """
        Normalize a date to YYYY-MM-DD format.

        Handles various input formats:
        - datetime object
        - String formats: DD/MM/YYYY, DD-MM-YYYY, YYYY-MM-DD, YYYYMMDD
        """
        if isinstance(date_input, datetime):
            return date_input.strftime('%Y-%m-%d')

        if hasattr(date_input, 'strftime'):
            return date_input.strftime('%Y-%m-%d')

        date_str = str(date_input).strip()

        # Try various formats
        formats = [
            '%Y-%m-%d',      # 2025-01-15
            '%d/%m/%Y',      # 15/01/2025
            '%d-%m-%Y',      # 15-01-2025
            '%Y%m%d',        # 20250115
            '%d/%m/%y',      # 15/01/25
            '%d-%m-%y',      # 15-01-25
        ]

        for fmt in formats:
            try:
                parsed = datetime.strptime(date_str, fmt)
                return parsed.strftime('%Y-%m-%d')
            except ValueError:
                continue

        # If all else fails, return as-is (will affect hash uniqueness)
        logger.warning(f"Could not parse date: {date_str}")
        return date_str

    def check_exact_duplicate(self, transaction_hash: str) -> Optional[Dict[str, Any]]:
        """
        Check if an exact duplicate exists in the database.

        Args:
            transaction_hash: The transaction hash to check

        Returns:
            The existing transaction record if found, None otherwise
        """
        return check_transaction_duplicate(self.user_id, self.company_name, transaction_hash)

    def check_fuzzy_duplicates(self, transaction_date, amount: float) -> List[Dict[str, Any]]:
        """
        Find potentially similar transactions using fuzzy matching.

        Args:
            transaction_date: Transaction date
            amount: Transaction amount

        Returns:
            List of similar transactions found
        """
        return find_similar_transactions(
            self.user_id,
            self.company_name,
            transaction_date,
            amount,
            self.tolerance_days,
            self.tolerance_percent
        ) or []

    def check_transaction(self, transaction: Dict[str, Any]) -> Tuple[bool, Optional[Dict], List[Dict]]:
        """
        Check a single transaction for duplicates.

        Args:
            transaction: Transaction dict with date, amount, reference_number, party_name

        Returns:
            Tuple of (is_duplicate, exact_match, fuzzy_matches)
            - is_duplicate: True if an exact duplicate was found
            - exact_match: The existing transaction if exact duplicate found
            - fuzzy_matches: List of similar transactions (potential duplicates)
        """
        # Generate hash
        transaction_hash = self.generate_transaction_hash(
            transaction.get('date') or transaction.get('transaction_date'),
            transaction.get('amount', 0),
            transaction.get('reference_number'),
            transaction.get('party_name') or transaction.get('description')
        )

        # Check exact duplicate
        exact_match = self.check_exact_duplicate(transaction_hash)
        if exact_match:
            return True, exact_match, []

        # Check fuzzy duplicates
        fuzzy_matches = self.check_fuzzy_duplicates(
            transaction.get('date') or transaction.get('transaction_date'),
            float(transaction.get('amount', 0))
        )

        return False, None, fuzzy_matches

    def check_transactions_batch(self, transactions: List[Dict[str, Any]]) -> Dict[int, Dict[str, Any]]:
        """
        Check a batch of transactions for duplicates.

        Args:
            transactions: List of transaction dicts

        Returns:
            Dict mapping transaction index to duplicate info:
            {
                0: {
                    'transaction_hash': 'abc123...',
                    'is_exact_duplicate': True,
                    'exact_match': {...},
                    'fuzzy_matches': [],
                    'status': 'duplicate'
                },
                1: {
                    'transaction_hash': 'def456...',
                    'is_exact_duplicate': False,
                    'exact_match': None,
                    'fuzzy_matches': [{...}],
                    'status': 'potential_duplicate'
                },
                2: {
                    'transaction_hash': 'ghi789...',
                    'is_exact_duplicate': False,
                    'exact_match': None,
                    'fuzzy_matches': [],
                    'status': 'unique'
                }
            }
        """
        results = {}

        for idx, transaction in enumerate(transactions):
            # Generate hash
            transaction_hash = self.generate_transaction_hash(
                transaction.get('date') or transaction.get('transaction_date'),
                transaction.get('amount', 0),
                transaction.get('reference_number'),
                transaction.get('party_name') or transaction.get('description')
            )

            is_duplicate, exact_match, fuzzy_matches = self.check_transaction(transaction)

            if is_duplicate:
                status = 'duplicate'
            elif fuzzy_matches:
                status = 'potential_duplicate'
            else:
                status = 'unique'

            results[idx] = {
                'transaction_hash': transaction_hash,
                'is_exact_duplicate': is_duplicate,
                'exact_match': exact_match,
                'fuzzy_matches': fuzzy_matches,
                'status': status
            }

        return results

    def check_internal_duplicates(self, transactions: List[Dict[str, Any]]) -> Dict[str, List[int]]:
        """
        Check for duplicates within a batch of transactions (internal duplicates).

        This detects when the same transaction appears multiple times in the
        input batch, before checking against the database.

        Args:
            transactions: List of transaction dicts

        Returns:
            Dict mapping transaction_hash to list of indices where it appears
            Only includes hashes that appear more than once
        """
        hash_to_indices = {}

        for idx, transaction in enumerate(transactions):
            transaction_hash = self.generate_transaction_hash(
                transaction.get('date') or transaction.get('transaction_date'),
                transaction.get('amount', 0),
                transaction.get('reference_number'),
                transaction.get('party_name') or transaction.get('description')
            )

            if transaction_hash not in hash_to_indices:
                hash_to_indices[transaction_hash] = []
            hash_to_indices[transaction_hash].append(idx)

        # Return only duplicates (appear more than once)
        return {h: indices for h, indices in hash_to_indices.items() if len(indices) > 1}


def generate_file_hash(file_content: bytes) -> str:
    """
    Generate a SHA256 hash for file content.

    Useful for detecting if the same file has been uploaded before.

    Args:
        file_content: Raw bytes of the file

    Returns:
        SHA256 hash string
    """
    return hashlib.sha256(file_content).hexdigest()
