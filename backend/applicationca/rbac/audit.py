"""Append-only audit log writer.

Every RBAC mutation and auth event flows through `record()`. The function
never raises — audit failures should not block the action that succeeded.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from .db import get_cursor

logger = logging.getLogger(__name__)


def record(
    *,
    action: str,
    actor_user_id: Optional[str] = None,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
    previous_value: Any = None,
    new_value: Any = None,
    metadata: Optional[dict] = None,
) -> None:
    """Persist one audit row.

    `previous_value` and `new_value` are JSON-serialised; pass plain dicts/lists.
    Use `metadata` for request context (ip, user_agent, request_id, etc.).
    """
    try:
        with get_cursor(dict_rows=False) as cur:
            cur.execute(
                """
                INSERT INTO rbac_audit_log
                    (actor_user_id, action, target_type, target_id,
                     previous_value, new_value, metadata)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    actor_user_id,
                    action,
                    target_type,
                    str(target_id) if target_id is not None else None,
                    json.dumps(previous_value) if previous_value is not None else None,
                    json.dumps(new_value) if new_value is not None else None,
                    json.dumps(metadata) if metadata is not None else None,
                ),
            )
    except Exception as exc:  # pragma: no cover — never break the caller
        logger.warning("rbac audit write failed for action=%s: %s", action, exc)
