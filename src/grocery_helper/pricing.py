"""
pricing.py
----------
Grocery pricing optimisation logic and store pricing providers.

Responsibilities:
- Fetch prices for a list of grocery items across multiple stores
- Compare prices and recommend the cheapest store per item
- Compute total cost and potential savings for an optimised cart
- Compute single-store basket totals to evaluate store trip trade-offs
"""

from __future__ import annotations

import hashlib
import logging
from decimal import Decimal, ROUND_HALF_UP
from typing import Protocol

from grocery_helper.models import (
    GroceryItem,
    GroceryList,
    OptimizedCart,
    PriceComparison,
    StorePrice,
    Unit,
)
from grocery_helper.utils import cache_key, get_cached_price, set_cached_price

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pricing Provider Protocol & Mock Implementation
# ---------------------------------------------------------------------------


class BasePricingProvider(Protocol):
    """Protocol for fetching store prices for grocery items."""

    async def get_prices(self, item: GroceryItem, store_zip: str) -> list[StorePrice]:
        """Fetch prices for an item across available stores."""
        ...


class MockPricingProvider:
    """
    Realistic mock pricing provider that generates consistent prices across
    popular grocery chains (ALDI, Trader Joe's, Kroger, Whole Foods).

    Prices are deterministically derived from the item name and store profile,
    ensuring consistent results between queries and cache hits.
    """

    KNOWN_CATALOG: dict[str, tuple[Decimal, Unit]] = {
        "milk": (Decimal("3.49"), Unit.GALLON),
        "bread": (Decimal("2.49"), Unit.EACH),
        "sourdough bread": (Decimal("3.99"), Unit.EACH),
        "eggs": (Decimal("3.29"), Unit.EACH),
        "free range eggs": (Decimal("4.49"), Unit.EACH),
        "apples": (Decimal("1.99"), Unit.LB),
        "honeycrisp apples": (Decimal("2.99"), Unit.LB),
        "bananas": (Decimal("0.59"), Unit.LB),
        "chicken breast": (Decimal("4.99"), Unit.LB),
        "ground beef": (Decimal("5.99"), Unit.LB),
        "spinach": (Decimal("2.29"), Unit.EACH),
        "cheese": (Decimal("3.99"), Unit.PACK),
        "butter": (Decimal("4.29"), Unit.PACK),
        "coffee": (Decimal("7.99"), Unit.PACK),
        "coffee beans": (Decimal("8.99"), Unit.PACK),
        "tortilla chips": (Decimal("2.99"), Unit.PACK),
        "salsa": (Decimal("3.19"), Unit.EACH),
        "guacamole": (Decimal("4.49"), Unit.EACH),
        "soda": (Decimal("5.99"), Unit.PACK),
        "soda 12pk": (Decimal("6.49"), Unit.PACK),
        "lime": (Decimal("0.50"), Unit.EACH),
        "limes": (Decimal("0.50"), Unit.EACH),
        "rice": (Decimal("2.19"), Unit.PACK),
        "pasta": (Decimal("1.49"), Unit.PACK),
        "olive oil": (Decimal("8.99"), Unit.EACH),
        "pancake mix": (Decimal("3.29"), Unit.PACK),
        "maple syrup": (Decimal("6.99"), Unit.EACH),
        "bacon": (Decimal("5.49"), Unit.PACK),
        "orange juice": (Decimal("3.99"), Unit.EACH),
    }

    # Store multipliers and sale probabilities: (multiplier, sale_multiplier or None)
    STORE_PROFILES: dict[str, tuple[Decimal, Decimal | None]] = {
        "ALDI": (Decimal("0.85"), None),
        "Trader Joe's": (Decimal("0.95"), None),
        "Kroger": (Decimal("1.02"), Decimal("0.82")),
        "Whole Foods": (Decimal("1.25"), Decimal("1.05")),
    }

    async def get_prices(self, item: GroceryItem, store_zip: str) -> list[StorePrice]:
        """Generate realistic prices for the requested item across configured stores."""
        item_lower = item.name.lower().strip()

        # Find matching known item or derive base price from name hash
        base_price: Decimal | None = None
        matched_unit: Unit = item.unit

        for key, (price, unit) in self.KNOWN_CATALOG.items():
            if key in item_lower or item_lower in key:
                base_price = price
                matched_unit = unit
                break

        if base_price is None:
            # Deterministic base price between $1.99 and $7.99 based on md5 hash
            hash_val = int(hashlib.md5(item_lower.encode()).hexdigest()[:6], 16)
            cents = 199 + (hash_val % 600)
            base_price = Decimal(f"{cents / 100:.2f}")

        prices: list[StorePrice] = []
        for store_name, (multiplier, sale_mult) in self.STORE_PROFILES.items():
            # Small store-specific variation derived from store name + item
            seed = int(hashlib.md5(f"{store_name}:{item_lower}".encode()).hexdigest()[:4], 16)
            jitter = Decimal(str(1.0 + ((seed % 15) - 7) / 100.0))

            price_raw = (base_price * multiplier * jitter).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            price = max(Decimal("0.49"), price_raw)

            sale_price: Decimal | None = None
            if sale_mult is not None and (seed % 3 == 0):
                sale_raw = (base_price * sale_mult).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
                sale_price = min(price - Decimal("0.30"), max(Decimal("0.39"), sale_raw))

            clean_store_id = store_name.lower().replace(" ", "_").replace("'", "")
            prices.append(
                StorePrice(
                    item_name=item.name,
                    store_name=store_name,
                    store_id=f"store_{clean_store_id}",
                    price=price,
                    unit=matched_unit,
                    sale_price=sale_price,
                )
            )

        return prices


# ---------------------------------------------------------------------------
# Pricing Service
# ---------------------------------------------------------------------------


class PricingService:
    """
    Orchestrates price lookups and optimisation for grocery lists.

    Usage::

        service = PricingService(store_zip="90210")
        cart = await service.optimise(grocery_list)
    """

    def __init__(
        self,
        store_zip: str = "",
        provider: BasePricingProvider | None = None,
    ) -> None:
        self._store_zip = store_zip
        self._provider: BasePricingProvider = provider or MockPricingProvider()

    # ── Public API ────────────────────────────────────────────────────────────

    async def fetch_prices(self, item: GroceryItem) -> list[StorePrice]:
        """
        Fetch prices for a single item from all available stores, using
        the in-memory TTL cache if available.
        """
        key = cache_key(item.name, self._store_zip)
        cached = get_cached_price(key)
        if cached is not None and isinstance(cached, list):
            logger.debug("Cache hit for '%s' in ZIP %s", item.name, self._store_zip)
            return cached  # type: ignore[no-any-return]

        logger.debug("Fetching prices for '%s' in ZIP %s", item.name, self._store_zip)
        prices = await self._provider.get_prices(item, self._store_zip)
        set_cached_price(key, prices)
        return prices

    async def compare(self, item: GroceryItem) -> PriceComparison:
        """Return a PriceComparison for a single item across all stores."""
        prices = await self.fetch_prices(item)
        return PriceComparison(item=item, prices=prices)

    async def optimise(self, grocery_list: GroceryList) -> OptimizedCart:
        """
        Run the optimiser over an entire grocery list.

        For each item, selects the store with the lowest effective price.
        Also calculates basket totals for each individual store to evaluate
        single-store shopping vs. multi-store splitting.
        """
        comparisons: list[PriceComparison] = []
        store_assignments: dict[str, str] = {}
        total_cost = Decimal("0.00")
        estimated_savings = Decimal("0.00")

        # Track single-store totals
        store_totals: dict[str, Decimal] = {}
        store_item_counts: dict[str, int] = {}

        for item in grocery_list.items:
            comparison = await self.compare(item)
            comparisons.append(comparison)

            qty = Decimal(str(item.quantity))

            if comparison.cheapest:
                store_assignments[item.id] = comparison.cheapest.store_name
                total_cost += comparison.cheapest.effective_price * qty
                if comparison.savings:
                    estimated_savings += comparison.savings * qty

            for sp in comparison.prices:
                store_totals.setdefault(sp.store_name, Decimal("0.00"))
                store_item_counts.setdefault(sp.store_name, 0)
                store_totals[sp.store_name] += sp.effective_price * qty
                store_item_counts[sp.store_name] += 1

        total_items = len(grocery_list.items)
        single_store_totals = {
            store: cost
            for store, cost in store_totals.items()
            if store_item_counts[store] == total_items
        } if total_items > 0 else {}

        return OptimizedCart(
            list_id=grocery_list.id,
            store_assignments=store_assignments,
            total_cost=total_cost,
            estimated_savings=estimated_savings,
            comparisons=comparisons,
            single_store_totals=single_store_totals,
        )
