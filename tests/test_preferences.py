"""Tests for persistent non-secret UI preferences and store-default configuration."""

from __future__ import annotations

from pathlib import Path

import pytest

from grocery_helper.config import Settings
from grocery_helper.preferences import (
    clear_ui_settings,
    load_ui_settings,
    resolve_preferences_path,
    save_ui_settings,
)


def test_save_load_merge_and_clear_preferences(tmp_path: Path) -> None:
    prefs_file = tmp_path / "nested" / "ui_settings.json"
    assert load_ui_settings(prefs_file) == {}

    saved_path = save_ui_settings(
        {
            "store_zip": "02139",
            "allowed_stores": ["Market Basket", "Whole Foods"],
            "max_stores": 2,
            "anylist_email": "secret@example.com",
            "anylist_password": "should_never_be_saved",
            "unknown_key": "ignored",
        },
        prefs_file,
    )
    assert saved_path == prefs_file
    assert prefs_file.exists()

    loaded = load_ui_settings(prefs_file)
    assert loaded == {
        "store_zip": "02139",
        "allowed_stores": ["Market Basket", "Whole Foods"],
        "max_stores": 2,
    }
    assert "anylist_email" not in loaded
    assert "anylist_password" not in loaded

    # Partial merge retains prior fields while adding selected_list_id
    save_ui_settings({"selected_list_id": "list_abc"}, prefs_file)
    merged = load_ui_settings(prefs_file)
    assert merged["selected_list_id"] == "list_abc"
    assert merged["allowed_stores"] == ["Market Basket", "Whole Foods"]

    assert clear_ui_settings(prefs_file) is True
    assert not prefs_file.exists()
    assert clear_ui_settings(prefs_file) is False


def test_load_corrupt_json_and_disabled_path(tmp_path: Path) -> None:
    bad_file = tmp_path / "broken.json"
    bad_file.write_text("{not valid json", encoding="utf-8")
    assert load_ui_settings(bad_file) == {}

    assert resolve_preferences_path("") is None
    assert save_ui_settings({"store_zip": "02139"}, "") is None
    assert load_ui_settings("") == {}
    assert clear_ui_settings("") is False


def test_config_default_allowed_stores_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEFAULT_ALLOWED_STORES", "Market Basket, Whole Foods, Trader Joe's")
    monkeypatch.setenv("DEFAULT_MAX_STORES", "2")
    cfg = Settings(_env_file=None)  # type: ignore[call-arg]
    assert cfg.default_allowed_stores == ["Market Basket", "Whole Foods", "Trader Joe's"]
    assert cfg.default_max_stores == 2

    monkeypatch.setenv("DEFAULT_ALLOWED_STORES", '["ALDI", "Market Basket"]')
    cfg_json = Settings(_env_file=None)  # type: ignore[call-arg]
    assert cfg_json.default_allowed_stores == ["ALDI", "Market Basket"]
