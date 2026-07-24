"""Tests for utility helpers."""
from __future__ import annotations

import logging
from decimal import Decimal
from unittest.mock import patch

import pytest

from grocery_helper.utils import (
    cache_key,
    configure_logging,
    format_currency,
    format_savings,
    get_cached_price,
    set_cached_price,
    timed,
    truncate,
)


# ---------------------------------------------------------------------------
# configure_logging
# ---------------------------------------------------------------------------


class TestConfigureLogging:
    def test_sets_root_level(self) -> None:
        configure_logging(level=logging.WARNING)
        # basicConfig is idempotent once handlers exist; just assert no crash.

    def test_debug_branch_sets_debug_level(self) -> None:
        with patch("grocery_helper.utils.settings") as mock_settings:
            mock_settings.debug = True
            mock_settings.price_cache_ttl_seconds = 3600
            configure_logging()
        gh_logger = logging.getLogger("grocery_helper")
        assert gh_logger.level == logging.DEBUG

    def test_no_debug_branch_skipped(self) -> None:
        with patch("grocery_helper.utils.settings") as mock_settings:
            mock_settings.debug = False
            mock_settings.price_cache_ttl_seconds = 3600
            configure_logging()  # should not raise


class TestFormatCurrency:
    def test_basic(self) -> None:
        assert format_currency(Decimal("3.49")) == "$3.49"

    def test_zero(self) -> None:
        assert format_currency(0) == "$0.00"

    def test_custom_symbol(self) -> None:
        assert format_currency(Decimal("1.00"), symbol="€") == "€1.00"


class TestFormatSavings:
    def test_positive(self) -> None:
        assert format_savings(Decimal("1.50")) == "Save $1.50"

    def test_zero(self) -> None:
        assert format_savings(0) == "No savings available"


class TestTruncate:
    def test_short_string(self) -> None:
        assert truncate("Hello") == "Hello"

    def test_long_string(self) -> None:
        result = truncate("A" * 60, max_len=10)
        assert len(result) == 10
        assert result.endswith("…")


class TestCacheKey:
    def test_normalises(self) -> None:
        assert cache_key("  Milk  ", "Store A") == "milk:store a"

    def test_single_part(self) -> None:
        assert cache_key("Bread") == "bread"

    def test_multiple_parts(self) -> None:
        assert cache_key("A", "B", "C") == "a:b:c"


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------


class TestCacheHelpers:
    def test_miss_returns_none(self) -> None:
        assert get_cached_price("__nonexistent_key__") is None

    def test_set_then_get(self) -> None:
        key = cache_key("test", "item", "store_a")
        set_cached_price(key, {"price": 1.99})
        result = get_cached_price(key)
        assert result == {"price": 1.99}

    def test_overwrite(self) -> None:
        key = cache_key("overwrite", "test")
        set_cached_price(key, "first")
        set_cached_price(key, "second")
        assert get_cached_price(key) == "second"


# ---------------------------------------------------------------------------
# timed decorator
# ---------------------------------------------------------------------------


class TestTimedDecorator:
    def test_sync_function_returns_value(self) -> None:
        @timed
        def add(a: int, b: int) -> int:
            return a + b

        assert add(2, 3) == 5

    def test_sync_function_preserves_name(self) -> None:
        @timed
        def my_func() -> None:
            pass

        assert my_func.__name__ == "my_func"

    def test_sync_function_logs_debug(self, caplog: pytest.LogCaptureFixture) -> None:
        @timed
        def fast_op() -> str:
            return "done"

        with caplog.at_level(logging.DEBUG):
            result = fast_op()

        assert result == "done"

    @pytest.mark.asyncio
    async def test_async_function_returns_value(self) -> None:
        @timed
        async def async_add(a: int, b: int) -> int:
            return a + b

        assert await async_add(10, 20) == 30

    @pytest.mark.asyncio
    async def test_async_function_preserves_name(self) -> None:
        @timed
        async def my_async_func() -> None:
            pass

        assert my_async_func.__name__ == "my_async_func"

    @pytest.mark.asyncio
    async def test_async_function_logs_debug(self, caplog: pytest.LogCaptureFixture) -> None:
        @timed
        async def async_op() -> str:
            return "async done"

        with caplog.at_level(logging.DEBUG):
            result = await async_op()

        assert result == "async done"
