"""
matcher.py
----------
Item name normalization and fuzzy matching utilities.

Helps reconcile free-form AnyList item names (e.g., "organic whole milk 1 gal", "2% milk",
"avacados") with specific grocer product titles and catalog items.
"""

from __future__ import annotations

import difflib
import re

from grocery_helper.models import Unit

# Common unit abbreviations mapped to Unit enum
UNIT_PATTERNS: dict[str, Unit] = {
    r"gal|gallon|gallons": Unit.GALLON,
    r"lb|lbs|pound|pounds": Unit.LB,
    r"oz|ounce|ounces": Unit.OZ,
    r"l|liter|litres|liters": Unit.LITER,
    r"pk|pack|packs|package|packages|box|boxes|ct|count": Unit.PACK,
    r"ea|each": Unit.EACH,
}


def parse_quantity_and_unit(raw_name: str) -> tuple[float | None, Unit | None, str]:
    """
    Extract embedded quantities and units from an item string.

    Example:
        "Honeycrisp Apples 3 lbs" -> (3.0, Unit.LB, "Honeycrisp Apples")
        "2% milk 1 gallon" -> (1.0, Unit.GALLON, "2% milk")
    """
    clean_name = raw_name.strip()
    detected_qty: float | None = None
    detected_unit: Unit | None = None

    # Match numeric quantity attached to or preceding a unit, e.g. "3 lbs" or "3.5 lb" or "12pk"
    for pattern, unit in UNIT_PATTERNS.items():
        qty_unit_pattern = rf"(\d+(?:\.\d+)?)\s*(?:{pattern})\b"
        match = re.search(qty_unit_pattern, clean_name, re.IGNORECASE)
        if match:
            try:
                detected_qty = float(match.group(1))
                detected_unit = unit
                # Strip the quantity + unit from the clean name
                clean_name = re.sub(qty_unit_pattern, "", clean_name, flags=re.IGNORECASE).strip()
                break
            except ValueError:
                pass

    # If no quantity was attached to unit, check for standalone unit
    if detected_unit is None:
        for pattern, unit in UNIT_PATTERNS.items():
            standalone_pattern = rf"\b(?:{pattern})\b"
            if re.search(standalone_pattern, clean_name, re.IGNORECASE):
                detected_unit = unit
                clean_name = re.sub(standalone_pattern, "", clean_name, flags=re.IGNORECASE).strip()
                break

    # Clean double spaces or leading/trailing dashes
    clean_name = re.sub(r"\s+", " ", clean_name).strip(" -:,")
    return detected_qty, detected_unit, clean_name or raw_name


def normalize_item_name(raw_name: str) -> str:
    """Normalize grocery item string by removing punctuation and lowercasing."""
    cleaned = raw_name.lower().strip()
    # Normalize common abbreviations like "w/" or "with"
    cleaned = re.sub(r"(?:\bwith\b|\bw/)", "", cleaned)
    cleaned = re.sub(r"[^\w\s%]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def calculate_match_score(query: str, candidate: str) -> float:
    """
    Calculate similarity score between 0.0 and 1.0 using token overlap and
    sequence matching.
    """
    q_norm = normalize_item_name(query)
    c_norm = normalize_item_name(candidate)

    if q_norm == c_norm:
        return 1.0

    # Token overlap bonus (e.g. "milk" in "organic whole milk")
    q_tokens = set(q_norm.split())
    c_tokens = set(c_norm.split())

    if not q_tokens or not c_tokens:
        return 0.0

    token_overlap = len(q_tokens & c_tokens) / max(len(q_tokens), len(c_tokens))

    # Difflib sequence similarity ratio
    seq_ratio = difflib.SequenceMatcher(None, q_norm, c_norm).ratio()

    # Blend token overlap and sequence ratio (giving slight preference to token overlap)
    return float(0.6 * token_overlap + 0.4 * seq_ratio)


def find_best_match(
    query: str,
    candidates: list[str],
    threshold: float = 0.35,
) -> tuple[str | None, float]:
    """
    Find candidate string with highest similarity score exceeding threshold.
    Returns (best_candidate, score) or (None, 0.0).
    """
    if not candidates:
        return None, 0.0

    best_cand: str | None = None
    best_score = 0.0

    for cand in candidates:
        score = calculate_match_score(query, cand)
        if score > best_score:
            best_score = score
            best_cand = cand

    if best_score >= threshold:
        return best_cand, best_score
    return None, 0.0
