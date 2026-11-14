"""
utils.py
--------
Shared utility helpers: formatting, caching, and logging setup.
"""

from __future__ import annotations

import inspect
import logging
import threading
import time
from collections.abc import Callable
from decimal import Decimal
from functools import wraps
from typing import Any, TypeVar

from cachetools import TTLCache

from grocery_helper.config import settings

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def configure_logging(level: int = logging.INFO) -> None:
    """Configure root logger with a sensible format."""
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    if settings.debug:
        logging.getLogger("grocery_helper").setLevel(logging.DEBUG)


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def format_currency(amount: Decimal | float, symbol: str = "$") -> str:
    """Return a human-readable currency string, e.g. '$3.49'."""
    return f"{symbol}{Decimal(str(amount)):.2f}"


def format_savings(savings: Decimal | float) -> str:
    """Return a savings string, e.g. 'Save $1.20'."""
    val = Decimal(str(savings))
    if val <= 0:
        return "No savings available"
    return f"Save {format_currency(val)}"


def truncate(text: str, max_len: int = 50) -> str:
    """Truncate a string and append '…' if it exceeds *max_len*."""
    if max_len <= 0:
        return ""
    return text if len(text) <= max_len else text[: max_len - 1] + "…"


# ---------------------------------------------------------------------------
# In-memory TTL cache
# ---------------------------------------------------------------------------

_cache_lock = threading.RLock()
_price_cache: TTLCache[str, Any] = TTLCache(
    maxsize=512,
    ttl=settings.price_cache_ttl_seconds,
)


def get_cached_price(key: str) -> Any | None:
    """Return a cached price result, or None if absent / expired."""
    with _cache_lock:
        return _price_cache.get(key)


def set_cached_price(key: str, value: Any) -> None:
    """Store a price result in the TTL cache."""
    with _cache_lock:
        _price_cache[key] = value


def clear_price_cache() -> None:
    """Clear all entries from the in-memory price cache."""
    with _cache_lock:
        _price_cache.clear()


def cache_key(*parts: str) -> str:
    """Build a normalised cache key from the given string parts."""
    return ":".join(p.lower().strip() for p in parts)


# ---------------------------------------------------------------------------
# Simple timing decorator (useful for debugging slow API calls)
# ---------------------------------------------------------------------------

F = TypeVar("F", bound=Callable[..., Any])


def timed(func: F) -> F:
    """Log the execution time of a function (sync or async)."""
    log = logging.getLogger(func.__module__)

    if inspect.iscoroutinefunction(func):

        @wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            start = time.perf_counter()
            result = await func(*args, **kwargs)
            elapsed = time.perf_counter() - start
            log.debug("%s completed in %.3fs", func.__qualname__, elapsed)
            return result

        return async_wrapper  # type: ignore[return-value]

    @wraps(func)
    def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
        start = time.perf_counter()
        result = func(*args, **kwargs)
        elapsed = time.perf_counter() - start
        log.debug("%s completed in %.3fs", func.__qualname__, elapsed)
        return result

    return sync_wrapper  # type: ignore[return-value]
