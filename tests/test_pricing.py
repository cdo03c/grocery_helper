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

    assert len(prices_known) == 5
    store_names = {p.store_name for p in prices_known}
    assert "ALDI" in store_names
    assert "Market Basket" in store_names
    assert "Trader Joe's" in store_names
    assert "Kroger" in store_names
    assert "Whole Foods" in store_names
    for p in prices_known:
        assert p.price > Decimal("0")

    # Unknown item should also produce realistic prices
    item_unknown = GroceryItem(id="item_2", name="Dragon Fruit Exotic", quantity=1)
    prices_unknown = await provider.get_prices(item_unknown, "90210")
    assert len(prices_unknown) == 5
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


@pytest.mark.asyncio
async def test_optimise_with_allowed_stores() -> None:
    """Restricting allowed_stores should only pick from the specified stores."""
    service = PricingService(store_zip="90210")
    cart = await service.optimise(make_list(), allowed_stores=["Trader Joe's", "Whole Foods"])

    stores_assigned = set(cart.store_assignments.values())
    assert stores_assigned.issubset({"Trader Joe's", "Whole Foods"})
    assert "ALDI" not in stores_assigned


@pytest.mark.asyncio
async def test_optimise_with_max_stores_constraint() -> None:
    """max_stores=1 should constrain all items to the single cheapest store."""
    service = PricingService(store_zip="90210")
    cart_constrained = await service.optimise(make_list(), max_stores=1)

    assert len(cart_constrained.stores_used) == 1
    best_store = cart_constrained.stores_used[0]
    assert cart_constrained.best_single_store is not None
    assert best_store == cart_constrained.best_single_store[0]
    assert cart_constrained.total_cost == cart_constrained.best_single_store[1]


@pytest.mark.asyncio
async def test_kroger_provider_unconfigured() -> None:
    """Unconfigured KrogerPricingProvider should return empty list."""
    from grocery_helper.pricing import KrogerPricingProvider

    provider = KrogerPricingProvider(client_id="", client_secret="")
    assert provider.is_configured is False
    res = await provider.get_prices(GroceryItem(id="k1", name="Bread"), "90210")
    assert res == []


@pytest.mark.asyncio
async def test_kroger_provider_live_flow() -> None:
    """Mock HTTP transport for Kroger OAuth2 token, location, and product pricing."""
    import httpx

    from grocery_helper.pricing import KrogerPricingProvider

    def kroger_handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/connect/oauth2/token" in url:
            return httpx.Response(200, json={"access_token": "kroger_test_token"})
        if "/locations" in url:
            return httpx.Response(200, json={"data": [{"locationId": "01400943"}]})
        if "/products" in url:
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "items": [
                                {
                                    "price": {
                                        "regular": 2.79,
                                        "promo": 2.29,
                                    }
                                }
                            ]
                        }
                    ]
                },
            )
        return httpx.Response(404)

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(kroger_handler))
    provider = KrogerPricingProvider(
        client_id="test_cid",
        client_secret="test_secret",
        base_url="https://api.kroger.com/v1",
        http_client=mock_client,
    )
    assert provider.is_configured is True

    item = GroceryItem(id="item_kr", name="Kroger Milk", unit=Unit.GALLON)
    prices = await provider.get_prices(item, "45202")

    assert len(prices) == 1
    assert prices[0].store_name == "Kroger"
    assert prices[0].price == Decimal("2.79")
    assert prices[0].sale_price == Decimal("2.29")
    assert prices[0].effective_price == Decimal("2.29")


@pytest.mark.asyncio
async def test_pricing_service_overlays_live_kroger() -> None:
    """PricingService should replace mock Kroger price with live Kroger price when available."""
    mock_kroger = AsyncMock()
    mock_kroger.is_configured = True
    mock_kroger.get_prices.return_value = [
        StorePrice(item_name="Eggs", store_name="Kroger", price=Decimal("1.49"))
    ]

    service = PricingService(store_zip="45202", kroger_provider=mock_kroger)
    item = GroceryItem(id="egg_1", name="Eggs", quantity=1)
    prices = await service.fetch_prices(item)

    kroger_prices = [p for p in prices if p.store_name == "Kroger"]
    assert len(kroger_prices) == 1
    assert kroger_prices[0].price == Decimal("1.49")


@pytest.mark.asyncio
async def test_optimise_max_stores_savings_reflects_assigned_store() -> None:
    """When max_stores forces a non-cheapest item, estimated_savings uses the assigned price."""
    mock_provider = AsyncMock()

    async def get_prices(item: GroceryItem, store_zip: str) -> list[StorePrice]:
        if item.name == "Milk":
            return [
                StorePrice(item_name="Milk", store_name="Store A", price=Decimal("2.00")),
                StorePrice(item_name="Milk", store_name="Store B", price=Decimal("5.00")),
            ]
        return [
            StorePrice(item_name="Bread", store_name="Store A", price=Decimal("10.00")),
            StorePrice(item_name="Bread", store_name="Store B", price=Decimal("1.00")),
        ]

    mock_provider.get_prices.side_effect = get_prices
    service = PricingService(store_zip="90210", provider=mock_provider)
    cart = await service.optimise(make_list(), max_stores=1)

    # Store B total: 2 * 5.00 + 1 * 1.00 = 11.00 (cheaper than Store A: 2*2.00 + 1*10.00 = 14.00)
    assert cart.stores_used == ["Store B"]
    assert cart.total_cost == Decimal("11.00")
    # Milk at Store B is the most expensive ($5.00), so savings on Milk is $0.00.
    # Bread at Store B saves $10.00 - $1.00 = $9.00.
    assert cart.estimated_savings == Decimal("9.00")


@pytest.mark.asyncio
async def test_optimise_max_stores_enforced_with_partial_coverage() -> None:
    """max_stores must never be exceeded even when no subset covers 100% of items."""
    mock_provider = AsyncMock()

    async def get_prices(item: GroceryItem, store_zip: str) -> list[StorePrice]:
        if item.name == "Milk":
            return [StorePrice(item_name="Milk", store_name="Store A", price=Decimal("3.00"))]
        return [StorePrice(item_name="Bread", store_name="Store B", price=Decimal("2.00"))]

    mock_provider.get_prices.side_effect = get_prices
    service = PricingService(store_zip="90210", provider=mock_provider)
    cart = await service.optimise(make_list(), max_stores=1)

    assert len(cart.stores_used) == 1


@pytest.mark.asyncio
async def test_optimise_invalid_max_stores_raises() -> None:
    """max_stores < 1 should raise ValueError."""
    service = PricingService(store_zip="90210")
    with pytest.raises(ValueError, match="at least 1"):
        await service.optimise(make_list(), max_stores=0)


@pytest.mark.asyncio
async def test_pricing_cache_isolated_by_unit_and_provider() -> None:
    """Cache keys should isolate different units and distinct provider instances."""
    provider_a = AsyncMock()
    provider_a.get_prices.return_value = [
        StorePrice(item_name="Milk", store_name="ALDI", price=Decimal("3.49"), unit=Unit.GALLON)
    ]
    provider_b = AsyncMock()
    provider_b.get_prices.return_value = [
        StorePrice(item_name="Milk", store_name="ALDI", price=Decimal("1.99"), unit=Unit.LITER)
    ]

    service_a = PricingService(store_zip="90210", provider=provider_a)
    service_b = PricingService(store_zip="90210", provider=provider_b)

    res_a = await service_a.fetch_prices(GroceryItem(id="1", name="Milk", unit=Unit.GALLON))
    res_b = await service_b.fetch_prices(GroceryItem(id="2", name="Milk", unit=Unit.LITER))

    assert res_a[0].price == Decimal("3.49")
    assert res_b[0].price == Decimal("1.99")
    assert provider_a.get_prices.call_count == 1
    assert provider_b.get_prices.call_count == 1


@pytest.mark.asyncio
async def test_kroger_provider_missing_token_returns_empty() -> None:
    """Missing access_token in Kroger OAuth response should fail gracefully in get_prices."""
    import httpx

    from grocery_helper.pricing import KrogerPricingProvider

    def handler(request: httpx.Request) -> httpx.Response:
        if "/connect/oauth2/token" in str(request.url):
            return httpx.Response(200, json={})
        return httpx.Response(404)

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = KrogerPricingProvider(
        client_id="cid",
        client_secret="sec",
        base_url="https://api.kroger.com/v1",
        http_client=mock_client,
    )
    prices = await provider.get_prices(GroceryItem(id="k1", name="Milk"), "45202")
    assert prices == []
    assert provider._access_token is None


@pytest.mark.asyncio
async def test_mock_provider_prefers_exact_pack_catalog_match() -> None:
    """'Soda 12pk' should match the 'soda 12pk' catalog price rather than generic 'soda'."""
    provider = MockPricingProvider()
    soda = await provider.get_prices(GroceryItem(id="s1", name="Soda", unit=Unit.PACK), "90210")
    soda_12pk = await provider.get_prices(
        GroceryItem(id="s2", name="Soda 12pk", unit=Unit.PACK), "90210"
    )
    assert soda_12pk != soda


@pytest.mark.asyncio
async def test_optimise_normalizes_convertible_units() -> None:
    """Optimiser should normalize weight/volume store prices to the requested item unit."""
    mock_provider = AsyncMock()
    mock_provider.get_prices.return_value = [
        StorePrice(item_name="Apples", store_name="Store A", price=Decimal("2.00"), unit=Unit.LB),
        # $0.25/oz = $4.00/lb
        StorePrice(item_name="Apples", store_name="Store B", price=Decimal("0.25"), unit=Unit.OZ),
    ]
    service = PricingService(store_zip="90210", provider=mock_provider)
    g_list = GroceryList(
        id="l_unit",
        name="Unit Test",
        items=[GroceryItem(id="a1", name="Apples", quantity=2.0, unit=Unit.LB)],
    )
    cart = await service.optimise(g_list)
    assert cart.store_assignments["a1"] == "Store A"
    assert cart.total_cost == Decimal("4.00")
    assert cart.estimated_savings == Decimal("4.00")


@pytest.mark.asyncio
async def test_kroger_provider_retries_on_401_and_parses_size() -> None:
    """KrogerPricingProvider should refresh expired tokens on 401 and parse size units."""
    import httpx

    from grocery_helper.pricing import KrogerPricingProvider

    token_calls = 0
    product_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_calls, product_calls
        url = str(request.url)
        if "/connect/oauth2/token" in url:
            token_calls += 1
            return httpx.Response(
                200, json={"access_token": f"tok_{token_calls}", "expires_in": 1800}
            )
        if "/locations" in url:
            return httpx.Response(200, json={"data": [{"locationId": "loc_1"}]})
        if "/products" in url:
            product_calls += 1
            if product_calls == 1:
                return httpx.Response(401, json={"error": "expired"})
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "items": [
                                {
                                    "size": "16 oz",
                                    "price": {"regular": 3.50},
                                }
                            ]
                        }
                    ]
                },
            )
        return httpx.Response(404)

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = KrogerPricingProvider(
        client_id="cid",
        client_secret="sec",
        base_url="https://api.kroger.com/v1",
        http_client=mock_client,
    )
    prices = await provider.get_prices(GroceryItem(id="k2", name="Cheese", unit=Unit.PACK), "45202")
    assert len(prices) == 1
    assert prices[0].unit == Unit.OZ
    assert token_calls == 2
    assert product_calls == 2


@pytest.mark.asyncio
async def test_kroger_provider_context_manager_lifecycle() -> None:
    """KrogerPricingProvider async context manager creates and closes its owned client."""
    from grocery_helper.pricing import KrogerPricingProvider

    provider = KrogerPricingProvider(client_id="cid", client_secret="sec")
    async with provider:
        owned = provider._owned_http
        assert owned is not None
        assert not owned.is_closed
    assert owned.is_closed
    assert provider._owned_http is None


@pytest.mark.asyncio
async def test_pricing_service_overlays_live_flyers_and_prewarms() -> None:
    """PricingService overlays live MB/WF flyer prices and pre-warms once per optimise run."""
    from grocery_helper.flyers import MarketBasketFlyerProvider, WholeFoodsFlyerProvider

    mb_provider = AsyncMock(spec=MarketBasketFlyerProvider)
    mb_provider.STORE_NAME = "Market Basket"
    mb_provider.fetch_flyer.return_value = []

    async def mb_get_prices(item: GroceryItem, store_zip: str) -> list[StorePrice]:
        if item.name == "Whole Milk":
            return [
                StorePrice(
                    item_name=item.name,
                    store_name="Market Basket",
                    store_id="store_market_basket",
                    price=Decimal("3.99"),
                    sale_price=Decimal("2.50"),
                    is_live=True,
                    matched_product_name="Market Basket Milk",
                    deal_details="Save $1.49",
                )
            ]
        return []

    mb_provider.get_prices.side_effect = mb_get_prices

    wf_provider = AsyncMock(spec=WholeFoodsFlyerProvider)
    wf_provider.STORE_NAME = "Whole Foods"
    wf_provider.store_id = "10010"
    wf_provider.use_prime = True
    wf_provider.fetch_flyer.return_value = []
    wf_provider.get_prices.return_value = []

    service = PricingService(
        store_zip="02139",
        enable_live_flyers=True,
        enable_flipp=False,
        market_basket_provider=mb_provider,
        whole_foods_provider=wf_provider,
    )
    g_list = GroceryList(
        id="list_mb",
        name="Weekly",
        items=[
            GroceryItem(id="1", name="Whole Milk", quantity=1),
            GroceryItem(id="2", name="Unlisted Item", quantity=1),
        ],
    )
    cart = await service.optimise(g_list)

    mb_provider.fetch_flyer.assert_awaited_once()
    wf_provider.fetch_flyer.assert_awaited_once()

    milk_comp = next(c for c in cart.comparisons if c.item.name == "Whole Milk")
    mb_milk = next(p for p in milk_comp.prices if p.store_name == "Market Basket")
    assert mb_milk.is_live is True
    assert mb_milk.sale_price == Decimal("2.50")

    # Non-flyer item still keeps Market Basket mock baseline when flyer_only_for_live_stores=False
    unlisted_comp = next(c for c in cart.comparisons if c.item.name == "Unlisted Item")
    assert any(p.store_name == "Market Basket" for p in unlisted_comp.prices)


@pytest.mark.asyncio
async def test_pricing_service_advertised_only_omits_unlisted_flyer_stores() -> None:
    """flyer_only_for_live_stores=True removes mock baselines for Market Basket & Whole Foods."""
    from grocery_helper.flyers import MarketBasketFlyerProvider, WholeFoodsFlyerProvider

    mb_provider = AsyncMock(spec=MarketBasketFlyerProvider)
    mb_provider.STORE_NAME = "Market Basket"
    mb_provider.fetch_flyer.return_value = []
    mb_provider.get_prices.return_value = []

    wf_provider = AsyncMock(spec=WholeFoodsFlyerProvider)
    wf_provider.STORE_NAME = "Whole Foods"
    wf_provider.store_id = "10010"
    wf_provider.use_prime = True
    wf_provider.fetch_flyer.return_value = []
    wf_provider.get_prices.return_value = [
        StorePrice(
            item_name="Organic Bananas",
            store_name="Whole Foods",
            store_id="whole_foods_10010",
            price=Decimal("0.99"),
            sale_price=Decimal("0.49"),
            is_live=True,
        )
    ]

    service = PricingService(
        store_zip="02139",
        enable_live_flyers=True,
        enable_flipp=False,
        flyer_only_for_live_stores=True,
        market_basket_provider=mb_provider,
        whole_foods_provider=wf_provider,
    )
    prices = await service.fetch_prices(GroceryItem(id="b1", name="Organic Bananas"))
    stores = {p.store_name for p in prices}

    assert "Market Basket" not in stores
    assert "Whole Foods" in stores
    wf_price = next(p for p in prices if p.store_name == "Whole Foods")
    assert wf_price.sale_price == Decimal("0.49")
    assert wf_price.is_live is True


@pytest.mark.asyncio
async def test_pricing_service_flipp_multi_store_overlay_and_precedence() -> None:
    """Flipp adds nearby stores while direct MB/Whole Foods flyers retain precedence."""
    from grocery_helper.flyers import (
        FlippFlyerProvider,
        MarketBasketFlyerProvider,
        WholeFoodsFlyerProvider,
    )

    mb_provider = AsyncMock(spec=MarketBasketFlyerProvider)
    mb_provider.STORE_NAME = "Market Basket"
    mb_provider.fetch_flyer.return_value = []
    mb_provider.get_prices.return_value = [
        StorePrice(
            item_name="Chicken Breast",
            store_name="Market Basket",
            store_id="store_market_basket",
            price=Decimal("2.99"),
            sale_price=Decimal("1.69"),
            unit=Unit.LB,
            is_live=True,
        )
    ]

    wf_provider = AsyncMock(spec=WholeFoodsFlyerProvider)
    wf_provider.STORE_NAME = "Whole Foods"
    wf_provider.store_id = "10010"
    wf_provider.use_prime = True
    wf_provider.fetch_flyer.return_value = []
    wf_provider.get_prices.return_value = []

    flipp_provider = AsyncMock(spec=FlippFlyerProvider)
    flipp_provider.postal_code = "02139"
    flipp_provider.merchants = None
    flipp_provider.fetch_flyer.return_value = []
    flipp_provider.get_prices.return_value = [
        StorePrice(
            item_name="Chicken Breast",
            store_name="Star Market",
            store_id="flipp_star_market_1",
            price=Decimal("4.99"),
            sale_price=Decimal("2.49"),
            unit=Unit.LB,
            is_live=True,
        ),
        StorePrice(
            item_name="Chicken Breast",
            store_name="Market Basket",
            store_id="flipp_mb_1",
            price=Decimal("3.99"),
            sale_price=Decimal("2.99"),
            unit=Unit.LB,
            is_live=True,
        ),
    ]

    service = PricingService(
        store_zip="02139",
        enable_live_flyers=True,
        enable_flipp=True,
        market_basket_provider=mb_provider,
        whole_foods_provider=wf_provider,
        flipp_provider=flipp_provider,
    )
    prices = await service.fetch_prices(
        GroceryItem(id="c1", name="Chicken Breast", quantity=2, unit=Unit.LB)
    )
    by_store = {p.store_name: p for p in prices}

    # Dynamic Flipp store added
    assert "Star Market" in by_store
    assert by_store["Star Market"].sale_price == Decimal("2.49")
    assert by_store["Star Market"].is_live is True
    # Direct Market Basket price overrides Flipp's Market Basket entry
    assert by_store["Market Basket"].sale_price == Decimal("1.69")
    assert by_store["Market Basket"].store_id == "store_market_basket"


def test_build_stop_count_summary_compares_stops_against_single_store() -> None:
    """build_stop_count_summary computes cost & savings vs best single store per stop count."""
    from grocery_helper.models import PriceComparison
    from grocery_helper.pricing import build_stop_count_summary, solve_cart_for_comparisons

    milk = GroceryItem(id="m1", name="Milk")
    bread = GroceryItem(id="b1", name="Bread")
    comps = [
        PriceComparison(
            item=milk,
            prices=[
                StorePrice(item_name="Milk", store_name="ALDI", price=Decimal("2.50")),
                StorePrice(item_name="Milk", store_name="Market Basket", price=Decimal("4.00")),
                StorePrice(item_name="Milk", store_name="Whole Foods", price=Decimal("5.00")),
            ],
        ),
        PriceComparison(
            item=bread,
            prices=[
                StorePrice(item_name="Bread", store_name="ALDI", price=Decimal("4.50")),
                StorePrice(item_name="Bread", store_name="Market Basket", price=Decimal("2.00")),
                StorePrice(item_name="Bread", store_name="Whole Foods", price=Decimal("5.00")),
            ],
        ),
    ]
    cart = solve_cart_for_comparisons("list_1", comps, max_stores=2)
    assert cart.total_cost == Decimal("4.50")
    assert cart.best_single_store == ("Market Basket", Decimal("6.00"))
    assert cart.savings_vs_best_single_store == Decimal("1.50")
    assert cart.savings_pct_vs_best_single_store == Decimal("25.0")

    rows = build_stop_count_summary(cart)
    assert len(rows) == 3
    assert rows[0]["stops"] == 1
    assert rows[0]["total_cost"] == Decimal("6.00")
    assert rows[0]["savings_vs_single"] == Decimal("0.00")
    assert rows[0]["is_selected"] is False

    assert rows[1]["stops"] == 2
    assert rows[1]["total_cost"] == Decimal("4.50")
    assert rows[1]["savings_vs_single"] == Decimal("1.50")
    assert rows[1]["savings_pct"] == Decimal("25.0")
    assert rows[1]["is_selected"] is True
