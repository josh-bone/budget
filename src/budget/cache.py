"""
cache.py — Simple in-memory cache for budget data.

Avoids hitting the Sheets API on every page load.
TTL is controlled by the BUDGET_CACHE_TTL env var (seconds, default 300).
"""

import logging
import os
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

DEFAULT_TTL = 300  # 5 minutes


def _get_ttl() -> int:
    try:
        return int(os.environ.get("BUDGET_CACHE_TTL", DEFAULT_TTL))
    except ValueError:
        return DEFAULT_TTL


@dataclass
class _CacheEntry:
    data: dict
    fetched_at: float = field(default_factory=time.monotonic)

    def is_fresh(self, ttl: int) -> bool:
        return (time.monotonic() - self.fetched_at) < ttl


class BudgetCache:
    def __init__(self):
        self._entry: _CacheEntry | None = None

    def get(self) -> dict | None:
        ttl = _get_ttl()
        if self._entry and self._entry.is_fresh(ttl):
            age = int(time.monotonic() - self._entry.fetched_at)
            logger.info(f"Cache hit (age {age}s, TTL {ttl}s)")
            return self._entry.data
        return None

    def set(self, data: dict) -> None:
        self._entry = _CacheEntry(data=data)
        logger.info(f"Cache updated with {len(data)} month(s)")

    def invalidate(self) -> None:
        self._entry = None
        logger.info("Cache invalidated")

    @property
    def fetched_at(self) -> float | None:
        return self._entry.fetched_at if self._entry else None


# Module-level singleton — shared across all requests in the process
budget_cache = BudgetCache()
