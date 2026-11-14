"""
open_prices.py
--------------
Crowdsourced retail grocery pricing client powered by Open Food Facts'
Open Prices API (https://prices.openfoodfacts.org/api/v1).

Open Prices collects receipt- and shelf-tag-verified prices across brick-and-mortar
stores (e.g., Whole Foods Market, Market Basket, Trader Joe's, ALDI, Target,
Costco, Walmart), helping fill in everyday non-sale shelf prices alongside
weekly advertised circulars.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from grocery_helper import __version__
from grocery_helper.config import settings
from grocery_helper.flyers import score_flyer_match
from grocery_helper.matcher import normalize_item_name, parse_quantity_and_unit
from grocery_helper.models import GroceryItem, StorePrice, Unit
from grocery_helper.utils import cache_key, get_cached_price, set_cached_price

logger = logging.getLogger(__name__)

_DEFAULT_HEADERS = {
    "User-Agent": f"grocery_helper/{__version__} (https://github.com/cuyler/grocery_helper)",
    "Accept": "application/json",
}

_KG_TO_LB = Decimal("2.2046226218")

# Canonicalize OpenStreetMap store/brand names returned by Open Prices so they
# cleanly match store names used by MockPricingProvider and weekly flyer providers.
_STORE_ALIASES: dict[str, str] = {
    "whole foods market": "Whole Foods",
    "whole foods": "Whole Foods",
    "market basket": "Market Basket",
    "demoulas market basket": "Market Basket",
    "trader joe's": "Trader Joe's",
    "trader joes": "Trader Joe's",
    "aldi": "ALDI",
    "kroger": "Kroger",
    "star market": "Star Market",
    "shaw's": "Shaw's",
    "shaws": "Shaw's",
    "stop & shop": "Stop & Shop",
    "stop and shop": "Stop & Shop",
    "hannaford": "Hannaford",
    "wegmans": "Wegmans",
    "big y": "Big Y",
    "walmart": "Walmart",
    "walmart supercenter": "Walmart",
    "walmart neighborhood market": "Walmart",
    "target": "Target",
    "costco": "Costco",
    "costco wholesale": "Costco",
}

# Common grocery staples -> Open Food Facts taxonomy tags for fallback discovery
# when product-name search primarily returns non-US barcode records.
_CATEGORY_TAG_HINTS: dict[str, str] = {
    "milk": "en:milks",
    "whole milk": "en:whole-milks",
    "skim milk": "en:skimmed-milks",
    "almond milk": "en:almond-milks",
    "oat milk": "en:oat-milks",
    "eggs": "en:eggs",
    "bread": "en:breads",
    "sourdough": "en:sourdough-breads",
    "butter": "en:butters",
    "cheese": "en:cheeses",
    "cheddar": "en:cheddar",
    "yogurt": "en:yogurts",
    "greek yogurt": "en:greek-style-yogurts",
    "apples": "en:apples",
    "bananas": "en:bananas",
    "strawberries": "en:strawberries",
    "olive oil": "en:olive-oils",
    "coffee": "en:coffees",
    "orange juice": "en:orange-juices",
    "chicken": "en:chicken-breasts",
    "pasta": "en:pastas",
}


def normalize_open_prices_store(osm_name: str | None, osm_brand: str | None = None) -> str:
    """Normalize an Open Prices OSM store name/brand into a canonical chain name."""
    raw = (osm_brand or osm_name or "").strip()
    cleaned = " ".join(raw.split())
    if not cleaned:
        return ""
    lowered = cleaned.lower()
    if lowered in _STORE_ALIASES:
        return _STORE_ALIASES[lowered]
    for prefix, canonical in _STORE_ALIASES.items():
        if lowered.startswith(f"{prefix} "):
            return canonical
    return cleaned


def _to_decimal(val: Any) -> Decimal | None:
    """Safely convert numeric or string values into a positive quantized Decimal."""
    if val is None or isinstance(val, bool):
        return None
    try:
        dec = Decimal(str(val))
    except (InvalidOperation, ValueError):
        return None
    if dec <= 0:
        return None
    return dec.quantize(Decimal("0.01"))


def _zip_proximity_bonus(store_zip: str, candidate_postcode: str) -> float:
    """Score geographic proximity between user ZIP and an Open Prices location."""
    target = re.sub(r"\D", "", store_zip)[:5]
    cand = re.sub(r"\D", "", candidate_postcode)[:5]
    if len(target) < 3 or len(cand) < 3:
        return 0.0
    if len(target) == 5 and cand == target:
        return 0.30
    if cand[:3] == target[:3]:
        return 0.15
    if cand[:1] == target[:1]:
        return 0.05
    return 0.0


@dataclass
class _RankedPrice:
    rank: tuple[float, str]
    store_price: StorePrice


class OpenPricesProvider:
    """
    Async pricing provider querying Open Food Facts' Open Prices REST API.

    API docs: https://prices.openfoodfacts.org/api/docs
    """

    SOURCE_NAME = "Open Prices"

    def __init__(
        self,
        base_url: str | None = None,
        http_client: httpx.AsyncClient | None = None,
        min_score: float = 0.45,
        currency: str = "USD",
    ) -> None:
        self._base_url = (base_url or settings.open_prices_base_url).rstrip("/")
        self._http = http_client
        self._min_score = min_score
        self._currency = currency.upper()
        self._shared_http_var: contextvars.ContextVar[httpx.AsyncClient | None] = (
            contextvars.ContextVar(f"open_prices_shared_http_{id(self)}", default=None)
        )

    @property
    def base_url(self) -> str:
        """Configured Open Prices API base URL."""
        return self._base_url

    @asynccontextmanager
    async def _get_client(self) -> Any:
        """Yield injected, context-shared, or freshly created AsyncClient."""
        if self._http is not None:
            yield self._http
            return
        shared = self._shared_http_var.get()
        if shared is not None:
            yield shared
            return
        async with httpx.AsyncClient(
            timeout=12.0,
            follow_redirects=True,
            headers=_DEFAULT_HEADERS,
        ) as client:
            yield client

    async def _get_json(
        self,
        client: httpx.AsyncClient,
        path: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """Issue a GET request to Open Prices and return parsed JSON (empty dict on error)."""
        url = f"{self._base_url}{path}"
        try:
            res = await client.get(url, params=params, headers=_DEFAULT_HEADERS)
        except Exception as exc:
            logger.warning("Open Prices request failed (%s): %s", path, exc)
            return {}
        if res.status_code != 200:
            logger.warning("Open Prices %s returned status %s", path, res.status_code)
            return {}
        try:
            payload = res.json()
        except ValueError as exc:
            logger.warning("Invalid JSON from Open Prices %s: %s", path, exc)
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _category_hint_for(query: str) -> str | None:
        """Resolve an Open Food Facts category tag for common staple queries."""
        norm = normalize_item_name(query)
        if norm in _CATEGORY_TAG_HINTS:
            return _CATEGORY_TAG_HINTS[norm]
        for key, tag in _CATEGORY_TAG_HINTS.items():
            if key in norm.split():
                return tag
        return None

    async def fetch_raw_prices(
        self,
        query: str,
        client: httpx.AsyncClient | None = None,
    ) -> list[dict[str, Any]]:
        """
        Search Open Prices for USD records matching *query*.

        Combines matching product barcodes (``/products`` -> ``/prices?product_code__in=...``)
        with category-tag fallback for staple groceries, caching raw API items.
        """
        cleaned = query.strip()
        if not cleaned:
            return []

        c_key = cache_key("open_prices", self._base_url, self._currency, cleaned.lower())
        cached = get_cached_price(c_key)
        if cached is not None and isinstance(cached, list):
            return list(cached)

        async def _run(active_client: httpx.AsyncClient) -> list[dict[str, Any]]:
            category_tag = self._category_hint_for(cleaned)
            tasks: list[Any] = [
                self._get_json(
                    active_client,
                    "/products",
                    {
                        "product_name__like": cleaned,
                        "price_count__gte": 1,
                        "order_by": "-unique_scans_n",
                        "size": 15,
                    },
                )
            ]
            if category_tag:
                tasks.append(
                    self._get_json(
                        active_client,
                        "/prices",
                        {
                            "currency": self._currency,
                            "product__categories_tags__contains": category_tag,
                            "order_by": "-date",
                            "size": 40,
                        },
                    )
                )

            results = await asyncio.gather(*tasks)
            prod_payload = results[0]
            cat_items: list[dict[str, Any]] = (
                list(results[1].get("items") or []) if len(results) > 1 else []
            )

            codes = [str(p.get("code")) for p in (prod_payload.get("items") or []) if p.get("code")]
            code_items: list[dict[str, Any]] = []
            if codes:
                prices_payload = await self._get_json(
                    active_client,
                    "/prices",
                    {
                        "currency": self._currency,
                        "product_code__in": ",".join(codes),
                        "order_by": "-date",
                        "size": 50,
                    },
                )
                code_items = list(prices_payload.get("items") or [])

            # Deduplicate by price record ID, preferring product-code matches first.
            seen_ids: set[Any] = set()
            merged: list[dict[str, Any]] = []
            for entry in code_items + cat_items:
                if not isinstance(entry, dict):
                    continue
                rec_id = entry.get("id")
                if rec_id is not None and rec_id in seen_ids:
                    continue
                if rec_id is not None:
                    seen_ids.add(rec_id)
                merged.append(entry)
            return merged

        if client is not None:
            raw_items = await _run(client)
        else:
            async with self._get_client() as active_client:
                raw_items = await _run(active_client)

        set_cached_price(c_key, raw_items)
        return raw_items

    def _entry_to_store_price(
        self,
        item: GroceryItem,
        cleaned_query: str,
        entry: dict[str, Any],
        store_zip: str,
    ) -> tuple[str, _RankedPrice] | None:
        """Convert a raw Open Prices JSON entry into a scored StorePrice, or None if invalid."""
        if str(entry.get("currency") or "").upper() != self._currency:
            return None

        location = entry.get("location") or {}
        if not isinstance(location, dict):
            return None
        country_code = str(location.get("osm_address_country_code") or "").upper()
        if country_code and country_code != "US" and self._currency == "USD":
            return None

        store_name = normalize_open_prices_store(
            location.get("osm_name"),
            location.get("osm_brand"),
        )
        if not store_name:
            return None

        product = entry.get("product") or {}
        if not isinstance(product, dict):
            product = {}
        matched_name = str(product.get("product_name") or entry.get("product_name") or "").strip()
        if not matched_name:
            return None

        title_score = max(
            score_flyer_match(item.name, matched_name),
            score_flyer_match(cleaned_query, matched_name),
        )
        if title_score < self._min_score:
            return None

        current_price = _to_decimal(entry.get("price"))
        if current_price is None:
            return None

        unit = Unit.EACH
        price_per = str(entry.get("price_per") or "").upper()
        if price_per == "KILOGRAM":
            current_price = (current_price / _KG_TO_LB).quantize(Decimal("0.01"))
            unit = Unit.LB

        regular_price = current_price
        sale_price: Decimal | None = None
        if entry.get("price_is_discounted"):
            orig = _to_decimal(entry.get("price_without_discount"))
            if orig is not None:
                if price_per == "KILOGRAM":
                    orig = (orig / _KG_TO_LB).quantize(Decimal("0.01"))
                if orig > current_price:
                    regular_price = orig
                    sale_price = current_price

        postcode = str(location.get("osm_address_postcode") or "").strip()
        date_str = str(entry.get("date") or "").strip()
        detail_parts = [self.SOURCE_NAME]
        if date_str:
            detail_parts.append(date_str)
        if postcode:
            detail_parts.append(f"ZIP {postcode}")
        if sale_price is not None:
            detail_parts.append(f"Sale (was ${regular_price})")

        prox = _zip_proximity_bonus(store_zip, postcode)
        store_id = str(location.get("id")) if location.get("id") is not None else None

        sp = StorePrice(
            item_name=item.name,
            store_name=store_name,
            store_id=store_id,
            price=regular_price,
            unit=unit,
            sale_price=sale_price,
            is_live=True,
            matched_product_name=matched_name,
            deal_details=" • ".join(detail_parts),
        )
        ranked = _RankedPrice(rank=(round(title_score + prox, 4), date_str), store_price=sp)
        return store_name, ranked

    async def get_prices(self, item: GroceryItem, store_zip: str = "") -> list[StorePrice]:
        """Return best crowdsourced Open Prices match per store chain for *item*."""
        _, _, cleaned_query = parse_quantity_and_unit(item.name)
        lookup_term = cleaned_query or item.name.strip()
        if not lookup_term:
            return []

        raw_entries = await self.fetch_raw_prices(lookup_term)
        best_by_store: dict[str, _RankedPrice] = {}
        for entry in raw_entries:
            parsed = self._entry_to_store_price(item, lookup_term, entry, store_zip)
            if parsed is None:
                continue
            store_name, candidate = parsed
            prev = best_by_store.get(store_name)
            if prev is None or candidate.rank > prev.rank:
                best_by_store[store_name] = candidate

        return [best_by_store[name].store_price for name in sorted(best_by_store)]
