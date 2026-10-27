"""
Run database migrations for import tables.

Usage:
    python -m applicationca.database.run_migration
"""

import psycopg2
import os
from pathlib import Path
from config.settings import settings

def run_migration():
    """Execute the import tables migration SQL."""
    # Get the SQL file path
    sql_file = Path(__file__).parent / "create_import_tables.sql"

    if not sql_file.exists():
        print(f"Error: Migration file not found at {sql_file}")
        return False

    # Read the SQL file
    with open(sql_file, 'r') as f:
        sql_content = f.read()

    # Connect to database
    try:
        conn = psycopg2.connect(settings.DATABASE_URL)
        cursor = conn.cursor()

        print("Connected to database. Running migration...")

        # Execute the SQL
        cursor.execute(sql_content)
        conn.commit()

        print("Migration completed successfully!")

        # Verify tables were created
        cursor.execute("""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
            AND table_name IN ('mapping_rules', 'import_batches', 'transaction_imports', 'ai_extraction_feedback')
        """)
        tables = cursor.fetchall()

        print(f"\nCreated tables: {[t[0] for t in tables]}")

        return True

    except Exception as e:
        print(f"Error running migration: {e}")
        if conn:
            conn.rollback()
        return False
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


if __name__ == "__main__":
    run_migration()
