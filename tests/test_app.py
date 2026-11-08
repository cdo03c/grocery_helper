"""Tests for the Streamlit app using streamlit.testing.v1.AppTest."""

from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest

APP_PATH = "src/grocery_helper/app.py"


@pytest.fixture()
def at() -> AppTest:
    """Return a fresh AppTest instance for each test."""
    return AppTest.from_file(APP_PATH, default_timeout=25)


# ---------------------------------------------------------------------------
# Basic load / no crash
# ---------------------------------------------------------------------------


class TestAppLoads:
    def test_no_exception_on_load(self, at: AppTest) -> None:
        at.run()
        assert not at.exception

    def test_page_title_in_markdown(self, at: AppTest) -> None:
        at.run()
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

    def test_sidebar_has_demo_toggle(self, at: AppTest) -> None:
        at.run()
        keys = [t.key for t in at.toggle]
        assert "demo_mode_toggle" in keys

    def test_sidebar_has_store_filters(self, at: AppTest) -> None:
        at.run()
        multiselect_keys = [m.key for m in at.multiselect]
        assert "allowed_stores_filter" in multiselect_keys
        slider_keys = [s.key for s in at.slider]
        assert "max_stores_slider" in slider_keys


# ---------------------------------------------------------------------------
# List selector & Full Optimization Flow
# ---------------------------------------------------------------------------


class TestListSelectorAndResults:
    def test_selectbox_present(self, at: AppTest) -> None:
        at.run()
        assert len(at.selectbox) >= 1

    def test_selecting_a_list_renders_comparison_and_cart(self, at: AppTest) -> None:
        """Selecting a list from the demo store should compute prices and render UI components."""
        at.run()
        # Selectbox options: index 0 is "— Select a list —", index 1 is "Weekly Shop (6 items)"
        assert len(at.selectbox[0].options) >= 2
        at.selectbox[0].select_index(1).run()

        assert not at.exception

        # Verify subheaders for Price Comparison and Optimised Cart exist
        subheaders = [s.value for s in at.subheader]
        assert any("Price Comparison" in s for s in subheaders)
        assert any("Optimised Cart" in s for s in subheaders)

        # Verify metrics (Total, Savings, Best Single Store)
        labels = [m.label for m in at.metric]
        assert "Optimised Basket Total" in labels
        assert "Estimated Total Savings" in labels

        # Verify comparison dataframe table rendered
        assert len(at.dataframe) >= 1
        df = at.dataframe[0].value
        assert "Item" in df.columns
        assert "Cheapest Store" in df.columns
        assert "Best Price" in df.columns

    def test_selecting_second_list(self, at: AppTest) -> None:
        """Selecting Party Supplies list works smoothly without errors."""
        at.run()
        at.selectbox[0].select_index(2).run()
        assert not at.exception
        labels = [m.label for m in at.metric]
        assert "Optimised Basket Total" in labels
