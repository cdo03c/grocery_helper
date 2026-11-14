"""Tests for the Open Prices crowdsourced retail pricing provider and integration."""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest

from grocery_helper.cli import build_parser
from grocery_helper.flyers import MarketBasketFlyerProvider, WholeFoodsFlyerProvider
from grocery_helper.models import FlyerDeal, GroceryItem, Unit
from grocery_helper.open_prices import OpenPricesProvider, normalize_open_prices_store
from grocery_helper.pricing import PricingService
from grocery_helper.utils import clear_price_cache


@pytest.fixture(autouse=True)
def _clean_cache() -> None:
    clear_price_cache()


class TestStoreNormalization:
    def test_canonicalizes_known_chains(self) -> None:
        assert normalize_open_prices_store("Whole Foods Market") == "Whole Foods"
        assert normalize_open_prices_store("Demoulas Market Basket") == "Market Basket"
        assert normalize_open_prices_store("Market Basket #12", None) == "Market Basket"
        assert normalize_open_prices_store("ALDI", "ALDI") == "ALDI"
        assert normalize_open_prices_store("Walmart Supercenter") == "Walmart"
        assert normalize_open_prices_store("Costco Wholesale") == "Costco"
        assert normalize_open_prices_store("Wegmans") == "Wegmans"


class TestOpenPricesProvider:
    @pytest.mark.asyncio
    async def test_product_lookup_returns_usd_store_prices_and_discounts(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/products"):
                return httpx.Response(
                    200,
                    json={
                        "items": [
                            {"code": "001122334455", "product_name": "Extra Virgin Olive Oil"}
                        ]
                    },
                )
            if request.url.path.endswith("/prices"):
                params = dict(request.url.params)
                if "product_code__in" in params:
                    return httpx.Response(
                        200,
                        json={
                            "items": [
                                {
                                    "id": 101,
                                    "currency": "USD",
                                    "price": 13.37,
                                    "price_without_discount": 17.49,
                                    "price_is_discounted": True,
                                    "price_per": None,
                                    "date": "2026-10-11",
                                    "product": {"product_name": "Extra Virgin Olive Oil"},
                                    "location": {
                                        "id": 42,
                                        "osm_name": "Whole Foods Market",
                                        "osm_brand": "Whole Foods Market",
                                        "osm_address_postcode": "02139",
                                        "osm_address_country_code": "US",
                                    },
                                },
                                {
                                    "id": 102,
                                    "currency": "EUR",
                                    "price": 9.99,
                                    "date": "2026-10-12",
                                    "product": {"product_name": "Extra Virgin Olive Oil"},
                                    "location": {
                                        "id": 99,
                                        "osm_name": "Carrefour",
                                        "osm_address_country_code": "FR",
                                    },
                                },
                            ]
                        },
                    )
                return httpx.Response(200, json={"items": []})
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = OpenPricesProvider(http_client=client)
            prices = await provider.get_prices(GroceryItem(id="1", name="Olive Oil"), "02139")

        assert len(prices) == 1
        wf = prices[0]
        assert wf.store_name == "Whole Foods"
        assert wf.store_id == "42"
        assert wf.price == Decimal("17.49")
        assert wf.sale_price == Decimal("13.37")
        assert wf.effective_price == Decimal("13.37")
        assert wf.is_live is True
        assert wf.matched_product_name == "Extra Virgin Olive Oil"
        assert wf.deal_details is not None
        assert "Open Prices" in wf.deal_details
        assert "ZIP 02139" in wf.deal_details
        assert "Sale (was $17.49)" in wf.deal_details

    @pytest.mark.asyncio
    async def test_category_fallback_and_zip_proximity_ranking(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/products"):
                return httpx.Response(200, json={"items": [{"code": "7622210720566"}]})
            if request.url.path.endswith("/prices"):
                params = dict(request.url.params)
                if "product__categories_tags__contains" in params:
                    assert params["product__categories_tags__contains"] == "en:whole-milks"
                    return httpx.Response(
                        200,
                        json={
                            "items": [
                                {
                                    "id": 201,
                                    "currency": "USD",
                                    "price": 5.49,
                                    "date": "2026-11-10",
                                    "product": {"product_name": "Organic Whole Milk"},
                                    "location": {
                                        "id": 10,
                                        "osm_name": "Market Basket",
                                        "osm_address_postcode": "94040",
                                        "osm_address_country_code": "US",
                                    },
                                },
                                {
                                    "id": 202,
                                    "currency": "USD",
                                    "price": 3.89,
                                    "date": "2026-09-01",
                                    "product": {"product_name": "Organic Whole Milk"},
                                    "location": {
                                        "id": 11,
                                        "osm_name": "Market Basket",
                                        "osm_address_postcode": "01845",
                                        "osm_address_country_code": "US",
                                    },
                                },
                            ]
                        },
                    )
                return httpx.Response(200, json={"items": []})
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = OpenPricesProvider(http_client=client)
            prices = await provider.get_prices(GroceryItem(id="1", name="Whole Milk"), "01845")

        assert len(prices) == 1
        assert prices[0].store_name == "Market Basket"
        assert prices[0].price == Decimal("3.89")
        assert prices[0].deal_details is not None
        assert "01845" in prices[0].deal_details

    @pytest.mark.asyncio
    async def test_per_kilogram_converts_to_lb(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/products"):
                return httpx.Response(200, json={"items": [{"code": "4011"}]})
            if request.url.path.endswith("/prices"):
                params = dict(request.url.params)
                if "product_code__in" in params:
                    return httpx.Response(
                        200,
                        json={
                            "items": [
                                {
                                    "id": 301,
                                    "currency": "USD",
                                    "price": 2.20,
                                    "price_per": "KILOGRAM",
                                    "date": "2026-10-05",
                                    "product": {"product_name": "Fresh Bananas"},
                                    "location": {
                                        "id": 50,
                                        "osm_name": "Trader Joe's",
                                        "osm_address_postcode": "02139",
                                        "osm_address_country_code": "US",
                                    },
                                }
                            ]
                        },
                    )
                return httpx.Response(200, json={"items": []})
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = OpenPricesProvider(http_client=client)
            prices = await provider.get_prices(GroceryItem(id="1", name="Bananas"), "02139")

        assert len(prices) == 1
        assert prices[0].unit == Unit.LB
        assert prices[0].price == Decimal("1.00")

    @pytest.mark.asyncio
    async def test_handles_network_errors_gracefully(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("offline", request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = OpenPricesProvider(http_client=client)
            prices = await provider.get_prices(GroceryItem(id="1", name="Coffee"), "02139")

        assert prices == []


class TestPricingAndCliIntegration:
    @pytest.mark.asyncio
    async def test_flyer_sale_overrides_open_prices_while_non_sale_keeps_open_prices(self) -> None:
        def op_handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/products"):
                return httpx.Response(200, json={"items": [{"code": "999"}]})
            if request.url.path.endswith("/prices"):
                params = dict(request.url.params)
                if "product_code__in" in params:
                    return httpx.Response(
                        200,
                        json={
                            "items": [
                                {
                                    "id": 401,
                                    "currency": "USD",
                                    "price": 6.49,
                                    "date": "2026-11-01",
                                    "product": {"product_name": "Arabica Coffee"},
                                    "location": {
                                        "id": 12,
                                        "osm_name": "Market Basket",
                                        "osm_address_postcode": "01845",
                                        "osm_address_country_code": "US",
                                    },
                                },
                                {
                                    "id": 402,
                                    "currency": "USD",
                                    "price": 5.99,
                                    "date": "2026-11-02",
                                    "product": {"product_name": "Arabica Coffee"},
                                    "location": {
                                        "id": 13,
                                        "osm_name": "ALDI",
                                        "osm_address_postcode": "02145",
                                        "osm_address_country_code": "US",
                                    },
                                },
                            ]
                        },
                    )
                return httpx.Response(200, json={"items": []})
            return httpx.Response(404)

        class _StubMBFlyer(MarketBasketFlyerProvider):
            async def fetch_flyer(self) -> list[FlyerDeal]:
                return [
                    FlyerDeal(
                        store_name="Market Basket",
                        product_name="Arabica Coffee",
                        sale_price=Decimal("4.99"),
                        deal_text="$4.99 Weekly Sale",
                    )
                ]

        class _StubWFFlyer(WholeFoodsFlyerProvider):
            async def fetch_flyer(self, store_id: str | None = None) -> list[FlyerDeal]:
                return []

        async with httpx.AsyncClient(transport=httpx.MockTransport(op_handler)) as op_client:
            op_provider = OpenPricesProvider(http_client=op_client)
            svc = PricingService(
                store_zip="02139",
                enable_live_flyers=True,
                enable_flipp=False,
                enable_open_prices=True,
                market_basket_provider=_StubMBFlyer(),
                whole_foods_provider=_StubWFFlyer(),
                open_prices_provider=op_provider,
            )

            prices = await svc.fetch_prices(GroceryItem(id="c1", name="Coffee"))

        by_store = {p.store_name: p for p in prices}
        # Market Basket weekly flyer ($4.99) takes priority over Open Prices ($6.49)
        assert by_store["Market Basket"].effective_price == Decimal("4.99")
        assert by_store["Market Basket"].deal_details == "$4.99 Weekly Sale"
        # ALDI wasn't in MB flyer, so Open Prices ($5.99) replaces MockPricingProvider!
        assert by_store["ALDI"].effective_price == Decimal("5.99")
        assert by_store["ALDI"].is_live is True
        assert by_store["ALDI"].deal_details is not None
        assert "Open Prices" in by_store["ALDI"].deal_details

    def test_cli_open_prices_flags(self) -> None:
        parser = build_parser()
        args_default = parser.parse_args(["optimise", "list_001"])
        assert args_default.open_prices is None

        args_on = parser.parse_args(["optimise", "list_001", "--open-prices"])
        assert args_on.open_prices is True

        args_off = parser.parse_args(["optimise", "list_001", "--no-open-prices"])
        assert args_off.open_prices is False
