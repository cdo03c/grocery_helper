"""
app.py
------
Streamlit entry-point for the Grocery Helper app.

Run with:
    streamlit run src/grocery_helper/app.py
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import logging
import uuid
from decimal import Decimal
from typing import Any, Literal

import pandas as pd
import streamlit as st

from grocery_helper import __version__
from grocery_helper.anylist_client import AnyListAPIError, AnyListAuthError, AnyListClient
from grocery_helper.config import settings
from grocery_helper.flyers import (
    WHOLE_FOODS_STORES,
    FlippFlyerProvider,
    MarketBasketFlyerProvider,
    WholeFoodsFlyerProvider,
    match_flyer_deals,
)
from grocery_helper.models import FlyerDeal, GroceryItem, GroceryList, OptimizedCart, Unit
from grocery_helper.pricing import MockPricingProvider, PricingService, build_stop_count_summary
from grocery_helper.utils import configure_logging, format_currency, format_savings

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

configure_logging()
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Async Helpers
# ---------------------------------------------------------------------------


def run_async(coro: Any) -> Any:
    """Execute an async coroutine synchronously in Streamlit."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(asyncio.run, coro).result()


# ---------------------------------------------------------------------------
# Sidebar – credentials & settings
# ---------------------------------------------------------------------------


def render_sidebar() -> dict[str, Any]:
    """Render the sidebar and return user-supplied settings."""
    with st.sidebar:
        st.title("⚙️ Settings")
        st.divider()

        email = st.text_input(
            "AnyList Email",
            value=settings.anylist_email,
            placeholder="you@example.com",
            key="anylist_email",
        )
        password = st.text_input(
            "AnyList Password",
            value=settings.anylist_password,
            type="password",
            key="anylist_password",
        )
        store_zip = st.text_input(
            "Store ZIP Code",
            value=settings.default_store_zip or "90210",
            placeholder="90210",
            key="store_zip",
        )

        has_creds = bool(email and password)
        demo_mode = st.toggle(
            "Demo / Sample Mode",
            value=not has_creds,
            help="Use sample lists and mock store pricing for testing.",
            key="demo_mode_toggle",
        )

        st.subheader("🛒 Optimisation Filters")
        available_stores = list(MockPricingProvider.STORE_PROFILES.keys())
        allowed_stores = st.multiselect(
            "Allowed Stores",
            options=available_stores,
            default=available_stores,
            help="Filter stores considered during pricing & optimization.",
            key="allowed_stores_filter",
        )
        max_stores: int | None
        if len(allowed_stores) > 1:
            max_stores = st.slider(
                "Max Stores to Visit",
                min_value=1,
                max_value=len(allowed_stores),
                value=len(allowed_stores),
                help="Limit the shopping route to at most N stores to reduce trips.",
                key="max_stores_slider",
            )
        elif len(allowed_stores) == 1:
            max_stores = 1
            st.caption(f"Max Stores to Visit: 1 ({allowed_stores[0]})")
        else:
            max_stores = None
            st.warning("Select at least one store to compare prices.")

        st.subheader("🏷️ Weekly Flyers (MB, Whole Foods & Flipp)")
        enable_live_flyers = st.toggle(
            "Enable Live Weekly Flyers",
            value=settings.enable_live_flyers,
            help=(
                "Overlay live advertised weekly sale prices from Market Basket, "
                "Whole Foods, and nearby stores on Flipp onto price comparisons."
            ),
            key="live_flyers_toggle",
        )
        enable_flipp = st.toggle(
            "Include Nearby Flipp Stores (by ZIP)",
            value=settings.enable_flipp,
            disabled=not enable_live_flyers,
            help=(
                "Discover local grocery store circulars for your ZIP code via Flipp "
                "(e.g. Star Market, Shaw's, Stop & Shop, Hannaford, Wegmans, ALDI, Big Y, Costco)."
            ),
            key="flipp_toggle",
        )
        advertised_only = st.checkbox(
            "Advertised flyer prices only (MB & Whole Foods)",
            value=False,
            disabled=not enable_live_flyers,
            help=(
                "Show only live weekly sale items for Market Basket and Whole Foods "
                "instead of mock estimates for unlisted items."
            ),
            key="advertised_only_toggle",
        )
        wf_store_id_raw = st.text_input(
            "Whole Foods Store ID",
            value=settings.whole_foods_store_id,
            help=(
                "Store ID from wholefoodsmarket.com/sales-flyer?store-id=... "
                "(e.g., 10010 Cambridge, 10380 Medford, 10156 Framingham, "
                "10090 Bedford, 10371 Andover)."
            ),
            key="wf_store_id_input",
        )
        wf_store_id = wf_store_id_raw.strip() or settings.whole_foods_store_id
        if wf_store_id in WHOLE_FOODS_STORES:
            wf_name, wf_zip = WHOLE_FOODS_STORES[wf_store_id]
            st.caption(f"Whole Foods location: {wf_name} ({wf_zip})")
        wf_prime = st.toggle(
            "Whole Foods Prime Pricing",
            value=settings.whole_foods_prime,
            help="Apply Amazon Prime member sale prices when listed in the Whole Foods flyer.",
            key="wf_prime_toggle",
        )
        enable_open_prices = st.toggle(
            "Include Open Prices (Crowdsourced)",
            value=settings.enable_open_prices,
            help=(
                "Overlay crowdsourced retail receipt/shelf prices from Open Prices "
                "(prices.openfoodfacts.org) for non-sale items when available."
            ),
            key="open_prices_toggle",
        )

        st.divider()
        st.caption(f"Grocery Helper v{__version__}")

    return {
        "email": email,
        "password": password,
        "store_zip": store_zip,
        "demo_mode": demo_mode,
        "allowed_stores": allowed_stores,
        "max_stores": max_stores,
        "enable_live_flyers": enable_live_flyers,
        "enable_flipp": enable_flipp,
        "enable_open_prices": enable_open_prices,
        "advertised_only": advertised_only,
        "whole_foods_store_id": wf_store_id,
        "whole_foods_prime": wf_prime,
    }


# ---------------------------------------------------------------------------
# Page sections
# ---------------------------------------------------------------------------


def _get_anylist_client(config: dict[str, Any]) -> AnyListClient:
    """Return a session-persisted demo client or a fresh live AnyList client."""
    if config["demo_mode"]:
        client = st.session_state.get("demo_anylist_client")
        if not isinstance(client, AnyListClient):
            fresh = AnyListClient(demo_mode=True)
            # Migrate in-memory demo lists if Streamlit reloaded AnyListClient.
            old_store = getattr(client, "_demo_store", None)
            if isinstance(old_store, dict):
                fresh._demo_store = {
                    lid: GroceryList.model_validate(lst) for lid, lst in old_store.items()
                }
            st.session_state["demo_anylist_client"] = fresh
            return fresh
        return client
    return AnyListClient(
        email=config["email"],
        password=config["password"],
        demo_mode=False,
    )


def render_hero() -> None:
    """Render the app header / hero section."""
    st.title("🛒 Grocery Helper")
    st.markdown(
        "Connect your **AnyList** account to automatically compare prices "
        "across stores and find the best deals on your grocery list."
    )
    st.divider()


def _live_cache_key(config: dict[str, Any]) -> str:
    """Return a non-secret fingerprint for caching live AnyList lists in session state."""
    raw = f"{config.get('email', '')}:{config.get('password', '')}".encode()
    return hashlib.sha256(raw).hexdigest()


def _invalidate_live_lists_cache() -> None:
    """Clear cached live AnyList lists from Streamlit session state."""
    st.session_state.pop("cached_live_lists", None)
    st.session_state.pop("cached_live_lists_key", None)


async def _fetch_user_lists(config: dict[str, Any]) -> list[GroceryList]:
    """Fetch lists from AnyList or Demo store."""
    client = _get_anylist_client(config)
    async with client:
        await client.authenticate()
        return await client.get_lists()


def render_list_selector(config: dict[str, Any]) -> tuple[str | None, GroceryList | None]:
    """
    Fetch and display available grocery lists; return the selected list ID and GroceryList.
    """
    st.subheader("📋 Your Lists")

    lists: list[GroceryList]
    if config["demo_mode"]:
        try:
            lists = run_async(_fetch_user_lists(config))
        except (AnyListAuthError, AnyListAPIError) as err:
            st.error(f"Failed to load AnyList lists: {err}")
            return None, None
    else:
        cache_fp = _live_cache_key(config)
        if st.session_state.get("cached_live_lists_key") != cache_fp:
            try:
                lists = run_async(_fetch_user_lists(config))
            except (AnyListAuthError, AnyListAPIError) as err:
                st.error(f"Failed to load AnyList lists: {err}")
                return None, None
            st.session_state["cached_live_lists"] = [lst.model_dump(mode="json") for lst in lists]
            st.session_state["cached_live_lists_key"] = cache_fp
        else:
            raw_cached = st.session_state.get("cached_live_lists", [])
            lists = [GroceryList.model_validate(entry) for entry in raw_cached]

    if not lists:
        st.warning("No grocery lists found.")
        return None, None

    list_by_id = {lst.id: lst for lst in lists}
    option_ids: list[str | None] = [None, *list_by_id.keys()]
    if st.session_state.get("selected_list_id") not in option_ids:
        st.session_state["selected_list_id"] = None

    if not config["demo_mode"]:
        col_sel, col_ref = st.columns([5, 1])
        with col_sel:
            selected_id = st.selectbox(
                "Choose a list",
                options=option_ids,
                format_func=lambda lid: (
                    "— Select a list —"
                    if lid is None
                    else f"{list_by_id[lid].name} ({len(list_by_id[lid].items)} items)"
                ),
                key="selected_list_id",
            )
        with col_ref:
            st.write("")
            if st.button("🔄 Refresh", help="Reload lists from AnyList"):
                _invalidate_live_lists_cache()
                st.rerun()
    else:
        selected_id = st.selectbox(
            "Choose a list",
            options=option_ids,
            format_func=lambda lid: (
                "— Select a list —"
                if lid is None
                else f"{list_by_id[lid].name} ({len(list_by_id[lid].items)} items)"
            ),
            key="selected_list_id",
        )
    selected_list = list_by_id.get(selected_id) if selected_id else None
    return (selected_list.id if selected_list else None), selected_list


async def _run_pricing(
    grocery_list: GroceryList,
    store_zip: str,
    allowed_stores: list[str] | None = None,
    max_stores: int | None = None,
    enable_live_flyers: bool | None = None,
    enable_flipp: bool | None = None,
    enable_open_prices: bool | None = None,
    flyer_only_for_live_stores: bool = False,
    whole_foods_store_id: str | None = None,
    whole_foods_prime: bool | None = None,
) -> OptimizedCart:
    """Fetch prices and optimize the cart."""
    service = PricingService(
        store_zip=store_zip,
        enable_live_flyers=enable_live_flyers,
        enable_flipp=enable_flipp,
        enable_open_prices=enable_open_prices,
        flyer_only_for_live_stores=flyer_only_for_live_stores,
        whole_foods_store_id=whole_foods_store_id,
        whole_foods_prime=whole_foods_prime,
    )
    return await service.optimise(
        grocery_list,
        allowed_stores=allowed_stores,
        max_stores=max_stores,
    )


async def _fetch_all_flyers(
    wf_store_id: str,
    store_zip: str = "02139",
    include_flipp: bool = True,
) -> tuple[list[FlyerDeal], list[FlyerDeal], list[FlyerDeal], list[str]]:
    """Fetch weekly flyers from Market Basket, Whole Foods, and optionally Flipp concurrently."""
    mb_provider = MarketBasketFlyerProvider()
    wf_provider = WholeFoodsFlyerProvider(store_id=wf_store_id)
    tasks: list[Any] = [mb_provider.fetch_flyer(), wf_provider.fetch_flyer()]
    if include_flipp:
        flipp_provider = FlippFlyerProvider(postal_code=store_zip or "02139")
        tasks.append(flipp_provider.fetch_flyer())

    results = await asyncio.gather(*tasks, return_exceptions=True)

    warnings: list[str] = []
    mb_deals: list[FlyerDeal] = []
    wf_deals: list[FlyerDeal] = []
    flipp_deals: list[FlyerDeal] = []

    if isinstance(results[0], BaseException):
        warnings.append(f"Market Basket flyer fetch failed: {results[0]}")
    else:
        mb_deals = results[0]

    if isinstance(results[1], BaseException):
        warnings.append(f"Whole Foods flyer fetch failed: {results[1]}")
    else:
        wf_deals = results[1]

    if include_flipp and len(results) > 2:
        if isinstance(results[2], BaseException):
            warnings.append(f"Flipp flyer fetch failed: {results[2]}")
        else:
            # Prefer Market Basket's direct circular when both exist to avoid duplicates
            flipp_deals = [
                d for d in results[2] if d.store_name != MarketBasketFlyerProvider.STORE_NAME
            ]

    return mb_deals, wf_deals, flipp_deals, warnings


def _format_deal_price(amount: Decimal | None, unit: Unit) -> str:
    """Format an optional flyer price with unit context when priced by weight/volume."""
    if amount is None:
        return "-"
    unit_suffix = f" /{unit.value}" if unit != Unit.EACH else ""
    return f"{format_currency(amount)}{unit_suffix}"


def build_flyer_rows(deals: list[FlyerDeal], use_prime: bool = True) -> list[dict[str, Any]]:
    """Build display rows for browsing weekly on-sale flyer items."""
    rows: list[dict[str, Any]] = []
    for deal in deals:
        effective = deal.effective_sale_price(use_prime=use_prime)
        validity = ""
        if deal.valid_from and deal.valid_to:
            validity = f"{deal.valid_from} – {deal.valid_to}"
        elif deal.valid_to:
            validity = f"Through {deal.valid_to}"

        rows.append(
            {
                "Store": deal.store_name,
                "Item": deal.product_name,
                "Brand / Details": deal.brand or deal.description or "-",
                "Best Sale Price": _format_deal_price(effective, deal.unit),
                "Prime Price": _format_deal_price(deal.prime_price, deal.unit),
                "Regular Price": _format_deal_price(deal.regular_price, deal.unit),
                "Deal": deal.deal_text or ("Featured" if deal.featured else "-"),
                "Department": deal.department or "-",
                "Valid": validity or "-",
            }
        )
    return rows


def build_matched_flyer_rows(
    grocery_list: GroceryList,
    deals: list[FlyerDeal],
    use_prime: bool = True,
) -> list[dict[str, Any]]:
    """Match items on a grocery list against active weekly flyer deals."""
    rows: list[dict[str, Any]] = []
    stores = sorted({d.store_name for d in deals})
    for item in grocery_list.items:
        for store in stores:
            store_deals = [d for d in deals if d.store_name == store]
            matches = match_flyer_deals(item.name, store_deals, limit=1)
            if not matches:
                continue
            deal, score = matches[0]
            effective = deal.effective_sale_price(use_prime=use_prime)
            display_price = _format_deal_price(effective, deal.unit)
            if display_price == "-" and deal.deal_text:
                display_price = deal.deal_text

            rows.append(
                {
                    "List Item": item.name,
                    "Store": deal.store_name,
                    "Matched Flyer Item": deal.product_name,
                    "Sale Price": display_price,
                    "Regular Price": _format_deal_price(deal.regular_price, deal.unit),
                    "Deal Details": deal.deal_text or deal.description or "-",
                    "Match": f"{score:.0%}",
                }
            )
    return rows


def build_comparison_rows(cart: OptimizedCart) -> list[dict[str, Any]]:
    """Build tabular display rows for price comparisons across stores."""
    stores = sorted({sp.store_name for comp in cart.comparisons for sp in comp.prices})
    rows: list[dict[str, Any]] = []

    for comp in cart.comparisons:
        row: dict[str, Any] = {
            "Item": comp.item.name,
            "Quantity": f"{comp.item.quantity:g} {comp.item.unit}",
            "Category": comp.item.category or "General",
        }
        price_map = {sp.store_name: sp for sp in comp.prices}
        for store in stores:
            if store in price_map:
                sp = price_map[store]
                unit_suffix = f" /{sp.unit}" if sp.unit != comp.item.unit else ""
                live_tag = " 🏷️" if sp.is_live else ""
                if sp.sale_price is not None:
                    row[store] = f"{format_currency(sp.sale_price)}{unit_suffix} (Sale{live_tag})"
                elif sp.is_live:
                    row[store] = f"{format_currency(sp.price)}{unit_suffix} (Live{live_tag})"
                else:
                    row[store] = f"{format_currency(sp.price)}{unit_suffix}"
            else:
                row[store] = "N/A"

        row["Cheapest Store"] = comp.cheapest.store_name if comp.cheapest else "N/A"
        row["Best Price"] = (
            format_currency(comp.normalized_price(comp.cheapest)) if comp.cheapest else "N/A"
        )
        if cart.max_stores_constraint is not None:
            row["Assigned Store"] = cart.store_assignments.get(comp.item.id, "N/A")
        qty_dec = Decimal(str(comp.item.quantity))
        row["Potential Savings"] = (
            format_savings(comp.savings * qty_dec) if comp.savings else "None"
        )
        rows.append(row)

    return rows


def group_cart_by_store(
    cart: OptimizedCart,
) -> tuple[dict[str, list[tuple[str, str, Decimal]]], dict[str, Decimal]]:
    """Group assigned cart items and Decimal subtotals by assigned store."""
    items_by_store: dict[str, list[tuple[str, str, Decimal]]] = {}
    store_subtotals: dict[str, Decimal] = {}

    for comp in cart.comparisons:
        assigned_store = cart.store_assignments.get(comp.item.id)
        if not assigned_store:
            continue
        assigned_sp = next(
            (p for p in comp.prices if p.store_name == assigned_store),
            comp.cheapest,
        )
        if assigned_sp is None:
            continue
        qty_dec = Decimal(str(comp.item.quantity))
        line_total = comp.normalized_price(assigned_sp) * qty_dec
        items_by_store.setdefault(assigned_store, []).append(
            (
                comp.item.name,
                f"{comp.item.quantity:g} {comp.item.unit}",
                line_total,
            )
        )
        store_subtotals[assigned_store] = (
            store_subtotals.get(assigned_store, Decimal("0.00")) + line_total
        )

    return items_by_store, store_subtotals


def render_price_comparison(cart: OptimizedCart) -> None:
    """Render the detailed price comparison table."""
    st.subheader("💰 Price Comparison Across Stores")

    if not cart.comparisons:
        st.info("No price comparison data available.")
        return

    df = pd.DataFrame(build_comparison_rows(cart))
    st.dataframe(df, use_container_width=True, hide_index=True)


def render_optimised_cart(
    cart: OptimizedCart,
    grocery_list: GroceryList,
    config: dict[str, Any] | None = None,
) -> None:
    """Display the optimised cart with metrics, store assignments, and totals."""
    st.subheader("🏪 Optimised Cart & Savings")

    # Top metrics
    best_single = cart.best_single_store
    savings_vs_single = cart.savings_vs_best_single_store
    pct_vs_single = cart.savings_pct_vs_best_single_store

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        stores_used_count = len(cart.stores_used)
        stop_label = f"{stores_used_count} store(s)" if stores_used_count else "0 stores"
        st.metric(
            label="Optimised Basket Total",
            value=format_currency(cart.total_cost),
            delta=stop_label,
            delta_color="off",
            help="Total cost when buying each item at its assigned store.",
        )
    with col2:
        if best_single is not None and savings_vs_single is not None and pct_vs_single is not None:
            best_store, best_total = best_single
            vs_delta = (
                f"{pct_vs_single:.1f}% saved vs {best_store}"
                if savings_vs_single > 0
                else f"Matches {best_store}"
            )
            st.metric(
                label="Savings vs. Best Single Store",
                value=format_currency(savings_vs_single),
                delta=vs_delta,
                delta_color="normal" if savings_vs_single > 0 else "off",
                help=(
                    "Overall savings from your selected stores compared to buying "
                    "every item at the generally cheapest single store."
                ),
            )
        else:
            st.metric(
                label="Savings vs. Best Single Store",
                value="N/A",
                help="Requires at least one store carrying all items for single-store comparison.",
            )
    with col3:
        if best_single is not None:
            best_store, best_total = best_single
            diff = best_total - cart.total_cost
            if diff > 0:
                delta_str: str | None = f"{format_currency(best_total)} (+{format_currency(diff)})"
                delta_color: Literal["normal", "inverse", "off"] = "inverse"
            elif diff == 0:
                delta_str = f"{format_currency(best_total)} (matches route)"
                delta_color = "off"
            else:
                delta_str = f"{format_currency(best_total)} (-{format_currency(abs(diff))})"
                delta_color = "normal"
            st.metric(
                label="Best Single Store",
                value=best_store,
                delta=delta_str,
                delta_color=delta_color,
                help="Cheapest option if you only make one stop.",
            )
        else:
            st.metric(
                label="Best Single Store",
                value="N/A",
                help="No single selected store covers every item on this list.",
            )
    with col4:
        st.metric(
            label="Estimated Total Savings",
            value=format_currency(cart.estimated_savings),
            help="Max savings compared to purchasing each item at the highest-priced store.",
        )

    if best_single is not None and savings_vs_single is not None and pct_vs_single is not None:
        best_store, best_total = best_single
        stops_desc = (
            f"**{len(cart.stores_used)} store(s)** ({', '.join(cart.stores_used)})"
            if cart.stores_used
            else "your selected stores"
        )
        if savings_vs_single > 0:
            st.success(
                f"Visiting {stops_desc} saves **{format_currency(savings_vs_single)} "
                f"({pct_vs_single:.1f}%)** overall compared to buying your whole list at "
                f"**{best_store}** ({format_currency(best_total)})."
            )
        else:
            st.info(
                f"Buying everything at **{best_store}** ({format_currency(best_total)}) "
                f"already matches your lowest split price ({format_currency(cart.total_cost)})."
            )

    st.divider()

    # Store split vs single store comparison chart
    if cart.single_store_totals:
        st.markdown("#### 📊 Total Basket Cost: Split vs. Single Store")
        chart_data: dict[str, float] = {
            "⚡ Optimised Split": float(cart.total_cost),
        }
        for store, store_total in sorted(cart.single_store_totals.items(), key=lambda x: x[1]):
            chart_data[store] = float(store_total)

        chart_df = pd.DataFrame(
            {"Store": list(chart_data.keys()), "Cost ($)": list(chart_data.values())}
        )
        st.bar_chart(chart_df.set_index("Store"), y="Cost ($)", use_container_width=True)

    stop_rows = build_stop_count_summary(cart)
    if len(stop_rows) >= 2:
        st.markdown("#### 🚗 Potential Savings by Number of Stores")
        formatted_stops = []
        for entry in stop_rows:
            sav = entry["savings_vs_single"]
            pct = entry["savings_pct"]
            formatted_stops.append(
                {
                    "Stops": f"{entry['stops']}{' (Selected)' if entry['is_selected'] else ''}",
                    "Stores": ", ".join(entry["stores"]) or "-",
                    "Basket Total": format_currency(entry["total_cost"]),
                    "Savings vs. Best Single Store": (
                        f"{format_currency(sav)} ({pct:.1f}%)"
                        if sav is not None and pct is not None
                        else "-"
                    ),
                }
            )
        st.dataframe(pd.DataFrame(formatted_stops), use_container_width=True, hide_index=True)

    # Store assignments breakdown
    st.markdown("#### 🛍️ Shopping Route by Store")
    items_by_store, store_subtotals = group_cart_by_store(cart)

    if not items_by_store:
        st.info("No store assignments available for the current filter settings.")

    for store, items in items_by_store.items():
        subtotal = store_subtotals.get(store, Decimal("0.00"))
        with st.expander(
            f"📍 **{store}** ({len(items)} items — Total: {format_currency(subtotal)})",
            expanded=True,
        ):
            for name, qty, price in items:
                st.write(f"- **{name}** ({qty}): {format_currency(price)}")

    if config is not None and cart.store_assignments:
        st.divider()
        if st.button(
            "📤 Export Store Lists to AnyList",
            help="Create store-specific lists in AnyList with the assigned items.",
        ):
            client = _get_anylist_client(config)

            async def _export() -> list[GroceryList]:
                async with client:
                    await client.authenticate()
                    return await client.export_split_lists(cart, base_name=grocery_list.name)

            try:
                with st.spinner("Creating store lists in AnyList..."):
                    created = run_async(_export())
            except (AnyListAuthError, AnyListAPIError) as err:
                st.error(f"Failed to export lists to AnyList: {err}")
            else:
                if not config["demo_mode"]:
                    _invalidate_live_lists_cache()
                st.success(f"Created {len(created)} store-specific list(s) in AnyList:")
                for cl in created:
                    st.write(f"- 📋 **{cl.name}** ({len(cl.items)} items)")


def render_flyers_tab(grocery_list: GroceryList, config: dict[str, Any]) -> None:
    """Render live weekly advertised sale items from Market Basket, Whole Foods, and Flipp."""
    st.subheader("🏷️ Weekly Sales Flyers (Market Basket, Whole Foods & Flipp)")
    wf_store_id = str(config.get("whole_foods_store_id") or settings.whole_foods_store_id)
    store_zip = str(config.get("store_zip") or settings.default_store_zip or "02139")
    include_flipp = bool(config.get("enable_flipp", settings.enable_flipp))
    use_prime = bool(config.get("whole_foods_prime", settings.whole_foods_prime))
    wf_label = WHOLE_FOODS_STORES.get(wf_store_id, (f"Store #{wf_store_id}", ""))[0]
    flipp_desc = f" & Flipp (ZIP {store_zip})" if include_flipp else ""
    st.caption(
        f"Sources: shopmarketbasket.com/weekly-flyer, "
        f"Whole Foods ({wf_label}, ID {wf_store_id}){flipp_desc}."
    )

    cache_state_key = "loaded_flyers_payload"
    refresh_clicked = st.button(
        "🔄 Load / Refresh Live Weekly Flyers",
        key="refresh_flyers_btn",
        help=(
            "Fetch current weekly sales circulars from Market Basket, "
            "Whole Foods, and nearby stores on Flipp."
        ),
    )

    cached_payload = st.session_state.get(cache_state_key)
    cache_sig = (wf_store_id, store_zip, include_flipp)
    should_auto_load = bool(config.get("enable_live_flyers")) and (
        not isinstance(cached_payload, dict) or cached_payload.get("cache_sig") != cache_sig
    )

    if refresh_clicked or should_auto_load:
        with st.spinner("Loading live weekly flyers across local stores..."):
            mb_deals, wf_deals, flipp_deals, warnings = run_async(
                _fetch_all_flyers(wf_store_id, store_zip=store_zip, include_flipp=include_flipp)
            )
        cached_payload = {
            "cache_sig": cache_sig,
            "deals": mb_deals + wf_deals + flipp_deals,
            "mb_count": len(mb_deals),
            "wf_count": len(wf_deals),
            "flipp_count": len(flipp_deals),
            "warnings": warnings,
        }
        st.session_state[cache_state_key] = cached_payload

    if not isinstance(cached_payload, dict):
        st.info(
            "Click **Load / Refresh Live Weekly Flyers** above (or toggle "
            "**Enable Live Weekly Flyers** in the sidebar) to view active weekly sale items."
        )
        return

    for warn in cached_payload.get("warnings", []):
        st.warning(warn)

    raw_deals = cached_payload.get("deals", [])
    deals: list[FlyerDeal] = [FlyerDeal.model_validate(d) for d in raw_deals]
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Market Basket Deals", cached_payload.get("mb_count", 0))
    with col2:
        st.metric("Whole Foods Deals", cached_payload.get("wf_count", 0))
    with col3:
        st.metric("Flipp Nearby Deals", cached_payload.get("flipp_count", 0))
    with col4:
        matched_rows = build_matched_flyer_rows(grocery_list, deals, use_prime=use_prime)
        st.metric("Matches on Your List", len(matched_rows))

    st.markdown("#### 🎯 On-Sale Matches for Your Current List")
    if matched_rows:
        st.dataframe(pd.DataFrame(matched_rows), use_container_width=True, hide_index=True)
    else:
        st.info("No items from your current list matched the active weekly flyers.")

    st.divider()
    st.markdown("#### 📋 Browse All Weekly On-Sale Items")
    store_options = ["All Stores"] + sorted({d.store_name for d in deals})
    col_store, col_search, col_feat = st.columns([1, 2, 1])
    with col_store:
        store_filter = st.selectbox(
            "Filter Store",
            options=store_options,
            key="flyer_store_filter",
        )
    with col_search:
        search_text = (
            st.text_input(
                "Search Flyer Items",
                placeholder="e.g. chicken, salmon, strawberries, yogurt...",
                key="flyer_search_input",
            )
            .strip()
            .lower()
        )
    with col_feat:
        featured_only = st.checkbox("Featured Only", value=False, key="flyer_featured_only")

    filtered_deals = [
        d
        for d in deals
        if (store_filter == "All Stores" or d.store_name == store_filter)
        and (not featured_only or d.featured)
        and (
            not search_text
            or search_text in d.product_name.lower()
            or search_text in (d.brand or "").lower()
            or search_text in (d.description or "").lower()
            or search_text in (d.department or "").lower()
        )
    ]
    st.caption(f"Showing {len(filtered_deals)} of {len(deals)} weekly sale items")
    if filtered_deals:
        st.dataframe(
            pd.DataFrame(build_flyer_rows(filtered_deals, use_prime=use_prime)),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("No weekly sale items match your current filter.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    """Top-level Streamlit render function."""
    st.set_page_config(
        page_title=settings.app_title,
        page_icon="🛒",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    config = render_sidebar()
    render_hero()

    flash_success = st.session_state.pop("flash_success", None)
    if flash_success:
        st.success(flash_success)

    selected_list_id, selected_list = render_list_selector(config)

    if selected_list_id and selected_list:
        with st.expander("➕ Add an Item to this List", expanded=False):
            with st.form("add_item_form", clear_on_submit=True):
                col_name, col_qty, col_unit = st.columns([3, 1, 1])
                with col_name:
                    new_name = st.text_input(
                        "Item Name", placeholder="e.g., Avocados, Greek Yogurt"
                    )
                with col_qty:
                    new_qty = st.number_input("Quantity", min_value=0.5, step=0.5, value=1.0)
                with col_unit:
                    new_unit = st.selectbox(
                        "Unit",
                        options=[u.value for u in Unit],
                        index=0,
                    )
                submitted = st.form_submit_button("Add to List")
                if submitted and new_name.strip():
                    new_item = GroceryItem(
                        id=f"item_{uuid.uuid4().hex[:8]}",
                        name=new_name.strip(),
                        quantity=float(new_qty),
                        unit=Unit(new_unit),
                    )
                    client = _get_anylist_client(config)

                    async def _add() -> None:
                        async with client:
                            await client.authenticate()
                            await client.add_item(selected_list.id, new_item)

                    try:
                        run_async(_add())
                    except (AnyListAuthError, AnyListAPIError) as err:
                        st.error(f"Failed to add item: {err}")
                    else:
                        if not config["demo_mode"]:
                            selected_list.items.append(new_item)
                            _invalidate_live_lists_cache()
                        st.session_state["flash_success"] = (
                            f"Added **{new_name.strip()}** ({new_qty:g} {new_unit})!"
                        )
                        st.rerun()

        if not config.get("allowed_stores"):
            st.warning("Select at least one store in the sidebar to view price comparisons.")
            return

        all_baseline_stores = set(MockPricingProvider.STORE_PROFILES.keys())
        selected_stores = config.get("allowed_stores")
        multi_chain_live = bool(
            (config.get("enable_live_flyers") and config.get("enable_flipp"))
            or config.get("enable_open_prices")
        )
        effective_allowed_stores = (
            None
            if (
                multi_chain_live
                and isinstance(selected_stores, list)
                and set(selected_stores) == all_baseline_stores
            )
            else selected_stores
        )

        with st.spinner("Fetching pricing & finding optimal deals..."):
            cart = run_async(
                _run_pricing(
                    selected_list,
                    config["store_zip"],
                    allowed_stores=effective_allowed_stores,
                    max_stores=config.get("max_stores"),
                    enable_live_flyers=config.get("enable_live_flyers"),
                    enable_flipp=config.get("enable_flipp"),
                    enable_open_prices=config.get("enable_open_prices"),
                    flyer_only_for_live_stores=bool(config.get("advertised_only")),
                    whole_foods_store_id=config.get("whole_foods_store_id"),
                    whole_foods_prime=config.get("whole_foods_prime"),
                )
            )

        tab_compare, tab_cart, tab_flyers = st.tabs(
            ["Price Comparison", "Optimised Cart", "🏷️ Weekly Sales & Flyers"]
        )

        with tab_compare:
            render_price_comparison(cart)

        with tab_cart:
            render_optimised_cart(cart, selected_list, config=config)

        with tab_flyers:
            render_flyers_tab(selected_list, config)
    else:
        st.info("Select a grocery list from the dropdown above to get started.", icon="👆")


if __name__ == "__main__":
    main()
