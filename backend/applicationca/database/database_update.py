# database_update.py

import psycopg2
from psycopg2.extras import execute_values
from uuid import UUID, uuid4
from datetime import datetime
from pinecone import Pinecone
from applicationca.database.database_retrieval import get_lockr_by_name_and_user

from applicationca.aws.s3.S3Operations import delete_file_from_s3
from config.settings import settings
import logging

logger = logging.getLogger(__name__)

# Initialize Pinecone (lazy - index is resolved on first use)
pc = Pinecone(api_key=settings.PINECONE_API_KEY)
_index = None

def get_pinecone_index():
    global _index
    if _index is None:
        _index = pc.Index(settings.PINECONE_INDEX_NAME)
    return _index

def create_connection():
    """Create and return a database connection."""
    try:
        connection = psycopg2.connect(settings.DATABASE_URL)
        return connection
    except Exception as e:
        logger.error(f"Error connecting to the database: {e}")
        return None


def add_new_user(username=None, user_id=None, email=None):
    """
    Add (or upsert) a user in the USERS table.

    - If username is not provided, it defaults to the user_id.
    - If user_id is not provided, a UUID is generated.
    - If email is provided and the existing row's email column is NULL,
      it is backfilled. This matters for Google-sign-in users whose row
      was created before the email parameter existed — without the
      backfill, RBAC's SUPER_ADMIN_EMAILS match (which queries
      users.email) silently fails for them.
    """
    if user_id is None:
        user_id = uuid4()
    if username is None:
        username = str(user_id)
    email = (email or None) and email.strip().lower() or None

    connection = create_connection()
    if connection:
        try:
            cursor = connection.cursor()

            check_query = "SELECT user_id, email FROM USERS WHERE user_id = %s"
            cursor.execute(check_query, (str(user_id),))
            existing_user = cursor.fetchone()

            if existing_user:
                # Backfill email if the existing row has it as NULL.
                if email and not existing_user[1]:
                    cursor.execute(
                        "UPDATE USERS SET email = %s WHERE user_id = %s AND email IS NULL",
                        (email, str(user_id)),
                    )
                    connection.commit()
                    print(f"Backfilled email for user_id={user_id}")
                return existing_user[0]
            else:
                if email:
                    cursor.execute(
                        "INSERT INTO USERS (user_id, username, email) VALUES (%s, %s, %s)",
                        (str(user_id), username, email),
                    )
                else:
                    cursor.execute(
                        "INSERT INTO USERS (user_id, username) VALUES (%s, %s)",
                        (str(user_id), username),
                    )
                connection.commit()
                print(f"User added successfully: user_id={user_id}, username={username}, email={email}")
                return user_id

        except Exception as e:
            connection.rollback()
            print(f"Error adding user: {e}")
        finally:
            cursor.close()
            connection.close()
    else:
        print("Failed to connect to the database.")

    return None

def add_new_lockr(user_id, name):
    """
    Add a new locker to the LOCKR table.
    If a locker with the same name exists for the user, returns the existing locker_id.
    """
    connection = create_connection()
    if connection:
        try:
            cursor = connection.cursor()
            
            # First check if locker exists
            existing_lockr = get_lockr_by_name_and_user(user_id, name)
            if existing_lockr:
                return existing_lockr
            
            # If not exists, create new locker
            query = "INSERT INTO LOCKR (user_id, name) VALUES (%s, %s) RETURNING lockr_id"
            cursor.execute(query, (str(user_id), name))
            lockr_id = cursor.fetchone()[0]
            connection.commit()
            return lockr_id
        except Exception as e:
            connection.rollback()
            print(f"Error adding locker: {e}")
        finally:
            cursor.close()
            connection.close()
    else:
        print("Failed to connect to the database.")
    return None


def add_new_client(user_id, name):
    """
    Add a new client to the CLIENTS table.
    """
    print(f"Adding client with user_id={user_id}, name={name}")  # Debug statement
    connection = create_connection()
    if connection:
        try:
            cursor = connection.cursor()
            query = "INSERT INTO CLIENTS (user_id, name) VALUES (%s, %s) RETURNING client_id"
            cursor.execute(query, (str(user_id), name))
            client_id = cursor.fetchone()[0]
            connection.commit()
            return client_id
        except Exception as e:
            connection.rollback()
            print(f"Error adding client: {e}")
        finally:
            cursor.close()
            connection.close()
    else:
        print("Failed to connect to the database.")
    return None


def add_new_chat(user_id, chat_id=None, client_id=None, lockr_id=None, chat_name=None, chat_description=None, chat_type=None):
    """
    Add a new chat to the CHATS table.
    """
    connection = create_connection()
    if connection:
        try:
            cursor = connection.cursor()
            query = """
            INSERT INTO CHATS (chat_id, user_id, client_id, lockr_id, chat_name, chat_description, chat_date, chat_type)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING chat_id
            """
            
            # Convert UUIDs to strings
            chat_id_str = str(chat_id) if chat_id else str(uuid4())
            user_id_str = str(user_id) if user_id else None
            client_id_str = str(client_id) if client_id else None
            lockr_id_str = str(lockr_id) if lockr_id else None
            
            # Set current timestamp for chat_date
            chat_date = datetime.now()

            cursor.execute(query, (chat_id_str, user_id_str, client_id_str, lockr_id_str, chat_name, chat_description, chat_date, chat_type))
            chat_id = cursor.fetchone()[0]
            connection.commit()
            return chat_id, user_id
        except Exception as e:
            connection.rollback()
        finally:
            cursor.close()
            connection.close()
    else:
        print("Failed to connect to the database.")
    return None

def upload_new_files(user_id, files, s3_results, lockr_id=None, client_id=None, chat_id=None):
    """
    Upload multiple files to the FILES table in a single insert query.
    At least one of lockr_id, client_id, or chat_id must be provided.
    """
    if lockr_id is None and client_id is None and chat_id is None:
        print("Error: At least one of lockr_id, client_id, or chat_id must be provided.")
        return None

    connection = create_connection()
    if connection:
        try:
            cursor = connection.cursor()

            query = """
            INSERT INTO FILES (
                user_id, client_id, lockr_id, chat_id, file_name,
                file_type, file_last_modified, file_link, file_location, file_size, is_synced
            ) VALUES %s
            RETURNING file_id
            """

            # Prepare the values list
            file_last_modified = datetime.now()
            values = []
            for file, s3_result in zip(files, s3_results):
                size_in_kb = file.size / 1024  # Convert bytes to KB
                values.append((
                    str(user_id) if user_id else None,
                    str(client_id) if client_id else None,
                    str(lockr_id) if lockr_id else None,
                    str(chat_id) if chat_id else None,
                    file.filename,
                    file.content_type,
                    file_last_modified,
                    s3_result.get("s3_url", ""),
                    s3_result.get("s3_key", ""),
                    size_in_kb,
                    False
                ))

            execute_values(cursor, query, values)

            inserted_file_ids = [file_id[0] for file_id in cursor.fetchall()]
            connection.commit()
            print(f"Files uploaded successfully: file_ids={inserted_file_ids}")
            return inserted_file_ids

        except Exception as e:
            connection.rollback()
            print(f"Error uploading files: {e}")
        finally:
            cursor.close()
            connection.close()
    else:
        print("Failed to connect to the database.")
    return None


def upload_new_file(user_id, lockr_id=None, client_id=None, chat_id=None, file_name=None, file_type=None, file_link=None, file_location=None):
    """
    Upload a new file to the FILES table.
    At least one of lockr_id, client_id, or chat_id must be provided.
    """
    if lockr_id is None and client_id is None and chat_id is None:
        print("Error: At least one of lockr_id, client_id, or chat_id must be provided.")
        return None

    connection = create_connection()
    if connection:
        try:
            cursor = connection.cursor()
            query = """
            INSERT INTO FILES (user_id, client_id, lockr_id, chat_id, file_name, file_type, file_last_modified, file_link, file_location)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING file_id
            """
            
            # Convert UUIDs to strings
            user_id_str = str(user_id) if user_id else None
            lockr_id_str = str(lockr_id) if lockr_id else None
            client_id_str = str(client_id) if client_id else None
            chat_id_str = str(chat_id) if chat_id else None
            
            # Set current timestamp for file_last_modified
            file_last_modified = datetime.now()
            
            cursor.execute(query, (user_id_str, client_id_str, lockr_id_str, chat_id_str, file_name, file_type, file_last_modified, file_link, file_location))
            file_id = cursor.fetchone()[0]
            connection.commit()
            print(f"File uploaded successfully: file_id={file_id}")
            return file_id
        except Exception as e:
            connection.rollback()
            print(f"Error uploading file: {e}")
        finally:
            cursor.close()
            connection.close()
    else:
        print("Failed to connect to the database.")
    return None


def get_user_chats(user_id):
    """
    Fetch all chats for a user from the CHATS table.
    Returns a list of chat dictionaries containing all chat information.
    """
    connection = create_connection()
    if connection:
        try:
            cursor = connection.cursor()
            query = """
            SELECT chat_id, user_id, client_id, lockr_id, chat_name, chat_description, chat_date, chat_type
            FROM CHATS
            WHERE user_id = %s
            ORDER BY chat_date DESC
            """
            
            cursor.execute(query, (str(user_id),))
            chats = cursor.fetchall()
            
            # Convert the results to a list of dictionaries
            chat_list = []
            for chat in chats:
                chat_dict = {
                    'chat_id': chat[0],
                    'user_id': chat[1],
                    'client_id': chat[2],
                    'lockr_id': chat[3],
                    'chat_name': chat[4],
                    'chat_description': chat[5],
                    'chat_date': chat[6].isoformat() if chat[6] else None,
                    'chat_type': chat[7]
                }
                chat_list.append(chat_dict)
            
            return chat_list
        except Exception as e:
            print(f"Error fetching chats: {e}")
            return []
        finally:
            cursor.close()
            connection.close()
    else:
        print("Failed to connect to the database.")
        return []

async def delete_lockr(lockr_id, user_id):
    """
    Delete a locker and all its associated data (files and chats).
    
    Args:
        lockr_id (str or UUID): The ID of the locker to delete
        user_id (str): The ID of the user who owns the locker
        
    Returns:
        bool: True if deletion was successful, False otherwise
    """
    connection = create_connection()
    if connection:
        try:
            cursor = connection.cursor()
            
            # Convert lockr_id to string if it's a UUID
            lockr_id_str = str(lockr_id)
            
            # First get all files associated with this locker
            get_files_query = "SELECT file_id, file_name FROM FILES WHERE lockr_id = %s"
            cursor.execute(get_files_query, (lockr_id_str,))
            files = cursor.fetchall()
            
            # Delete each file from S3
            for file_id, file_name in files:
                try:
                    await delete_file_from_s3(
                        user_id=user_id,
                        file_name=file_name,
                        folder_id=lockr_id_str
                    )
                except Exception as e:
                    print(f"Error deleting file {file_name} from S3: {e}")
                    # Continue with other files even if one fails

            # Then delete all files from database
            delete_files_query = "DELETE FROM FILES WHERE lockr_id = %s AND user_id = %s"
            cursor.execute(delete_files_query, (lockr_id_str, user_id))

            # Then delete all chats associated with this locker
            delete_chats_query = "DELETE FROM CHATS WHERE lockr_id = %s AND user_id = %s"
            cursor.execute(delete_chats_query, (lockr_id_str, user_id))

            # Finally delete the locker itself
            delete_lockr_query = "DELETE FROM LOCKR WHERE lockr_id = %s AND user_id = %s"
            cursor.execute(delete_lockr_query, (lockr_id_str, user_id))

            delete_chunks_query = "DELETE FROM CHUNKS WHERE folder_id = %s AND user_id = %s"
            cursor.execute(delete_chunks_query, (lockr_id_str, user_id))

            # Step 1: Retrieve all matching vectors using metadata filter
            pinecone_index = get_pinecone_index()
            query_results = pinecone_index.query(
                vector=[0.0] * 1536,  # Dummy vector (assuming text-embedding-3-small)
                top_k=10000,  # Large value to fetch all matches
                filter={"folder": lockr_id_str, "user_id": user_id}  # Apply metadata filter
            )

            # Step 2: Extract all vector IDs
            ids_to_delete = [match["id"] for match in query_results.get("matches", [])]

            # Optional: Print how many vectors were found
            print(f"Found {len(ids_to_delete)} vectors matching the filter.")

            # If needed, delete the fetched vector IDs
            if ids_to_delete:
                pinecone_index.delete(ids=ids_to_delete)

            connection.commit()
            print(f"Successfully deleted locker {lockr_id_str} and all associated data")
            return True
            
        except Exception as e:
            connection.rollback()
            print(f"Error deleting locker: {e}")
            return False
        finally:
            cursor.close()
            connection.close()
    else:
        print("Failed to connect to the database.")
        return False

async def delete_client(client_id, user_id):
    """
    Delete a client and all its associated data (files and chats).
    
    Args:
        client_id (str or UUID): The ID of the client to delete
        user_id (str): The ID of the user who owns the client
        
    Returns:
        bool: True if deletion was successful, False otherwise
    """
    connection = create_connection()
    if connection:
        try:
            cursor = connection.cursor()
            
            # Convert client_id to string if it's a UUID
            client_id_str = str(client_id)
            
            # First get all files associated with this client
            get_files_query = "SELECT file_id, file_name FROM FILES WHERE client_id = %s"
            cursor.execute(get_files_query, (client_id_str,))
            files = cursor.fetchall()
            
            # Delete each file from S3
            for file_id, file_name in files:
                try:
                    await delete_file_from_s3(
                        user_id=user_id,
                        file_name=file_name,
                        folder_id=client_id_str
                    )
                except Exception as e:
                    print(f"Error deleting file {file_name} from S3: {e}")
                    # Continue with other files even if one fails
            
            # Then delete all files from database
            delete_files_query = "DELETE FROM FILES WHERE client_id = %s AND user_id = %s"
            cursor.execute(delete_files_query, (client_id_str, user_id))
            
            # Then delete all chats associated with this client
            delete_chats_query = "DELETE FROM CHATS WHERE client_id = %s AND user_id = %s"
            cursor.execute(delete_chats_query, (client_id_str, user_id))
            
            # Finally delete the client itself
            delete_client_query = "DELETE FROM CLIENTS WHERE client_id = %s AND user_id = %s"
            cursor.execute(delete_client_query, (client_id_str, user_id))

            delete_chunks_query = "DELETE FROM CHUNKS WHERE folder_id = %s AND user_id = %s"
            cursor.execute(delete_chunks_query, (client_id_str, user_id))

            # Step 1: Retrieve all matching vectors using metadata filter
            pinecone_index = get_pinecone_index()
            query_results = pinecone_index.query(
                vector=[0.0] * 1536,  # Dummy vector (assuming text-embedding-3-small)
                top_k=10000,  # Large value to fetch all matches
                filter={"folder": client_id_str, "user_id": user_id}  # Apply metadata filter
            )

            print(query_results)

            # Step 2: Extract all vector IDs
            ids_to_delete = [match["id"] for match in query_results.get("matches", [])]

            # Optional: Print how many vectors were found
            print(f"Found {len(ids_to_delete)} vectors matching the filter.")

            # If needed, delete the fetched vector IDs
            if ids_to_delete:
                pinecone_index.delete(ids=ids_to_delete)
            
            connection.commit()
            print(f"Successfully deleted client {client_id_str} and all associated data")
            return True
            
        except Exception as e:
            connection.rollback()
            print(f"Error deleting client: {e}")
            return False
        finally:
            cursor.close()
            connection.close()
    else:
        print("Failed to connect to the database.")
        return False

# ============================================
# MAPPING RULES FUNCTIONS
# ============================================

def create_mapping_rule(user_id: str, company_name: str, rule_name: str, rule_type: str,
                        document_type: str, target_ledger: str, keywords: list = None,
                        pattern: str = None, vendor_name: str = None, amount_min: float = None,
                        amount_max: float = None, target_voucher_type: str = None, priority: int = 0,
                        conditions: dict = None, ledger_slot: str = None):
    """
    Create a new mapping rule.

    Args:
        user_id: User ID
        company_name: Company name
        rule_name: Unique name for the rule
        rule_type: keyword | pattern | vendor | amount_range | composite
        document_type: Document type (bank_statement, purchase_invoice, sales_invoice)
        target_ledger: The ledger to assign when rule matches
        keywords: List of keywords to match (for keyword rules)
        pattern: Regex pattern (for pattern rules)
        vendor_name: Vendor name to match (for vendor rules)
        amount_min: Minimum amount (for amount_range rules)
        amount_max: Maximum amount (for amount_range rules)
        target_voucher_type: Optional voucher type (Payment/Receipt/Purchase)
        priority: Rule priority (higher = evaluated first)
        conditions: Composite condition tree (dict) for composite rules
        ledger_slot: 'party' | 'expense' | None. None = legacy behavior
                     (rule fires for whichever slot's universe contains the
                     target ledger). Set explicitly to disambiguate.

    Returns:
        The created rule_id or None on error
    """
    import json

    connection = create_connection()
    if connection:
        try:
            cursor = connection.cursor()
            query = """
            INSERT INTO mapping_rules (
                user_id, company_name, rule_name, rule_type, document_type,
                keywords, pattern, vendor_name, amount_min, amount_max,
                target_ledger, target_voucher_type, ledger_slot, priority, conditions
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            RETURNING rule_id
            """

            keywords_json = json.dumps(keywords) if keywords else None
            conditions_json = json.dumps(conditions) if conditions else None

            cursor.execute(query, (
                user_id, company_name, rule_name, rule_type, document_type,
                keywords_json, pattern, vendor_name, amount_min, amount_max,
                target_ledger, target_voucher_type, ledger_slot, priority, conditions_json
            ))
            rule_id = cursor.fetchone()[0]
            connection.commit()
            logger.info(f"Created mapping rule: {rule_id}")
            return rule_id

        except Exception as e:
            connection.rollback()
            logger.error(f"Error creating mapping rule: {e}")
            return None
        finally:
            cursor.close()
            connection.close()

    return None


def update_mapping_rule(rule_id: str, **kwargs):
    """
    Update a mapping rule.

    Args:
        rule_id: The rule ID to update
        **kwargs: Fields to update (rule_name, keywords, pattern, target_ledger, priority, is_active, etc.)

    Returns:
        True on success, False on error
    """
    import json

    allowed_fields = ['rule_name', 'rule_type', 'keywords', 'pattern', 'vendor_name',
                      'amount_min', 'amount_max', 'target_ledger', 'target_voucher_type',
                      'ledger_slot', 'priority', 'is_active', 'conditions']

    # Filter to only allowed fields
    updates = {k: v for k, v in kwargs.items() if k in allowed_fields}

    if not updates:
        return False

    connection = create_connection()
    if connection:
        try:
            cursor = connection.cursor()

            # Convert keywords list to JSON if present
            if 'keywords' in updates and updates['keywords'] is not None:
                updates['keywords'] = json.dumps(updates['keywords'])

            # Conditions JSONB — accept either dict or already-JSON-string
            if 'conditions' in updates and updates['conditions'] is not None and not isinstance(updates['conditions'], str):
                updates['conditions'] = json.dumps(updates['conditions'])

            # Build dynamic UPDATE query. conditions needs an explicit ::jsonb cast.
            def _placeholder(field):
                return "%s::jsonb" if field == 'conditions' else "%s"
            set_clauses = [f"{field} = {_placeholder(field)}" for field in updates.keys()]
            query = f"UPDATE mapping_rules SET {', '.join(set_clauses)} WHERE rule_id = %s"

            cursor.execute(query, (*updates.values(), rule_id))
            connection.commit()
            logger.info(f"Updated mapping rule: {rule_id}")
            return True

        except Exception as e:
            connection.rollback()
            logger.error(f"Error updating mapping rule: {e}")
            return False
        finally:
            cursor.close()
            connection.close()

    return False


def delete_mapping_rule(rule_id: str):
    """Delete a mapping rule."""
    connection = create_connection()
    if connection:
        try:
            cursor = connection.cursor()
            cursor.execute("DELETE FROM mapping_rules WHERE rule_id = %s", (rule_id,))
            connection.commit()
            logger.info(f"Deleted mapping rule: {rule_id}")
            return True
        except Exception as e:
            connection.rollback()
            logger.error(f"Error deleting mapping rule: {e}")
            return False
        finally:
            cursor.close()
            connection.close()
    return False


def increment_rule_match_count(rule_id: str):
    """Increment the match count for a rule and update last_used_at."""
    connection = create_connection()
    if connection:
        try:
            cursor = connection.cursor()
            cursor.execute("""
                UPDATE mapping_rules
                SET match_count = match_count + 1, last_used_at = CURRENT_TIMESTAMP
                WHERE rule_id = %s
            """, (rule_id,))
            connection.commit()
            return True
        except Exception as e:
            connection.rollback()
            logger.error(f"Error incrementing rule match count: {e}")
            return False
        finally:
            cursor.close()
            connection.close()
    return False


# ============================================
# IMPORT BATCH FUNCTIONS
# ============================================

def create_import_batch(user_id: str, company_name: str, document_type: str, files: list = None):
    """
    Create a new import batch.

    Args:
        user_id: User ID
        company_name: Company name
        document_type: Document type (bank_statement, purchase_invoice)
        files: List of file info dicts [{filename, file_hash, transaction_count}, ...]

    Returns:
        The created batch_id or None on error
    """
    import json

    connection = create_connection()
    if connection:
        try:
            cursor = connection.cursor()
            query = """
            INSERT INTO import_batches (user_id, company_name, document_type, files, status)
            VALUES (%s, %s, %s, %s, 'processing')
            RETURNING batch_id
            """

            files_json = json.dumps(files) if files else None

            cursor.execute(query, (user_id, company_name, document_type, files_json))
            batch_id = cursor.fetchone()[0]
            connection.commit()
            logger.info(f"Created import batch: {batch_id}")
            return batch_id

        except Exception as e:
            connection.rollback()
            logger.error(f"Error creating import batch: {e}")
            return None
        finally:
            cursor.close()
            connection.close()

    return None


def update_import_batch(batch_id: str, **kwargs):
    """
    Update an import batch.

    Args:
        batch_id: The batch ID to update
        **kwargs: Fields to update (total_transactions, processed_count, duplicate_count,
                  error_count, pushed_count, status, completed_at)

    Returns:
        True on success, False on error
    """
    allowed_fields = ['total_transactions', 'processed_count', 'duplicate_count',
                      'error_count', 'pushed_count', 'status', 'completed_at', 'files']

    updates = {k: v for k, v in kwargs.items() if k in allowed_fields}

    if not updates:
        return False

    connection = create_connection()
    if connection:
        try:
            cursor = connection.cursor()

            # Convert files list to JSON if present
            if 'files' in updates and updates['files'] is not None:
                import json
                updates['files'] = json.dumps(updates['files'])

            set_clauses = [f"{field} = %s" for field in updates.keys()]
            query = f"UPDATE import_batches SET {', '.join(set_clauses)} WHERE batch_id = %s"

            cursor.execute(query, (*updates.values(), batch_id))
            connection.commit()
            return True

        except Exception as e:
            connection.rollback()
            logger.error(f"Error updating import batch: {e}")
            return False
        finally:
            cursor.close()
            connection.close()

    return False


# ============================================
# TRANSACTION IMPORT FUNCTIONS
# ============================================

def create_transaction_import(batch_id: str, user_id: str, company_name: str, document_type: str,
                              original_filename: str, transaction_date, amount: float,
                              transaction_hash: str, description: str = None, party_name: str = None,
                              reference_number: str = None, file_hash: str = None,
                              extracted_data: dict = None, confidence_scores: dict = None,
                              debit_ledger: str = None, credit_ledger: str = None, rule_applied: str = None):
    """
    Create a new transaction import record.

    Returns:
        The created import_id or None on error
    """
    import json

    connection = create_connection()
    if connection:
        try:
            cursor = connection.cursor()
            query = """
            INSERT INTO transaction_imports (
                batch_id, user_id, company_name, document_type,
                original_filename, file_hash, transaction_date, reference_number,
                amount, description, party_name, transaction_hash,
                extracted_data, confidence_scores, debit_ledger, credit_ledger, rule_applied
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING import_id
            """

            extracted_json = json.dumps(extracted_data) if extracted_data else None
            confidence_json = json.dumps(confidence_scores) if confidence_scores else None

            cursor.execute(query, (
                batch_id, user_id, company_name, document_type,
                original_filename, file_hash, transaction_date, reference_number,
                amount, description, party_name, transaction_hash,
                extracted_json, confidence_json, debit_ledger, credit_ledger, rule_applied
            ))
            import_id = cursor.fetchone()[0]
            connection.commit()
            return import_id

        except psycopg2.IntegrityError as e:
            # Likely a duplicate (unique constraint violation)
            connection.rollback()
            logger.warning(f"Duplicate transaction detected: {transaction_hash}")
            return None
        except Exception as e:
            connection.rollback()
            logger.error(f"Error creating transaction import: {e}")
            return None
        finally:
            cursor.close()
            connection.close()

    return None


def create_transaction_imports_batch(transactions: list):
    """
    Batch insert multiple transaction imports.

    Args:
        transactions: List of transaction dicts with all required fields

    Returns:
        List of created import_ids or empty list on error
    """
    import json

    if not transactions:
        return []

    connection = create_connection()
    if connection:
        try:
            cursor = connection.cursor()
            query = """
            INSERT INTO transaction_imports (
                batch_id, user_id, company_name, document_type,
                original_filename, file_hash, transaction_date, reference_number,
                amount, description, party_name, transaction_hash,
                extracted_data, confidence_scores, debit_ledger, credit_ledger, rule_applied
            ) VALUES %s
            ON CONFLICT (user_id, company_name, transaction_hash) DO NOTHING
            RETURNING import_id
            """

            values = []
            for t in transactions:
                extracted_json = json.dumps(t.get('extracted_data')) if t.get('extracted_data') else None
                confidence_json = json.dumps(t.get('confidence_scores')) if t.get('confidence_scores') else None

                values.append((
                    t['batch_id'], t['user_id'], t['company_name'], t['document_type'],
                    t['original_filename'], t.get('file_hash'), t['transaction_date'], t.get('reference_number'),
                    t['amount'], t.get('description'), t.get('party_name'), t['transaction_hash'],
                    extracted_json, confidence_json, t.get('debit_ledger'), t.get('credit_ledger'), t.get('rule_applied')
                ))

            execute_values(cursor, query, values)
            import_ids = [row[0] for row in cursor.fetchall()]
            connection.commit()
            logger.info(f"Created {len(import_ids)} transaction imports")
            return import_ids

        except Exception as e:
            connection.rollback()
            logger.error(f"Error batch creating transaction imports: {e}")
            return []
        finally:
            cursor.close()
            connection.close()

    return []


def update_transaction_import(import_id: str, **kwargs):
    """
    Update a transaction import record.

    Args:
        import_id: The import ID to update
        **kwargs: Fields to update (status, debit_ledger, credit_ledger, tally_voucher_number, etc.)

    Returns:
        True on success, False on error
    """
    import json

    allowed_fields = ['status', 'debit_ledger', 'credit_ledger', 'tally_voucher_number',
                      'extracted_data', 'confidence_scores', 'rule_applied',
                      'processed_at', 'pushed_at']

    updates = {k: v for k, v in kwargs.items() if k in allowed_fields}

    if not updates:
        return False

    connection = create_connection()
    if connection:
        try:
            cursor = connection.cursor()

            # Convert dicts to JSON if present
            for field in ['extracted_data', 'confidence_scores']:
                if field in updates and updates[field] is not None:
                    updates[field] = json.dumps(updates[field])

            set_clauses = [f"{field} = %s" for field in updates.keys()]
            query = f"UPDATE transaction_imports SET {', '.join(set_clauses)} WHERE import_id = %s"

            cursor.execute(query, (*updates.values(), import_id))
            connection.commit()
            return True

        except Exception as e:
            connection.rollback()
            logger.error(f"Error updating transaction import: {e}")
            return False
        finally:
            cursor.close()
            connection.close()

    return False


def update_transaction_imports_batch(import_ids: list, **kwargs):
    """
    Batch update multiple transaction imports with the same values.

    Args:
        import_ids: List of import IDs to update
        **kwargs: Fields to update

    Returns:
        True on success, False on error
    """
    import json

    allowed_fields = ['status', 'debit_ledger', 'credit_ledger', 'processed_at', 'pushed_at']
    updates = {k: v for k, v in kwargs.items() if k in allowed_fields}

    if not updates or not import_ids:
        return False

    connection = create_connection()
    if connection:
        try:
            cursor = connection.cursor()

            set_clauses = [f"{field} = %s" for field in updates.keys()]
            placeholders = ', '.join(['%s'] * len(import_ids))
            query = f"UPDATE transaction_imports SET {', '.join(set_clauses)} WHERE import_id IN ({placeholders})"

            cursor.execute(query, (*updates.values(), *import_ids))
            connection.commit()
            logger.info(f"Batch updated {len(import_ids)} transaction imports")
            return True

        except Exception as e:
            connection.rollback()
            logger.error(f"Error batch updating transaction imports: {e}")
            return False
        finally:
            cursor.close()
            connection.close()

    return False


# ============================================
# AI FEEDBACK FUNCTIONS
# ============================================

def create_ai_feedback(user_id: str, company_name: str, document_type: str,
                       original_field: str, corrected_value: str,
                       original_value: str = None, original_confidence: float = None,
                       import_id: str = None, document_text_snippet: str = None,
                       extraction_context: dict = None):
    """
    Record AI extraction feedback (user correction).

    Returns:
        The created feedback_id or None on error
    """
    import json

    connection = create_connection()
    if connection:
        try:
            cursor = connection.cursor()
            query = """
            INSERT INTO ai_extraction_feedback (
                user_id, company_name, document_type, import_id,
                original_field, original_value, original_confidence,
                corrected_value, document_text_snippet, extraction_context
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING feedback_id
            """

            context_json = json.dumps(extraction_context) if extraction_context else None

            cursor.execute(query, (
                user_id, company_name, document_type, import_id,
                original_field, original_value, original_confidence,
                corrected_value, document_text_snippet, context_json
            ))
            feedback_id = cursor.fetchone()[0]
            connection.commit()
            logger.info(f"Created AI feedback: {feedback_id}")
            return feedback_id

        except Exception as e:
            connection.rollback()
            logger.error(f"Error creating AI feedback: {e}")
            return None
        finally:
            cursor.close()
            connection.close()

    return None


# Example usage
if __name__ == "__main__":
    # Add a new user
    # user_id = add_new_user(username="testuser")
    # 
    # # Add a new locker for a user
    # lockr_id = add_new_lockr(user_id=user_id, name="Test Locker")
    # 
    # # Add a new client for a user
    # client_id = add_new_client(user_id=user_id, name="Test Client")
    # 
    # # Add a new chat
    # chat_id = add_new_chat(
    #     user_id=user_id,
    #     client_id=client_id,
    #     chat_name="Test Chat",
    #     chat_description="This is a test chat",
    #     chat_type="text"
    # )
    # 
    # # Upload a new file
    # upload_new_file(
    #     user_id=user_id,
    #     lockr_id=lockr_id,
    #     file_name="example_file.pdf",
    #     file_type="pdf",
    #     file_link="https://example.com/file.pdf",
    #     file_location="s3://bucket/file.pdf"
    # )
    pass