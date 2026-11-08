"""Tests for pricing optimisation logic."""
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from grocery_helper.models import GroceryItem, GroceryList, StorePrice, Unit
from grocery_helper.pricing import MockPricingProvider, PricingService


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
    assert cart.single_store_totals == {}
    assert cart.best_single_store is None


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


@pytest.mark.asyncio
async def test_mock_pricing_provider_returns_prices() -> None:
    """MockPricingProvider should return prices across stores for known and unknown items."""
    provider = MockPricingProvider()
    item_known = GroceryItem(id="item_1", name="Eggs", quantity=1, unit=Unit.EACH)
    prices_known = await provider.get_prices(item_known, "90210")

    assert len(prices_known) == 4
    store_names = {p.store_name for p in prices_known}
    assert "ALDI" in store_names
    assert "Trader Joe's" in store_names
    assert "Kroger" in store_names
    assert "Whole Foods" in store_names
    for p in prices_known:
        assert p.price > Decimal("0")

    # Unknown item should also produce realistic prices
    item_unknown = GroceryItem(id="item_2", name="Dragon Fruit Exotic", quantity=1)
    prices_unknown = await provider.get_prices(item_unknown, "90210")
    assert len(prices_unknown) == 4
    for p in prices_unknown:
        assert p.price > Decimal("0")


@pytest.mark.asyncio
async def test_pricing_service_uses_cache() -> None:
    """PricingService should cache prices and avoid multiple provider calls."""
    mock_provider = AsyncMock()
    mock_provider.get_prices.return_value = [
        StorePrice(item_name="Spinach", store_name="ALDI", price=Decimal("1.99"))
    ]
    service = PricingService(store_zip="11201", provider=mock_provider)
    item = GroceryItem(id="sp_1", name="Spinach Organic", quantity=1)

    prices1 = await service.fetch_prices(item)
    prices2 = await service.fetch_prices(item)

    assert prices1 == prices2
    assert mock_provider.get_prices.call_count == 1


@pytest.mark.asyncio
async def test_optimise_with_real_mock_provider() -> None:
    """Full optimisation run with MockPricingProvider calculates single-store totals."""
    service = PricingService(store_zip="90210")
    cart = await service.optimise(make_list())

    assert cart.total_cost > Decimal("0.00")
    assert cart.list_id == "list_001"
    assert len(cart.store_assignments) == 2
    assert len(cart.single_store_totals) > 0
    assert cart.best_single_store is not None
    best_store, best_total = cart.best_single_store
    assert best_store in cart.single_store_totals
    assert best_total >= cart.total_cost
