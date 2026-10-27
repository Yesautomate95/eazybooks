import json

import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2 import pool
import logging
from uuid import UUID
from functools import wraps
import time

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

from config.settings import settings

# Lazy connection pool initialization
connection_pool = None

def _get_pool():
    """Lazily initialize and return the connection pool."""
    global connection_pool
    if connection_pool is not None:
        return connection_pool
    try:
        connection_pool = pool.ThreadedConnectionPool(
            minconn=1,
            maxconn=30,
            dsn=settings.DATABASE_URL,
            connect_timeout=5
        )
        logger.info("Database connection pool created successfully")
    except Exception as e:
        logger.error(f"Error creating connection pool: {e}")
        connection_pool = None
    return connection_pool

def create_connection():
    """Create and return a database connection."""
    return get_connection()

def get_connection():
    """Get a connection from the pool."""
    p = _get_pool()
    if p:
        try:
            return p.getconn()
        except Exception as e:
            logger.error(f"Error getting connection from pool: {e}")

    # Fallback to direct connection if pool is not available
    try:
        connection = psycopg2.connect(settings.DATABASE_URL, connect_timeout=5)
        return connection
    except Exception as e:
        logger.error(f"Error connecting to the database: {e}")
        return None

def return_connection(connection):
    """Return a connection to the pool."""
    p = _get_pool()
    if p and connection:
        try:
            p.putconn(connection)
        except Exception as e:
            logger.error(f"Error returning connection to pool: {e}")
            try:
                connection.close()
            except:
                pass

def timing_decorator(func):
    """Decorator to log execution time of functions."""
    @wraps(func)
    def wrapper(*args, **kwargs):
        start_time = time.time()
        result = func(*args, **kwargs)
        end_time = time.time()
        logger.debug(f"Function {func.__name__} took {end_time - start_time:.4f} seconds to execute")
        return result
    return wrapper


def fetch_file_summaries(user_id: str, folder_id: str = None):
    """
    Fetch file summaries and file names from the database for a given user_id and optional folder_id.
    Returns the results as a formatted string.

    Parameters:
    - user_id (str): The ID of the user whose files to retrieve
    - folder_id (str, optional): The ID of the folder to filter by. If None, returns all files for the user.

    Returns:
    - str: A formatted string containing all file names and summaries
    """
    conn = create_connection()
    cursor = conn.cursor()

    try:
        if folder_id:
            # Query with both user_id and folder_id
            cursor.execute("""
                SELECT file_name, file_summary
                FROM file_content
                WHERE user_id = %s AND folder_id = %s
                ORDER BY file_name
            """, (user_id, folder_id))
        else:
            # Query with only user_id
            cursor.execute("""
                SELECT file_name, file_summary
                FROM file_content
                WHERE user_id = %s
                ORDER BY file_name
            """, (user_id,))

        # Fetch all results
        results = cursor.fetchall()

        # Convert results to a formatted string
        if not results:
            return "No files found."

        output_string = ""
        for row in results:
            file_name = row[0]
            file_summary = row[1] if row[1] else "No summary available"
            output_string += f"File: {file_name}\nSummary: {file_summary}\n\n"

        return output_string.strip()

    except Exception as e:
        error_msg = f"Error fetching file summaries: {str(e)}"
        return error_msg
    finally:
        conn.close()


def fetch_chunk_count(user_id: str, folder_id: str = None):
    """
    Fetch the count of chunks from the database for a given user_id and optional folder_id.

    Parameters:
    - user_id (str): The ID of the user whose chunks to count
    - folder_id (str, optional): The ID of the folder to filter by. If None, counts all chunks for the user.

    Returns:
    - int: The total number of chunks
    """
    conn = create_connection()
    cursor = conn.cursor()

    try:
        if folder_id:
            # Query with both user_id and folder_id
            cursor.execute("""
                SELECT COUNT(*)
                FROM chunks
                WHERE user_id = %s AND folder_id = %s
            """, (user_id, folder_id))
        else:
            # Query with only user_id
            cursor.execute("""
                SELECT COUNT(*)
                FROM chunks
                WHERE user_id = %s
            """, (user_id,))

        # Fetch the count result as an integer
        count = cursor.fetchone()[0]

        # Return the integer count
        return count

    except Exception as e:
        print(f"Error fetching chunk count: {str(e)}")
        return 0  # Return 0 in case of error
    finally:
        conn.close()

def db_operation(func):
    """Decorator to handle database operations with proper connection management."""
    @wraps(func)
    def wrapper(*args, **kwargs):
        connection = get_connection()
        if not connection:
            logger.error(f"Failed to get database connection for {func.__name__}")
            return None
        
        try:
            kwargs['connection'] = connection
            result = func(*args, **kwargs)
            return result
        except Exception as e:
            logger.error(f"Error in {func.__name__}: {e}")
            return None
        finally:
            return_connection(connection)
    
    return wrapper

@timing_decorator
@db_operation
def execute_query(query, params=None, connection=None):
    """Execute a SQL query and return the result."""
    cursor = connection.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute(query, params)
        if query.strip().upper().startswith('SELECT'):
            result = cursor.fetchall()
            return result
        connection.commit()
        return True
    except Exception as e:
        connection.rollback()
        logger.error(f"Error executing query: {e}")
        logger.error(f"Query: {query}")
        if params:
            logger.error(f"Params: {params}")
        return None
    finally:
        cursor.close()

@timing_decorator
@db_operation
def get_lockr_by_name_and_user(user_id: str, name: str, connection=None):
    """
    Check if a locker with the given name exists for the user.
    Returns the locker_id if found, None otherwise.
    """
    if connection is None:
        connection = get_connection()
        if not connection:
            logger.error("Failed to get database connection")
            return None
        should_close = True
    else:
        should_close = False
    cursor = connection.cursor(cursor_factory=RealDictCursor)
    try:
        query = """
        SELECT lockr_id 
        FROM LOCKR 
        WHERE user_id = %s AND LOWER(name) = LOWER(%s)
        """
        cursor.execute(query, (str(user_id), name))
        result = cursor.fetchone()
        return result['lockr_id'] if result else None
    finally:
        cursor.close()

@timing_decorator
def get_user_id_by_username(username):
    """Retrieve the user_id (UUID) for a given username."""
    query = "SELECT user_id FROM USERS WHERE username = %s"
    result = execute_query(query, (username,))
    
    if result and len(result) > 0:
        return result[0]['user_id']
    else:
        logger.warning(f"No user found with username: {username}")
        return None

@timing_decorator
def get_lockers_and_clients_by_user(user_id):
    """Retrieve lockers and clients associated with a user."""
    query = """
    SELECT 'LOCKR' AS type, lockr_id AS id, name FROM LOCKR WHERE user_id = %s
    UNION ALL
    SELECT 'CLIENT' AS type, client_id AS id, name FROM CLIENTS WHERE user_id = %s
    ORDER BY type, name;
    """
    return execute_query(query, (user_id, user_id))

@timing_decorator
def get_files_by_locker(lockr_id):
    """Retrieve files associated with a locker."""
    query = """
    SELECT * FROM FILES 
    WHERE lockr_id = %s
    ORDER BY file_last_modified DESC
    """
    return execute_query(query, (lockr_id,))

@timing_decorator
def get_files_by_client(client_id):
    """Retrieve files associated with a client."""
    query = """
    SELECT * FROM FILES 
    WHERE client_id = %s
    ORDER BY file_last_modified DESC
    """
    return execute_query(query, (client_id,))

@timing_decorator
def get_files_by_user(user_id):
    """Retrieve all files associated with a user."""
    query = """
    SELECT * FROM FILES 
    WHERE user_id = %s
    ORDER BY file_last_modified DESC
    """
    return execute_query(query, (user_id,))

@timing_decorator
def get_files_by_chat(chat_id):
    """Retrieve files associated with a chat."""
    query = """
    SELECT * FROM FILES 
    WHERE chat_id = %s
    ORDER BY file_last_modified DESC
    """
    return execute_query(query, (chat_id,))

@timing_decorator
def get_file_info(file_id):
    """Retrieve information about a specific file."""
    query = "SELECT * FROM FILES WHERE file_id = %s"
    result = execute_query(query, (str(file_id),))
    
    if result and len(result) > 0:
        return result[0]
    return None

@timing_decorator
def get_chats_by_locker(lockr_id):
    """Retrieve chats associated with a locker."""
    query = """
    SELECT * FROM CHATS 
    WHERE lockr_id = %s
    ORDER BY chat_date DESC
    """
    return execute_query(query, (lockr_id,))

@timing_decorator
def get_chats_by_client(client_id):
    """Retrieve chats associated with a client."""
    query = """
    SELECT * FROM CHATS 
    WHERE client_id = %s
    ORDER BY chat_date DESC
    """
    return execute_query(query, (client_id,))

@timing_decorator
def get_chats_by_user(user_id):
    """Retrieve all chats associated with a user."""
    query = """
    SELECT * FROM CHATS 
    WHERE user_id = %s
    ORDER BY chat_date DESC
    """
    return execute_query(query, (user_id,))

@timing_decorator
def get_chat_info(chat_id):
    """Retrieve information about a specific chat."""
    query = "SELECT * FROM CHATS WHERE chat_id = %s"
    result = execute_query(query, (str(chat_id),))
    
    if result and len(result) > 0:
        return result[0]
    return None

@timing_decorator
def get_all_user_chats(user_id):
    """
    Retrieve all chats associated with a user.
    
    Args:
        user_id (str): The ID of the user
        
    Returns:
        list: A list of chat dictionaries containing chat details
    """
    query = """
    SELECT * FROM CHATS 
    WHERE user_id = %s
    ORDER BY chat_name
    """
    return execute_query(query, (user_id,))

@timing_decorator
def get_user_dashboard_data(user_id):
    """
    Get all data needed for a user's dashboard in a single query.
    This optimizes multiple separate queries into one.
    
    Args:
        user_id (str): The ID of the user
        
    Returns:
        dict: A dictionary containing all dashboard data
    """
    connection = get_connection()
    if not connection:
        logger.error("Failed to get database connection for get_user_dashboard_data")
        return None
    
    try:
        # Get lockers and clients
        items_query = """
        SELECT 'LOCKR' AS type, lockr_id AS id, name FROM LOCKR WHERE user_id = %s
        UNION ALL
        SELECT 'CLIENT' AS type, client_id AS id, name FROM CLIENTS WHERE user_id = %s
        ORDER BY type, name;
        """
        items_cursor = connection.cursor(cursor_factory=RealDictCursor)
        items_cursor.execute(items_query, (user_id, user_id))
        items = items_cursor.fetchall()
        
        # Get recent files
        files_query = """
        SELECT * FROM FILES 
        WHERE user_id = %s
        ORDER BY file_last_modified DESC
        LIMIT 10
        """
        files_cursor = connection.cursor(cursor_factory=RealDictCursor)
        files_cursor.execute(files_query, (user_id,))
        recent_files = files_cursor.fetchall()
        
        # Get recent chats
        chats_query = """
        SELECT * FROM CHATS 
        WHERE user_id = %s
        ORDER BY chat_date DESC
        LIMIT 10
        """
        chats_cursor = connection.cursor(cursor_factory=RealDictCursor)
        chats_cursor.execute(chats_query, (user_id,))
        recent_chats = chats_cursor.fetchall()
        
        # Get counts
        counts_query = """
        SELECT 
            (SELECT COUNT(*) FROM LOCKR WHERE user_id = %s) AS locker_count,
            (SELECT COUNT(*) FROM CLIENTS WHERE user_id = %s) AS client_count,
            (SELECT COUNT(*) FROM FILES WHERE user_id = %s) AS file_count,
            (SELECT COUNT(*) FROM CHATS WHERE user_id = %s) AS chat_count
        """
        counts_cursor = connection.cursor(cursor_factory=RealDictCursor)
        counts_cursor.execute(counts_query, (user_id, user_id, user_id, user_id))
        counts = counts_cursor.fetchone()
        
        return {
            'items': items,
            'recent_files': recent_files,
            'recent_chats': recent_chats,
            'counts': counts
        }
    except Exception as e:
        logger.error(f"Error in get_user_dashboard_data: {e}")
        return None
    finally:
        return_connection(connection)

def print_users_table():
    """Print all rows from the USERS table."""
    query = "SELECT user_id, username FROM USERS"
    users = execute_query(query)
    
    if users:
        print("\nUSERS Table:")
        print("-" * 40)
        print(f"{'User ID':<38} | {'Username'}")
        print("-" * 40)
        for user in users:
            print(f"{user['user_id']} | {user['username']}")
        print("-" * 40)
    else:
        print("No users found in the USERS table.")

@timing_decorator
def get_clients_with_details_optimized(user_id):
    """
    Get all clients with their associated files and chats in an optimized way.
    This reduces the number of database queries compared to separate calls.
    
    Instead of making N+1 queries (1 for clients + N for each client's files and chats),
    this function makes only 3 queries total:
    1. Get all clients for the user
    2. Get all files for all clients in a single query
    3. Get all chats for all clients in a single query
    
    This significantly reduces database round trips and improves performance,
    especially when there are many clients.
    
    Args:
        user_id (str): The ID of the user
        
    Returns:
        list: A list of dictionaries containing client details with their files and chats
    """
    connection = get_connection()
    if not connection:
        logger.error("Failed to get database connection for get_clients_with_details_optimized")
        return None
    
    try:
        # Get all clients for the user
        clients_query = """
        SELECT client_id, name 
        FROM CLIENTS 
        WHERE user_id = %s
        ORDER BY name
        """
        clients_cursor = connection.cursor(cursor_factory=RealDictCursor)
        clients_cursor.execute(clients_query, (user_id,))
        clients = clients_cursor.fetchall()
        
        # If no clients found, return an empty list
        if not clients or len(clients) == 0:
            logger.info(f"No clients found for user_id: {user_id}")
            return []
            
        # Get all client IDs
        client_ids = [client['client_id'] for client in clients]
        
        # Format the list of client IDs for the IN clause
        placeholders = ', '.join(['%s'] * len(client_ids))
        
        # Get all files for these clients in a single query
        files_query = f"""
        SELECT client_id, file_id, file_name, file_type, file_last_modified, file_size as size, file_link, is_synced
        FROM FILES 
        WHERE client_id IN ({placeholders})
        ORDER BY file_last_modified DESC
        """
        files_cursor = connection.cursor(cursor_factory=RealDictCursor)
        files_cursor.execute(files_query, client_ids)
        all_files = files_cursor.fetchall()
        
        # Get all chats for these clients in a single query
        chats_query = f"""
        SELECT client_id, chat_id, chat_name, chat_description, chat_date, chat_type
        FROM CHATS 
        WHERE client_id IN ({placeholders})
        ORDER BY chat_date DESC
        """
        chats_cursor = connection.cursor(cursor_factory=RealDictCursor)
        chats_cursor.execute(chats_query, client_ids)
        all_chats = chats_cursor.fetchall()
        
        # Organize files and chats by client_id
        files_by_client = {}
        chats_by_client = {}
        
        for file in all_files:
            client_id = file['client_id']
            if client_id not in files_by_client:
                files_by_client[client_id] = []
            files_by_client[client_id].append(file)
        
        for chat in all_chats:
            client_id = chat['client_id']
            if client_id not in chats_by_client:
                chats_by_client[client_id] = []
            chats_by_client[client_id].append(chat)
        
        # Build the final result
        clients_with_details = []
        for client in clients:
            client_id = client['client_id']
            clients_with_details.append({
                'client_id': client_id,
                'name': client['name'],
                'files': files_by_client.get(client_id, []),
                'chats': chats_by_client.get(client_id, [])
            })
        
        return clients_with_details
    except Exception as e:
        logger.error(f"Error in get_clients_with_details_optimized: {e}")
        return None
    finally:
        return_connection(connection)

@timing_decorator
def get_lockers_with_details_optimized(user_id):
    """
    Get all lockers with their associated files and chats in an optimized way.
    This reduces the number of database queries compared to separate calls.
    
    Instead of making N+1 queries (1 for lockers + N for each locker's files and chats),
    this function makes only 3 queries total:
    1. Get all lockers for the user
    2. Get all files for all lockers in a single query
    3. Get all chats for all lockers in a single query
    
    This significantly reduces database round trips and improves performance,
    especially when there are many lockers.
    
    Args:
        user_id (str): The ID of the user
        
    Returns:
        list: A list of dictionaries containing locker details with their files and chats
    """
    connection = get_connection()
    if not connection:
        logger.error("Failed to get database connection for get_lockers_with_details_optimized")
        return None
    
    try:
        # Get all lockers for the user
        lockers_query = """
        SELECT lockr_id, name 
        FROM LOCKR 
        WHERE user_id = %s
        ORDER BY name
        """
        lockers_cursor = connection.cursor(cursor_factory=RealDictCursor)
        lockers_cursor.execute(lockers_query, (user_id,))
        lockers = lockers_cursor.fetchall()
        
        # If no lockers found, return an empty list
        if not lockers or len(lockers) == 0:
            logger.info(f"No lockers found for user_id: {user_id}")
            return []
            
        # Get all locker IDs
        locker_ids = [locker['lockr_id'] for locker in lockers]
        
        # Format the list of locker IDs for the IN clause
        placeholders = ', '.join(['%s'] * len(locker_ids))
        
        # Get all files for these lockers in a single query
        files_query = f"""
        SELECT lockr_id, file_id, file_name, file_type, file_last_modified, file_size as size, file_link, is_synced 
        FROM FILES 
        WHERE lockr_id IN ({placeholders})
        ORDER BY file_last_modified DESC
        """
        files_cursor = connection.cursor(cursor_factory=RealDictCursor)
        files_cursor.execute(files_query, locker_ids)
        all_files = files_cursor.fetchall()
        
        # Get all chats for these lockers in a single query
        chats_query = f"""
        SELECT lockr_id, chat_id, chat_name, chat_description, chat_date, chat_type
        FROM CHATS 
        WHERE lockr_id IN ({placeholders})
        ORDER BY chat_date DESC
        """
        chats_cursor = connection.cursor(cursor_factory=RealDictCursor)
        chats_cursor.execute(chats_query, locker_ids)
        all_chats = chats_cursor.fetchall()
        
        # Organize files and chats by lockr_id
        files_by_locker = {}
        chats_by_locker = {}
        
        for file in all_files:
            lockr_id = file['lockr_id']
            if lockr_id not in files_by_locker:
                files_by_locker[lockr_id] = []
            files_by_locker[lockr_id].append(file)
        
        for chat in all_chats:
            lockr_id = chat['lockr_id']
            if lockr_id not in chats_by_locker:
                chats_by_locker[lockr_id] = []
            chats_by_locker[lockr_id].append(chat)
        
        # Build the final result
        lockers_with_details = []
        for locker in lockers:
            lockr_id = locker['lockr_id']
            lockers_with_details.append({
                'lockr_id': lockr_id,
                'name': locker['name'],
                'files': files_by_locker.get(lockr_id, []),
                'chats': chats_by_locker.get(lockr_id, [])
            })
        
        return lockers_with_details
    except Exception as e:
        logger.error(f"Error in get_lockers_with_details_optimized: {e}")
        return None
    finally:
        return_connection(connection)


@timing_decorator
@db_operation
def get_user_chats(user_id, client_id=None, lockr_id=None, connection=None):
    """
    Fetch all chats for a user from the CHATS table based on user_id, client_id, and lockr_id.
    Returns a list of chat dictionaries containing all chat information.
    """
    # Base query
    query = """
    SELECT chat_id, user_id, client_id, lockr_id, chat_name, chat_description, chat_date, chat_type
    FROM CHATS
    WHERE user_id = %s
    """

    # Parameters list
    params = [str(user_id)]

    # Add conditions based on client_id and lockr_id
    if client_id is not None and lockr_id is not None:
        query += " AND client_id = %s AND lockr_id = %s"
        params.extend([str(client_id), str(lockr_id)])
    elif client_id is not None:
        query += " AND client_id = %s"
        params.append(str(client_id))
    elif lockr_id is not None:
        query += " AND lockr_id = %s"
        params.append(str(lockr_id))

    # Order by chat_date
    query += " ORDER BY chat_date DESC"

    logger.info(f"Executing query with params: {params}")
    result = execute_query(query, tuple(params), connection=connection)

    if result:
        # Convert datetime objects to strings
        for chat in result:
            if 'chat_date' in chat and chat['chat_date']:
                chat['chat_date'] = chat['chat_date'].isoformat()  # Convert datetime to ISO format string
        return result
    return []

def get_file_content(user_id: str, file_name: str, folder_id: str = None):
    query = """
        SELECT file_name, content
        FROM file_content
        WHERE user_id = %s AND file_name = %s AND folder_id = %s
    """

    return execute_query(query, (user_id, file_name, folder_id))
def main():
    """Main function to interact with the database."""
    print_users_table()
    
    user_id = input("Enter user ID: ")
    try:
        print("Lockers and Clients associated with user: ", get_lockers_and_clients_by_user(user_id))
        print("Files associated with user: ", get_files_by_user(user_id))
        print("Chats associated with user: ", get_chats_by_user(user_id))
    except ValueError:
        print("Invalid user ID. Please enter a valid UUID.")
    
    lockr_id = input("Enter locker ID: ")
    try:
        print("Files in Locker:", get_files_by_locker(lockr_id))
        print("Chats in Locker:", get_chats_by_locker(lockr_id))
    except ValueError:
        print("Invalid locker ID. Please enter a valid UUID.")
    
    client_id = input("Enter client ID: ")
    try:
        print("Files for Client:", get_files_by_client(client_id))
        print("Chats for Client:", get_chats_by_client(client_id))
    except ValueError:
        print("Invalid client ID. Please enter a valid UUID.")
    
    chat_id = input("Enter chat ID: ")
    try:
        chat_id_uuid = UUID(chat_id)  # Validate UUID
        print("Chat Info:", get_chat_info(chat_id_uuid))
        print("Files in Chat:", get_files_by_chat(chat_id))
    except ValueError:
        print("Invalid chat ID. Please enter a valid UUID.")
    
    file_id = input("Enter file ID: ")
    try:
        file_id_uuid = UUID(file_id)  # Validate UUID
        print("File Info:", get_file_info(file_id_uuid))
    except ValueError:
        print("Invalid file ID. Please enter a valid UUID.")

# ============================================
# MAPPING RULES FUNCTIONS
# ============================================

@timing_decorator
def get_mapping_rules(
    user_id: str,
    company_name: str,
    document_type: str = None,
    active_only: bool = True,
    ledger_slot: str = None,
):
    """
    Get mapping rules for a user/company.

    Args:
        user_id: User ID
        company_name: Company name
        document_type: Optional filter by document type
        active_only: If True, only return active rules
        ledger_slot: Optional slot filter ('party' or 'expense'). When set,
                     returns rules where ledger_slot matches OR is NULL
                     (legacy rules without an explicit slot continue to
                     evaluate against both slots so they keep working).

    Returns:
        List of mapping rule dictionaries
    """
    query = """
    SELECT rule_id, user_id, company_name, rule_name, rule_type, document_type,
           keywords, pattern, vendor_name, amount_min, amount_max,
           target_ledger, target_voucher_type, ledger_slot, priority, is_active,
           match_count, last_used_at, created_at, updated_at,
           conditions
    FROM mapping_rules
    WHERE user_id = %s AND company_name = %s
    """
    params = [user_id, company_name]

    if document_type:
        query += " AND document_type = %s"
        params.append(document_type)

    if ledger_slot:
        # NULL slot rules are "either" — keep firing for both party and expense
        # calls so users who haven't migrated their rules don't lose behavior.
        query += " AND (ledger_slot = %s OR ledger_slot IS NULL)"
        params.append(ledger_slot)

    if active_only:
        query += " AND is_active = true"

    query += " ORDER BY priority DESC, match_count DESC"

    return execute_query(query, tuple(params))


@timing_decorator
def get_mapping_rule_by_id(rule_id: str):
    """Get a specific mapping rule by ID."""
    query = """
    SELECT rule_id, user_id, company_name, rule_name, rule_type, document_type,
           keywords, pattern, vendor_name, amount_min, amount_max,
           target_ledger, target_voucher_type, ledger_slot, priority, is_active,
           match_count, last_used_at, created_at, updated_at,
           conditions
    FROM mapping_rules
    WHERE rule_id = %s
    """
    result = execute_query(query, (rule_id,))
    return result[0] if result else None


# ============================================
# IMPORT BATCH FUNCTIONS
# ============================================

@timing_decorator
def get_import_batch(batch_id: str):
    """Get an import batch by ID."""
    query = """
    SELECT batch_id, user_id, company_name, document_type,
           total_transactions, processed_count, duplicate_count, error_count, pushed_count,
           status, files, created_at, completed_at
    FROM import_batches
    WHERE batch_id = %s
    """
    result = execute_query(query, (batch_id,))
    return result[0] if result else None


@timing_decorator
def get_import_batches_by_user(user_id: str, company_name: str = None, document_type: str = None, limit: int = 50):
    """Get import batches for a user."""
    query = """
    SELECT batch_id, user_id, company_name, document_type,
           total_transactions, processed_count, duplicate_count, error_count, pushed_count,
           status, files, created_at, completed_at
    FROM import_batches
    WHERE user_id = %s
    """
    params = [user_id]

    if company_name:
        query += " AND company_name = %s"
        params.append(company_name)

    if document_type:
        query += " AND document_type = %s"
        params.append(document_type)

    query += " ORDER BY created_at DESC LIMIT %s"
    params.append(limit)

    return execute_query(query, tuple(params))


# ============================================
# TRANSACTION IMPORT FUNCTIONS
# ============================================

@timing_decorator
def get_transaction_imports_by_batch(batch_id: str):
    """Get all transaction imports for a batch."""
    query = """
    SELECT import_id, batch_id, user_id, company_name, document_type,
           original_filename, file_hash, transaction_date, reference_number,
           amount, description, party_name, transaction_hash,
           status, tally_voucher_number, extracted_data, confidence_scores,
           debit_ledger, credit_ledger, rule_applied,
           created_at, processed_at, pushed_at
    FROM transaction_imports
    WHERE batch_id = %s
    ORDER BY transaction_date, created_at
    """
    return execute_query(query, (batch_id,))


@timing_decorator
def get_transaction_import_by_id(import_id: str):
    """Get a specific transaction import by ID."""
    query = """
    SELECT import_id, batch_id, user_id, company_name, document_type,
           original_filename, file_hash, transaction_date, reference_number,
           amount, description, party_name, transaction_hash,
           status, tally_voucher_number, extracted_data, confidence_scores,
           debit_ledger, credit_ledger, rule_applied,
           created_at, processed_at, pushed_at
    FROM transaction_imports
    WHERE import_id = %s
    """
    result = execute_query(query, (import_id,))
    return result[0] if result else None


@timing_decorator
def check_transaction_duplicate(user_id: str, company_name: str, transaction_hash: str):
    """
    Check if a transaction already exists (duplicate detection).

    Returns:
        The existing transaction import if found, None otherwise
    """
    query = """
    SELECT import_id, batch_id, transaction_date, reference_number, amount,
           description, party_name, status, created_at
    FROM transaction_imports
    WHERE user_id = %s AND company_name = %s AND transaction_hash = %s
    """
    result = execute_query(query, (user_id, company_name, transaction_hash))
    return result[0] if result else None


@timing_decorator
def find_similar_transactions(user_id: str, company_name: str, transaction_date, amount: float,
                              tolerance_days: int = 3, tolerance_percent: float = 0.01):
    """
    Find potentially similar transactions for fuzzy duplicate detection.

    Args:
        user_id: User ID
        company_name: Company name
        transaction_date: Date of the transaction
        amount: Transaction amount
        tolerance_days: Number of days tolerance for date matching
        tolerance_percent: Percentage tolerance for amount matching (0.01 = 1%)

    Returns:
        List of similar transactions
    """
    amount_min = amount * (1 - tolerance_percent)
    amount_max = amount * (1 + tolerance_percent)

    query = """
    SELECT import_id, batch_id, transaction_date, reference_number, amount,
           description, party_name, status, created_at
    FROM transaction_imports
    WHERE user_id = %s
      AND company_name = %s
      AND transaction_date BETWEEN %s::date - make_interval(days => %s) AND %s::date + make_interval(days => %s)
      AND amount BETWEEN %s AND %s
    ORDER BY ABS(amount - %s), ABS(transaction_date::date - %s::date)
    LIMIT 10
    """
    return execute_query(query, (
        user_id, company_name,
        transaction_date, tolerance_days, transaction_date, tolerance_days,
        amount_min, amount_max,
        amount, transaction_date
    ))


# ============================================
# AI FEEDBACK FUNCTIONS
# ============================================

@timing_decorator
def get_ai_feedback_for_learning(user_id: str, company_name: str, document_type: str, field: str = None, limit: int = 100):
    """
    Get AI extraction feedback for learning/improving suggestions.

    Args:
        user_id: User ID
        company_name: Company name
        document_type: Document type
        field: Optional specific field to get feedback for
        limit: Maximum number of records to return

    Returns:
        List of feedback records
    """
    query = """
    SELECT feedback_id, original_field, original_value, original_confidence,
           corrected_value, document_text_snippet, extraction_context, created_at
    FROM ai_extraction_feedback
    WHERE user_id = %s AND company_name = %s AND document_type = %s
    """
    params = [user_id, company_name, document_type]

    if field:
        query += " AND original_field = %s"
        params.append(field)

    query += " ORDER BY created_at DESC LIMIT %s"
    params.append(limit)

    return execute_query(query, tuple(params))


if __name__ == "__main__":
    main()