"""
app.py
------
Streamlit entry-point for the Grocery Helper app.

Run with:
    streamlit run src/grocery_helper/app.py
"""

from __future__ import annotations

import asyncio
import logging
from decimal import Decimal
from typing import Any

import pandas as pd
import streamlit as st

from grocery_helper.anylist_client import AnyListAPIError, AnyListAuthError, AnyListClient
from grocery_helper.config import settings
from grocery_helper.models import GroceryList, OptimizedCart
from grocery_helper.pricing import PricingService
from grocery_helper.utils import configure_logging, format_currency, format_savings

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

configure_logging()
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Page config (must be first Streamlit call)
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title=settings.app_title,
    page_icon="🛒",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ---------------------------------------------------------------------------
# Async Helpers
# ---------------------------------------------------------------------------


def run_async(coro: Any) -> Any:
    """Helper to execute an async coroutine synchronously in Streamlit."""
    try:
        return asyncio.run(coro)
    except RuntimeError:
        loop = asyncio.get_event_loop()
        return loop.run_until_complete(coro)


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
        available_stores = ["ALDI", "Trader Joe's", "Kroger", "Whole Foods"]
        allowed_stores = st.multiselect(
            "Allowed Stores",
            options=available_stores,
            default=available_stores,
            help="Filter stores considered during pricing & optimization.",
            key="allowed_stores_filter",
        )
        max_stores = st.slider(
            "Max Stores to Visit",
            min_value=1,
            max_value=max(1, len(allowed_stores)),
            value=max(1, len(allowed_stores)),
            help="Limit the shopping route to at most N stores to reduce trips.",
            key="max_stores_slider",
        )

        st.divider()
        st.caption("Grocery Helper v0.1.0")

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


def render_hero() -> None:
    """Render the app header / hero section."""
    st.title("🛒 Grocery Helper")
    st.markdown(
        "Connect your **AnyList** account to automatically compare prices "
        "across stores and find the best deals on your grocery list."
    )
    st.divider()


async def _fetch_user_lists(config: dict[str, Any]) -> list[GroceryList]:
    """Fetch lists from AnyList or Demo store."""
    client = AnyListClient(
        email=config["email"],
        password=config["password"],
        demo_mode=config["demo_mode"],
    )
    async with client:
        await client.authenticate()
        return await client.get_lists()


def render_list_selector(config: dict[str, Any]) -> tuple[str | None, GroceryList | None]:
    """
    Fetch and display available grocery lists; return the selected list ID and GroceryList.
    """
    st.subheader("📋 Your Lists")

    try:
        lists = run_async(_fetch_user_lists(config))
    except (AnyListAuthError, AnyListAPIError) as err:
        st.error(f"Failed to load AnyList lists: {err}")
        return None, None

    if not lists:
        st.warning("No grocery lists found.")
        return None, None

    list_options: dict[str, GroceryList | None] = {"— Select a list —": None}
    for lst in lists:
        list_options[f"{lst.name} ({len(lst.items)} items)"] = lst

    choice = st.selectbox("Choose a list", options=list(list_options.keys()))
    selected_list = list_options.get(choice)
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


def render_price_comparison(cart: OptimizedCart) -> None:
    """Render the detailed price comparison table."""
    st.subheader("💰 Price Comparison Across Stores")

    if not cart.comparisons:
        st.info("No price comparison data available.")
        return

    # Extract all unique stores
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
                if sp.sale_price is not None:
                    row[store] = f"${sp.sale_price:.2f} (Sale)"
                else:
                    row[store] = f"${sp.price:.2f}"
            else:
                row[store] = "N/A"

        row["Cheapest Store"] = comp.cheapest.store_name if comp.cheapest else "N/A"
        row["Best Price"] = (
            format_currency(comp.cheapest.effective_price) if comp.cheapest else "N/A"
        )
        qty_dec = Decimal(str(comp.item.quantity))
        row["Potential Savings"] = (
            format_savings(comp.savings * qty_dec) if comp.savings else "None"
        )
        rows.append(row)

    df = pd.DataFrame(rows)
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
            help="Total cost when buying each item at its cheapest store.",
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
            st.metric(
                label="Best Single Store",
                value=best_store,
                delta=f"+{format_currency(diff)} vs split",
                delta_color="inverse",
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
    items_by_store: dict[str, list[tuple[str, str, str]]] = {}
    for comp in cart.comparisons:
        if comp.cheapest:
            store = comp.cheapest.store_name
            qty_dec = Decimal(str(comp.item.quantity))
            items_by_store.setdefault(store, []).append(
                (
                    comp.item.name,
                    f"{comp.item.quantity:g} {comp.item.unit}",
                    format_currency(comp.cheapest.effective_price * qty_dec),
                )
            )

    for store, items in items_by_store.items():
        subtotal = sum(float(item[2].replace("$", "")) for item in items)
        with st.expander(
            f"📍 **{store}** ({len(items)} items — Total: ${subtotal:.2f})", expanded=True
        ):
            for name, qty, price in items:
                st.write(f"- **{name}** ({qty}): {price}")

    if config is not None:
        st.divider()
        if st.button(
            "📤 Export Store Lists to AnyList",
            help="Create store-specific lists in AnyList with the assigned items.",
        ):
            client = AnyListClient(
                email=config["email"],
                password=config["password"],
                demo_mode=config["demo_mode"],
            )

            async def _export() -> list[GroceryList]:
                async with client:
                    await client.authenticate()
                    return await client.export_split_lists(cart, base_name=grocery_list.name)

            with st.spinner("Creating store lists in AnyList..."):
                created = run_async(_export())
            st.success(f"Created {len(created)} store-specific list(s) in AnyList:")
            for cl in created:
                st.write(f"- 📋 **{cl.name}** ({len(cl.items)} items)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    """Top-level Streamlit render function."""
    config = render_sidebar()
    render_hero()

    selected_list_id, selected_list = render_list_selector(config)

    if selected_list_id and selected_list:
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
