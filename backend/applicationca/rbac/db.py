"""Thin connection helper for the RBAC module.

Uses the same DATABASE_URL via psycopg2 as the rest of the app — no
SQLAlchemy. Each call opens a fresh connection (matching the existing
codebase pattern in applicationca/database/database_update.py).
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator

import psycopg2
import psycopg2.extras

from config.settings import settings

logger = logging.getLogger(__name__)


@contextmanager
def get_conn() -> Iterator[psycopg2.extensions.connection]:
    """Yield a Postgres connection; commit on success, rollback on error."""
    conn = psycopg2.connect(settings.DATABASE_URL)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def get_cursor(dict_rows: bool = True) -> Iterator[psycopg2.extensions.cursor]:
    """Yield a cursor (DictCursor by default) with auto commit/rollback."""
    with get_conn() as conn:
        factory = psycopg2.extras.RealDictCursor if dict_rows else None
        cur = conn.cursor(cursor_factory=factory) if factory else conn.cursor()
        try:
            yield cur
        finally:
            cur.close()
