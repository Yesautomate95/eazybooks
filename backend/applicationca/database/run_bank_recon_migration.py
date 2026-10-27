"""
Run database migration for bank reconciliation tables.

Usage:
    python -m applicationca.database.run_bank_recon_migration
"""

import psycopg2
from pathlib import Path
from config.settings import settings


def run_migration():
    sql_file = Path(__file__).parent / "create_bank_recon_tables.sql"

    if not sql_file.exists():
        print(f"Error: Migration file not found at {sql_file}")
        return False

    with open(sql_file, 'r') as f:
        sql_content = f.read()

    conn = None
    cursor = None
    try:
        conn = psycopg2.connect(settings.DATABASE_URL)
        cursor = conn.cursor()
        print("Connected to database. Running bank recon migration...")
        cursor.execute(sql_content)
        conn.commit()
        print("Base tables migration completed successfully!")

        # Apply the match-groups upgrade (idempotent). On a fresh DB the base
        # script above already created the new schema, so these ALTERs are no-ops;
        # on an existing DB they add the members table, tier column, nullable 1:1
        # columns and the closing-balance override. Mirrors QBO migration 029.
        groups_file = Path(__file__).parent / "migrations" / "030_bank_recon_match_groups.sql"
        if groups_file.exists():
            with open(groups_file, "r") as f:
                cursor.execute(f.read())
            conn.commit()
            print("Match-groups upgrade (030) applied successfully!")
        else:
            print(f"Warning: match-groups migration not found at {groups_file}")

        cursor.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public'
            AND table_name IN ('bank_recon_reports', 'bank_recon_transactions', 'bank_recon_matches')
        """)
        tables = cursor.fetchall()
        print(f"Created tables: {[t[0] for t in tables]}")
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
