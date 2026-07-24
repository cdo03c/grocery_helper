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

import streamlit as st

from grocery_helper.config import settings
from grocery_helper.utils import configure_logging

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
# Sidebar – credentials & settings
# ---------------------------------------------------------------------------

def render_sidebar() -> dict[str, str]:
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
            value=settings.default_store_zip,
            placeholder="90210",
            key="store_zip",
        )

        st.divider()
        st.caption("Grocery Helper v0.1.0")

    return {"email": email, "password": password, "store_zip": store_zip}


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


def render_list_selector() -> str | None:
    """
    Fetch and display available grocery lists; return the selected list ID.

    TODO: Replace the placeholder items with a real AnyList API call.
    """
    st.subheader("📋 Your Lists")

    # Placeholder list until AnyList client is wired up
    placeholder_lists = {
        "— Select a list —": None,
        "Weekly Shop (placeholder)": "list_001",
        "Party Supplies (placeholder)": "list_002",
    }

    choice = st.selectbox("Choose a list", options=list(placeholder_lists.keys()))
    return placeholder_lists[choice]


def render_price_comparison(list_id: str) -> None:
    """
    Fetch prices and render the comparison table for the selected list.

    TODO: Wire up PricingService and AnyListClient here.
    """
    st.subheader("💰 Price Comparison")

    with st.spinner("Fetching prices…"):
        # ── Placeholder ────────────────────────────────────────────────────
        st.info(
            "Price comparison is not yet implemented. "
            "Wire up `AnyListClient` and `PricingService` in this section.",
            icon="ℹ️",
        )
        # ──────────────────────────────────────────────────────────────────


def render_optimised_cart(list_id: str) -> None:
    """Display the optimised cart with store assignments and totals."""
    st.subheader("🏪 Optimised Cart")

    # ── Placeholder ────────────────────────────────────────────────────────
    st.info(
        "The optimised cart will appear here once `PricingService.optimise()` "
        "is implemented.",
        icon="ℹ️",
    )
    # ──────────────────────────────────────────────────────────────────────


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    """Top-level Streamlit render function."""
    sidebar_config = render_sidebar()
    render_hero()

    selected_list_id = render_list_selector()

    if selected_list_id:
        tab_compare, tab_cart = st.tabs(["Price Comparison", "Optimised Cart"])

        with tab_compare:
            render_price_comparison(selected_list_id)

        with tab_cart:
            render_optimised_cart(selected_list_id)
    else:
        st.info("Select a grocery list from the dropdown above to get started.", icon="👆")


if __name__ == "__main__":
    main()
