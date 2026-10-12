"""Short-lived in-process cache for the computed E05 risk register."""
from __future__ import annotations

import threading
from typing import Any

from cachetools import TTLCache

from app.core.config import settings

_risk_cache: TTLCache | None = None
_lock = threading.Lock()


def get(key: str) -> Any | None:
    with _lock:
        cache = _get_cache_unlocked()
        return cache.get(key) if cache is not None else None


def set(key: str, value: Any) -> None:
    with _lock:
        cache = _get_cache_unlocked()
        if cache is not None:
            cache[key] = value


def _get_cache_unlocked() -> TTLCache | None:
    global _risk_cache
    if settings.RISK_REGISTER_CACHE_TTL <= 0:
        return None
    if _risk_cache is None:
        _risk_cache = TTLCache(
            maxsize=settings.RISK_REGISTER_CACHE_MAXSIZE,
            ttl=settings.RISK_REGISTER_CACHE_TTL,
        )
    return _risk_cache


def invalidate_all() -> None:
    global _risk_cache
    with _lock:
        if _risk_cache is not None:
            _risk_cache.clear()
        _risk_cache = None
