"""
models.py
---------
Pydantic data models for groceries, stores, and pricing.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Optional

from pydantic import BaseModel, Field


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


# ---------------------------------------------------------------------------
# Core models
# ---------------------------------------------------------------------------


class GroceryItem(BaseModel):
    """A single item on a grocery list."""

    id: str = Field(description="Unique identifier (e.g., from AnyList)")
    name: str = Field(description="Display name of the item")
    quantity: float = Field(default=1.0, ge=0, description="How many units to purchase")
    unit: Unit = Field(default=Unit.EACH, description="Unit of measurement")
    category: Optional[str] = Field(default=None, description="e.g., 'Produce', 'Dairy'")
    notes: Optional[str] = Field(default=None, description="Free-text shopper notes")
    checked: bool = Field(default=False, description="Marked off in shopping session")


class GroceryList(BaseModel):
    """A named grocery list containing multiple items."""

    id: str = Field(description="Unique list identifier")
    name: str = Field(description="List name (e.g., 'Weekly Shop')")
    items: list[GroceryItem] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class StorePrice(BaseModel):
    """Price of a grocery item at a specific store."""

    item_name: str
    store_name: str
    store_id: Optional[str] = None
    price: Decimal = Field(ge=Decimal("0"), description="Unit price in USD")
    unit: Unit = Field(default=Unit.EACH)
    sale_price: Optional[Decimal] = Field(default=None, ge=Decimal("0"))
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def effective_price(self) -> Decimal:
        """Return sale price if available, otherwise regular price."""
        return self.sale_price if self.sale_price is not None else self.price


class PriceComparison(BaseModel):
    """Aggregated price comparison for a single grocery item across stores."""

    item: GroceryItem
    prices: list[StorePrice] = Field(default_factory=list)

    @property
    def cheapest(self) -> Optional[StorePrice]:
        """Return the StorePrice entry with the lowest effective price."""
        if not self.prices:
            return None
        return min(self.prices, key=lambda p: p.effective_price)

    @property
    def savings(self) -> Optional[Decimal]:
        """Potential savings between most expensive and cheapest option."""
        if len(self.prices) < 2:
            return None
        cheapest = self.cheapest
        most_expensive = max(self.prices, key=lambda p: p.effective_price)
        if cheapest is None:
            return None
        return most_expensive.effective_price - cheapest.effective_price


class OptimizedCart(BaseModel):
    """Result of running the pricing optimiser over a grocery list."""

    list_id: str
    store_assignments: dict[str, str] = Field(
        description="Mapping of item_id → store_name for optimal pricing"
    )
    total_cost: Decimal
    estimated_savings: Decimal
    comparisons: list[PriceComparison] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
