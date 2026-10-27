"""
Database Migration Runner
Run with: python -m applicationca.database.migrations.run_migrations
"""

import os
import psycopg2
from pathlib import Path
from config.settings import settings
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def run_migrations():
    """Execute all SQL migration files in order."""
    migrations_dir = Path(__file__).parent

    # Get all .sql files sorted by name
    sql_files = sorted(migrations_dir.glob("*.sql"))

    if not sql_files:
        logger.info("No migration files found")
        return

    logger.info(f"Found {len(sql_files)} migration file(s)")

    try:
        connection = psycopg2.connect(settings.DATABASE_URL)
        cursor = connection.cursor()

        for sql_file in sql_files:
            logger.info(f"Running migration: {sql_file.name}")

            with open(sql_file, 'r') as f:
                sql_content = f.read()

            try:
                cursor.execute(sql_content)
                connection.commit()
                logger.info(f"Successfully executed: {sql_file.name}")
            except psycopg2.Error as e:
                connection.rollback()
                # Check if it's a "already exists" error - that's OK
                if "already exists" in str(e).lower():
                    logger.warning(f"Objects in {sql_file.name} already exist (skipping): {e}")
                else:
                    logger.error(f"Error executing {sql_file.name}: {e}")
                    raise

        cursor.close()
        connection.close()
        logger.info("All migrations completed successfully")

    except Exception as e:
        logger.error(f"Migration failed: {e}")
        raise


if __name__ == "__main__":
    run_migrations()
