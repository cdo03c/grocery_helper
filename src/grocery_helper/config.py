"""
config.py
---------
Centralised configuration loaded from environment variables or a .env file.

Usage:
    from grocery_helper.config import settings
    print(settings.anylist_api_base_url)
"""

from __future__ import annotations

from functools import lru_cache

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings  # pip: pydantic-settings

# Load .env file at import time (no-op if file doesn't exist)
load_dotenv()


class Settings(BaseSettings):
    """Application settings, sourced from environment variables."""

    # ── AnyList ──────────────────────────────────────────────────────────────
    anylist_email: str = Field(default="", description="AnyList account email")
    anylist_password: str = Field(default="", description="AnyList account password")
    anylist_api_base_url: str = Field(
        default="https://www.anylist.com",
        description="Base URL for the AnyList API",
    )

    # ── Pricing ───────────────────────────────────────────────────────────────
    default_store_zip: str = Field(default="", description="ZIP code for store lookups")
    price_cache_ttl_seconds: int = Field(
        default=3600, description="How long to cache price results (seconds)"
    )

    # ── Streamlit ─────────────────────────────────────────────────────────────
    app_title: str = Field(default="Grocery Helper", description="Streamlit page title")
    debug: bool = Field(default=False, description="Enable verbose debug logging")

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached Settings instance (singleton per process)."""
    return Settings()


# Convenience alias
settings = get_settings()
