"""In-process TTL cache for dashboard summaries (dashboard_tasks #2).

First caching layer in the codebase — deliberately in-process
(`cachetools.TTLCache`) rather than Redis. Each worker process has its
own cache; with the 30s default TTL that staleness window is acceptable
for a read-only overview surface. No explicit invalidation calls are
wired into module routers — the TTL is the invalidation strategy (see
task spec, "Cache invalidation triggers" decision).
"""
from __future__ import annotations

import threading

from cachetools import TTLCache

from app.core.config import settings

_dashboard_cache: TTLCache | None = None
_lock = threading.Lock()


def get_cache() -> TTLCache | None:
    """Return the singleton cache, or None when caching is disabled (TTL <= 0)."""
    global _dashboard_cache
    if settings.DASHBOARD_CACHE_TTL <= 0:
        return None
    with _lock:
        if _dashboard_cache is None:
            _dashboard_cache = TTLCache(
                maxsize=settings.DASHBOARD_CACHE_MAXSIZE,
                ttl=settings.DASHBOARD_CACHE_TTL,
            )
        return _dashboard_cache


def invalidate_all() -> None:
    """Drop every cached summary. Also resets the singleton so a changed
    TTL setting (e.g. in tests) takes effect on the next get_cache()."""
    global _dashboard_cache
    with _lock:
        if _dashboard_cache is not None:
            _dashboard_cache.clear()
        _dashboard_cache = None
