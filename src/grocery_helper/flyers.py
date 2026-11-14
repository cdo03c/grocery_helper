"""
flyers.py
---------
Live weekly flyer / advertised sales clients.

Sources:
- Market Basket: https://www.shopmarketbasket.com/wp-json/mb/v1/weekly-flyer
- Whole Foods Market: https://www.wholefoodsmarket.com/sales-flyer?store-id=<id>
- Flipp (multi-store aggregator by ZIP): https://backflipp.wishabi.com/flipp
"""

from __future__ import annotations

import asyncio
import difflib
import json
import logging
import re
from contextlib import asynccontextmanager
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Protocol

import httpx

from grocery_helper.config import settings
from grocery_helper.matcher import normalize_item_name, parse_quantity_and_unit
from grocery_helper.models import FlyerDeal, GroceryItem, StorePrice, Unit
from grocery_helper.utils import cache_key, get_cached_price, set_cached_price

logger = logging.getLogger(__name__)

_DEFAULT_HEADERS = {
    "User-Agent": "GroceryHelper/0.1",
    "Accept": "application/json, text/html;q=0.9",
}

# Common Whole Foods stores (store_id -> (display_name, zip_code))
WHOLE_FOODS_STORES: dict[str, tuple[str, str]] = {
    "10010": ("Cambridge (River St)", "02139"),
    "10380": ("Medford", "02155"),
    "10090": ("Bedford", "01730"),
    "10156": ("Framingham", "01701"),
    "10371": ("Andover", "01810"),
    "10216": ("Westlake (Seattle)", "98109"),
    "10492": ("Albany", "12205"),
}


# ---------------------------------------------------------------------------
# Price & text parsing helpers
# ---------------------------------------------------------------------------


def _detect_unit_hint(text: str) -> Unit | None:
    """Detect unit hints like '/lb', 'lb.', or 'ea' in a price string."""
    lower = text.lower()
    if re.search(r"(?:/|\b)lbs?\.?\b", lower):
        return Unit.LB
    if re.search(r"(?:/|\b)oz\.?\b", lower):
        return Unit.OZ
    if re.search(r"(?:/|\b)(?:ea|each)\b", lower):
        return Unit.EACH
    if re.search(r"(?:/|\b)gal(?:lon)?\b", lower):
        return Unit.GALLON
    return None


def parse_price_expression(raw: str) -> tuple[Decimal | None, Unit | None, str | None, int]:
    """
    Parse advertised flyer price expressions into (unit_price, unit, deal_label, bundle_count).

    Examples:
        "$5.99"          -> (Decimal("5.99"), None, None, 1)
        "99¢"            -> (Decimal("0.99"), None, None, 1)
        "1.69 lb."       -> (Decimal("1.69"), Unit.LB, None, 1)
        "2 for $5"       -> (Decimal("2.50"), None, "2 for $5", 2)
        "4 for $5.56"    -> (Decimal("1.39"), None, "4 for $5.56", 4)
        "$2.49 to $2.79/lb" -> (Decimal("2.49"), Unit.LB, None, 1)
    """
    text = raw.strip()
    if not text:
        return None, None, None, 1

    unit = _detect_unit_hint(text)

    # Multi-buy: "2 for $5", "3 for $5.56", "2/$5"
    multi_match = re.search(
        r"(\d+)\s*(?:for|/)\s*\$?\s*(\d+(?:\.\d{1,2})?)",
        text,
        re.IGNORECASE,
    )
    if multi_match:
        try:
            count = int(multi_match.group(1))
            total = Decimal(multi_match.group(2))
            if count > 0 and total >= 0:
                unit_price = (total / Decimal(count)).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
                return unit_price, unit, text, count
        except (InvalidOperation, ZeroDivisionError):
            pass

    # Cents: "99¢", "49¢ lb."
    cents_match = re.search(r"(\d+)\s*¢", text)
    if cents_match:
        try:
            cents = Decimal(cents_match.group(1))
            return (cents / Decimal("100")).quantize(Decimal("0.01")), unit, None, 1
        except InvalidOperation:
            pass

    # Standard dollar amount or range: "$5.99", "2.99", "$2.49 to $2.79/lb"
    dollar_match = re.search(r"\$?\s*(\d+(?:\.\d{1,2})?)", text)
    if dollar_match and "%" not in text:
        try:
            amount = Decimal(dollar_match.group(1))
            return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), unit, None, 1
        except InvalidOperation:
            pass

    return None, unit, None, 1


def parse_discount_percent(raw: str) -> Decimal | None:
    """Parse percentage-off strings like '20% off' into Decimal('0.20')."""
    match = re.search(r"(\d+(?:\.\d+)?)\s*%\s*off", raw, re.IGNORECASE)
    if not match:
        return None
    try:
        pct = Decimal(match.group(1)) / Decimal("100")
        if Decimal("0") <= pct <= Decimal("1"):
            return pct
    except InvalidOperation:
        pass
    return None


# ---------------------------------------------------------------------------
# Flyer item matching
# ---------------------------------------------------------------------------


def _singularize(token: str) -> str:
    """Light plural stripping for grocery token matching."""
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 3 and token.endswith("es") and not token.endswith(("ses", "xes", "ches")):
        return token[:-1] if token.endswith("es") and token[-3] in "aeiou" else token[:-2]
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _flyer_tokens(text: str) -> set[str]:
    cleaned = normalize_item_name(text)
    for prefix in ("market basket ", "365 by whole foods market ", "365 brand ", "365 "):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix) :]
    return {_singularize(t) for t in cleaned.split() if t}


def score_flyer_match(query: str, product_name: str) -> float:
    """Score similarity between a grocery list item and a flyer product title."""
    q_tokens = _flyer_tokens(query)
    p_tokens = _flyer_tokens(product_name)
    if not q_tokens or not p_tokens:
        return 0.0

    common = len(q_tokens & p_tokens)
    if common == 0:
        # Require at least one shared normalized word so "milk" never matches "buttermilk".
        return 0.0

    max_overlap = common / max(len(q_tokens), len(p_tokens))
    containment = common / min(len(q_tokens), len(p_tokens))
    seq_ratio = difflib.SequenceMatcher(
        None,
        " ".join(sorted(q_tokens)),
        " ".join(sorted(p_tokens)),
    ).ratio()
    return float(0.35 * max_overlap + 0.45 * containment + 0.20 * seq_ratio)


def match_flyer_deals(
    query: str,
    deals: list[FlyerDeal],
    min_score: float = 0.50,
    limit: int = 5,
) -> list[tuple[FlyerDeal, float]]:
    """Return top flyer deals matching *query*, sorted by match score descending."""
    _, _, cleaned_query = parse_quantity_and_unit(query)
    scored: list[tuple[FlyerDeal, float]] = []

    for deal in deals:
        s_raw = score_flyer_match(query, deal.product_name)
        s_clean = score_flyer_match(cleaned_query, deal.product_name)
        best = max(s_raw, s_clean)
        if best >= min_score:
            scored.append((deal, best))

    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored[:limit]


# ---------------------------------------------------------------------------
# Market Basket Weekly Flyer Provider
# ---------------------------------------------------------------------------


class MarketBasketFlyerProvider:
    """
    Fetches live weekly flyer sales from Market Basket's WordPress JSON endpoint.

    Endpoint: https://www.shopmarketbasket.com/wp-json/mb/v1/weekly-flyer
    """

    STORE_NAME = "Market Basket"

    def __init__(
        self,
        flyer_url: str | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._flyer_url = flyer_url or settings.market_basket_flyer_url
        self._http = http_client

    async def fetch_flyer(self) -> list[FlyerDeal]:
        """Fetch and parse all current weekly flyer items from Market Basket."""
        c_key = cache_key("flyer", "market_basket", self._flyer_url)
        cached = get_cached_price(c_key)
        if cached is not None and isinstance(cached, list):
            return [d.model_copy() for d in cached]

        try:
            if self._http is not None:
                res = await self._http.get(self._flyer_url, headers=_DEFAULT_HEADERS)
            else:
                async with httpx.AsyncClient(
                    timeout=15.0,
                    follow_redirects=True,
                    headers=_DEFAULT_HEADERS,
                ) as client:
                    res = await client.get(self._flyer_url)
        except Exception as exc:
            logger.warning("Failed to fetch Market Basket weekly flyer: %s", exc)
            return []

        if res.status_code != 200:
            logger.warning("Market Basket weekly flyer returned status %s", res.status_code)
            return []

        try:
            payload = res.json()
        except ValueError as exc:
            logger.warning("Invalid JSON from Market Basket weekly flyer: %s", exc)
            return []

        deals = self.parse_flyer_payload(payload)
        set_cached_price(c_key, [d.model_copy() for d in deals])
        return deals

    @classmethod
    def parse_flyer_payload(cls, payload: dict[str, Any]) -> list[FlyerDeal]:
        """Parse the Market Basket /wp-json/mb/v1/weekly-flyer JSON payload."""
        dates = payload.get("dates") or {}
        valid_from = dates.get("start_date")
        valid_to = dates.get("end_date")
        deals: list[FlyerDeal] = []

        for raw in payload.get("products", []):
            name = str(raw.get("name") or "").strip()
            if not name:
                continue

            deal_pricing = str(raw.get("deal_pricing") or "").strip()
            deal_text_raw = str(raw.get("deal_text") or "").strip()
            description = str(raw.get("description") or "").strip() or None

            sale_price, price_unit, bundle_label, bundle_count = parse_price_expression(
                deal_pricing
            )
            if sale_price is None:
                continue

            # Use unit only when explicitly part of the price (e.g. "1.69 lb.");
            # package sizes in description/name (like "32 oz." or "3 Lb.") are not per-unit prices.
            unit = price_unit
            if unit is None:
                combined = f"{name} {description or ''}".lower()
                unit = Unit.PACK if re.search(r"\b(?:pack|pk)\b", combined) else Unit.EACH

            # Derive regular price from Market Basket's savings field (`deal_text`)
            regular_price: Decimal | None = None
            savings_val, _, _, _ = parse_price_expression(deal_text_raw)
            if savings_val is not None and savings_val > 0:
                per_item_savings = (
                    (savings_val / Decimal(bundle_count)).quantize(
                        Decimal("0.01"), rounding=ROUND_HALF_UP
                    )
                    if bundle_count > 1
                    else savings_val
                )
                regular_price = sale_price + per_item_savings

            depts = raw.get("departments") or []
            dept_name = str(depts[0].get("name")) if depts and isinstance(depts[0], dict) else None
            if dept_name:
                dept_name = dept_name.replace("&amp;", "&").replace("&#039;", "'")

            deal_parts = []
            if bundle_label:
                deal_parts.append(bundle_label)
            if deal_text_raw:
                deal_parts.append(f"Save {deal_text_raw}")
            if valid_from and valid_to:
                deal_parts.append(f"{valid_from} – {valid_to}")

            deals.append(
                FlyerDeal(
                    store_name=cls.STORE_NAME,
                    store_id="store_market_basket",
                    product_name=name,
                    department=dept_name,
                    regular_price=regular_price,
                    sale_price=sale_price,
                    unit=unit,
                    deal_text=" · ".join(deal_parts) if deal_parts else None,
                    description=description,
                    valid_from=valid_from,
                    valid_to=valid_to,
                    featured=bool(raw.get("featured", False)),
                    url=raw.get("link"),
                )
            )

        return deals

    async def get_prices(self, item: GroceryItem, store_zip: str) -> list[StorePrice]:
        """Return a live StorePrice if *item* matches an active Market Basket flyer deal."""
        deals = await self.fetch_flyer()
        matches = match_flyer_deals(item.name, deals, min_score=0.50, limit=3)
        if not matches:
            return []

        best_deal, _ = matches[0]
        if best_deal.sale_price is None:
            return []

        regular = best_deal.regular_price or best_deal.sale_price
        return [
            StorePrice(
                item_name=item.name,
                store_name=self.STORE_NAME,
                store_id=best_deal.store_id,
                price=regular,
                unit=best_deal.unit,
                sale_price=best_deal.sale_price,
                is_live=True,
                matched_product_name=best_deal.product_name,
                deal_details=best_deal.deal_text,
            )
        ]


# ---------------------------------------------------------------------------
# Whole Foods Weekly Sales Flyer Provider
# ---------------------------------------------------------------------------


class WholeFoodsFlyerProvider:
    """
    Fetches live weekly sales flyer promotions from Whole Foods Market.

    Source: https://www.wholefoodsmarket.com/sales-flyer?store-id=<store_id>
    """

    STORE_NAME = "Whole Foods"

    def __init__(
        self,
        store_id: str | None = None,
        use_prime: bool | None = None,
        base_url: str | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._store_id = store_id or settings.whole_foods_store_id or "10010"
        self._use_prime = settings.whole_foods_prime if use_prime is None else use_prime
        self._base_url = (base_url or settings.whole_foods_base_url).rstrip("/")
        self._http = http_client

    @property
    def store_id(self) -> str:
        """Configured Whole Foods store ID."""
        return self._store_id

    @property
    def use_prime(self) -> bool:
        """Whether Prime member flyer prices are preferred."""
        return self._use_prime

    async def fetch_flyer(self, store_id: str | None = None) -> list[FlyerDeal]:
        """Fetch and parse all active weekly sales promotions for a Whole Foods store."""
        sid = store_id or self._store_id
        c_key = cache_key("flyer", "whole_foods", sid, str(self._use_prime))
        cached = get_cached_price(c_key)
        if cached is not None and isinstance(cached, list):
            return [d.model_copy() for d in cached]

        url = f"{self._base_url}/sales-flyer"
        try:
            if self._http is not None:
                res = await self._http.get(
                    url,
                    params={"store-id": sid},
                    headers=_DEFAULT_HEADERS,
                )
            else:
                async with httpx.AsyncClient(
                    timeout=15.0,
                    follow_redirects=True,
                    headers=_DEFAULT_HEADERS,
                ) as client:
                    res = await client.get(url, params={"store-id": sid})
        except Exception as exc:
            logger.warning("Failed to fetch Whole Foods sales flyer (store %s): %s", sid, exc)
            return []

        if res.status_code != 200:
            logger.warning("Whole Foods sales flyer returned status %s", res.status_code)
            return []

        deals = self.parse_flyer_html(res.text, store_id=sid)
        set_cached_price(c_key, [d.model_copy() for d in deals])
        return deals

    @classmethod
    def parse_flyer_html(cls, html: str, store_id: str = "") -> list[FlyerDeal]:
        """Extract promotions from Whole Foods __NEXT_DATA__ script JSON."""
        match = re.search(
            r'<script\s+id="__NEXT_DATA__"[^>]*>(.*?)</script>',
            html,
            re.DOTALL,
        )
        if not match:
            return []

        try:
            next_data = json.loads(match.group(1))
        except ValueError as exc:
            logger.warning("Failed to parse Whole Foods __NEXT_DATA__ JSON: %s", exc)
            return []

        page_props = next_data.get("props", {}).get("pageProps", {})
        promotions = page_props.get("promotions") or []
        store_name_label = page_props.get("storeName") or ""
        sid = str(page_props.get("storeId") or store_id or "")

        deals: list[FlyerDeal] = []
        for promo in promotions:
            prod_name = str(promo.get("productName") or "").strip()
            if not prod_name:
                continue

            reg_raw = str(promo.get("regularPrice") or "").strip()
            sale_raw = str(promo.get("salePrice") or "").strip()
            prime_raw = str(promo.get("primePrice") or "").strip()

            reg_price, reg_unit, _, _ = parse_price_expression(reg_raw)
            sale_price, sale_unit, sale_bundle, _ = parse_price_expression(sale_raw)
            prime_price, prime_unit, prime_bundle, _ = parse_price_expression(prime_raw)

            # Handle percentage-off promos when regular price is numeric
            if sale_price is None:
                pct = parse_discount_percent(sale_raw)
                if pct is not None and reg_price is not None:
                    sale_price = (reg_price * (Decimal("1") - pct)).quantize(
                        Decimal("0.01"), rounding=ROUND_HALF_UP
                    )
            if prime_price is None:
                pct_p = parse_discount_percent(prime_raw)
                if pct_p is not None and reg_price is not None:
                    prime_price = (reg_price * (Decimal("1") - pct_p)).quantize(
                        Decimal("0.01"), rounding=ROUND_HALF_UP
                    )

            # Only use unit hints embedded in the price string (e.g. "$2.49/lb");
            # package weights/volumes in productName describe container size, not unit pricing.
            unit = prime_unit or sale_unit or reg_unit or Unit.EACH

            deal_parts: list[str] = []
            if prime_raw:
                deal_parts.append(f"Prime: {prime_raw}")
            if sale_raw:
                deal_parts.append(f"Sale: {sale_raw}")
            elif sale_bundle or prime_bundle:
                deal_parts.append(prime_bundle or sale_bundle or "")
            if store_name_label:
                deal_parts.append(f"WFM {store_name_label}")

            start_date = str(promo.get("startDate") or "")[:10] or None
            end_date = str(promo.get("endDate") or "")[:10] or None

            deals.append(
                FlyerDeal(
                    store_name=cls.STORE_NAME,
                    store_id=f"whole_foods_{sid}" if sid else "store_whole_foods",
                    product_name=prod_name,
                    brand=promo.get("originBrandName") or None,
                    regular_price=reg_price,
                    sale_price=sale_price,
                    prime_price=prime_price,
                    unit=unit,
                    deal_text=" · ".join(p for p in deal_parts if p) or None,
                    description=promo.get("headline") or promo.get("packageSize") or None,
                    valid_from=start_date,
                    valid_to=end_date,
                    featured=promo.get("tier") == "SUPER_HERO",
                    url=f"https://www.wholefoodsmarket.com/sales-flyer?store-id={sid}"
                    if sid
                    else None,
                )
            )

        return deals

    async def get_prices(self, item: GroceryItem, store_zip: str) -> list[StorePrice]:
        """Return a live StorePrice if *item* matches an active Whole Foods weekly promo."""
        deals = await self.fetch_flyer()
        matches = match_flyer_deals(item.name, deals, min_score=0.50, limit=5)
        if not matches:
            return []

        # Prefer matches that have a concrete numeric price
        chosen_deal: FlyerDeal | None = None
        chosen_price: Decimal | None = None
        for deal, _ in matches:
            candidate_price = deal.effective_sale_price(use_prime=self._use_prime)
            if candidate_price is not None:
                chosen_deal = deal
                chosen_price = candidate_price
                break

        if chosen_deal is None or chosen_price is None:
            return []

        regular = chosen_deal.regular_price or chosen_price
        return [
            StorePrice(
                item_name=item.name,
                store_name=self.STORE_NAME,
                store_id=chosen_deal.store_id,
                price=regular,
                unit=chosen_deal.unit,
                sale_price=chosen_price,
                is_live=True,
                matched_product_name=chosen_deal.product_name,
                deal_details=chosen_deal.deal_text,
            )
        ]


# ---------------------------------------------------------------------------
# Generic Multi-Store Weekly Flyer Aggregator (Flipp)
# ---------------------------------------------------------------------------


class BaseFlyerProvider(Protocol):
    """Protocol shared by store-specific and aggregator flyer providers."""

    async def fetch_flyer(self) -> list[FlyerDeal]:
        """Return active advertised flyer deals."""
        ...

    async def get_prices(self, item: GroceryItem, store_zip: str) -> list[StorePrice]:
        """Return live flyer StorePrice matches for *item*."""
        ...


class FlippFlyerProvider:
    """
    Generic multi-store weekly ad / circular client powered by Flipp's public JSON API.

    Discovers grocery circulars near any US/Canadian postal code (e.g. ALDI,
    Star Market, Shaw's, Stop & Shop, Hannaford, Wegmans, Big Y, Costco,
    Target, Walmart, Market Basket, Kroger) and supports both full circular
    browsing and keyword search across local circulars.
    """

    SOURCE_NAME = "Flipp"

    _MERCHANT_ALIASES: dict[str, str] = {
        "demoulas market basket": "Market Basket",
    }

    _MAIN_FLYER_KEYWORDS = ("weekly ad", "weekly flyer", "weekly circular", "indexed weekly")

    def __init__(
        self,
        postal_code: str = "02139",
        merchants: list[str] | set[str] | None = None,
        base_url: str | None = None,
        http_client: httpx.AsyncClient | None = None,
        grocery_only: bool = True,
    ) -> None:
        self._postal_code = postal_code.strip() or "02139"
        self._merchants: set[str] | None = (
            {self.normalize_merchant(m).lower() for m in merchants if m.strip()}
            if merchants
            else None
        )
        self._base_url = (base_url or settings.flipp_base_url).rstrip("/")
        self._http = http_client
        self._grocery_only = grocery_only

    @property
    def postal_code(self) -> str:
        """Configured ZIP / postal code for circular discovery."""
        return self._postal_code

    @property
    def merchants(self) -> set[str] | None:
        """Optional normalized lowercase merchant allow-list."""
        return self._merchants

    @classmethod
    def normalize_merchant(cls, name: str) -> str:
        """Clean trailing tabs/spaces and unify known Flipp merchant aliases."""
        cleaned = " ".join(name.split()).strip()
        return cls._MERCHANT_ALIASES.get(cleaned.lower(), cleaned)

    @staticmethod
    def _compose_price_text(
        pre: str | None,
        price: Any,
        post: str | None,
    ) -> str:
        """Combine Flipp's pre_price_text, numeric/string price, and post_price_text."""
        price_str = str(price).strip() if price is not None else ""
        if not price_str:
            return ""
        pre_str = (pre or "").strip()
        post_str = (post or "").strip()
        if pre_str:
            if pre_str.endswith(("/", "$")):
                joined = f"{pre_str}{price_str}"
            else:
                joined = f"{pre_str} {price_str}"
        else:
            joined = price_str
        return f"{joined} {post_str}".strip() if post_str else joined

    @asynccontextmanager
    async def _get_client(self) -> Any:
        """Yield the injected AsyncClient or a temporary shared AsyncClient."""
        if self._http is not None:
            yield self._http
            return
        async with httpx.AsyncClient(
            timeout=15.0,
            follow_redirects=True,
            headers=_DEFAULT_HEADERS,
        ) as client:
            yield client

    async def fetch_available_flyers(
        self,
        postal_code: str | None = None,
        merchants: list[str] | set[str] | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch active flyers near *postal_code*, optionally filtered to grocery stores."""
        z_code = (postal_code or self._postal_code).strip() or "02139"
        c_key = cache_key("flipp", "flyers", z_code, str(self._grocery_only))
        cached = get_cached_price(c_key)

        raw_flyers: list[dict[str, Any]]
        if cached is not None and isinstance(cached, list):
            raw_flyers = list(cached)
        else:
            url = f"{self._base_url}/flyers"
            params = {"locale": "en-us", "postal_code": z_code}
            try:
                if client is not None:
                    res = await client.get(url, params=params, headers=_DEFAULT_HEADERS)
                else:
                    async with self._get_client() as active_client:
                        res = await active_client.get(url, params=params, headers=_DEFAULT_HEADERS)
            except Exception as exc:
                logger.warning("Failed to fetch Flipp flyers for ZIP %s: %s", z_code, exc)
                return []

            if res.status_code != 200:
                logger.warning("Flipp flyers returned status %s for ZIP %s", res.status_code, z_code)
                return []

            try:
                payload = res.json()
            except ValueError as exc:
                logger.warning("Invalid JSON from Flipp flyers: %s", exc)
                return []

            raw_flyers = list(payload.get("flyers") or [])
            set_cached_price(c_key, raw_flyers)

        allowed = (
            {self.normalize_merchant(m).lower() for m in merchants if m.strip()}
            if merchants is not None
            else self._merchants
        )

        filtered: list[dict[str, Any]] = []
        for flyer in raw_flyers:
            categories = flyer.get("categories") or []
            if self._grocery_only and "Groceries" not in categories:
                continue
            merchant_norm = self.normalize_merchant(str(flyer.get("merchant") or ""))
            if not merchant_norm:
                continue
            if allowed is not None and merchant_norm.lower() not in allowed:
                continue
            enriched = dict(flyer)
            enriched["merchant_normalized"] = merchant_norm
            filtered.append(enriched)

        return filtered

    @classmethod
    def parse_flyer_items(
        cls,
        flyer_id: int | str,
        items: list[dict[str, Any]],
        merchant_name: str,
        flyer_name: str = "",
    ) -> list[FlyerDeal]:
        """Parse items from ``/flipp/flyers/{flyer_id}`` into FlyerDeal models."""
        store_name = cls.normalize_merchant(merchant_name)
        slug = re.sub(r"[^a-z0-9]+", "_", store_name.lower()).strip("_")
        deals: list[FlyerDeal] = []

        for raw in items:
            name = str(raw.get("name") or "").strip()
            if not name:
                continue

            price_expr = cls._compose_price_text(
                raw.get("pre_price_text"),
                raw.get("price"),
                raw.get("post_price_text"),
            )
            sale_price, price_unit, bundle_label, _ = parse_price_expression(price_expr)
            if sale_price is None:
                continue

            discount_raw = raw.get("discount")
            regular_price: Decimal | None = None
            if isinstance(discount_raw, (int, float)) and 0 < float(discount_raw) < 100:
                factor = Decimal("1") - (Decimal(str(discount_raw)) / Decimal("100"))
                if factor > 0:
                    regular_price = (sale_price / factor).quantize(
                        Decimal("0.01"), rounding=ROUND_HALF_UP
                    )

            unit = price_unit
            if unit is None:
                unit = Unit.PACK if re.search(r"\b(?:pack|pk)\b", name.lower()) else Unit.EACH

            deal_parts: list[str] = []
            if bundle_label:
                deal_parts.append(bundle_label)
            if isinstance(discount_raw, (int, float)) and discount_raw > 0:
                deal_parts.append(f"{int(round(float(discount_raw)))}% off")
            if flyer_name:
                deal_parts.append(flyer_name)

            valid_from = str(raw.get("valid_from") or "")[:10] or None
            valid_to = str(raw.get("valid_to") or "")[:10] or None

            deals.append(
                FlyerDeal(
                    store_name=store_name,
                    store_id=f"flipp_{slug}_{flyer_id}",
                    product_name=name,
                    brand=str(raw.get("brand") or "").strip() or None,
                    regular_price=regular_price,
                    sale_price=sale_price,
                    unit=unit,
                    deal_text=" · ".join(deal_parts) if deal_parts else None,
                    valid_from=valid_from,
                    valid_to=valid_to,
                    url=f"https://flipp.com/flyer/{flyer_id}",
                )
            )

        return deals

    async def fetch_flyer_items(
        self,
        flyer_id: int | str,
        merchant_name: str,
        flyer_name: str = "",
        client: httpx.AsyncClient | None = None,
    ) -> list[FlyerDeal]:
        """Fetch and parse all indexed items inside a single Flipp circular."""
        c_key = cache_key("flipp", "flyer_items", str(flyer_id))
        cached = get_cached_price(c_key)
        if cached is not None and isinstance(cached, list):
            return [d.model_copy() for d in cached]

        url = f"{self._base_url}/flyers/{flyer_id}"
        try:
            if client is not None:
                res = await client.get(url, params={"locale": "en-us"}, headers=_DEFAULT_HEADERS)
            else:
                async with self._get_client() as active_client:
                    res = await active_client.get(
                        url, params={"locale": "en-us"}, headers=_DEFAULT_HEADERS
                    )
        except Exception as exc:
            logger.warning("Failed to fetch Flipp flyer %s: %s", flyer_id, exc)
            return []

        if res.status_code != 200:
            logger.warning("Flipp flyer %s returned status %s", flyer_id, res.status_code)
            return []

        try:
            payload = res.json()
        except ValueError as exc:
            logger.warning("Invalid JSON from Flipp flyer %s: %s", flyer_id, exc)
            return []

        deals = self.parse_flyer_items(
            flyer_id=flyer_id,
            items=list(payload.get("items") or []),
            merchant_name=merchant_name,
            flyer_name=flyer_name,
        )
        set_cached_price(c_key, [d.model_copy() for d in deals])
        return deals

    def _select_primary_flyers(
        self, flyers: list[dict[str, Any]], max_flyers: int
    ) -> list[dict[str, Any]]:
        """Pick one primary circular per merchant (preferring main weekly ads)."""

        def _priority(flyer: dict[str, Any]) -> tuple[int, int]:
            name_lower = str(flyer.get("name") or "").lower()
            is_main = 0 if any(kw in name_lower for kw in self._MAIN_FLYER_KEYWORDS) else 1
            return (is_main, -int(flyer.get("id") or 0))

        by_merchant: dict[str, list[dict[str, Any]]] = {}
        for flyer in flyers:
            merchant = str(flyer.get("merchant_normalized") or "")
            by_merchant.setdefault(merchant, []).append(flyer)

        selected: list[dict[str, Any]] = []
        for merchant in sorted(by_merchant):
            best = min(by_merchant[merchant], key=_priority)
            selected.append(best)

        selected.sort(key=_priority)
        return selected[:max_flyers]

    async def fetch_flyer(
        self,
        postal_code: str | None = None,
        merchants: list[str] | set[str] | None = None,
        max_flyers: int = 12,
    ) -> list[FlyerDeal]:
        """Fetch weekly on-sale deals across discovered local grocery flyers on Flipp."""
        async with self._get_client() as client:
            flyers = await self.fetch_available_flyers(
                postal_code=postal_code,
                merchants=merchants,
                client=client,
            )
            chosen = self._select_primary_flyers(flyers, max_flyers=max_flyers)
            if not chosen:
                return []

            results = await asyncio.gather(
                *(
                    self.fetch_flyer_items(
                        flyer_id=f["id"],
                        merchant_name=f["merchant_normalized"],
                        flyer_name=str(f.get("name") or ""),
                        client=client,
                    )
                    for f in chosen
                ),
                return_exceptions=True,
            )

        deals: list[FlyerDeal] = []
        for res in results:
            if isinstance(res, list):
                deals.extend(res)
        return deals

    @classmethod
    def parse_search_items(
        cls,
        items: list[dict[str, Any]],
        merchants: set[str] | None = None,
    ) -> list[FlyerDeal]:
        """Parse rich flyer item results from ``/flipp/items/search``."""
        deals: list[FlyerDeal] = []

        for raw in items:
            if raw.get("item_type", "flyer") != "flyer":
                continue
            name = str(raw.get("name") or "").strip()
            merchant_raw = str(raw.get("merchant_name") or "").strip()
            if not name or not merchant_raw:
                continue

            store_name = cls.normalize_merchant(merchant_raw)
            if merchants is not None and store_name.lower() not in merchants:
                continue

            price_expr = cls._compose_price_text(
                raw.get("pre_price_text"),
                raw.get("current_price"),
                raw.get("post_price_text"),
            )
            sale_price, price_unit, bundle_label, _ = parse_price_expression(price_expr)
            if sale_price is None:
                continue

            orig_raw = raw.get("original_price")
            regular_price: Decimal | None = None
            if orig_raw is not None:
                try:
                    orig_dec = Decimal(str(orig_raw)).quantize(
                        Decimal("0.01"), rounding=ROUND_HALF_UP
                    )
                    if orig_dec > sale_price:
                        regular_price = orig_dec
                except InvalidOperation:
                    pass

            unit = price_unit
            if unit is None:
                unit = Unit.PACK if re.search(r"\b(?:pack|pk)\b", name.lower()) else Unit.EACH

            story = str(raw.get("sale_story") or "").strip()
            deal_parts = [p for p in (bundle_label, story) if p]
            slug = re.sub(r"[^a-z0-9]+", "_", store_name.lower()).strip("_")
            flyer_id = raw.get("flyer_id")

            deals.append(
                FlyerDeal(
                    store_name=store_name,
                    store_id=f"flipp_{slug}_{flyer_id}" if flyer_id else f"flipp_{slug}",
                    product_name=name,
                    regular_price=regular_price,
                    sale_price=sale_price,
                    unit=unit,
                    deal_text=" · ".join(deal_parts) if deal_parts else None,
                    valid_from=str(raw.get("valid_from") or "")[:10] or None,
                    valid_to=str(raw.get("valid_to") or "")[:10] or None,
                    url=f"https://flipp.com/flyer/{flyer_id}" if flyer_id else None,
                )
            )

        return deals

    async def search_items(
        self,
        query: str,
        postal_code: str | None = None,
        merchants: list[str] | set[str] | None = None,
    ) -> list[FlyerDeal]:
        """Search all active local circulars on Flipp for *query*."""
        z_code = (postal_code or self._postal_code).strip() or "02139"
        q_clean = query.strip()
        if not q_clean:
            return []

        allowed = (
            {self.normalize_merchant(m).lower() for m in merchants if m.strip()}
            if merchants is not None
            else self._merchants
        )
        c_key = cache_key("flipp", "search", z_code, q_clean.lower())
        cached = get_cached_price(c_key)

        raw_items: list[dict[str, Any]]
        if cached is not None and isinstance(cached, list):
            raw_items = list(cached)
        else:
            url = f"{self._base_url}/items/search"
            params = {"locale": "en-us", "postal_code": z_code, "q": q_clean}
            try:
                async with self._get_client() as client:
                    res = await client.get(url, params=params, headers=_DEFAULT_HEADERS)
            except Exception as exc:
                logger.warning("Failed Flipp item search for '%s' (%s): %s", q_clean, z_code, exc)
                return []

            if res.status_code != 200:
                logger.warning("Flipp item search returned status %s for '%s'", res.status_code, q_clean)
                return []

            try:
                payload = res.json()
            except ValueError as exc:
                logger.warning("Invalid JSON from Flipp search '%s': %s", q_clean, exc)
                return []

            raw_items = list(payload.get("items") or [])
            set_cached_price(c_key, raw_items)

        return self.parse_search_items(raw_items, merchants=allowed)

    async def get_prices(self, item: GroceryItem, store_zip: str) -> list[StorePrice]:
        """Return the best live Flipp flyer StorePrice per local store for *item*."""
        z_code = store_zip.strip() or self._postal_code
        _, _, cleaned_query = parse_quantity_and_unit(item.name)
        query = cleaned_query or item.name
        deals = await self.search_items(query, postal_code=z_code)
        if not deals:
            return []

        by_store: dict[str, list[FlyerDeal]] = {}
        for deal in deals:
            if deal.sale_price is None:
                continue
            by_store.setdefault(deal.store_name, []).append(deal)

        prices: list[StorePrice] = []
        for store_name, store_deals in by_store.items():
            matches = match_flyer_deals(item.name, store_deals, min_score=0.50, limit=1)
            if not matches:
                continue
            best_deal, _ = matches[0]
            if best_deal.sale_price is None:
                continue
            regular = best_deal.regular_price or best_deal.sale_price
            prices.append(
                StorePrice(
                    item_name=item.name,
                    store_name=store_name,
                    store_id=best_deal.store_id,
                    price=regular,
                    unit=best_deal.unit,
                    sale_price=best_deal.sale_price,
                    is_live=True,
                    matched_product_name=best_deal.product_name,
                    deal_details=best_deal.deal_text,
                )
            )
        return prices
