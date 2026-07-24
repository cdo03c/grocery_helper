"""Tests for utility helpers."""
from decimal import Decimal

import pytest

from grocery_helper.utils import cache_key, format_currency, format_savings, truncate


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
