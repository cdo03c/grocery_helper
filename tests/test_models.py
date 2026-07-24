"""Tests for data models."""
from decimal import Decimal

import pytest

from grocery_helper.models import (
    GroceryItem,
    GroceryList,
    PriceComparison,
    StorePrice,
    Unit,
)


def make_item(name: str = "Milk", quantity: float = 1.0) -> GroceryItem:
    return GroceryItem(id="item_001", name=name, quantity=quantity, unit=Unit.GALLON)


def make_price(store: str, price: str, sale: str | None = None) -> StorePrice:
    return StorePrice(
        item_name="Milk",
        store_name=store,
        price=Decimal(price),
        sale_price=Decimal(sale) if sale else None,
    )


class TestGroceryItem:
    def test_defaults(self) -> None:
        item = GroceryItem(id="x", name="Bread")
        assert item.quantity == 1.0
        assert item.unit == Unit.EACH
        assert item.checked is False

    def test_invalid_quantity(self) -> None:
        bad_qty: float = -1
        with pytest.raises(Exception):
            GroceryItem(id="x", name="Bread", quantity=bad_qty)


class TestStorePrice:
    def test_effective_price_no_sale(self) -> None:
        p = make_price("Store A", "3.99")
        assert p.effective_price == Decimal("3.99")

    def test_effective_price_with_sale(self) -> None:
        p = make_price("Store B", "3.99", sale="2.99")
        assert p.effective_price == Decimal("2.99")


class TestPriceComparison:
    def test_cheapest(self) -> None:
        item = make_item()
        prices = [make_price("A", "3.99"), make_price("B", "2.49"), make_price("C", "4.50")]
        comp = PriceComparison(item=item, prices=prices)
        assert comp.cheapest is not None
        assert comp.cheapest.store_name == "B"

    def test_savings(self) -> None:
        item = make_item()
        prices = [make_price("A", "4.50"), make_price("B", "2.50")]
        comp = PriceComparison(item=item, prices=prices)
        assert comp.savings == Decimal("2.00")

    def test_empty_prices(self) -> None:
        comp = PriceComparison(item=make_item(), prices=[])
        assert comp.cheapest is None
        assert comp.savings is None
