"""In-memory TTL cache for resolved permission sets.

Computing a user's effective permissions hits 3 tables (user_roles,
role_permissions, user_permissions). Doing that on every request would be
expensive, so we cache per-user for a short TTL and let mutations invalidate.

Tradeoffs:
- TTL is short enough that stale grants self-heal even if invalidation is
  missed somewhere (defence in depth).
- The cache is process-local. In a multi-process deployment, mutations done
  on one worker will only invalidate that worker's cache; the others wait
  out the TTL. For this app that's acceptable; a distributed cache (Redis)
  is the obvious upgrade path.
"""
from __future__ import annotations

import threading
import time
from typing import Dict, Set, Tuple, List, Optional

DEFAULT_TTL_SECONDS = 300  # 5 minutes


class _UserAuthCache:
    """Caches (permission_set, role_list, is_active) per user."""

    def __init__(self, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> None:
        self._ttl = ttl_seconds
        self._lock = threading.RLock()
        # user_id -> (expires_at_epoch, perms, roles, is_active)
        self._store: Dict[str, Tuple[float, Set[str], List[dict], bool]] = {}

    def get(self, user_id: str) -> Optional[Tuple[Set[str], List[dict], bool]]:
        now = time.time()
        with self._lock:
            entry = self._store.get(user_id)
            if not entry:
                return None
            expires_at, perms, roles, is_active = entry
            if expires_at < now:
                self._store.pop(user_id, None)
                return None
            return perms, roles, is_active

    def put(self, user_id: str, perms: Set[str], roles: List[dict], is_active: bool) -> None:
        expires_at = time.time() + self._ttl
        with self._lock:
            self._store[user_id] = (expires_at, perms, roles, is_active)

    def invalidate(self, user_id: str) -> None:
        with self._lock:
            self._store.pop(user_id, None)

    def invalidate_all(self) -> None:
        with self._lock:
            self._store.clear()


# Module-level singleton
USER_AUTH_CACHE = _UserAuthCache()


def invalidate_user_cache(user_id: str) -> None:
    """Call this after mutating a user's roles or direct permissions."""
    USER_AUTH_CACHE.invalidate(user_id)


def invalidate_all() -> None:
    """Call this after mutating a role's permission set (affects many users)."""
    USER_AUTH_CACHE.invalidate_all()
