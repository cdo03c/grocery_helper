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
from grocery_helper.models import GroceryItem, GroceryList, OptimizedCart, Unit
from grocery_helper.pricing import MockPricingProvider, PricingService
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

        st.divider()
        st.caption(f"Grocery Helper v{__version__}")

    return {
        "email": email,
        "password": password,
        "store_zip": store_zip,
        "demo_mode": demo_mode,
        "allowed_stores": allowed_stores,
        "max_stores": max_stores,
    }


# ---------------------------------------------------------------------------
# Page sections
# ---------------------------------------------------------------------------


def _get_anylist_client(config: dict[str, Any]) -> AnyListClient:
    """Return a session-persisted demo client or a fresh live AnyList client."""
    if config["demo_mode"]:
        client = st.session_state.get("demo_anylist_client")
        if not isinstance(client, AnyListClient):
            client = AnyListClient(demo_mode=True)
            st.session_state["demo_anylist_client"] = client
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
            st.session_state["cached_live_lists"] = lists
            st.session_state["cached_live_lists_key"] = cache_fp
        else:
            lists = st.session_state.get("cached_live_lists", [])

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
) -> OptimizedCart:
    """Fetch prices and optimize the cart."""
    service = PricingService(store_zip=store_zip)
    return await service.optimise(
        grocery_list,
        allowed_stores=allowed_stores,
        max_stores=max_stores,
    )


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
                if sp.sale_price is not None:
                    row[store] = f"{format_currency(sp.sale_price)}{unit_suffix} (Sale)"
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
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric(
            label="Optimised Basket Total",
            value=format_currency(cart.total_cost),
            help="Total cost when buying each item at its assigned store.",
        )
    with col2:
        st.metric(
            label="Estimated Total Savings",
            value=format_currency(cart.estimated_savings),
            help="Savings compared to purchasing items at the highest-priced store.",
        )
    with col3:
        if cart.best_single_store:
            best_store, best_total = cart.best_single_store
            diff = best_total - cart.total_cost
            if diff > 0:
                delta_str: str | None = f"+{format_currency(diff)} vs split"
                delta_color: Literal["normal", "inverse", "off"] = "inverse"
            elif diff == 0:
                delta_str = "Matches route total"
                delta_color = "off"
            else:
                delta_str = f"-{format_currency(abs(diff))} vs route"
                delta_color = "normal"
            st.metric(
                label="Best Single Store",
                value=best_store,
                delta=delta_str,
                delta_color=delta_color,
                help="Cheapest option if you only make one stop.",
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

        with st.spinner("Fetching pricing & finding optimal deals..."):
            cart = run_async(
                _run_pricing(
                    selected_list,
                    config["store_zip"],
                    allowed_stores=config.get("allowed_stores"),
                    max_stores=config.get("max_stores"),
                )
            )

        tab_compare, tab_cart = st.tabs(["Price Comparison", "Optimised Cart"])

        with tab_compare:
            render_price_comparison(cart)

        with tab_cart:
            render_optimised_cart(cart, selected_list, config=config)
    else:
        st.info("Select a grocery list from the dropdown above to get started.", icon="👆")


if __name__ == "__main__":
    main()
