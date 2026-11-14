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

import asyncio
import base64
import contextlib
import contextvars
import hashlib
import itertools
import logging
import time
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Protocol

import httpx

from grocery_helper.config import settings
from grocery_helper.flyers import (
    BaseFlyerProvider,
    FlippFlyerProvider,
    MarketBasketFlyerProvider,
    WholeFoodsFlyerProvider,
)
from grocery_helper.matcher import find_best_match, parse_quantity_and_unit
from grocery_helper.models import (
    GroceryItem,
    GroceryList,
    OptimizedCart,
    PriceComparison,
    StorePrice,
    Unit,
)
from grocery_helper.open_prices import OpenPricesProvider
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
        "Market Basket": (Decimal("0.88"), None),
        "Trader Joe's": (Decimal("0.95"), None),
        "Kroger": (Decimal("1.02"), Decimal("0.82")),
        "Whole Foods": (Decimal("1.25"), Decimal("1.05")),
    }

    async def get_prices(self, item: GroceryItem, store_zip: str) -> list[StorePrice]:
        """Generate realistic prices for the requested item across configured stores."""
        _, parsed_unit, cleaned_name = parse_quantity_and_unit(item.name)
        matched_unit: Unit = parsed_unit or item.unit
        catalog_keys = list(self.KNOWN_CATALOG.keys())
        raw_cand, raw_score = find_best_match(item.name, catalog_keys)
        cleaned_cand, cleaned_score = find_best_match(cleaned_name, catalog_keys)
        best_cand: str | None
        score: float
        if raw_cand and raw_score >= cleaned_score:
            best_cand, score = raw_cand, raw_score
        else:
            best_cand, score = cleaned_cand, cleaned_score

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
        self._owned_http: httpx.AsyncClient | None = None
        self._shared_http_var: contextvars.ContextVar[httpx.AsyncClient | None] = (
            contextvars.ContextVar(f"kroger_shared_http_{id(self)}", default=None)
        )
        self._access_token: str | None = None
        self._token_expires_at: float = 0.0
        self._token_lock = asyncio.Lock()
        self._location_lock = asyncio.Lock()
        self._location_cache: dict[str, str] = {}

    async def __aenter__(self) -> KrogerPricingProvider:
        if self._http is None and self._owned_http is None:
            self._owned_http = httpx.AsyncClient(timeout=10.0)
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the internally managed HTTP client if one was created."""
        if self._owned_http is not None:
            await self._owned_http.aclose()
            self._owned_http = None

    @property
    def is_configured(self) -> bool:
        """Whether valid client credentials have been supplied."""
        return bool(self._client_id and self._client_secret)

    async def _get_token(self, client: httpx.AsyncClient, force_refresh: bool = False) -> str:
        """Acquire an OAuth2 bearer token using client_credentials grant."""
        async with self._token_lock:
            if (
                not force_refresh
                and self._access_token
                and time.monotonic() < self._token_expires_at
            ):
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

            payload = res.json()
            token_raw = payload.get("access_token")
            if not token_raw:
                raise RuntimeError("Kroger authentication response missing access_token")

            token = str(token_raw)
            expires_in = float(payload.get("expires_in", 1800))
            self._access_token = token
            self._token_expires_at = time.monotonic() + max(0.0, expires_in - 60.0)
            return token

    async def _authorized_get(
        self,
        client: httpx.AsyncClient,
        path: str,
        params: dict[str, Any],
    ) -> httpx.Response:
        """Send an authenticated GET request, refreshing the token once on 401."""
        token = await self._get_token(client)
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        res = await client.get(f"{self._base_url}{path}", params=params, headers=headers)
        if res.status_code == 401:
            token = await self._get_token(client, force_refresh=True)
            headers["Authorization"] = f"Bearer {token}"
            res = await client.get(f"{self._base_url}{path}", params=params, headers=headers)
        return res

    async def get_location_id(self, client: httpx.AsyncClient, store_zip: str) -> str | None:
        """Find the nearest Kroger store location identifier for the given ZIP code."""
        if store_zip in self._location_cache:
            return self._location_cache[store_zip]

        async with self._location_lock:
            if store_zip in self._location_cache:
                return self._location_cache[store_zip]

            res = await self._authorized_get(
                client,
                "/locations",
                params={"filter.zipCode.near": store_zip, "filter.limit": 1},
            )
            if res.status_code != 200:
                return None

            data = res.json().get("data", [])
            if not data:
                return None

            loc_raw = data[0].get("locationId")
            if not loc_raw:
                return None

            loc_id = str(loc_raw)
            self._location_cache[store_zip] = loc_id
            return loc_id

    async def get_prices(self, item: GroceryItem, store_zip: str) -> list[StorePrice]:
        """Fetch live item price from the nearest Kroger location."""
        if not self.is_configured:
            return []

        async def _query(client: httpx.AsyncClient) -> list[StorePrice]:
            location_id = await self.get_location_id(client, store_zip)
            if not location_id:
                return []

            res = await self._authorized_get(
                client,
                "/products",
                params={
                    "filter.term": item.name,
                    "filter.locationId": location_id,
                    "filter.limit": 1,
                },
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

            product_item = items[0]
            price_info = product_item.get("price", {})
            regular_price = price_info.get("regular")
            promo_price = price_info.get("promo")

            if regular_price is None:
                return []

            size_raw = product_item.get("size")
            _, parsed_unit, _ = (
                parse_quantity_and_unit(str(size_raw))
                if size_raw
                else (
                    None,
                    None,
                    "",
                )
            )
            unit = parsed_unit or item.unit

            regular_dec = Decimal(str(regular_price))
            promo_dec = Decimal(str(promo_price)) if promo_price is not None else None

            return [
                StorePrice(
                    item_name=item.name,
                    store_name="Kroger",
                    store_id=f"kroger_{location_id}",
                    price=regular_dec,
                    unit=unit,
                    sale_price=promo_dec,
                )
            ]

        try:
            active_client = self._shared_http_var.get() or self._http or self._owned_http
            if active_client is not None:
                return await _query(active_client)
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
        enable_live_flyers: bool | None = None,
        enable_flipp: bool | None = None,
        enable_open_prices: bool | None = None,
        flyer_only_for_live_stores: bool = False,
        whole_foods_store_id: str | None = None,
        whole_foods_prime: bool | None = None,
        flipp_merchants: list[str] | set[str] | None = None,
        market_basket_provider: MarketBasketFlyerProvider | None = None,
        whole_foods_provider: WholeFoodsFlyerProvider | None = None,
        flipp_provider: FlippFlyerProvider | None = None,
        open_prices_provider: OpenPricesProvider | None = None,
    ) -> None:
        self._store_zip = store_zip
        self._provider: BasePricingProvider = provider or MockPricingProvider()
        self._kroger_provider: KrogerPricingProvider = kroger_provider or KrogerPricingProvider()
        self._enable_live_flyers: bool = (
            settings.enable_live_flyers if enable_live_flyers is None else enable_live_flyers
        )
        self._enable_flipp: bool = settings.enable_flipp if enable_flipp is None else enable_flipp
        self._enable_open_prices: bool = (
            settings.enable_open_prices if enable_open_prices is None else enable_open_prices
        )
        self._flyer_only_for_live_stores = flyer_only_for_live_stores
        self._market_basket_provider: MarketBasketFlyerProvider = (
            market_basket_provider or MarketBasketFlyerProvider()
        )
        self._whole_foods_provider: WholeFoodsFlyerProvider = (
            whole_foods_provider
            or WholeFoodsFlyerProvider(
                store_id=whole_foods_store_id,
                use_prime=whole_foods_prime,
            )
        )
        self._flipp_provider: FlippFlyerProvider = flipp_provider or FlippFlyerProvider(
            postal_code=store_zip or "02139",
            merchants=flipp_merchants,
        )
        self._open_prices_provider: OpenPricesProvider = (
            open_prices_provider or OpenPricesProvider()
        )

    # ── Public API ────────────────────────────────────────────────────────────

    def _cache_key_for(self, item: GroceryItem) -> str:
        provider_tag = (
            "MockPricingProvider"
            if isinstance(self._provider, MockPricingProvider)
            else f"{type(self._provider).__qualname__}:{id(self._provider)}"
        )
        if self._kroger_provider.is_configured:
            kroger_tag = (
                "kroger_on"
                if type(self._kroger_provider) is KrogerPricingProvider
                else f"kroger_custom:{id(self._kroger_provider)}"
            )
        else:
            kroger_tag = "kroger_off"
        if self._enable_live_flyers:
            mb_tag = (
                "mb_std"
                if type(self._market_basket_provider) is MarketBasketFlyerProvider
                else f"mb_custom:{id(self._market_basket_provider)}"
            )
            wf_tag = (
                f"wf_std:{self._whole_foods_provider.store_id}:{self._whole_foods_provider.use_prime}"
                if type(self._whole_foods_provider) is WholeFoodsFlyerProvider
                else f"wf_custom:{id(self._whole_foods_provider)}"
            )
            if self._enable_flipp:
                merch_key = (
                    ",".join(sorted(self._flipp_provider.merchants))
                    if self._flipp_provider.merchants
                    else "all"
                )
                flipp_tag = (
                    f"flipp_std:{self._flipp_provider.postal_code}:{merch_key}"
                    if type(self._flipp_provider) is FlippFlyerProvider
                    else f"flipp_custom:{id(self._flipp_provider)}"
                )
            else:
                flipp_tag = "flipp_off"
            flyer_tag = (
                f"flyers_on:{mb_tag}:{wf_tag}:{flipp_tag}:{self._flyer_only_for_live_stores}"
            )
        else:
            flyer_tag = "flyers_off"
        if self._enable_open_prices:
            op_tag = (
                f"op_on:{self._open_prices_provider.base_url}"
                if type(self._open_prices_provider) is OpenPricesProvider
                else f"op_custom:{id(self._open_prices_provider)}"
            )
        else:
            op_tag = "op_off"
        return cache_key(
            provider_tag,
            kroger_tag,
            flyer_tag,
            op_tag,
            item.name,
            item.unit.value,
            self._store_zip,
        )

    @staticmethod
    async def _safe_flyer_prices(
        provider: BaseFlyerProvider,
        item: GroceryItem,
        store_zip: str,
    ) -> list[StorePrice]:
        """Fetch flyer overlay prices while isolating network/parse errors per source."""
        try:
            return await provider.get_prices(item, store_zip)
        except Exception as exc:
            logger.warning(
                "Flyer price lookup failed for %s (%s): %s",
                type(provider).__name__,
                item.name,
                exc,
            )
            return []

    @staticmethod
    async def _safe_warm_flyer(
        provider: BaseFlyerProvider,
    ) -> None:
        """Warm the flyer cache once before running concurrent item comparisons."""
        try:
            await provider.fetch_flyer()
        except Exception as exc:
            logger.warning("Pre-warming flyer failed for %s: %s", type(provider).__name__, exc)

    @staticmethod
    def _merge_store_prices(
        base: list[StorePrice],
        overlay: list[StorePrice],
        *,
        clear_stores: set[str] | None = None,
    ) -> list[StorePrice]:
        """Overlay live store prices onto base prices, replacing matching store names."""
        replaced_stores = {sp.store_name for sp in overlay}
        if clear_stores:
            replaced_stores |= clear_stores
        kept = [p for p in base if p.store_name not in replaced_stores]
        return kept + overlay

    async def fetch_prices(self, item: GroceryItem) -> list[StorePrice]:
        """
        Fetch prices for a single item from all available stores, using
        the in-memory TTL cache if available.
        """
        key = self._cache_key_for(item)
        cached = get_cached_price(key)
        if cached is not None and isinstance(cached, list):
            logger.debug("Cache hit for '%s' in ZIP %s", item.name, self._store_zip)
            return [
                sp.model_copy() if isinstance(sp, StorePrice) else StorePrice.model_validate(sp)
                for sp in cached
            ]

        logger.debug("Fetching prices for '%s' in ZIP %s", item.name, self._store_zip)
        prices = await self._provider.get_prices(item, self._store_zip)

        # Overlay crowdsourced Open Prices shelf data first so live official APIs
        # and current weekly flyers always take precedence for on-sale items.
        if self._enable_open_prices:
            try:
                open_live = await self._open_prices_provider.get_prices(item, self._store_zip)
            except Exception as exc:
                logger.warning("Open Prices lookup failed for '%s': %s", item.name, exc)
                open_live = []
            if open_live:
                prices = self._merge_store_prices(prices, open_live)

        # If Kroger live provider is configured, overlay live Kroger prices
        live_kroger: list[StorePrice] = []
        if self._kroger_provider.is_configured:
            live_kroger = await self._kroger_provider.get_prices(item, self._store_zip)
            if live_kroger:
                prices = self._merge_store_prices(prices, live_kroger)

        # Overlay live weekly sale flyers (Flipp generic aggregator, plus direct MB & Whole Foods)
        if self._enable_live_flyers:
            use_flipp = self._enable_flipp and bool(self._store_zip.strip())
            tasks: list[Any] = [
                self._safe_flyer_prices(self._market_basket_provider, item, self._store_zip),
                self._safe_flyer_prices(self._whole_foods_provider, item, self._store_zip),
            ]
            if use_flipp:
                tasks.append(self._safe_flyer_prices(self._flipp_provider, item, self._store_zip))
            results = await asyncio.gather(*tasks)
            mb_live: list[StorePrice] = results[0]
            wf_live: list[StorePrice] = results[1]
            flipp_live: list[StorePrice] = results[2] if use_flipp else []

            # Apply Flipp first, then let store-specific direct providers take precedence
            if flipp_live:
                skip_flipp = {sp.store_name for sp in live_kroger}
                filtered_flipp = [sp for sp in flipp_live if sp.store_name not in skip_flipp]
                if filtered_flipp:
                    prices = self._merge_store_prices(prices, filtered_flipp)

            clear_direct = (
                {MarketBasketFlyerProvider.STORE_NAME, WholeFoodsFlyerProvider.STORE_NAME}
                if self._flyer_only_for_live_stores
                else None
            )
            prices = self._merge_store_prices(
                prices,
                mb_live + wf_live,
                clear_stores=clear_direct,
            )

        set_cached_price(key, [sp.model_copy() for sp in prices])
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
                size that maximizes covered items and minimizes total cart cost.

        Returns an OptimizedCart with store assignments and basket totals.
        """
        if max_stores is not None and max_stores < 1:
            raise ValueError("max_stores must be at least 1")

        allowed_set = set(allowed_stores) if allowed_stores is not None else None

        if self._enable_live_flyers:
            await asyncio.gather(
                self._safe_warm_flyer(self._market_basket_provider),
                self._safe_warm_flyer(self._whole_foods_provider),
            )

        # Fetch comparisons concurrently across all items, sharing HTTP clients
        # for Kroger and Open Prices when enabled.
        share_kroger = (
            isinstance(self._kroger_provider, KrogerPricingProvider)
            and self._kroger_provider.is_configured
            and self._kroger_provider._http is None
            and self._kroger_provider._owned_http is None
            and self._kroger_provider._shared_http_var.get() is None
        )
        share_open_prices = (
            self._enable_open_prices
            and isinstance(self._open_prices_provider, OpenPricesProvider)
            and self._open_prices_provider._http is None
            and self._open_prices_provider._shared_http_var.get() is None
        )
        async with contextlib.AsyncExitStack() as stack:
            if share_kroger or share_open_prices:
                shared_client = await stack.enter_async_context(
                    httpx.AsyncClient(timeout=12.0, follow_redirects=True)
                )
                if share_kroger:
                    k_tok = self._kroger_provider._shared_http_var.set(shared_client)
                    stack.callback(self._kroger_provider._shared_http_var.reset, k_tok)
                if share_open_prices:
                    op_tok = self._open_prices_provider._shared_http_var.set(shared_client)
                    stack.callback(self._open_prices_provider._shared_http_var.reset, op_tok)

            raw_comparisons = await asyncio.gather(
                *(self.compare(item) for item in grocery_list.items)
            )

        comparisons: list[PriceComparison] = []
        for comparison in raw_comparisons:
            if allowed_set is not None:
                filtered_prices = [p for p in comparison.prices if p.store_name in allowed_set]
                comparison = PriceComparison(item=comparison.item, prices=filtered_prices)
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
                store_totals[sp.store_name] += comp.normalized_price(sp) * qty
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
        if max_stores is not None and len(candidate_stores) > max_stores:
            best_covered_count = -1
            best_subset_cost: Decimal | None = None
            best_subset: tuple[str, ...] | None = None

            for store_combo in itertools.combinations(candidate_stores, max_stores):
                combo_set = set(store_combo)
                combo_cost = Decimal("0.00")
                covered_count = 0

                for comp in comparisons:
                    avail = [p for p in comp.prices if p.store_name in combo_set]
                    if not avail:
                        continue
                    covered_count += 1
                    cheapest_in_combo = min(avail, key=comp.normalized_price)
                    combo_cost += comp.normalized_price(cheapest_in_combo) * Decimal(
                        str(comp.item.quantity)
                    )

                is_better = covered_count > best_covered_count or (
                    covered_count == best_covered_count
                    and (best_subset_cost is None or combo_cost < best_subset_cost)
                )
                if is_better:
                    best_covered_count = covered_count
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
                cheapest_sp = min(eligible_prices, key=comp.normalized_price)
                cheapest_norm = comp.normalized_price(cheapest_sp)
                store_assignments[comp.item.id] = cheapest_sp.store_name
                total_cost += cheapest_norm * qty
                if len(comp.prices) >= 2:
                    most_expensive_sp = max(comp.prices, key=comp.normalized_price)
                    item_savings = max(
                        Decimal("0.00"),
                        comp.normalized_price(most_expensive_sp) - cheapest_norm,
                    )
                    estimated_savings += item_savings * qty

        return OptimizedCart(
            list_id=grocery_list.id,
            store_assignments=store_assignments,
            total_cost=total_cost,
            estimated_savings=estimated_savings,
            comparisons=comparisons,
            single_store_totals=single_store_totals,
            max_stores_constraint=max_stores,
        )
