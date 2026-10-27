import psycopg2

def drop_all_tables(conn):
    """Drop all tables in the database."""
    try:
        cursor = conn.cursor()

        # List of tables to drop
        tables = ["FILES", "CLIENTS", "LOCKR", "USERS", "CHATS"]  # Excluding CHUNKS table

        for table in tables:
            cursor.execute(f"DROP TABLE IF EXISTS {table} CASCADE;")
            print(f"Dropped table: {table}")

        conn.commit()
        print("All tables dropped successfully.")
    except Exception as e:
        conn.rollback()
        print(f"Error dropping tables: {e}")
    finally:
        cursor.close()

def main():
    from config.settings import settings
    # Connection details
    conn = psycopg2.connect(settings.DATABASE_URL)

    try:
        # Drop all tables
        drop_all_tables(conn)
    except Exception as e:
        print(f"Error: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    main()