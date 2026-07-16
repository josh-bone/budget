import os
import time
from unittest.mock import patch

from budget.cache import BudgetCache

# ── Helpers ────────────────────────────────────────────────────────────────────

SAMPLE_DATA = {"1/2026": {"summary": {"net_income": 4000.0}}}


def make_cache(**env) -> BudgetCache:
    """Return a fresh BudgetCache with a controlled environment."""
    return BudgetCache()


# ── Basic get/set/invalidate ───────────────────────────────────────────────────


def test_get_returns_none_when_empty():
    cache = BudgetCache()
    assert cache.get() is None


def test_set_then_get_returns_data():
    cache = BudgetCache()
    cache.set(SAMPLE_DATA)
    assert cache.get() == SAMPLE_DATA


def test_invalidate_clears_cache():
    cache = BudgetCache()
    cache.set(SAMPLE_DATA)
    cache.invalidate()
    assert cache.get() is None


def test_fetched_at_is_none_before_set():
    cache = BudgetCache()
    assert cache.fetched_at is None


def test_fetched_at_is_set_after_set():
    cache = BudgetCache()
    before = time.monotonic()
    cache.set(SAMPLE_DATA)
    after = time.monotonic()
    assert before <= cache.fetched_at <= after


def test_fetched_at_is_none_after_invalidate():
    cache = BudgetCache()
    cache.set(SAMPLE_DATA)
    cache.invalidate()
    assert cache.fetched_at is None


# ── TTL behaviour ──────────────────────────────────────────────────────────────


def test_cache_hit_within_ttl():
    cache = BudgetCache()
    cache.set(SAMPLE_DATA)
    with patch.dict(os.environ, {"BUDGET_CACHE_TTL": "60"}):
        assert cache.get() == SAMPLE_DATA


def test_cache_miss_after_ttl_expires():
    cache = BudgetCache()
    cache.set(SAMPLE_DATA)
    # Simulate the entry being old by backdating fetched_at
    cache._entry.fetched_at = time.monotonic() - 400
    with patch.dict(os.environ, {"BUDGET_CACHE_TTL": "300"}):
        assert cache.get() is None


def test_cache_respects_ttl_env_var():
    cache = BudgetCache()
    cache.set(SAMPLE_DATA)
    # Age the entry by 10 seconds
    cache._entry.fetched_at = time.monotonic() - 10
    # TTL of 5 → stale
    with patch.dict(os.environ, {"BUDGET_CACHE_TTL": "5"}):
        assert cache.get() is None
    # TTL of 60 → still fresh
    with patch.dict(os.environ, {"BUDGET_CACHE_TTL": "60"}):
        assert cache.get() == SAMPLE_DATA


def test_invalid_ttl_env_falls_back_to_default():
    cache = BudgetCache()
    cache.set(SAMPLE_DATA)
    with patch.dict(os.environ, {"BUDGET_CACHE_TTL": "not_a_number"}):
        # Default is 300s; entry is brand new so should be fresh
        assert cache.get() == SAMPLE_DATA


# ── Overwrite behaviour ────────────────────────────────────────────────────────


def test_set_overwrites_previous_data():
    cache = BudgetCache()
    cache.set({"old": "data"})
    new_data = {"1/2026": {"summary": {}}}
    cache.set(new_data)
    assert cache.get() == new_data


def test_multiple_invalidate_calls_are_safe():
    cache = BudgetCache()
    cache.invalidate()
    cache.invalidate()  # should not raise
    assert cache.get() is None
