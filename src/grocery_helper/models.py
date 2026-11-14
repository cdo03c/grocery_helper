"""
models.py
---------
Pydantic data models for groceries, stores, and pricing.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class _ReloadSafeModel(BaseModel):
    """Coerce instances created before a hot-module reload into the active model class."""

    @model_validator(mode="before")
    @classmethod
    def _coerce_reloaded_instance(cls, value: Any) -> Any:
        val_cls = type(value)
        if (
            not isinstance(value, cls)
            and val_cls.__module__ == cls.__module__
            and val_cls.__qualname__ == cls.__qualname__
            and hasattr(value, "model_dump")
        ):
            return value.model_dump(mode="json")
        return value


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class Unit(StrEnum):
    """Common measurement units for grocery items."""

    EACH = "each"
    LB = "lb"
    OZ = "oz"
    GALLON = "gallon"
    LITER = "liter"
    PACK = "pack"


_VOLUME_TO_LITERS: dict[Unit, Decimal] = {
    Unit.LITER: Decimal("1"),
    Unit.GALLON: Decimal("3.785411784"),
}

_WEIGHT_TO_OZ: dict[Unit, Decimal] = {
    Unit.OZ: Decimal("1"),
    Unit.LB: Decimal("16"),
}


def convert_unit_price(price: Decimal, from_unit: Unit, to_unit: Unit) -> Decimal | None:
    """
    Convert a unit price from *from_unit* to *to_unit* if both belong to the
    same measurement dimension (volume or weight). Returns None otherwise.
    """
    if from_unit == to_unit:
        return price

    for factors in (_VOLUME_TO_LITERS, _WEIGHT_TO_OZ):
        if from_unit in factors and to_unit in factors:
            converted = (price / factors[from_unit]) * factors[to_unit]
            return converted.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    return None


# ---------------------------------------------------------------------------
# Core models
# ---------------------------------------------------------------------------


class GroceryItem(_ReloadSafeModel):
    """A single item on a grocery list."""

    id: str = Field(description="Unique identifier (e.g., from AnyList)")
    name: str = Field(description="Display name of the item")
    quantity: float = Field(default=1.0, ge=0, description="How many units to purchase")
    unit: Unit = Field(default=Unit.EACH, description="Unit of measurement")
    category: str | None = Field(default=None, description="e.g., 'Produce', 'Dairy'")
    notes: str | None = Field(default=None, description="Free-text shopper notes")
    checked: bool = Field(default=False, description="Marked off in shopping session")


class GroceryList(_ReloadSafeModel):
    """A named grocery list containing multiple items."""

    id: str = Field(description="Unique list identifier")
    name: str = Field(description="List name (e.g., 'Weekly Shop')")
    items: list[GroceryItem] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class StorePrice(_ReloadSafeModel):
    """Price of a grocery item at a specific store."""

    item_name: str
    store_name: str
    store_id: str | None = None
    price: Decimal = Field(ge=Decimal("0"), description="Unit price in USD")
    unit: Unit = Field(default=Unit.EACH)
    sale_price: Decimal | None = Field(default=None, ge=Decimal("0"))
    is_live: bool = Field(
        default=False, description="Whether this price came from a live store or flyer feed"
    )
    matched_product_name: str | None = Field(
        default=None, description="Matched store product or flyer item title"
    )
    deal_details: str | None = Field(
        default=None, description="Promotional note (e.g. '2 for $5 · Save $1.50')"
    )
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def effective_price(self) -> Decimal:
        """Return sale price if available, otherwise regular price."""
        return self.sale_price if self.sale_price is not None else self.price


class FlyerDeal(_ReloadSafeModel):
    """An advertised weekly sale or promotional item from a store flyer."""

    store_name: str
    store_id: str | None = None
    product_name: str
    brand: str | None = None
    department: str | None = None
    regular_price: Decimal | None = Field(default=None, ge=Decimal("0"))
    sale_price: Decimal | None = Field(default=None, ge=Decimal("0"))
    prime_price: Decimal | None = Field(default=None, ge=Decimal("0"))
    unit: Unit = Field(default=Unit.EACH)
    deal_text: str | None = None
    description: str | None = None
    valid_from: str | None = None
    valid_to: str | None = None
    featured: bool = False
    url: str | None = None

    def effective_sale_price(self, use_prime: bool = True) -> Decimal | None:
        """Return the best applicable advertised sale price."""
        if use_prime and self.prime_price is not None:
            return self.prime_price
        return self.sale_price


class PriceComparison(_ReloadSafeModel):
    """Aggregated price comparison for a single grocery item across stores."""

    item: GroceryItem
    prices: list[StorePrice] = Field(default_factory=list)

    def normalized_price(self, store_price: StorePrice) -> Decimal:
        """Return effective price normalized to the item's unit when convertible."""
        converted = convert_unit_price(
            store_price.effective_price,
            store_price.unit,
            self.item.unit,
        )
        return converted if converted is not None else store_price.effective_price

    @property
    def has_unit_mismatch(self) -> bool:
        """Whether any store price uses a non-convertible unit different from the item's."""
        return any(
            convert_unit_price(sp.effective_price, sp.unit, self.item.unit) is None
            for sp in self.prices
        )

    @property
    def cheapest(self) -> StorePrice | None:
        """Return the StorePrice entry with the lowest unit-normalized effective price."""
        if not self.prices:
            return None
        return min(self.prices, key=self.normalized_price)

    @property
    def savings(self) -> Decimal | None:
        """Potential unit-normalized savings between most expensive and cheapest option."""
        if len(self.prices) < 2:
            return None
        cheapest = self.cheapest
        if cheapest is None:
            return None
        most_expensive = max(self.prices, key=self.normalized_price)
        return self.normalized_price(most_expensive) - self.normalized_price(cheapest)


class OptimizedCart(_ReloadSafeModel):
    """Result of running the pricing optimiser over a grocery list."""

    list_id: str
    store_assignments: dict[str, str] = Field(
        description="Mapping of item_id → store_name for optimal pricing"
    )
    total_cost: Decimal
    estimated_savings: Decimal
    comparisons: list[PriceComparison] = Field(default_factory=list)
    single_store_totals: dict[str, Decimal] = Field(
        default_factory=dict,
        description="Total cost per store if all carried items are purchased at that store",
    )
    max_stores_constraint: int | None = Field(
        default=None, description="Max stores constraint applied during optimization"
    )
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def stores_used(self) -> list[str]:
        """Return unique list of stores assigned in this cart."""
        return sorted(set(self.store_assignments.values()))

    @property
    def best_single_store(self) -> tuple[str, Decimal] | None:
        """Return (store_name, total) for the cheapest single store that carries items."""
        if not self.single_store_totals:
            return None
        return min(self.single_store_totals.items(), key=lambda x: x[1])
