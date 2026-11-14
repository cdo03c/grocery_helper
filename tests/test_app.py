"""Tests for the Streamlit app using streamlit.testing.v1.AppTest."""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP_PATH = "src/grocery_helper/app.py"


@pytest.fixture()
def at(monkeypatch: pytest.MonkeyPatch) -> AppTest:
    """Return a fresh AppTest instance forced into offline demo mode."""
    from grocery_helper.config import settings

    monkeypatch.setattr(settings, "anylist_email", "")
    monkeypatch.setattr(settings, "anylist_password", "")
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
        toggle_keys = [t.key for t in at.toggle]
        assert "open_prices_toggle" in toggle_keys


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

        # Verify tab order: Optimised Cart first, Price Comparison second
        assert [t.label for t in at.tabs[:2]] == ["Optimised Cart", "Price Comparison"]

        # Verify subheaders for Price Comparison and Optimised Cart exist
        subheaders = [s.value for s in at.subheader]
        assert any("Price Comparison" in s for s in subheaders)
        assert any("Optimised Cart" in s for s in subheaders)

        # Verify metrics (Total, Single-Store Savings, Best Single Store, Max Savings)
        labels = [m.label for m in at.metric]
        assert "Optimised Basket Total" in labels
        assert "Savings vs. Best Single Store" in labels
        assert "Best Single Store" in labels
        assert "Estimated Total Savings" in labels

        # Verify comparison dataframe table rendered
        comp_dfs = [d.value for d in at.dataframe if "Item" in d.value.columns]
        assert comp_dfs
        df = comp_dfs[0]
        assert "Cheapest Store" in df.columns
        assert "Best Price" in df.columns

    def test_selecting_second_list(self, at: AppTest) -> None:
        """Selecting Party Supplies list works smoothly without errors."""
        at.run()
        at.selectbox[0].select_index(2).run()
        assert not at.exception
        labels = [m.label for m in at.metric]
        assert "Optimised Basket Total" in labels

    def test_single_and_empty_allowed_stores_do_not_crash(self, at: AppTest) -> None:
        """Selecting 1 or 0 allowed stores must not crash the max_stores slider."""
        at.run()
        at.multiselect(key="allowed_stores_filter").set_value(["ALDI"]).run()
        assert not at.exception

        at.multiselect(key="allowed_stores_filter").set_value([]).run()
        assert not at.exception
        warnings = [w.value for w in at.warning]
        assert any("Select at least one store" in w for w in warnings)

    def test_add_item_persists_in_demo_mode_and_keeps_selection(self, at: AppTest) -> None:
        """Adding an item in demo mode should persist across rerun and keep list selected."""
        at.run()
        at.selectbox[0].select_index(1).run()
        assert not at.exception

        # Populate the add_item_form text_input (first non-sidebar text_input)
        item_name_inputs = [ti for ti in at.text_input if ti.key is None]
        assert len(item_name_inputs) >= 1
        item_name_inputs[0].set_value("Greek Yogurt")
        submit_buttons = [b for b in at.button if "Add to List" in b.label]
        assert len(submit_buttons) == 1
        submit_buttons[0].click().run()

        assert not at.exception
        comp_dfs = [d.value for d in at.dataframe if "Item" in d.value.columns]
        assert comp_dfs
        assert "Greek Yogurt" in comp_dfs[0]["Item"].tolist()

    def test_max_stores_constraint_matches_route_expanders(self, at: AppTest) -> None:
        """Setting max_stores=1 must constrain Shopping Route expanders to a single store."""
        at.run()
        at.slider(key="max_stores_slider").set_value(1).run()
        at.selectbox[0].select_index(1).run()
        assert not at.exception

        route_expanders = [e for e in at.expander if "Total:" in e.label]
        assert len(route_expanders) == 1


class TestAppViewModels:
    def test_build_comparison_rows_and_group_cart_by_store(self) -> None:
        """Pure view-model helpers respect assigned stores and Decimal subtotals."""
        from decimal import Decimal

        from grocery_helper.app import build_comparison_rows, group_cart_by_store
        from grocery_helper.models import (
            GroceryItem,
            OptimizedCart,
            PriceComparison,
            StorePrice,
            Unit,
        )

        milk = GroceryItem(id="i1", name="Milk", quantity=2.0, unit=Unit.GALLON, category="Dairy")
        comp = PriceComparison(
            item=milk,
            prices=[
                StorePrice(
                    item_name="Milk",
                    store_name="ALDI",
                    price=Decimal("3.50"),
                    unit=Unit.GALLON,
                ),
                StorePrice(
                    item_name="Milk",
                    store_name="Kroger",
                    price=Decimal("4.00"),
                    sale_price=Decimal("3.00"),
                    unit=Unit.GALLON,
                ),
            ],
        )
        cart = OptimizedCart(
            list_id="l1",
            store_assignments={"i1": "ALDI"},
            total_cost=Decimal("7.00"),
            estimated_savings=Decimal("0.00"),
            comparisons=[comp],
            max_stores_constraint=1,
        )

        rows = build_comparison_rows(cart)
        assert len(rows) == 1
        assert rows[0]["Assigned Store"] == "ALDI"
        assert rows[0]["Cheapest Store"] == "Kroger"
        assert "(Sale)" in rows[0]["Kroger"]

        items_by_store, subtotals = group_cart_by_store(cart)
        assert list(items_by_store.keys()) == ["ALDI"]
        assert subtotals["ALDI"] == Decimal("7.00")


class TestFlyerHelpers:
    def test_build_flyer_rows_and_matched_flyer_rows(self) -> None:
        from decimal import Decimal

        from grocery_helper.app import build_flyer_rows, build_matched_flyer_rows
        from grocery_helper.models import FlyerDeal, GroceryItem, GroceryList, Unit

        deals = [
            FlyerDeal(
                store_name="Market Basket",
                product_name="Fresh Strawberries",
                regular_price=Decimal("4.99"),
                sale_price=Decimal("2.50"),
                unit=Unit.EACH,
                deal_text="2 for $5 · Save $4.98",
                department="Produce",
                valid_from="2026-11-08",
                valid_to="2026-11-14",
                featured=True,
            ),
            FlyerDeal(
                store_name="Whole Foods",
                product_name="Organic Honeycrisp Apples",
                brand="Whole Foods Market",
                regular_price=Decimal("3.99"),
                sale_price=Decimal("2.49"),
                prime_price=Decimal("1.99"),
                unit=Unit.LB,
                deal_text="Prime: $1.99/lb",
            ),
        ]
        rows = build_flyer_rows(deals, use_prime=True)
        assert len(rows) == 2
        assert rows[0]["Store"] == "Market Basket"
        assert rows[0]["Best Sale Price"] == "$2.50"
        assert rows[1]["Store"] == "Whole Foods"
        assert rows[1]["Best Sale Price"] == "$1.99 /lb"
        assert rows[1]["Prime Price"] == "$1.99 /lb"

        g_list = GroceryList(
            id="demo_1",
            name="Weekly",
            items=[GroceryItem(id="1", name="Strawberries", quantity=2, unit=Unit.EACH)],
        )
        matched = build_matched_flyer_rows(g_list, deals, use_prime=True)
        assert len(matched) == 1
        assert matched[0]["List Item"] == "Strawberries"
        assert matched[0]["Store"] == "Market Basket"
        assert matched[0]["Sale Price"] == "$2.50"

    def test_live_flyers_sidebar_toggles_present(self, at: AppTest) -> None:
        at.run()
        assert not at.exception
        toggle_keys = {t.key for t in at.toggle}
        assert "live_flyers_toggle" in toggle_keys
        assert "flipp_toggle" in toggle_keys
        assert "wf_prime_toggle" in toggle_keys
        input_keys = {ti.key for ti in at.text_input}
        assert "wf_store_id_input" in input_keys

    def test_deal_to_grocery_item_and_add_from_flyers_tab(self, at: AppTest) -> None:
        from decimal import Decimal

        from grocery_helper.app import deal_to_grocery_item
        from grocery_helper.models import FlyerDeal, Unit

        deal = FlyerDeal(
            store_name="Market Basket",
            product_name="Wild Caught Salmon Fillet",
            sale_price=Decimal("7.99"),
            unit=Unit.LB,
            department="Seafood",
            deal_text="$7.99/lb",
        )
        converted = deal_to_grocery_item(deal, quantity=2.0)
        assert converted.name == "Wild Caught Salmon Fillet"
        assert converted.quantity == 2.0
        assert converted.unit == Unit.LB
        assert converted.category == "Seafood"
        assert converted.notes is not None
        assert "Market Basket" in converted.notes

        at.session_state["loaded_flyers_payload"] = {
            "cache_sig": ("10010", "", True),
            "deals": [deal.model_dump(mode="json")],
            "mb_count": 1,
            "wf_count": 0,
            "flipp_count": 0,
            "warnings": [],
        }
        at.run()
        at.selectbox[0].select_index(1).run()
        assert not at.exception

        at.multiselect(key="flyer_add_picker").set_value([0])
        at.number_input(key="flyer_picker_qty").set_value(2.0)
        add_buttons = [b for b in at.button if "Add Selected Sale Item" in b.label]
        assert len(add_buttons) == 1
        add_buttons[0].click().run()

        assert not at.exception
        assert any("Wild Caught Salmon Fillet" in s.value for s in at.success)
        comp_dfs = [
            d.value
            for d in at.dataframe
            if "Item" in d.value.columns and "Cheapest Store" in d.value.columns
        ]
        assert comp_dfs
        assert "Wild Caught Salmon Fillet" in comp_dfs[-1]["Item"].tolist()

    def test_settings_persist_between_runs_and_reset(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        from grocery_helper.config import settings

        prefs_path = tmp_path / "ui_settings.json"
        monkeypatch.setattr(settings, "anylist_email", "")
        monkeypatch.setattr(settings, "anylist_password", "")
        monkeypatch.setattr(settings, "ui_settings_file", str(prefs_path))
        monkeypatch.setattr(settings, "default_allowed_stores", ["Market Basket", "Whole Foods"])

        # First app run picks up .env default_allowed_stores and lets user customize
        app1 = AppTest.from_file(APP_PATH, default_timeout=25)
        app1.run()
        assert not app1.exception
        assert app1.multiselect(key="allowed_stores_filter").value == [
            "Market Basket",
            "Whole Foods",
        ]

        app1.multiselect(key="allowed_stores_filter").set_value(
            ["Market Basket", "Trader Joe's", "Whole Foods"]
        ).run()
        app1.slider(key="max_stores_slider").set_value(2).run()
        app1.text_input(key="store_zip").set_value("02139").run()
        assert prefs_path.exists()

        # Fresh second AppTest simulates restarting the app
        app2 = AppTest.from_file(APP_PATH, default_timeout=25)
        app2.run()
        assert not app2.exception
        assert app2.multiselect(key="allowed_stores_filter").value == [
            "Market Basket",
            "Trader Joe's",
            "Whole Foods",
        ]
        assert app2.slider(key="max_stores_slider").value == 2
        assert app2.text_input(key="store_zip").value == "02139"

        # Reset button clears saved prefs and restores .env defaults
        reset_btns = [b for b in app2.button if b.key == "reset_saved_settings_btn"]
        assert len(reset_btns) == 1
        reset_btns[0].click().run()
        assert not app2.exception
        assert app2.multiselect(key="allowed_stores_filter").value == [
            "Market Basket",
            "Whole Foods",
        ]
