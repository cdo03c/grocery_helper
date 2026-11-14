"""Tests for Market Basket & Whole Foods live weekly flyer providers and matching."""

from __future__ import annotations

import json
from decimal import Decimal

import httpx
import pytest

from grocery_helper.flyers import (
    MarketBasketFlyerProvider,
    WholeFoodsFlyerProvider,
    match_flyer_deals,
    parse_discount_percent,
    parse_price_expression,
    score_flyer_match,
)
from grocery_helper.models import FlyerDeal, GroceryItem, Unit


def test_parse_price_expression_variants() -> None:
    assert parse_price_expression("$5.99") == (Decimal("5.99"), None, None, 1)
    assert parse_price_expression("99¢") == (Decimal("0.99"), None, None, 1)
    assert parse_price_expression("49¢ lb.") == (Decimal("0.49"), Unit.LB, None, 1)
    assert parse_price_expression("1.69 lb.") == (Decimal("1.69"), Unit.LB, None, 1)
    assert parse_price_expression("2 for $5") == (Decimal("2.50"), None, "2 for $5", 2)
    assert parse_price_expression("4 for $5.56") == (Decimal("1.39"), None, "4 for $5.56", 4)
    assert parse_price_expression("$2.49 to $2.79/lb") == (Decimal("2.49"), Unit.LB, None, 1)
    assert parse_price_expression("20% off") == (None, None, None, 1)
    assert parse_price_expression("") == (None, None, None, 1)


def test_parse_discount_percent() -> None:
    assert parse_discount_percent("20% off") == Decimal("0.20")
    assert parse_discount_percent("30% Off Reg") == Decimal("0.30")
    assert parse_discount_percent("$2.99") is None


def test_score_flyer_match_prevents_substring_false_positives() -> None:
    assert score_flyer_match("milk", "Buttermilk") == 0.0
    assert score_flyer_match("butter", "Butternut Squash") == 0.0
    assert score_flyer_match("Free Range Eggs", "Orange Sweet Potatoes") == 0.0
    assert score_flyer_match("Strawberries", "Organic Fresh Strawberries") > 0.65
    assert score_flyer_match("Whole Milk", "Market Basket Whole Milk 1 Gallon") > 0.55


def test_match_flyer_deals_ranks_best_match_first() -> None:
    deals = [
        FlyerDeal(
            store_name="Market Basket", product_name="Greek Yogurt Cup", sale_price=Decimal("1.00")
        ),
        FlyerDeal(
            store_name="Market Basket",
            product_name="Chobani Plain Greek Yogurt 32 oz",
            sale_price=Decimal("4.99"),
        ),
        FlyerDeal(
            store_name="Market Basket", product_name="Atlantic Salmon", sale_price=Decimal("7.99")
        ),
    ]
    matches = match_flyer_deals("Plain Greek Yogurt", deals)
    assert matches
    assert "Plain Greek Yogurt" in matches[0][0].product_name


def test_market_basket_parse_flyer_payload() -> None:
    payload = {
        "dates": {"start_date": "2026-11-08", "end_date": "2026-11-14"},
        "products": [
            {
                "name": "Market Basket Strawberries",
                "deal_pricing": "2 for $5",
                "deal_text": "$1.98",
                "description": "16 oz pkg",
                "departments": [{"name": "Produce &amp; Floral"}],
                "featured": True,
                "link": "https://www.shopmarketbasket.com/product/strawberries",
            },
            {
                "name": "Boneless Chicken Breast",
                "deal_pricing": "1.99 lb.",
                "deal_text": "",
                "description": "Value pack",
                "departments": [{"name": "Meat"}],
                "featured": False,
            },
        ],
    }
    deals = MarketBasketFlyerProvider.parse_flyer_payload(payload)
    assert len(deals) == 2

    strawberries = deals[0]
    assert strawberries.store_name == "Market Basket"
    assert strawberries.sale_price == Decimal("2.50")
    assert strawberries.regular_price == Decimal("3.49")
    assert strawberries.department == "Produce & Floral"
    assert strawberries.featured is True
    assert strawberries.valid_from == "2026-11-08"

    chicken = deals[1]
    assert chicken.sale_price == Decimal("1.99")
    assert chicken.unit == Unit.LB


@pytest.mark.asyncio
async def test_market_basket_fetch_and_get_prices_mocked_http() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.headers["User-Agent"] == "GroceryHelper/0.1"
        return httpx.Response(
            200,
            json={
                "dates": {"start_date": "2026-11-08", "end_date": "2026-11-14"},
                "products": [
                    {
                        "name": "Hood Whole Milk",
                        "deal_pricing": "$3.49",
                        "deal_text": "$0.50",
                        "description": "1 gal",
                        "departments": [{"name": "Dairy"}],
                        "featured": False,
                    }
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = MarketBasketFlyerProvider(
            flyer_url="https://example.test/wp-json/mb/v1/weekly-flyer",
            http_client=client,
        )
        prices = await provider.get_prices(
            GroceryItem(id="m1", name="Whole Milk", unit=Unit.GALLON),
            store_zip="01876",
        )
        # Second call hits cache
        prices_cached = await provider.get_prices(
            GroceryItem(id="m2", name="Organic Bananas", unit=Unit.LB),
            store_zip="01876",
        )

    assert calls == 1
    assert len(prices) == 1
    assert prices[0].store_name == "Market Basket"
    assert prices[0].sale_price == Decimal("3.49")
    assert prices[0].price == Decimal("3.99")
    assert prices[0].unit == Unit.EACH
    assert prices[0].is_live is True
    assert prices_cached == []


def test_whole_foods_parse_flyer_html() -> None:
    next_data = {
        "props": {
            "pageProps": {
                "storeId": "10010",
                "storeName": "Cambridge",
                "promotions": [
                    {
                        "productName": "Organic Honeycrisp Apples",
                        "originBrandName": "Whole Foods Market",
                        "regularPrice": "$3.99/lb",
                        "salePrice": "$2.49/lb",
                        "primePrice": "$1.99/lb",
                        "headline": "Fresh seasonal apples",
                        "startDate": "2026-11-11T00:00:00Z",
                        "endDate": "2026-11-17T23:59:59Z",
                        "tier": "SUPER_HERO",
                    },
                    {
                        "productName": "365 Everyday Almond Butter",
                        "originBrandName": "365 by Whole Foods Market",
                        "regularPrice": "$7.99",
                        "salePrice": "20% off",
                        "primePrice": "30% off",
                        "headline": "Selected varieties",
                        "startDate": "2026-11-11",
                        "endDate": "2026-11-17",
                        "tier": "STANDARD",
                    },
                ],
            }
        }
    }
    html = (
        "<html><head>"
        f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(next_data)}</script>'
        "</head><body></body></html>"
    )
    deals = WholeFoodsFlyerProvider.parse_flyer_html(html, store_id="10010")
    assert len(deals) == 2

    apples = deals[0]
    assert apples.store_name == "Whole Foods"
    assert apples.store_id == "whole_foods_10010"
    assert apples.regular_price == Decimal("3.99")
    assert apples.sale_price == Decimal("2.49")
    assert apples.prime_price == Decimal("1.99")
    assert apples.unit == Unit.LB
    assert apples.featured is True
    assert apples.valid_from == "2026-11-11"

    almond_butter = deals[1]
    assert almond_butter.sale_price == Decimal("6.39")
    assert almond_butter.prime_price == Decimal("5.59")


@pytest.mark.asyncio
async def test_whole_foods_get_prices_respects_prime_flag() -> None:
    next_data = {
        "props": {
            "pageProps": {
                "storeId": "10380",
                "promotions": [
                    {
                        "productName": "Wild Atlantic Salmon Fillet",
                        "regularPrice": "$14.99/lb",
                        "salePrice": "$11.99/lb",
                        "primePrice": "$9.99/lb",
                    }
                ],
            }
        }
    }
    html = f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(next_data)}</script>'

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["store-id"] == "10380"
        return httpx.Response(200, text=html)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        prime_provider = WholeFoodsFlyerProvider(
            store_id="10380",
            use_prime=True,
            base_url="https://example.test",
            http_client=client,
        )
        non_prime_provider = WholeFoodsFlyerProvider(
            store_id="10380",
            use_prime=False,
            base_url="https://example.test",
            http_client=client,
        )
        prime_prices = await prime_provider.get_prices(
            GroceryItem(id="s1", name="Atlantic Salmon Fillet", unit=Unit.LB),
            store_zip="02155",
        )
        reg_sale_prices = await non_prime_provider.get_prices(
            GroceryItem(id="s2", name="Atlantic Salmon Fillet", unit=Unit.LB),
            store_zip="02155",
        )

    assert len(prime_prices) == 1
    assert prime_prices[0].sale_price == Decimal("9.99")
    assert prime_prices[0].is_live is True

    assert len(reg_sale_prices) == 1
    assert reg_sale_prices[0].sale_price == Decimal("11.99")


def test_flipp_normalize_and_parsers() -> None:
    from grocery_helper.flyers import FlippFlyerProvider

    assert FlippFlyerProvider.normalize_merchant("Star Market\t") == "Star Market"
    assert FlippFlyerProvider.normalize_merchant("Demoulas Market Basket") == "Market Basket"

    flyer_deals = FlippFlyerProvider.parse_flyer_items(
        flyer_id=8220001,
        merchant_name="Star Market\t",
        flyer_name="Weekly Ad",
        items=[
            {
                "name": "Green Giant Green Beans",
                "price": "1.0",
                "discount": 50,
                "brand": "Green Giant",
                "valid_from": "2026-11-13T00:00:00-05:00",
                "valid_to": "2026-11-19T23:59:59-05:00",
            },
            {"name": None, "price": "3.99"},
        ],
    )
    assert len(flyer_deals) == 1
    assert flyer_deals[0].store_name == "Star Market"
    assert flyer_deals[0].sale_price == Decimal("1.00")
    assert flyer_deals[0].regular_price == Decimal("2.00")

    search_deals = FlippFlyerProvider.parse_search_items(
        [
            {
                "item_type": "flyer",
                "name": "Boneless Chicken Breast",
                "merchant_name": "Hannaford",
                "current_price": 3.99,
                "post_price_text": "lb.",
                "original_price": 5.99,
                "sale_story": "SAVE $2 PER LB.",
                "flyer_id": 8227690,
            },
            {
                "item_type": "flyer",
                "name": "Strawberries 1 lb",
                "merchant_name": "Stop & Shop",
                "pre_price_text": "2/$",
                "current_price": "5.00",
                "flyer_id": 8216624,
            },
            {
                "item_type": "ecom",
                "name": "Sponsored Item",
                "merchant_name": "Other",
                "current_price": 1.00,
            },
        ]
    )
    assert len(search_deals) == 2
    assert search_deals[0].unit == Unit.LB
    assert search_deals[0].sale_price == Decimal("3.99")
    assert search_deals[0].regular_price == Decimal("5.99")
    assert search_deals[1].sale_price == Decimal("2.50")


@pytest.mark.asyncio
async def test_flipp_fetch_flyer_and_get_prices_mocked_http() -> None:
    from grocery_helper.flyers import FlippFlyerProvider

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/flyers"):
            return httpx.Response(
                200,
                json={
                    "flyers": [
                        {
                            "id": 101,
                            "merchant": "Hannaford",
                            "name": "Weekly Ad",
                            "categories": ["All Flyers", "Groceries"],
                        },
                        {
                            "id": 102,
                            "merchant": "Best Buy",
                            "name": "Tech Deals",
                            "categories": ["All Flyers", "Electronics"],
                        },
                    ]
                },
            )
        if path.endswith("/flyers/101"):
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "name": "Organic Whole Milk",
                            "price": "3.99",
                            "discount": None,
                            "brand": "Nature's Promise",
                        }
                    ]
                },
            )
        if path.endswith("/items/search"):
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "item_type": "flyer",
                            "name": "Hannaford Boneless Chicken Breast",
                            "merchant_name": "Hannaford",
                            "current_price": 2.49,
                            "post_price_text": "lb.",
                            "original_price": 4.99,
                            "sale_story": "SAVE $2.50/LB",
                            "flyer_id": 101,
                        }
                    ]
                },
            )
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = FlippFlyerProvider(
            postal_code="02139",
            base_url="https://example.test/flipp",
            http_client=client,
        )
        deals = await provider.fetch_flyer()
        prices = await provider.get_prices(
            GroceryItem(id="f1", name="Chicken Breast", unit=Unit.LB),
            store_zip="02139",
        )

    assert len(deals) == 1
    assert deals[0].store_name == "Hannaford"
    assert deals[0].sale_price == Decimal("3.99")

    assert len(prices) == 1
    assert prices[0].store_name == "Hannaford"
    assert prices[0].sale_price == Decimal("2.49")
    assert prices[0].unit == Unit.LB
    assert prices[0].is_live is True
