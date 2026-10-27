"""
Import Service
==============
Main orchestration service for document imports (bank statements, purchase invoices).
"""

import os
import hashlib
import logging
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple

from applicationca.agents.document_specific_functions.common.duplicate_detection import (
    DuplicateDetector, generate_file_hash
)
from applicationca.agents.document_specific_functions.common.confidence_scoring import (
    ConfidenceScorer, score_batch
)
from applicationca.services.mapping_service import MappingService
from applicationca.database.database_update import (
    create_import_batch,
    update_import_batch,
    create_transaction_import,
    create_transaction_imports_batch,
    update_transaction_import,
    create_ai_feedback
)
from applicationca.database.database_retrieval import (
    get_import_batch,
    get_transaction_imports_by_batch
)

logger = logging.getLogger(__name__)


class ImportService:
    """
    Main orchestration service for document import workflows.

    Handles:
    - Batch creation and tracking
    - Duplicate detection
    - Confidence scoring
    - Mapping rule application
    - Transaction storage
    """

    def __init__(self, user_id: str, company_name: str):
        """
        Initialize the ImportService.

        Args:
            user_id: User ID
            company_name: Company name
        """
        self.user_id = user_id
        self.company_name = company_name
        self.duplicate_detector = DuplicateDetector(user_id, company_name)
        self.mapping_service = MappingService(user_id, company_name)

    def create_batch(self, document_type: str, files: List[Dict] = None) -> Optional[str]:
        """
        Create a new import batch.

        Args:
            document_type: Type of document (bank_statement, purchase_invoice)
            files: Optional list of file info dicts

        Returns:
            The created batch_id or None on error
        """
        return create_import_batch(
            user_id=self.user_id,
            company_name=self.company_name,
            document_type=document_type,
            files=files
        )

    def get_batch(self, batch_id: str) -> Optional[Dict[str, Any]]:
        """Get batch details."""
        return get_import_batch(batch_id)

    def get_batch_transactions(self, batch_id: str) -> List[Dict[str, Any]]:
        """Get all transactions in a batch."""
        return get_transaction_imports_by_batch(batch_id) or []

    def process_bank_statement(self, batch_id: str, transactions: List[Dict[str, Any]],
                                original_filename: str, file_hash: str = None,
                                apply_rules: bool = True) -> Dict[str, Any]:
        """
        Process extracted bank statement transactions.

        Args:
            batch_id: The batch ID for this import
            transactions: List of extracted transactions
            original_filename: Original file name
            file_hash: Optional file hash for duplicate detection
            apply_rules: Whether to apply mapping rules

        Returns:
            Processing result with statistics and processed transactions
        """
        document_type = 'bank_statement'
        scorer = ConfidenceScorer(document_type)

        results = {
            'batch_id': batch_id,
            'total': len(transactions),
            'processed': 0,
            'duplicates': 0,
            'potential_duplicates': 0,
            'rules_applied': 0,
            'transactions': []
        }

        # Check for internal duplicates
        internal_dups = self.duplicate_detector.check_internal_duplicates(transactions)
        if internal_dups:
            logger.warning(f"Found {len(internal_dups)} internal duplicate groups")

        # Apply mapping rules if requested
        if apply_rules:
            transactions = self.mapping_service.apply_rules(transactions, document_type)
            results['rules_applied'] = sum(1 for t in transactions if t.get('rule_applied'))

        # Process each transaction
        processed_transactions = []
        for idx, transaction in enumerate(transactions):
            # Generate hash
            transaction_hash = self.duplicate_detector.generate_transaction_hash(
                transaction.get('date') or transaction.get('transaction_date'),
                transaction.get('amount', 0),
                transaction.get('reference_number'),
                transaction.get('party_name') or transaction.get('description')
            )

            # Check duplicates
            is_dup, exact_match, fuzzy_matches = self.duplicate_detector.check_transaction(transaction)

            # Score confidence
            confidence_scores = scorer.score_transaction(transaction)

            # Prepare transaction record
            processed = {
                'index': idx,
                'transaction_hash': transaction_hash,
                'is_duplicate': is_dup,
                'is_potential_duplicate': len(fuzzy_matches) > 0 and not is_dup,
                'duplicate_info': exact_match if is_dup else None,
                'fuzzy_matches': fuzzy_matches if not is_dup else [],
                'confidence_scores': confidence_scores,
                'overall_confidence': confidence_scores.get('_overall', 0.5),
                **transaction
            }

            processed_transactions.append(processed)

            if is_dup:
                results['duplicates'] += 1
            elif fuzzy_matches:
                results['potential_duplicates'] += 1

            results['processed'] += 1

        results['transactions'] = processed_transactions

        # Update batch statistics
        update_import_batch(
            batch_id,
            total_transactions=results['total'],
            processed_count=results['processed'],
            duplicate_count=results['duplicates'],
            status='ready'
        )

        return results

    def process_purchase_invoices(self, batch_id: str, invoices: List[Dict[str, Any]],
                                   apply_rules: bool = True) -> Dict[str, Any]:
        """
        Process extracted purchase invoice data.

        Args:
            batch_id: The batch ID for this import
            invoices: List of extracted invoice data
            apply_rules: Whether to apply mapping rules

        Returns:
            Processing result with statistics and processed invoices
        """
        document_type = 'purchase_invoice'
        scorer = ConfidenceScorer(document_type)

        results = {
            'batch_id': batch_id,
            'total': len(invoices),
            'processed': 0,
            'duplicates': 0,
            'vendors_to_create': 0,
            'items_to_create': 0,
            'invoices': []
        }

        # Apply mapping rules for vendor matching if requested
        if apply_rules:
            invoices = self.mapping_service.apply_rules(invoices, document_type)

        # Process each invoice
        processed_invoices = []
        for idx, invoice in enumerate(invoices):
            # Generate hash for duplicate detection
            transaction_hash = self.duplicate_detector.generate_transaction_hash(
                invoice.get('date') or invoice.get('invoice_date'),
                invoice.get('total') or invoice.get('amount', 0),
                invoice.get('invoice_number') or invoice.get('reference_number'),
                invoice.get('vendor_name') or invoice.get('party_name')
            )

            # Check duplicates
            is_dup, exact_match, fuzzy_matches = self.duplicate_detector.check_transaction({
                'date': invoice.get('date') or invoice.get('invoice_date'),
                'amount': invoice.get('total') or invoice.get('amount', 0),
                'reference_number': invoice.get('invoice_number'),
                'party_name': invoice.get('vendor_name')
            })

            # Score confidence
            confidence_scores = scorer.score_transaction(invoice)

            # Track new vendor/item requirements
            needs_vendor_creation = not invoice.get('vendor_matched', False)
            needs_item_creation = any(
                not item.get('matched', False)
                for item in invoice.get('items', [])
            )

            # Prepare processed record
            processed = {
                'index': idx,
                'transaction_hash': transaction_hash,
                'is_duplicate': is_dup,
                'is_potential_duplicate': len(fuzzy_matches) > 0 and not is_dup,
                'duplicate_info': exact_match if is_dup else None,
                'fuzzy_matches': fuzzy_matches if not is_dup else [],
                'confidence_scores': confidence_scores,
                'overall_confidence': confidence_scores.get('_overall', 0.5),
                'needs_vendor_creation': needs_vendor_creation,
                'needs_item_creation': needs_item_creation,
                **invoice
            }

            processed_invoices.append(processed)

            if is_dup:
                results['duplicates'] += 1
            if needs_vendor_creation:
                results['vendors_to_create'] += 1
            if needs_item_creation:
                results['items_to_create'] += 1

            results['processed'] += 1

        results['invoices'] = processed_invoices

        # Update batch statistics
        update_import_batch(
            batch_id,
            total_transactions=results['total'],
            processed_count=results['processed'],
            duplicate_count=results['duplicates'],
            status='ready'
        )

        return results

    def save_transactions(self, batch_id: str, transactions: List[Dict[str, Any]],
                          document_type: str, original_filename: str,
                          file_hash: str = None) -> List[str]:
        """
        Save processed transactions to the database.

        Args:
            batch_id: The batch ID
            transactions: List of processed transactions
            document_type: Document type
            original_filename: Original file name
            file_hash: Optional file hash

        Returns:
            List of created import_ids
        """
        records = []
        for t in transactions:
            # Skip exact duplicates if marked
            if t.get('is_duplicate') and not t.get('force_save'):
                continue

            records.append({
                'batch_id': batch_id,
                'user_id': self.user_id,
                'company_name': self.company_name,
                'document_type': document_type,
                'original_filename': original_filename,
                'file_hash': file_hash,
                'transaction_date': t.get('date') or t.get('transaction_date'),
                'reference_number': t.get('reference_number') or t.get('invoice_number'),
                'amount': float(t.get('amount', 0)),
                'description': t.get('description') or t.get('narration'),
                'party_name': t.get('party_name') or t.get('vendor_name'),
                'transaction_hash': t.get('transaction_hash'),
                'extracted_data': t,
                'confidence_scores': t.get('confidence_scores'),
                'debit_ledger': t.get('debit_ledger'),
                'credit_ledger': t.get('credit_ledger') or t.get('suggested_ledger'),
                'rule_applied': t.get('rule_applied')
            })

        if records:
            return create_transaction_imports_batch(records)

        return []

    def update_transaction_ledgers(self, import_ids: List[str],
                                     debit_ledger: str = None,
                                     credit_ledger: str = None) -> bool:
        """
        Bulk update ledger assignments for transactions.

        Args:
            import_ids: List of import IDs to update
            debit_ledger: New debit ledger (optional)
            credit_ledger: New credit ledger (optional)

        Returns:
            True on success, False on error
        """
        from applicationca.database.database_update import update_transaction_imports_batch

        updates = {}
        if debit_ledger:
            updates['debit_ledger'] = debit_ledger
        if credit_ledger:
            updates['credit_ledger'] = credit_ledger

        if not updates:
            return False

        return update_transaction_imports_batch(import_ids, **updates)

    def record_correction(self, import_id: str, field: str,
                          original_value: str, corrected_value: str,
                          document_type: str, auto_learn: bool = True) -> Optional[str]:
        """
        Record a user correction for AI learning.

        Args:
            import_id: The transaction import ID
            field: The field that was corrected
            original_value: The original (AI) value
            corrected_value: The user's corrected value
            document_type: Document type
            auto_learn: Whether to automatically create rules from corrections

        Returns:
            Feedback ID on success
        """
        feedback_id = create_ai_feedback(
            user_id=self.user_id,
            company_name=self.company_name,
            document_type=document_type,
            original_field=field,
            original_value=original_value,
            corrected_value=corrected_value,
            import_id=import_id
        )

        # Auto-learn: Create mapping rule if this is a ledger correction
        if auto_learn and field in ['credit_ledger', 'party_name', 'vendor_name']:
            # Get the transaction for context
            transaction_imports = get_transaction_imports_by_batch(None)  # This won't work, need to get by import_id
            # For now, skip auto-learning from corrections
            # TODO: Implement get_transaction_import_by_id and use it here

            pass

        return feedback_id

    def mark_transactions_pushed(self, import_ids: List[str],
                                  voucher_numbers: Dict[str, str] = None) -> bool:
        """
        Mark transactions as pushed to Tally.

        Args:
            import_ids: List of import IDs that were pushed
            voucher_numbers: Optional dict mapping import_id to Tally voucher number

        Returns:
            True on success
        """
        for import_id in import_ids:
            update_transaction_import(
                import_id,
                status='pushed',
                pushed_at=datetime.now(),
                tally_voucher_number=voucher_numbers.get(import_id) if voucher_numbers else None
            )

        # Update batch status
        # TODO: Check if all transactions in batch are pushed and update batch status

        return True

    def get_import_summary(self, batch_id: str) -> Dict[str, Any]:
        """
        Get a summary of an import batch.

        Args:
            batch_id: The batch ID

        Returns:
            Summary dict with statistics
        """
        batch = self.get_batch(batch_id)
        transactions = self.get_batch_transactions(batch_id)

        if not batch:
            return {'error': 'Batch not found'}

        # Calculate statistics
        by_status = {}
        by_ledger = {}
        total_amount = 0

        for t in transactions:
            status = t.get('status', 'pending')
            by_status[status] = by_status.get(status, 0) + 1

            ledger = t.get('credit_ledger') or t.get('debit_ledger') or 'Unassigned'
            by_ledger[ledger] = by_ledger.get(ledger, 0) + 1

            total_amount += float(t.get('amount', 0))

        return {
            'batch_id': batch_id,
            'document_type': batch.get('document_type'),
            'status': batch.get('status'),
            'total_transactions': batch.get('total_transactions', 0),
            'by_status': by_status,
            'by_ledger': by_ledger,
            'total_amount': total_amount,
            'created_at': batch.get('created_at'),
            'completed_at': batch.get('completed_at')
        }
