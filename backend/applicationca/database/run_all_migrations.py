"""
Generic migration runner for files under applicationca/database/migrations/.

Each .sql file applies once; we track applied files in `_schema_migrations`.

Usage:
    python -m applicationca.database.run_all_migrations
"""

import logging
from pathlib import Path

import psycopg2

from config.settings import settings

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


SCHEMA_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS _schema_migrations (
    filename   TEXT PRIMARY KEY,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


def run_all():
    migrations_dir = Path(__file__).parent / "migrations"
    if not migrations_dir.exists():
        print(f"No migrations dir at {migrations_dir}")
        return False

    files = sorted(migrations_dir.glob("*.sql"))
    if not files:
        print("No migration files found.")
        return False

    conn = psycopg2.connect(settings.DATABASE_URL)
    cur = conn.cursor()
    try:
        cur.execute(SCHEMA_TABLE_DDL)
        conn.commit()

        cur.execute("SELECT filename FROM _schema_migrations")
        applied = {row[0] for row in cur.fetchall()}

        for path in files:
            if path.name in applied:
                print(f"  skip   {path.name} (already applied)")
                continue
            print(f"  apply  {path.name}")
            sql = path.read_text(encoding="utf-8")
            try:
                cur.execute(sql)
                cur.execute(
                    "INSERT INTO _schema_migrations (filename) VALUES (%s)",
                    (path.name,),
                )
                conn.commit()
            except Exception as exc:
                conn.rollback()
                print(f"  FAIL   {path.name}: {exc}")
                return False

        print("All migrations applied.")
        return True
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    run_all()
