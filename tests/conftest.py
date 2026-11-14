"""Shared pytest fixtures isolating unit tests from local .env credentials and live feeds."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_settings_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent local .env credentials or live toggles from hitting external APIs during tests."""
    from grocery_helper.config import settings

    monkeypatch.setattr(settings, "anylist_email", "")
    monkeypatch.setattr(settings, "anylist_password", "")
    monkeypatch.setattr(settings, "enable_live_flyers", False)
    monkeypatch.setattr(settings, "enable_open_prices", False)
    monkeypatch.setattr(settings, "kroger_client_id", "")
    monkeypatch.setattr(settings, "kroger_client_secret", "")
