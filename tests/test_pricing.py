"""Tests for pricing optimisation logic."""
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from grocery_helper.models import GroceryItem, GroceryList, StorePrice, Unit
from grocery_helper.pricing import PricingService


def make_list() -> GroceryList:
    return GroceryList(
        id="list_001",
        name="Test List",
        items=[
            GroceryItem(id="item_a", name="Milk", quantity=2, unit=Unit.GALLON),
            GroceryItem(id="item_b", name="Bread", quantity=1, unit=Unit.EACH),
        ],
    )


@pytest.mark.asyncio
async def test_optimise_empty_prices() -> None:
    """With no prices, optimise should return a zero-cost cart."""
    service = PricingService(store_zip="12345")

    with patch.object(service, "fetch_prices", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = []
        cart = await service.optimise(make_list())

    assert cart.total_cost == Decimal("0.00")
    assert cart.estimated_savings == Decimal("0.00")
    assert cart.store_assignments == {}


@pytest.mark.asyncio
async def test_optimise_assigns_cheapest_store() -> None:
    """Optimiser should assign the cheapest store for each item."""
    service = PricingService(store_zip="12345")

    def side_effect(item: GroceryItem) -> list[StorePrice]:
        if item.name == "Milk":
            return [
                StorePrice(item_name="Milk", store_name="Store A", price=Decimal("3.99")),
                StorePrice(item_name="Milk", store_name="Store B", price=Decimal("2.49")),
            ]
        return [
            StorePrice(item_name="Bread", store_name="Store A", price=Decimal("1.99")),
        ]

    with patch.object(service, "fetch_prices", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.side_effect = side_effect
        cart = await service.optimise(make_list())

    assert cart.store_assignments["item_a"] == "Store B"
    assert cart.store_assignments["item_b"] == "Store A"
    # Milk: 2 × $2.49 = $4.98, Bread: 1 × $1.99 = $1.99 → total $6.97
    assert cart.total_cost == Decimal("6.97")
