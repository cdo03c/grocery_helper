"""
anylist_client.py
-----------------
Async HTTP client for the AnyList API, supporting real endpoints and demo mode.

AnyList uses multipart/form-data authentication at ``/auth/token`` and
Protocol Buffers payloads on ``/data/`` endpoints with custom headers:
- ``X-AnyLeaf-API-Version: 3``
- ``X-AnyLeaf-Client-Identifier: <32-hex UUID>``
- ``Authorization: Bearer <access_token>``
"""

from __future__ import annotations

import base64
import json
import logging
import re
import struct
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from grocery_helper.config import settings
from grocery_helper.matcher import parse_quantity_and_unit
from grocery_helper.models import (
    GroceryItem,
    GroceryList,
    OptimizedCart,
    PriceComparison,
    Unit,
)

logger = logging.getLogger(__name__)

# Sample demo data used when demo_mode=True or when operating offline
DEMO_LISTS: list[GroceryList] = [
    GroceryList(
        id="list_001",
        name="Weekly Shop",
        items=[
            GroceryItem(
                id="item_001", name="Milk", quantity=1.0, unit=Unit.GALLON, category="Dairy"
            ),
            GroceryItem(
                id="item_002",
                name="Sourdough Bread",
                quantity=1.0,
                unit=Unit.EACH,
                category="Bakery",
            ),
            GroceryItem(
                id="item_003",
                name="Free Range Eggs",
                quantity=1.0,
                unit=Unit.EACH,
                category="Dairy",
            ),
            GroceryItem(
                id="item_004",
                name="Honeycrisp Apples",
                quantity=3.0,
                unit=Unit.LB,
                category="Produce",
            ),
            GroceryItem(
                id="item_005", name="Chicken Breast", quantity=2.0, unit=Unit.LB, category="Meat"
            ),
            GroceryItem(
                id="item_006", name="Spinach", quantity=1.0, unit=Unit.EACH, category="Produce"
            ),
        ],
    ),
    GroceryList(
        id="list_002",
        name="Party Supplies",
        items=[
            GroceryItem(
                id="item_007",
                name="Tortilla Chips",
                quantity=2.0,
                unit=Unit.PACK,
                category="Snacks",
            ),
            GroceryItem(
                id="item_008", name="Salsa", quantity=2.0, unit=Unit.EACH, category="Snacks"
            ),
            GroceryItem(
                id="item_009", name="Guacamole", quantity=1.0, unit=Unit.EACH, category="Deli"
            ),
            GroceryItem(
                id="item_010", name="Soda 12pk", quantity=2.0, unit=Unit.PACK, category="Beverages"
            ),
            GroceryItem(
                id="item_011", name="Limes", quantity=4.0, unit=Unit.EACH, category="Produce"
            ),
        ],
    ),
    GroceryList(
        id="list_003",
        name="Brunch & Coffee",
        items=[
            GroceryItem(
                id="item_012", name="Pancake Mix", quantity=1.0, unit=Unit.PACK, category="Pantry"
            ),
            GroceryItem(
                id="item_013", name="Maple Syrup", quantity=1.0, unit=Unit.EACH, category="Pantry"
            ),
            GroceryItem(id="item_014", name="Bacon", quantity=2.0, unit=Unit.PACK, category="Meat"),
            GroceryItem(
                id="item_015",
                name="Orange Juice",
                quantity=1.0,
                unit=Unit.EACH,
                category="Beverages",
            ),
            GroceryItem(
                id="item_016", name="Coffee Beans", quantity=1.0, unit=Unit.PACK, category="Coffee"
            ),
        ],
    ),
]


class AnyListAuthError(Exception):
    """Raised when authentication with AnyList fails."""


class AnyListAPIError(Exception):
    """Raised for unexpected API errors."""


# ---------------------------------------------------------------------------
# Minimal Protocol Buffers Wire Codec for AnyList API Messages
# ---------------------------------------------------------------------------


def _encode_varint(value: int) -> bytes:
    """Encode an unsigned integer as a base-128 varint."""
    v = value & 0xFFFFFFFFFFFFFFFF
    out = bytearray()
    while True:
        byte = v & 0x7F
        v >>= 7
        if v:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            break
    return bytes(out)


def _decode_varint(buf: bytes, pos: int) -> tuple[int, int]:
    """Decode a base-128 varint starting at *pos*, returning (value, next_pos)."""
    result = 0
    shift = 0
    while pos < len(buf):
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, pos
        shift += 7
    raise ValueError("Truncated protobuf varint")


def _encode_field_bytes(field_no: int, payload: bytes) -> bytes:
    """Encode a length-delimited protobuf field (wire type 2)."""
    tag = _encode_varint((field_no << 3) | 2)
    return tag + _encode_varint(len(payload)) + payload


def _encode_field_str(field_no: int, value: str | None) -> bytes:
    """Encode an optional UTF-8 string field when non-None."""
    if value is None:
        return b""
    return _encode_field_bytes(field_no, value.encode("utf-8"))


def _encode_field_varint(field_no: int, value: int | None) -> bytes:
    """Encode an optional integer/bool varint field when non-None."""
    if value is None:
        return b""
    return _encode_varint((field_no << 3) | 0) + _encode_varint(value)


def _encode_field_double(field_no: int, value: float | None) -> bytes:
    """Encode an optional 64-bit IEEE-754 double field (wire type 1)."""
    if value is None:
        return b""
    return _encode_varint((field_no << 3) | 1) + struct.pack("<d", float(value))


def _encode_raw_proto_field(field_no: int, wire_type: int, val: int | bytes) -> bytes:
    """Re-serialize a raw ``(field_no, wire_type, val)`` protobuf field unchanged."""
    tag = _encode_varint((field_no << 3) | wire_type)
    if wire_type == 0 and isinstance(val, int):
        return tag + _encode_varint(val)
    if wire_type == 2 and isinstance(val, bytes):
        return tag + _encode_varint(len(val)) + val
    if wire_type in (1, 5) and isinstance(val, bytes):
        return tag + val
    return b""


# PBListItem field numbers actively managed by `_encode_list_item_pb`.
_MANAGED_LIST_ITEM_FIELDS = frozenset({1, 3, 4, 5, 6, 11, 12, 13, 16, 18, 19, 20, 21})


@dataclass
class _ListCategoryMeta:
    """Category definition from an AnyList ``PBListCategory`` message."""

    identifier: str
    group_id: str
    name: str
    system_category: str = ""


@dataclass
class _ListMetadata:
    """Cached per-list metadata (stores and category groups) from AnyList."""

    list_id: str
    stores: dict[str, str] = field(default_factory=dict)  # store_id -> store_name
    default_category_group_id: str | None = None
    default_category_id: str | None = None
    categories: list[_ListCategoryMeta] = field(default_factory=list)


_STANDARD_CATEGORY_SLUGS: dict[str, str] = {
    "baby": "baby",
    "bakery": "bakery",
    "beverages": "beverages",
    "breakfast & cereal": "breakfast-and-cereal",
    "condiments & dressings": "condiments-oils-and-salad-dressings",
    "cooking & baking": "cooking-and-baking",
    "dairy": "dairy",
    "deli": "deli",
    "frozen foods": "frozen-foods",
    "grains, pasta & sides": "grains-pasta-and-side-dishes",
    "health & personal care": "health-and-personal-care",
    "household & cleaning": "household-and-cleaning",
    "meat": "meat",
    "pet supplies": "pet-supplies",
    "produce": "produce",
    "seafood": "seafood",
    "snacks": "snacks-cookies-and-candy",
    "soups & canned goods": "soups-and-canned-goods",
    "wine, beer & spirits": "wine-beer-spirits",
    "other": "other",
}

_CATEGORY_NAME_BY_SLUG: dict[str, str] = {
    slug: name.title() for name, slug in _STANDARD_CATEGORY_SLUGS.items()
}
_CATEGORY_NAME_BY_SLUG.update(
    {
        "breakfast-and-cereal": "Breakfast & Cereal",
        "condiments-oils-and-salad-dressings": "Condiments & Dressings",
        "cooking-and-baking": "Cooking & Baking",
        "frozen-foods": "Frozen Foods",
        "grains-pasta-and-side-dishes": "Grains, Pasta & Sides",
        "health-and-personal-care": "Health & Personal Care",
        "household-and-cleaning": "Household & Cleaning",
        "pet-supplies": "Pet Supplies",
        "snacks-cookies-and-candy": "Snacks",
        "soups-and-canned-goods": "Soups & Canned Goods",
        "wine-beer-spirits": "Wine, Beer & Spirits",
    }
)

_FROZEN_KEYWORDS = (
    "frozen",
    "ice cream",
    "gelato",
    "sorbet",
    "waffle",
    "pizza",
    "french fries",
    "tater",
    "pops",
    "sherbet",
)
_SEAFOOD_KEYWORDS = (
    "salmon",
    "tuna",
    "cod",
    "haddock",
    "tilapia",
    "shrimp",
    "crab",
    "lobster",
    "scallop",
    "fish",
    "seafood",
    "clam",
    "mussel",
    "oyster",
    "trout",
    "halibut",
)
_NAME_CATEGORY_KEYWORDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (_FROZEN_KEYWORDS, "Frozen Foods"),
    (_SEAFOOD_KEYWORDS, "Seafood"),
    (
        ("milk", "yogurt", "cheese", "butter", "cream", "egg", "kefir", "creamer"),
        "Dairy",
    ),
    (
        ("chicken", "beef", "pork", "steak", "turkey", "bacon", "sausage", "ham", "lamb"),
        "Meat",
    ),
    (
        (
            "apple",
            "banana",
            "berry",
            "strawberr",
            "blueberr",
            "raspberr",
            "avocado",
            "lettuce",
            "spinach",
            "kale",
            "tomato",
            "pepper",
            "onion",
            "garlic",
            "potato",
            "carrot",
            "broccoli",
            "cucumber",
            "lime",
            "lemon",
            "orange",
            "grape",
            "celery",
            "mushroom",
        ),
        "Produce",
    ),
    (("bread", "bagel", "muffin", "croissant", "tortilla", "bun", "roll", "loaf"), "Bakery"),
    (("hummus", "guacamole", "salami", "rotisserie"), "Deli"),
    (("coffee", "tea", "juice", "soda", "sparkling", "kombucha", "water", "latte"), "Beverages"),
    (("chip", "cracker", "cookie", "popcorn", "pretzel", "candy", "chocolate", "snack"), "Snacks"),
    (("cereal", "oatmeal", "granola", "pancake", "syrup"), "Breakfast & Cereal"),
    (("pasta", "spaghetti", "rice", "quinoa", "couscous", "noodle"), "Grains, Pasta & Sides"),
    (("soup", "broth", "canned", "beans", "chickpea", "lentil"), "Soups & Canned Goods"),
    (
        ("ketchup", "mustard", "mayo", "olive oil", "vinegar", "dressing", "salsa", "sauce"),
        "Condiments & Dressings",
    ),
    (("flour", "sugar", "baking", "vanilla", "yeast", "honey", "spice"), "Cooking & Baking"),
    (
        ("detergent", "soap", "paper towel", "trash bag", "cleaner", "sponge"),
        "Household & Cleaning",
    ),
    (("shampoo", "toothpaste", "deodorant", "vitamin", "lotion"), "Health & Personal Care"),
    (("beer", "wine", "vodka", "whiskey", "bourbon", "tequila"), "Wine, Beer & Spirits"),
    (("diaper", "baby"), "Baby"),
    (("dog food", "cat food", "pet", "litter"), "Pet Supplies"),
)


def _normalize_store_key(name: str) -> str:
    """Normalize a store name for apostrophe- and case-insensitive matching."""
    return re.sub(r"[\s'’`.-]+", " ", name.strip().lower()).strip()


def _infer_category_from_name(item_name: str) -> str | None:
    """Infer an AnyList category name from common grocery item keywords."""
    lower = item_name.strip().lower()
    if not lower:
        return None
    for keywords, category_name in _NAME_CATEGORY_KEYWORDS:
        if any(kw in lower for kw in keywords):
            return category_name
    return None


def normalize_category_name(category: str | None, item_name: str = "") -> str | None:
    """Normalize flyer/department category names to canonical AnyList category names."""
    raw = (category or "").strip()
    inferred = _infer_category_from_name(item_name)
    if not raw:
        return inferred

    lower = raw.lower()
    if lower in _STANDARD_CATEGORY_SLUGS:
        return _CATEGORY_NAME_BY_SLUG[_STANDARD_CATEGORY_SLUGS[lower]]
    if lower in _CATEGORY_NAME_BY_SLUG:
        return _CATEGORY_NAME_BY_SLUG[lower]

    item_lower = item_name.strip().lower()
    if "dairy" in lower and "frozen" in lower:
        return "Frozen Foods" if any(kw in item_lower for kw in _FROZEN_KEYWORDS) else "Dairy"
    if "frozen" in lower:
        return "Frozen Foods"
    if "dairy" in lower:
        return "Dairy"
    if "meat" in lower and "seafood" in lower:
        return "Seafood" if any(kw in item_lower for kw in _SEAFOOD_KEYWORDS) else "Meat"
    if "seafood" in lower or "fish" in lower:
        return "Seafood"
    if "meat" in lower or "poultry" in lower:
        return "Meat"
    if "produce" in lower or "fruit" in lower or "vegetable" in lower:
        return "Produce"
    if "bakery" in lower or "bread" in lower:
        return "Bakery"
    if "deli" in lower or "prepared" in lower:
        return "Deli"
    if "coffee" in lower or "beverage" in lower or "drink" in lower:
        return "Beverages"
    if "snack" in lower or "candy" in lower or "cookie" in lower:
        return "Snacks"
    if "breakfast" in lower or "cereal" in lower:
        return "Breakfast & Cereal"
    if "condiment" in lower or "dressing" in lower:
        return "Condiments & Dressings"
    if "grain" in lower or "pasta" in lower:
        return "Grains, Pasta & Sides"
    if "soup" in lower or "canned" in lower:
        return "Soups & Canned Goods"
    if "baking" in lower or "cooking" in lower or "spice" in lower:
        return "Cooking & Baking"
    if "pantry" in lower or "grocery" in lower or "dry good" in lower or "center store" in lower:
        return inferred or "Cooking & Baking"
    if "household" in lower or "cleaning" in lower or "paper" in lower:
        return "Household & Cleaning"
    if "health" in lower or "personal" in lower or "beauty" in lower or "pharmacy" in lower:
        return "Health & Personal Care"
    if "wine" in lower or "beer" in lower or "spirit" in lower or "alcohol" in lower:
        return "Wine, Beer & Spirits"
    if "baby" in lower:
        return "Baby"
    if "pet" in lower:
        return "Pet Supplies"
    return inferred or raw


def _format_raw_quantity(quantity: float, unit: Unit) -> str:
    """Format quantity + unit for AnyList's ``rawQuantity`` / ``deprecatedQuantity`` strings."""
    if quantity == 1.0 and unit == Unit.EACH:
        return ""
    qty_str = f"{quantity:g}"
    if unit != Unit.EACH:
        return f"{qty_str} {unit.value}"
    return qty_str


def _resolve_category(
    item: GroceryItem,
    list_meta: _ListMetadata | None,
) -> tuple[str | None, str | None, tuple[str, str] | None]:
    """
    Resolve AnyList ``(category, categoryMatchId, (categoryGroupId, categoryId))``
    for *item* against *list_meta*.
    """
    canonical = normalize_category_name(item.category, item.name)
    raw_cat = (item.category or "").strip()

    if list_meta is not None and list_meta.categories:
        # 1. Exact match against explicit list category (handles custom categories like "Organic").
        for candidate in (raw_cat, canonical or ""):
            if not candidate:
                continue
            cand_lower = candidate.lower()
            for cat in list_meta.categories:
                if cat.name.strip().lower() == cand_lower or (
                    cat.system_category and cat.system_category.lower() == cand_lower
                ):
                    slug = cat.system_category or re.sub(r"[^a-z0-9]+", "-", cand_lower).strip("-")
                    return (
                        cat.system_category or "other",
                        slug or "other",
                        (cat.group_id, cat.identifier),
                    )

        # 2. Match canonical category slug against list categories.
        if canonical:
            target_slug = _STANDARD_CATEGORY_SLUGS.get(canonical.lower())
            if target_slug:
                for cat in list_meta.categories:
                    if cat.system_category == target_slug:
                        return target_slug, target_slug, (cat.group_id, cat.identifier)

        # 3. Fall back to list's "Other" / default category when present.
        for cat in list_meta.categories:
            if cat.system_category == "other" or cat.identifier == list_meta.default_category_id:
                return "other", "other", (cat.group_id, cat.identifier)

    # No list metadata available (e.g. brand-new list or offline mock) — send clean slugs.
    chosen = canonical or raw_cat
    if not chosen:
        return None, None, None
    slug = _STANDARD_CATEGORY_SLUGS.get(
        chosen.lower(), re.sub(r"[^a-z0-9]+", "-", chosen.lower()).strip("-") or "other"
    )
    return slug, slug, None


def _iter_proto_fields(buf: bytes) -> Iterator[tuple[int, int, int | bytes]]:
    """Yield ``(field_no, wire_type, value)`` tuples from a serialized protobuf buffer."""
    pos = 0
    end = len(buf)
    while pos < end:
        key, pos = _decode_varint(buf, pos)
        field_no = key >> 3
        wire_type = key & 0x07
        if wire_type == 0:
            val, pos = _decode_varint(buf, pos)
            yield field_no, wire_type, val
        elif wire_type == 1:
            val_bytes = buf[pos : pos + 8]
            pos += 8
            yield field_no, wire_type, val_bytes
        elif wire_type == 2:
            length, pos = _decode_varint(buf, pos)
            val_bytes = buf[pos : pos + length]
            pos += length
            yield field_no, wire_type, val_bytes
        elif wire_type == 5:
            val_bytes = buf[pos : pos + 4]
            pos += 4
            yield field_no, wire_type, val_bytes
        else:
            raise ValueError(f"Unsupported protobuf wire type: {wire_type}")


def _decode_jwt_user_id(token: str) -> str | None:
    """Extract ``sub`` / ``user_id`` from a JWT access token without verifying signature."""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    payload_b64 = parts[1]
    padding = "=" * (-len(payload_b64) % 4)
    try:
        decoded = base64.urlsafe_b64decode(payload_b64 + padding)
        claims = json.loads(decoded.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    uid = claims.get("sub") or claims.get("user_id") or claims.get("userId")
    return str(uid) if uid else None


def _decode_item_quantity(buf: bytes) -> tuple[float, Unit]:
    """Decode ``PBItemQuantity`` into ``(quantity, Unit)``."""
    amount_str = ""
    unit_str = ""
    raw_str = ""
    for field_no, wire_type, val in _iter_proto_fields(buf):
        if wire_type != 2 or not isinstance(val, bytes):
            continue
        text = val.decode("utf-8", errors="replace").strip()
        if field_no == 1:
            amount_str = text
        elif field_no == 2:
            unit_str = text
        elif field_no == 3:
            raw_str = text

    combined = f"{amount_str} {unit_str}".strip() or raw_str
    if combined:
        parsed_qty, parsed_unit, _ = parse_quantity_and_unit(combined)
        qty = 1.0
        if parsed_qty is not None:
            qty = max(0.0, parsed_qty)
        elif amount_str:
            try:
                qty = max(0.0, float(amount_str))
            except ValueError:
                qty = 1.0
        if parsed_unit is not None:
            return qty, parsed_unit
        if unit_str:
            try:
                return qty, Unit(unit_str.lower())
            except ValueError:
                pass
        return qty, Unit.EACH

    return 1.0, Unit.EACH


def _decode_item_price_pb(buf: bytes) -> tuple[Decimal | None, str | None, str | None]:
    """Decode ``PBItemPrice`` into ``(amount, details, store_id)``."""
    amount: Decimal | None = None
    details: str | None = None
    store_id: str | None = None
    for field_no, wire_type, val in _iter_proto_fields(buf):
        if field_no == 2 and wire_type == 1 and isinstance(val, bytes) and len(val) == 8:
            raw_amt = struct.unpack("<d", val)[0]
            try:
                amount = Decimal(str(raw_amt)).quantize(Decimal("0.01"))
            except (InvalidOperation, ValueError):
                amount = None
        elif wire_type == 2 and isinstance(val, bytes):
            text = val.decode("utf-8", errors="replace").strip() or None
            if field_no == 3:
                details = text
            elif field_no == 4:
                store_id = text
    return amount, details, store_id


def _decode_category_assignment(buf: bytes) -> tuple[str | None, str | None]:
    """Decode ``PBListItemCategoryAssignment`` into ``(group_id, category_id)``."""
    group_id: str | None = None
    category_id: str | None = None
    for field_no, wire_type, val in _iter_proto_fields(buf):
        if wire_type == 2 and isinstance(val, bytes):
            text = val.decode("utf-8", errors="replace") or None
            if field_no == 2:
                group_id = text
            elif field_no == 3:
                category_id = text
    return group_id, category_id


def _decode_list_item_full(
    buf: bytes,
    *,
    list_meta: _ListMetadata | None = None,
) -> tuple[GroceryItem, list[tuple[int, int, int | bytes]]]:
    """Decode a protobuf ``ListItem`` plus any unmanaged pass-through fields."""
    item_id = ""
    name = "Unknown Item"
    details: str | None = None
    checked = False
    category: str | None = None
    category_match_id: str | None = None
    deprecated_qty = ""
    qty_pb_bytes: bytes | None = None
    store_ids: list[str] = []
    parsed_prices: list[tuple[Decimal | None, str | None, str | None]] = []
    assignments: list[tuple[str | None, str | None]] = []
    preserved: list[tuple[int, int, int | bytes]] = []

    for field_no, wire_type, val in _iter_proto_fields(buf):
        if field_no not in _MANAGED_LIST_ITEM_FIELDS:
            preserved.append((field_no, wire_type, val))
        if wire_type == 2 and isinstance(val, bytes):
            if field_no == 1:
                item_id = val.decode("utf-8", errors="replace")
            elif field_no == 4:
                name = val.decode("utf-8", errors="replace") or "Unknown Item"
            elif field_no == 5:
                details = val.decode("utf-8", errors="replace") or None
            elif field_no == 11:
                category = val.decode("utf-8", errors="replace") or None
            elif field_no == 13:
                category_match_id = val.decode("utf-8", errors="replace") or None
            elif field_no == 16:
                sid = val.decode("utf-8", errors="replace")
                if sid:
                    store_ids.append(sid)
            elif field_no == 18:
                deprecated_qty = val.decode("utf-8", errors="replace")
            elif field_no == 19:
                parsed_prices.append(_decode_item_price_pb(val))
            elif field_no == 20:
                assignments.append(_decode_category_assignment(val))
            elif field_no == 21:
                qty_pb_bytes = val
        elif wire_type == 0 and isinstance(val, int) and field_no == 6:
            checked = bool(val)

    qty = 1.0
    unit = Unit.EACH
    if qty_pb_bytes is not None:
        qty, unit = _decode_item_quantity(qty_pb_bytes)
    elif deprecated_qty:
        parsed_qty, parsed_unit, _ = parse_quantity_and_unit(deprecated_qty)
        if parsed_qty is not None:
            qty = max(0.0, parsed_qty)
        else:
            try:
                qty = max(0.0, float(deprecated_qty))
            except ValueError:
                qty = 1.0
        if parsed_unit is not None:
            unit = parsed_unit

    resolved_category: str | None = None
    if list_meta is not None:
        assigned_ids = {cid for _, cid in assignments if cid}
        for cat in list_meta.categories:
            if cat.identifier in assigned_ids:
                resolved_category = cat.name
                break
        if resolved_category is None and category_match_id:
            for cat in list_meta.categories:
                if cat.system_category == category_match_id:
                    resolved_category = cat.name
                    break
    if resolved_category is None:
        for slug_cand in (category_match_id, category):
            if slug_cand and slug_cand in _CATEGORY_NAME_BY_SLUG:
                resolved_category = _CATEGORY_NAME_BY_SLUG[slug_cand]
                break
    if resolved_category is None:
        resolved_category = category or category_match_id

    store_name: str | None = None
    primary_store_id = store_ids[0] if store_ids else None
    if list_meta is not None and primary_store_id:
        store_name = list_meta.stores.get(primary_store_id)

    price: Decimal | None = None
    price_details: str | None = None
    if parsed_prices:
        chosen = next(
            (p for p in parsed_prices if primary_store_id and p[2] == primary_store_id),
            parsed_prices[0],
        )
        price, price_details, price_store_id = chosen
        if store_name is None and list_meta is not None and price_store_id:
            store_name = list_meta.stores.get(price_store_id)

    item = GroceryItem(
        id=item_id or uuid.uuid4().hex,
        name=name,
        quantity=qty,
        unit=unit,
        category=resolved_category,
        notes=details,
        price=price,
        price_details=price_details,
        store=store_name,
        checked=checked,
    )
    return item, preserved


def _decode_list_item(buf: bytes) -> GroceryItem:
    """Decode a protobuf ``ListItem`` message."""
    item, _ = _decode_list_item_full(buf)
    return item


def _decode_store_pb(buf: bytes) -> tuple[str, str]:
    """Decode ``PBStore`` into ``(store_id, store_name)``."""
    store_id = ""
    store_name = ""
    for field_no, wire_type, val in _iter_proto_fields(buf):
        if wire_type == 2 and isinstance(val, bytes):
            text = val.decode("utf-8", errors="replace")
            if field_no == 1:
                store_id = text
            elif field_no == 4:
                store_name = text
    return store_id, store_name


def _decode_category_group_pb(
    buf: bytes,
) -> tuple[str, str | None, list[_ListCategoryMeta]]:
    """Decode ``PBListCategoryGroup`` into ``(group_id, default_category_id, categories)``."""
    group_id = ""
    default_category_id: str | None = None
    categories: list[_ListCategoryMeta] = []

    for field_no, wire_type, val in _iter_proto_fields(buf):
        if wire_type != 2 or not isinstance(val, bytes):
            continue
        if field_no == 1:
            group_id = val.decode("utf-8", errors="replace")
        elif field_no == 8:
            default_category_id = val.decode("utf-8", errors="replace") or None
        elif field_no == 5:
            cat_id = ""
            cat_group_id = ""
            cat_name = ""
            sys_cat = ""
            for cf, cw, cv in _iter_proto_fields(val):
                if cw == 2 and isinstance(cv, bytes):
                    ctext = cv.decode("utf-8", errors="replace")
                    if cf == 1:
                        cat_id = ctext
                    elif cf == 3:
                        cat_group_id = ctext
                    elif cf == 5:
                        cat_name = ctext
                    elif cf == 7:
                        sys_cat = ctext
            if cat_id:
                categories.append(
                    _ListCategoryMeta(
                        identifier=cat_id,
                        group_id=cat_group_id or group_id,
                        name=cat_name,
                        system_category=sys_cat,
                    )
                )
    return group_id, default_category_id, categories


def _decode_list_response_pb(buf: bytes) -> _ListMetadata | None:
    """Decode a ``PBListResponse`` message into ``_ListMetadata``."""
    list_id = ""
    stores: dict[str, str] = {}
    default_group_id: str | None = None
    default_category_id: str | None = None
    categories: list[_ListCategoryMeta] = []

    for field_no, wire_type, val in _iter_proto_fields(buf):
        if wire_type != 2 or not isinstance(val, bytes):
            continue
        if field_no == 1:
            list_id = val.decode("utf-8", errors="replace")
        elif field_no == 9:
            sid, sname = _decode_store_pb(val)
            if sid and sname:
                stores[sid] = sname
        elif field_no == 7:
            for gf, gw, gv in _iter_proto_fields(val):
                if gf == 1 and gw == 2 and isinstance(gv, bytes):
                    gid, def_cid, grp_cats = _decode_category_group_pb(gv)
                    if gid and default_group_id is None:
                        default_group_id = gid
                    if def_cid and default_category_id is None:
                        default_category_id = def_cid
                    for c in grp_cats:
                        if not c.group_id and gid:
                            c.group_id = gid
                        categories.append(c)

    if not list_id:
        return None
    return _ListMetadata(
        list_id=list_id,
        stores=stores,
        default_category_group_id=default_group_id,
        default_category_id=default_category_id,
        categories=categories,
    )


def _decode_shopping_list_full(
    buf: bytes,
    *,
    meta_by_list_id: dict[str, _ListMetadata] | None = None,
) -> tuple[GroceryList, dict[tuple[str, str], list[tuple[int, int, int | bytes]]]]:
    """Decode a protobuf ``ShoppingList`` message using list metadata when available."""
    list_id = ""
    name = "Unnamed List"
    raw_items: list[bytes] = []

    for field_no, wire_type, val in _iter_proto_fields(buf):
        if wire_type != 2 or not isinstance(val, bytes):
            continue
        if field_no == 1:
            list_id = val.decode("utf-8", errors="replace")
        elif field_no == 3:
            name = val.decode("utf-8", errors="replace") or "Unnamed List"
        elif field_no == 4:
            raw_items.append(val)

    resolved_list_id = list_id or uuid.uuid4().hex
    list_meta = meta_by_list_id.get(resolved_list_id) if meta_by_list_id else None
    items: list[GroceryItem] = []
    preserved_map: dict[tuple[str, str], list[tuple[int, int, int | bytes]]] = {}
    for item_bytes in raw_items:
        item, preserved = _decode_list_item_full(item_bytes, list_meta=list_meta)
        items.append(item)
        if preserved:
            preserved_map[(resolved_list_id, item.id)] = preserved

    return (
        GroceryList(
            id=resolved_list_id,
            name=name,
            items=items,
        ),
        preserved_map,
    )


def _decode_shopping_list(buf: bytes) -> GroceryList:
    """Decode a protobuf ``ShoppingList`` message."""
    lst, _ = _decode_shopping_list_full(buf)
    return lst


def _decode_user_data_lists_with_meta(
    buf: bytes,
) -> tuple[
    list[GroceryList],
    dict[str, _ListMetadata],
    dict[tuple[str, str], list[tuple[int, int, int | bytes]]],
]:
    """Decode ``PBUserDataResponse`` bytes into lists, list metadata, and preserved item fields."""
    raw_lists: list[bytes] = []
    meta_by_list_id: dict[str, _ListMetadata] = {}

    for field_no, wire_type, val in _iter_proto_fields(buf):
        if field_no == 1 and wire_type == 2 and isinstance(val, bytes):
            # ShoppingListsResponse: 1 = newLists, 2 = modifiedLists, 6 = listResponses
            for sub_no, sub_wire, sub_val in _iter_proto_fields(val):
                if sub_wire != 2 or not isinstance(sub_val, bytes):
                    continue
                if sub_no in (1, 2):
                    raw_lists.append(sub_val)
                elif sub_no == 6:
                    meta = _decode_list_response_pb(sub_val)
                    if meta is not None:
                        meta_by_list_id[meta.list_id] = meta

    lists: list[GroceryList] = []
    preserved_all: dict[tuple[str, str], list[tuple[int, int, int | bytes]]] = {}
    for raw_list in raw_lists:
        lst, preserved_map = _decode_shopping_list_full(raw_list, meta_by_list_id=meta_by_list_id)
        lists.append(lst)
        preserved_all.update(preserved_map)
    return lists, meta_by_list_id, preserved_all


def _decode_user_data_lists(buf: bytes) -> list[GroceryList]:
    """Decode ``PBUserDataResponse`` bytes and extract all shopping lists."""
    lists, _, _ = _decode_user_data_lists_with_meta(buf)
    return lists


def _encode_operation_metadata(
    handler_id: str,
    user_id: str | None,
    operation_class: int = 0,
) -> bytes:
    """Encode ``PBOperationMetadata``."""
    return (
        _encode_field_str(1, uuid.uuid4().hex)
        + _encode_field_str(2, handler_id)
        + _encode_field_str(3, user_id)
        + _encode_field_varint(4, operation_class)
    )


def _encode_item_price_pb(
    price: Decimal,
    details: str | None = None,
    store_id: str | None = None,
    date_str: str | None = None,
) -> bytes:
    """Encode a protobuf ``PBItemPrice`` message."""
    return (
        _encode_field_double(2, float(price))
        + _encode_field_str(3, details if details is not None else "")
        + _encode_field_str(4, store_id)
        + _encode_field_str(5, date_str or datetime.now(UTC).date().isoformat())
    )


def _encode_store_pb(
    store_id: str,
    list_id: str,
    name: str,
    sort_index: int = 0,
) -> bytes:
    """Encode a protobuf ``PBStore`` message."""
    return (
        _encode_field_str(1, store_id)
        + _encode_field_str(3, list_id)
        + _encode_field_str(4, name)
        + _encode_field_varint(5, sort_index)
    )


def _encode_list_item_pb(
    list_id: str,
    item: GroceryItem,
    user_id: str | None,
    *,
    store_id: str | None = None,
    list_meta: _ListMetadata | None = None,
    preserved_fields: list[tuple[int, int, int | bytes]] | None = None,
) -> bytes:
    """Encode a protobuf ``ListItem`` message including category, quantity, store, and price."""
    raw_qty = _format_raw_quantity(item.quantity, item.unit)
    qty_pb_bytes = b""
    deprecated_qty_bytes = b""
    if raw_qty:
        qty_pb = _encode_field_str(1, f"{item.quantity:g}")
        if item.unit != Unit.EACH:
            qty_pb += _encode_field_str(2, item.unit.value)
        qty_pb += _encode_field_str(3, raw_qty)
        qty_pb_bytes = _encode_field_bytes(21, qty_pb)
        deprecated_qty_bytes = _encode_field_str(18, raw_qty)

    cat_slug, cat_match_id, assignment = _resolve_category(item, list_meta)
    assignment_bytes = b""
    if assignment is not None:
        group_id, category_id = assignment
        assignment_msg = (
            _encode_field_str(1, uuid.uuid4().hex)
            + _encode_field_str(2, group_id)
            + _encode_field_str(3, category_id)
        )
        assignment_bytes = _encode_field_bytes(20, assignment_msg)

    store_bytes = _encode_field_str(16, store_id) if store_id else b""

    price_bytes = b""
    if item.price is not None:
        details = item.price_details
        if details is None and item.unit != Unit.EACH:
            details = f"/{item.unit.value}"
        price_bytes = _encode_field_bytes(
            19,
            _encode_item_price_pb(item.price, details=details, store_id=store_id),
        )

    parts = [
        _encode_field_str(1, item.id),
        _encode_field_str(3, list_id),
        _encode_field_str(4, item.name),
        _encode_field_str(5, item.notes),
        _encode_field_varint(6, 1 if item.checked else 0),
        _encode_field_str(11, cat_slug),
        _encode_field_str(12, user_id),
        _encode_field_str(13, cat_match_id),
        store_bytes,
        deprecated_qty_bytes,
        price_bytes,
        assignment_bytes,
        qty_pb_bytes,
    ]
    if preserved_fields:
        for fn, wt, val in preserved_fields:
            if fn not in _MANAGED_LIST_ITEM_FIELDS:
                parts.append(_encode_raw_proto_field(fn, wt, val))
    return b"".join(parts)


def _encode_shopping_list_pb(list_id: str, name: str, user_id: str | None) -> bytes:
    """Encode a protobuf ``ShoppingList`` message for list creation."""
    return (
        _encode_field_str(1, list_id)
        + _encode_field_str(3, name)
        + _encode_field_str(5, user_id)
        + _encode_field_varint(16, 1)
        + _encode_field_varint(17, 0)
        + _encode_field_varint(18, 0)
    )


def _encode_list_operation(
    *,
    handler_id: str,
    user_id: str | None,
    list_id: str,
    list_item_id: str | None = None,
    updated_value: str | None = None,
    list_item_bytes: bytes | None = None,
    shopping_list_bytes: bytes | None = None,
    updated_store_bytes: bytes | None = None,
    item_price_bytes: bytes | None = None,
    operation_class: int = 0,
) -> bytes:
    """Encode a ``PBListOperation`` message."""
    parts = [
        _encode_field_bytes(
            1,
            _encode_operation_metadata(handler_id, user_id, operation_class=operation_class),
        ),
        _encode_field_str(2, list_id),
        _encode_field_str(3, list_item_id),
        _encode_field_str(4, updated_value),
    ]
    if list_item_bytes is not None:
        parts.append(_encode_field_bytes(6, list_item_bytes))
    if shopping_list_bytes is not None:
        parts.append(_encode_field_bytes(7, shopping_list_bytes))
    if updated_store_bytes is not None:
        parts.append(_encode_field_bytes(10, updated_store_bytes))
    if item_price_bytes is not None:
        parts.append(_encode_field_bytes(16, item_price_bytes))
    return b"".join(parts)


def _encode_operation_list(operations: list[bytes]) -> bytes:
    """Wrap encoded ``PBListOperation`` messages in ``PBListOperationList``."""
    return b"".join(_encode_field_bytes(1, op) for op in operations)


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class AnyListClient:
    """
    Async client for interacting with AnyList.

    Supports both live API interactions and an offline `demo_mode` for
    testing and interactive development without requiring account credentials.
    """

    def __init__(
        self,
        email: str | None = None,
        password: str | None = None,
        base_url: str | None = None,
        demo_mode: bool = False,
    ) -> None:
        self._email = email if email is not None else settings.anylist_email
        self._password = password if password is not None else settings.anylist_password
        self._base_url = (base_url or settings.anylist_api_base_url).rstrip("/")
        self._demo_mode = demo_mode
        self._session_token: str | None = None
        self._refresh_token: str | None = None
        self._user_id: str | None = None
        self._client_id: str = uuid.uuid4().hex
        self._http: httpx.AsyncClient | None = None
        self._list_metadata: dict[str, _ListMetadata] = {}
        self._preserved_item_fields: dict[tuple[str, str], list[tuple[int, int, int | bytes]]] = {}
        self._demo_store: dict[str, GroceryList] = {
            lst.id: lst.model_copy(deep=True) for lst in DEMO_LISTS
        }

    @property
    def is_authenticated(self) -> bool:
        """Whether the client currently has a valid session token."""
        return self._session_token is not None

    @property
    def is_demo(self) -> bool:
        """Whether the client is running in mock/demo mode."""
        return self._demo_mode

    # ── Context manager ───────────────────────────────────────────────────────

    async def __aenter__(self) -> AnyListClient:
        headers = {
            "X-AnyLeaf-API-Version": "3",
            "X-AnyLeaf-Client-Identifier": self._client_id,
            "User-Agent": "AnyList/5.32 (iPhone; iOS 16.0; Scale/3.00)",
        }
        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            headers=headers,
            timeout=15.0,
        )
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._http:
            await self._http.aclose()
            self._http = None

    # ── Auth ──────────────────────────────────────────────────────────────────

    async def authenticate(self) -> None:
        """
        Authenticate with AnyList and store the session token.
        In demo mode, authenticates instantly.
        """
        if self._demo_mode:
            self._session_token = "demo_session_token"
            self._user_id = "demo_user"
            logger.info("Authenticated AnyListClient in demo mode")
            return

        if not self._http:
            raise RuntimeError("Client not started. Use async with AnyListClient().")

        email = self._email.strip()
        if not email or not self._password:
            raise AnyListAuthError(
                "AnyList credentials missing. Provide email and password, or run in demo mode."
            )

        logger.info("Authenticating with AnyList as %s", email)

        try:
            response = await self._http.post(
                "/auth/token",
                files={"email": (None, email), "password": (None, self._password)},
            )
        except httpx.RequestError as exc:
            raise AnyListAPIError(f"Network error connecting to AnyList: {exc}") from exc

        if response.status_code == 401:
            raise AnyListAuthError("Invalid AnyList email or password.")
        if response.status_code != 200:
            raise AnyListAPIError(
                f"AnyList login failed with status {response.status_code}: {response.text}"
            )

        data = response.json()
        token = data.get("access_token") or data.get("token")
        if not token:
            token = response.cookies.get("anyleaf_token") or "session_active"

        self._session_token = str(token)
        refresh = data.get("refresh_token")
        self._refresh_token = str(refresh) if refresh else None
        uid = data.get("user_id") or data.get("userId") or _decode_jwt_user_id(self._session_token)
        self._user_id = str(uid) if uid else None
        self._http.headers["Authorization"] = f"Bearer {self._session_token}"

    async def _try_refresh_token(self) -> bool:
        """Attempt to refresh the AnyList access token using ``/auth/token/refresh``."""
        if not self._http or not self._refresh_token:
            return False
        try:
            res = await self._http.post(
                "/auth/token/refresh",
                files={"refresh_token": (None, self._refresh_token)},
            )
        except httpx.RequestError:
            return False

        if res.status_code != 200:
            return False

        try:
            data = res.json()
        except ValueError:
            return False

        token = data.get("access_token") or data.get("token")
        if not token:
            return False

        self._session_token = str(token)
        new_refresh = data.get("refresh_token")
        if new_refresh:
            self._refresh_token = str(new_refresh)
        self._http.headers["Authorization"] = f"Bearer {self._session_token}"
        return True

    # ── Lists ─────────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_json_lists(data: dict[str, Any]) -> list[GroceryList]:
        """Parse JSON shopping-list payloads (used if an intermediary/mock returns JSON)."""
        raw_lists = data.get("shoppingLists") or data.get("lists") or []
        lists: list[GroceryList] = []
        for raw in raw_lists:
            list_id = str(raw.get("identifier") or raw.get("id") or uuid.uuid4().hex)
            name = str(raw.get("name") or "Unnamed List")
            items: list[GroceryItem] = []

            for raw_item in raw.get("items", []):
                item_id = str(raw_item.get("identifier") or raw_item.get("id") or uuid.uuid4().hex)
                item_name = str(raw_item.get("name") or "Unknown Item")
                raw_qty = raw_item.get("quantity")
                try:
                    qty = max(0.0, float(raw_qty)) if raw_qty is not None else 1.0
                except (TypeError, ValueError):
                    qty = 1.0
                raw_unit = raw_item.get("unit")
                try:
                    unit = Unit(raw_unit) if raw_unit else Unit.EACH
                except ValueError:
                    unit = Unit.EACH
                category = raw_item.get("category")
                notes = raw_item.get("details") or raw_item.get("notes")
                raw_price = raw_item.get("price")
                try:
                    price = (
                        Decimal(str(raw_price)).quantize(Decimal("0.01"))
                        if raw_price is not None
                        else None
                    )
                except (InvalidOperation, ValueError):
                    price = None
                price_details = raw_item.get("price_details") or raw_item.get("priceDetails")
                store = raw_item.get("store")
                checked = bool(raw_item.get("checked", False))

                items.append(
                    GroceryItem(
                        id=item_id,
                        name=item_name,
                        quantity=qty,
                        unit=unit,
                        category=category,
                        notes=notes,
                        price=price,
                        price_details=price_details,
                        store=store,
                        checked=checked,
                    )
                )

            lists.append(GroceryList(id=list_id, name=name, items=items))
        return lists

    async def get_lists(self) -> list[GroceryList]:
        """Fetch all grocery lists for the authenticated user."""
        self._require_auth()

        if self._demo_mode:
            return list(self._demo_store.values())

        response = await self._post("/data/user-data/get", action="fetch lists")
        content_type = (response.headers.get("content-type") or "").lower()
        if "json" in content_type:
            return self._parse_json_lists(response.json())
        lists, meta_map, preserved_map = _decode_user_data_lists_with_meta(response.content)
        self._list_metadata.update(meta_map)
        self._preserved_item_fields.update(preserved_map)
        return lists

    async def get_list(self, list_id: str) -> GroceryList:
        """Fetch a single grocery list by ID."""
        self._require_auth()

        if self._demo_mode:
            if list_id in self._demo_store:
                return self._demo_store[list_id]
            raise AnyListAPIError(f"List with ID '{list_id}' not found.")

        lists = await self.get_lists()
        for lst in lists:
            if lst.id == list_id:
                return lst
        raise AnyListAPIError(f"List with ID '{list_id}' not found.")

    async def _try_load_list_metadata(self, list_id: str, *, force: bool = False) -> None:
        """Best-effort load of stores and categories for *list_id*; propagates auth errors."""
        if self._demo_mode:
            return
        if not force and list_id in self._list_metadata:
            return
        try:
            await self.get_lists()
        except AnyListAPIError:
            # Allow callers and minimal mock tests to proceed without metadata.
            return

    async def ensure_store(self, list_id: str, store_name: str) -> str | None:
        """Return the AnyList store ID on *list_id* for *store_name*, creating it if needed."""
        clean_name = store_name.strip()
        if not clean_name:
            return None
        self._require_auth()
        await self._try_load_list_metadata(list_id)

        target_key = _normalize_store_key(clean_name)
        meta = self._list_metadata.get(list_id)
        if meta is not None:
            for sid, existing_name in meta.stores.items():
                if _normalize_store_key(existing_name) == target_key:
                    return sid

        if self._demo_mode:
            return clean_name

        store_id = uuid.uuid4().hex
        sort_idx = len(meta.stores) if meta is not None else 0
        store_pb = _encode_store_pb(store_id, list_id, clean_name, sort_index=sort_idx)
        op = _encode_list_operation(
            handler_id="new-store",
            user_id=self._user_id,
            list_id=list_id,
            updated_store_bytes=store_pb,
            operation_class=1,
        )
        await self._post_operations_v2(_encode_operation_list([op]), action="create store")
        if meta is None:
            meta = _ListMetadata(list_id=list_id)
            self._list_metadata[list_id] = meta
        meta.stores[store_id] = clean_name
        return store_id

    async def _encode_item_for_list(
        self,
        list_id: str,
        item: GroceryItem,
        *,
        preserve_existing: bool = False,
    ) -> bytes:
        """Load list metadata, ensure store exists if assigned, and encode ``PBListItem``."""
        await self._try_load_list_metadata(list_id)
        store_id: str | None = None
        if item.store:
            store_id = await self.ensure_store(list_id, item.store)
        list_meta = self._list_metadata.get(list_id)
        preserved = (
            self._preserved_item_fields.get((list_id, item.id)) if preserve_existing else None
        )
        return _encode_list_item_pb(
            list_id,
            item,
            self._user_id,
            store_id=store_id,
            list_meta=list_meta,
            preserved_fields=preserved,
        )

    async def add_item(self, list_id: str, item: GroceryItem) -> GroceryItem:
        """Add an item to an existing list."""
        self._require_auth()

        if self._demo_mode:
            lst = await self.get_list(list_id)
            lst.items.append(item)
            return item

        item_bytes = await self._encode_item_for_list(list_id, item, preserve_existing=False)
        op = _encode_list_operation(
            handler_id="add-shopping-list-item",
            user_id=self._user_id,
            list_id=list_id,
            list_item_id=item.id,
            list_item_bytes=item_bytes,
        )
        await self._post_operations(_encode_operation_list([op]), action="add item")
        return item

    async def remove_item(self, list_id: str, item_id: str) -> None:
        """Remove an item from a list."""
        self._require_auth()

        if self._demo_mode:
            lst = await self.get_list(list_id)
            lst.items = [i for i in lst.items if i.id != item_id]
            return

        stub_item = _encode_field_str(1, item_id) + _encode_field_str(3, list_id)
        op = _encode_list_operation(
            handler_id="remove-shopping-list-item",
            user_id=self._user_id,
            list_id=list_id,
            list_item_id=item_id,
            list_item_bytes=stub_item,
        )
        await self._post_operations(_encode_operation_list([op]), action="remove item")

    async def update_item(self, list_id: str, item: GroceryItem) -> GroceryItem:
        """Update an existing item on a list using a full ``update-list-item`` operation."""
        self._require_auth()

        if self._demo_mode:
            lst = await self.get_list(list_id)
            for idx, existing in enumerate(lst.items):
                if existing.id == item.id:
                    lst.items[idx] = item
                    return item
            raise AnyListAPIError(f"Item '{item.id}' not found on list '{list_id}'.")

        item_bytes = await self._encode_item_for_list(list_id, item, preserve_existing=True)
        op = _encode_list_operation(
            handler_id="update-list-item",
            user_id=self._user_id,
            list_id=list_id,
            list_item_id=item.id,
            list_item_bytes=item_bytes,
        )
        await self._post_operations(_encode_operation_list([op]), action="update item")
        return item

    async def create_list(self, name: str) -> GroceryList:
        """Create a new grocery list."""
        self._require_auth()

        if self._demo_mode:
            new_id = f"list_{uuid.uuid4().hex[:8]}"
            new_list = GroceryList(id=new_id, name=name, items=[])
            self._demo_store[new_id] = new_list
            return new_list

        list_id = uuid.uuid4().hex
        op = _encode_list_operation(
            handler_id="new-shopping-list",
            user_id=self._user_id,
            list_id=list_id,
            shopping_list_bytes=_encode_shopping_list_pb(list_id, name, self._user_id),
        )
        await self._post_operations(_encode_operation_list([op]), action="create list")
        return GroceryList(id=list_id, name=name, items=[])

    @staticmethod
    def _enrich_item_from_cart(comp: PriceComparison, store_name: str) -> GroceryItem:
        """Copy *comp.item* with assigned store, normalized price, and canonical category."""
        enriched = comp.item.model_copy()
        enriched.store = store_name
        sp = next((p for p in comp.prices if p.store_name == store_name), None)
        if sp is not None:
            enriched.price = comp.normalized_price(sp)
            if sp.deal_details:
                enriched.price_details = sp.deal_details
            elif enriched.unit != Unit.EACH:
                enriched.price_details = f"/{enriched.unit.value}"
        canonical_cat = normalize_category_name(enriched.category, enriched.name)
        if canonical_cat:
            enriched.category = canonical_cat
        return enriched

    async def sync_cart_to_list(
        self,
        cart: OptimizedCart,
        grocery_list: GroceryList,
    ) -> list[GroceryItem]:
        """
        Update items on an existing AnyList list with optimized store, price,
        category, and quantity subfields.
        """
        self._require_auth()
        await self._try_load_list_metadata(grocery_list.id)

        comp_by_id = {comp.item.id: comp for comp in cart.comparisons}
        updated_items: list[GroceryItem] = []

        for idx, existing in enumerate(list(grocery_list.items)):
            store_name = cart.store_assignments.get(existing.id)
            comp = comp_by_id.get(existing.id)
            if not store_name or comp is None:
                continue
            enriched = self._enrich_item_from_cart(comp, store_name)
            await self.update_item(grocery_list.id, enriched)
            grocery_list.items[idx] = enriched
            updated_items.append(enriched)

        return updated_items

    async def export_split_lists(
        self,
        cart: OptimizedCart,
        base_name: str = "Shop",
    ) -> list[GroceryList]:
        """
        Create store-specific grocery lists based on the optimized cart assignments.

        Each exported item includes store, price, resolved category, and quantity subfields.
        """
        self._require_auth()

        items_by_store: dict[str, list[GroceryItem]] = {}
        comp_lookup = {comp.item.id: comp for comp in cart.comparisons}

        for item_id, store_name in cart.store_assignments.items():
            comp = comp_lookup.get(item_id)
            if comp is not None:
                enriched = self._enrich_item_from_cart(comp, store_name)
                items_by_store.setdefault(store_name, []).append(enriched)

        created_lists: list[GroceryList] = []
        for store_name, items in items_by_store.items():
            list_name = f"{base_name} ({store_name})"
            new_list = await self.create_list(list_name)
            if not self._demo_mode:
                await self._try_load_list_metadata(new_list.id, force=True)
            for item in items:
                await self.add_item(new_list.id, item.model_copy())
            full_list = await self.get_list(new_list.id)
            created_lists.append(full_list)

        return created_lists

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _post_operations(self, ops_bytes: bytes, *, action: str) -> httpx.Response:
        """Send a protobuf ``PBListOperationList`` payload to ``/data/shopping-lists/update``."""
        # Omit filename in Content-Disposition so AnyList parses `operations` as a form field
        # rather than treating it as an uploaded file (which AnyList ignores with 200 empty body).
        return await self._post(
            "/data/shopping-lists/update",
            files={"operations": (None, ops_bytes)},
            action=action,
        )

    async def _post_operations_v2(self, ops_bytes: bytes, *, action: str) -> httpx.Response:
        """Send a protobuf ``PBListOperationList`` payload to ``/data/shopping-lists/update-v2``."""
        return await self._post(
            "/data/shopping-lists/update-v2",
            files={"operations": (None, ops_bytes)},
            action=action,
        )

    async def _post(
        self,
        path: str,
        *,
        files: dict[str, Any] | None = None,
        action: str,
    ) -> httpx.Response:
        """Send an authenticated POST request with token-refresh retry and error handling."""
        if not self._http:
            raise RuntimeError("Client not started.")

        try:
            response = (
                await self._http.post(path, files=files) if files else await self._http.post(path)
            )
            if response.status_code == 401 and await self._try_refresh_token():
                response = (
                    await self._http.post(path, files=files)
                    if files
                    else await self._http.post(path)
                )
        except httpx.RequestError as exc:
            raise AnyListAPIError(f"Network error during {action}: {exc}") from exc

        if response.status_code in (401, 403):
            raise AnyListAuthError("Session expired or unauthorised.")
        if response.status_code != 200:
            raise AnyListAPIError(f"Failed to {action}: {response.text}")

        return response

    def _require_auth(self) -> None:
        if not self._session_token:
            raise AnyListAuthError(
                "Not authenticated. Call authenticate() before making API requests."
            )
