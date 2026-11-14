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
        default=3600,
        ge=0,
        description="How long to cache price results (seconds)",
    )

    # ── Kroger Developer API ─────────────────────────────────────────────────
    kroger_client_id: str = Field(default="", description="Kroger API Client ID")
    kroger_client_secret: str = Field(default="", description="Kroger API Client Secret")
    kroger_api_base_url: str = Field(
        default="https://api.kroger.com/v1",
        description="Base URL for the Kroger Developer API",
    )

    # ── Weekly Flyer Sources (Market Basket & Whole Foods) ───────────────────
    market_basket_flyer_url: str = Field(
        default="https://www.shopmarketbasket.com/wp-json/mb/v1/weekly-flyer",
        description="JSON endpoint for Market Basket weekly flyer items",
    )
    whole_foods_base_url: str = Field(
        default="https://www.wholefoodsmarket.com",
        description="Base URL for Whole Foods Market website",
    )
    whole_foods_store_id: str = Field(
        default="10010",
        description="Whole Foods store ID for local weekly sales flyer (e.g. 10010 Cambridge)",
    )
    whole_foods_prime: bool = Field(
        default=True,
        description="Apply Prime member pricing for Whole Foods weekly deals",
    )
    enable_live_flyers: bool = Field(
        default=False,
        description="Fetch live weekly flyer sale items from Market Basket, Whole Foods, and Flipp",
    )
    flipp_base_url: str = Field(
        default="https://backflipp.wishabi.com/flipp",
        description="Base URL for Flipp weekly flyer & item search API",
    )
    enable_flipp: bool = Field(
        default=True,
        description=(
            "Include generic nearby grocery store flyers via Flipp when live flyers are enabled"
        ),
    )

    # ── Open Prices (Crowdsourced Retail Shelf Prices) ────────────────────────
    open_prices_base_url: str = Field(
        default="https://prices.openfoodfacts.org/api/v1",
        description="Base URL for the Open Food Facts Open Prices REST API",
    )
    enable_open_prices: bool = Field(
        default=False,
        description=(
            "Overlay crowdsourced retail receipt/shelf prices from Open Prices "
            "(prices.openfoodfacts.org)"
        ),
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
