"""
pricing.py
----------
Grocery pricing optimisation logic.

Responsibilities:
- Fetch prices for a list of grocery items across multiple stores
- Compare prices and recommend the cheapest store per item
- Compute total cost and potential savings for an optimised cart
"""

from __future__ import annotations

import logging
from decimal import Decimal

from grocery_helper.models import (
    GroceryItem,
    GroceryList,
    OptimizedCart,
    PriceComparison,
    StorePrice,
)

logger = logging.getLogger(__name__)


class PricingService:
    """
    Orchestrates price lookups and optimisation for grocery lists.

    Usage::

        service = PricingService(store_zip="90210")
        cart = await service.optimise(grocery_list)
    """

    def __init__(self, store_zip: str = "") -> None:
        self._store_zip = store_zip

    # ── Public API ────────────────────────────────────────────────────────────

    async def fetch_prices(self, item: GroceryItem) -> list[StorePrice]:
        """
        Fetch prices for a single item from all available stores.

        TODO: Integrate with a real grocery pricing data source
              (e.g., Kroger API, grocery store scraper, etc.)
        """
        logger.debug("Fetching prices for '%s' in ZIP %s", item.name, self._store_zip)

        # ── Placeholder: return empty list until real source is wired up ─────
        return []

    async def compare(self, item: GroceryItem) -> PriceComparison:
        """Return a PriceComparison for a single item across all stores."""
        prices = await self.fetch_prices(item)
        return PriceComparison(item=item, prices=prices)

    async def optimise(self, grocery_list: GroceryList) -> OptimizedCart:
        """
        Run the optimiser over an entire grocery list.

        For each item, selects the store with the lowest effective price.
        Returns an OptimizedCart with store assignments and total cost.
        """
        comparisons: list[PriceComparison] = []
        store_assignments: dict[str, str] = {}
        total_cost = Decimal("0.00")
        estimated_savings = Decimal("0.00")

        for item in grocery_list.items:
            comparison = await self.compare(item)
            comparisons.append(comparison)

            if comparison.cheapest:
                store_assignments[item.id] = comparison.cheapest.store_name
                total_cost += comparison.cheapest.effective_price * Decimal(str(item.quantity))
                if comparison.savings:
                    estimated_savings += comparison.savings * Decimal(str(item.quantity))

        return OptimizedCart(
            list_id=grocery_list.id,
            store_assignments=store_assignments,
            total_cost=total_cost,
            estimated_savings=estimated_savings,
            comparisons=comparisons,
        )
