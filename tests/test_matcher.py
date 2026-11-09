"""Tests for the item normalization and fuzzy matching utilities."""

from __future__ import annotations

from grocery_helper.matcher import (
    calculate_match_score,
    find_best_match,
    normalize_item_name,
    parse_quantity_and_unit,
)
from grocery_helper.models import Unit


class TestParseQuantityAndUnit:
    def test_extracts_lb_quantity(self) -> None:
        qty, unit, name = parse_quantity_and_unit("Honeycrisp Apples 3 lbs")
        assert qty == 3.0
        assert unit == Unit.LB
        assert name == "Honeycrisp Apples"

    def test_extracts_gallon_quantity(self) -> None:
        qty, unit, name = parse_quantity_and_unit("2% Organic Milk 1 gal")
        assert qty == 1.0
        assert unit == Unit.GALLON
        assert name == "2% Organic Milk"

    def test_extracts_pack_quantity(self) -> None:
        qty, unit, name = parse_quantity_and_unit("Soda 12pk")
        assert qty == 12.0
        assert unit == Unit.PACK
        assert name == "Soda"

    def test_no_quantity_returns_none(self) -> None:
        qty, unit, name = parse_quantity_and_unit("Spinach")
        assert qty is None
        assert unit is None
        assert name == "Spinach"


class TestNormalizeItemName:
    def test_normalizes_case_and_punctuation(self) -> None:
        assert normalize_item_name("  Eggs (Grade A)!  ") == "eggs grade a"
        assert normalize_item_name("Coffee w/ Chicory") == "coffee chicory"


class TestFuzzyMatching:
    def test_exact_match_score(self) -> None:
        score = calculate_match_score("milk", "milk")
        assert score == 1.0

    def test_partial_match_score(self) -> None:
        score = calculate_match_score("organic sourdough", "sourdough bread")
        assert score > 0.4

    def test_find_best_match_success(self) -> None:
        candidates = ["milk", "sourdough bread", "honeycrisp apples", "eggs"]
        best, score = find_best_match("organic sourdough", candidates)
        assert best == "sourdough bread"
        assert score >= 0.4

    def test_find_best_match_below_threshold(self) -> None:
        candidates = ["milk", "sourdough bread", "eggs"]
        best, score = find_best_match("power drill", candidates, threshold=0.5)
        assert best is None
        assert score < 0.5
