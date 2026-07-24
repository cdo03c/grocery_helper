"""Tests for the Streamlit app using streamlit.testing.v1.AppTest."""
from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest


APP_PATH = "src/grocery_helper/app.py"


@pytest.fixture()
def at() -> AppTest:
    """Return a fresh AppTest instance for each test."""
    return AppTest.from_file(APP_PATH, default_timeout=10)


# ---------------------------------------------------------------------------
# Basic load / no crash
# ---------------------------------------------------------------------------


class TestAppLoads:
    def test_no_exception_on_load(self, at: AppTest) -> None:
        at.run()
        assert not at.exception

    def test_page_title_in_markdown(self, at: AppTest) -> None:
        at.run()
        # The hero renders st.title("🛒 Grocery Helper")
        titles = [t.value for t in at.title]
        assert any("Grocery Helper" in t for t in titles)

    def test_initial_info_shown(self, at: AppTest) -> None:
        """With no list selected, the 'select a list' info block is visible."""
        at.run()
        infos = [i.value for i in at.info]
        assert any("Select a grocery list" in i for i in infos)


# ---------------------------------------------------------------------------
# Sidebar inputs
# ---------------------------------------------------------------------------


class TestSidebar:
    def test_sidebar_has_email_input(self, at: AppTest) -> None:
        at.run()
        keys = [inp.key for inp in at.text_input]
        assert "anylist_email" in keys

    def test_sidebar_has_password_input(self, at: AppTest) -> None:
        at.run()
        keys = [inp.key for inp in at.text_input]
        assert "anylist_password" in keys

    def test_sidebar_has_zip_input(self, at: AppTest) -> None:
        at.run()
        keys = [inp.key for inp in at.text_input]
        assert "store_zip" in keys


# ---------------------------------------------------------------------------
# List selector
# ---------------------------------------------------------------------------


class TestListSelector:
    def test_selectbox_present(self, at: AppTest) -> None:
        at.run()
        assert len(at.selectbox) >= 1

    def test_selecting_a_list_shows_tabs(self, at: AppTest) -> None:
        """Picking a real list ID should reveal the Price Comparison tab content."""
        at.run()
        # Select the first non-placeholder option
        at.selectbox[0].select("Weekly Shop (placeholder)").run()
        assert not at.exception
        # The price-comparison info placeholder should appear
        infos = [i.value for i in at.info]
        assert any("Price comparison" in i for i in infos)

    def test_selecting_second_list_works(self, at: AppTest) -> None:
        at.run()
        at.selectbox[0].select("Party Supplies (placeholder)").run()
        assert not at.exception


# ---------------------------------------------------------------------------
# Price comparison & optimised cart placeholders
# ---------------------------------------------------------------------------


class TestPlaceholderSections:
    def test_price_comparison_info_shown(self, at: AppTest) -> None:
        at.run()
        at.selectbox[0].select("Weekly Shop (placeholder)").run()
        infos = [i.value for i in at.info]
        assert any("AnyListClient" in i for i in infos)

    def test_optimised_cart_info_shown(self, at: AppTest) -> None:
        at.run()
        at.selectbox[0].select("Weekly Shop (placeholder)").run()
        infos = [i.value for i in at.info]
        assert any("PricingService" in i for i in infos)
