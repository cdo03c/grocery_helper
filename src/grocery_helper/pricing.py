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

import base64
import hashlib
import itertools
import logging
from decimal import ROUND_HALF_UP, Decimal
from typing import Protocol

import httpx

from grocery_helper.config import settings
from grocery_helper.matcher import find_best_match, parse_quantity_and_unit
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
        parsed_qty, parsed_unit, cleaned_name = parse_quantity_and_unit(item.name)
        matched_unit: Unit = parsed_unit or item.unit
        best_cand, score = find_best_match(cleaned_name, list(self.KNOWN_CATALOG.keys()))

        if best_cand and score >= 0.35:
            base_price, catalog_unit = self.KNOWN_CATALOG[best_cand]
            if parsed_unit is None:
                matched_unit = catalog_unit
        else:
            item_lower = cleaned_name.lower().strip()
            hash_val = int(hashlib.md5(item_lower.encode()).hexdigest()[:6], 16)
            cents = 199 + (hash_val % 600)
            base_price = Decimal(f"{cents / 100:.2f}")

        prices: list[StorePrice] = []
        for store_name, (multiplier, sale_mult) in self.STORE_PROFILES.items():
            # Small store-specific variation derived from store name + item
            seed = int(
                hashlib.md5(f"{store_name}:{cleaned_name.lower()}".encode()).hexdigest()[:4], 16
            )
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


class KrogerPricingProvider:
    """
    Live store pricing provider using the Kroger Developer API.

    API docs: https://developer.kroger.com
    Requires client_id and client_secret from Kroger Developer Portal.
    Uses OAuth2 client_credentials flow to query store locations and live item pricing.
    """

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        base_url: str | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._client_id = client_id if client_id is not None else settings.kroger_client_id
        self._client_secret = (
            client_secret if client_secret is not None else settings.kroger_client_secret
        )
        self._base_url = (base_url or settings.kroger_api_base_url).rstrip("/")
        self._http = http_client
        self._access_token: str | None = None
        self._location_cache: dict[str, str] = {}

    @property
    def is_configured(self) -> bool:
        """Whether valid client credentials have been supplied."""
        return bool(self._client_id and self._client_secret)

    async def _get_token(self, client: httpx.AsyncClient) -> str:
        """Acquire an OAuth2 bearer token using client_credentials grant."""
        if self._access_token:
            return self._access_token

        creds = f"{self._client_id}:{self._client_secret}"
        encoded = base64.b64encode(creds.encode()).decode()
        headers = {
            "Authorization": f"Basic {encoded}",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        res = await client.post(
            f"{self._base_url}/connect/oauth2/token",
            data={"grant_type": "client_credentials", "scope": "product.compact"},
            headers=headers,
        )
        if res.status_code != 200:
            raise RuntimeError(
                f"Kroger authentication failed with status {res.status_code}: {res.text}"
            )

        token = str(res.json().get("access_token"))
        self._access_token = token
        return token

    async def get_location_id(self, client: httpx.AsyncClient, store_zip: str) -> str | None:
        """Find the nearest Kroger store location identifier for the given ZIP code."""
        if store_zip in self._location_cache:
            return self._location_cache[store_zip]

        token = await self._get_token(client)
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        res = await client.get(
            f"{self._base_url}/locations",
            params={"filter.zipCode.near": store_zip, "filter.limit": 1},
            headers=headers,
        )
        if res.status_code != 200:
            return None

        data = res.json().get("data", [])
        if not data:
            return None

        loc_id = str(data[0].get("locationId"))
        self._location_cache[store_zip] = loc_id
        return loc_id

    async def get_prices(self, item: GroceryItem, store_zip: str) -> list[StorePrice]:
        """Fetch live item price from the nearest Kroger location."""
        if not self.is_configured:
            return []

        async def _query(client: httpx.AsyncClient) -> list[StorePrice]:
            token = await self._get_token(client)
            location_id = await self.get_location_id(client, store_zip)
            if not location_id:
                return []

            headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
            res = await client.get(
                f"{self._base_url}/products",
                params={
                    "filter.term": item.name,
                    "filter.locationId": location_id,
                    "filter.limit": 1,
                },
                headers=headers,
            )
            if res.status_code != 200:
                return []

            products = res.json().get("data", [])
            if not products:
                return []

            product = products[0]
            items = product.get("items", [])
            if not items:
                return []

            price_info = items[0].get("price", {})
            regular_price = price_info.get("regular")
            promo_price = price_info.get("promo")

            if regular_price is None:
                return []

            regular_dec = Decimal(str(regular_price))
            promo_dec = Decimal(str(promo_price)) if promo_price is not None else None

            return [
                StorePrice(
                    item_name=item.name,
                    store_name="Kroger",
                    store_id=f"kroger_{location_id}",
                    price=regular_dec,
                    unit=item.unit,
                    sale_price=promo_dec,
                )
            ]

        try:
            if self._http is not None:
                return await _query(self._http)
            async with httpx.AsyncClient(timeout=10.0) as client:
                return await _query(client)
        except Exception as exc:
            logger.warning("Error querying Kroger API for '%s': %s", item.name, exc)
            return []


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
        kroger_provider: KrogerPricingProvider | None = None,
    ) -> None:
        self._store_zip = store_zip
        self._provider: BasePricingProvider = provider or MockPricingProvider()
        self._kroger_provider: KrogerPricingProvider = kroger_provider or KrogerPricingProvider()

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
            return list(cached)

        logger.debug("Fetching prices for '%s' in ZIP %s", item.name, self._store_zip)
        prices = await self._provider.get_prices(item, self._store_zip)

        # If Kroger live provider is configured, overlay live Kroger prices
        if self._kroger_provider.is_configured:
            live_kroger = await self._kroger_provider.get_prices(item, self._store_zip)
            if live_kroger:
                prices = [p for p in prices if p.store_name != "Kroger"] + live_kroger

        set_cached_price(key, prices)
        return prices

    async def compare(self, item: GroceryItem) -> PriceComparison:
        """Return a PriceComparison for a single item across all stores."""
        prices = await self.fetch_prices(item)
        return PriceComparison(item=item, prices=prices)

    async def optimise(
        self,
        grocery_list: GroceryList,
        allowed_stores: list[str] | set[str] | None = None,
        max_stores: int | None = None,
    ) -> OptimizedCart:
        """
        Run the optimiser over an entire grocery list.

        Args:
            grocery_list: The list of grocery items to optimise.
            allowed_stores: Optional list or set of store names to restrict search to.
            max_stores: Maximum number of distinct stores to assign items to.
                If set (e.g. 1 or 2), finds the combination of stores of that
                size that minimizes total cart cost.

        Returns an OptimizedCart with store assignments and basket totals.
        """
        comparisons: list[PriceComparison] = []
        allowed_set = set(allowed_stores) if allowed_stores is not None else None

        # Fetch comparisons for all items
        for item in grocery_list.items:
            comparison = await self.compare(item)
            if allowed_set is not None:
                filtered_prices = [p for p in comparison.prices if p.store_name in allowed_set]
                comparison = PriceComparison(item=item, prices=filtered_prices)
            comparisons.append(comparison)

        # Track single-store totals (across all allowed stores that carry all items)
        store_totals: dict[str, Decimal] = {}
        store_item_counts: dict[str, int] = {}
        total_items = len(grocery_list.items)

        for comp in comparisons:
            qty = Decimal(str(comp.item.quantity))
            for sp in comp.prices:
                store_totals.setdefault(sp.store_name, Decimal("0.00"))
                store_item_counts.setdefault(sp.store_name, 0)
                store_totals[sp.store_name] += sp.effective_price * qty
                store_item_counts[sp.store_name] += 1

        single_store_totals = (
            {
                store: cost
                for store, cost in store_totals.items()
                if store_item_counts[store] == total_items
            }
            if total_items > 0
            else {}
        )

        # Determine store candidates
        candidate_stores = sorted({sp.store_name for comp in comparisons for sp in comp.prices})

        # Apply max_stores constraint if requested and fewer than candidate count
        selected_store_subset: set[str] | None = None
        if max_stores is not None and max_stores > 0 and len(candidate_stores) > max_stores:
            best_subset_cost: Decimal | None = None
            best_subset: tuple[str, ...] | None = None

            for store_combo in itertools.combinations(candidate_stores, max_stores):
                combo_set = set(store_combo)
                combo_cost = Decimal("0.00")
                combo_covers_all = True

                for comp in comparisons:
                    avail = [p for p in comp.prices if p.store_name in combo_set]
                    if not avail:
                        combo_covers_all = False
                        break
                    cheapest_in_combo = min(avail, key=lambda p: p.effective_price)
                    combo_cost += cheapest_in_combo.effective_price * Decimal(
                        str(comp.item.quantity)
                    )

                if combo_covers_all and (best_subset_cost is None or combo_cost < best_subset_cost):
                    best_subset_cost = combo_cost
                    best_subset = store_combo

            if best_subset is not None:
                selected_store_subset = set(best_subset)

        store_assignments: dict[str, str] = {}
        total_cost = Decimal("0.00")
        estimated_savings = Decimal("0.00")

        for comp in comparisons:
            qty = Decimal(str(comp.item.quantity))
            eligible_prices = (
                [p for p in comp.prices if p.store_name in selected_store_subset]
                if selected_store_subset is not None
                else comp.prices
            )

            if eligible_prices:
                cheapest_sp = min(eligible_prices, key=lambda p: p.effective_price)
                store_assignments[comp.item.id] = cheapest_sp.store_name
                total_cost += cheapest_sp.effective_price * qty
                if comp.savings:
                    estimated_savings += comp.savings * qty

        return OptimizedCart(
            list_id=grocery_list.id,
            store_assignments=store_assignments,
            total_cost=total_cost,
            estimated_savings=estimated_savings,
            comparisons=comparisons,
            single_store_totals=single_store_totals,
            max_stores_constraint=max_stores,
        )
