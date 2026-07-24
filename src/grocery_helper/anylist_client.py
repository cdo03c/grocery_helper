"""
anylist_client.py
-----------------
Async HTTP client for the AnyList API.

NOTE: AnyList does not publish an official public API. This client is a
placeholder / reverse-engineered skeleton. Fill in the real endpoints once
you have confirmed the authentication flow and route structure.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from grocery_helper.config import settings
from grocery_helper.models import GroceryItem, GroceryList

logger = logging.getLogger(__name__)


class AnyListAuthError(Exception):
    """Raised when authentication with AnyList fails."""


class AnyListAPIError(Exception):
    """Raised for unexpected API errors."""


class AnyListClient:
    """
    Async client for interacting with AnyList.

    Example usage::

        async with AnyListClient() as client:
            await client.authenticate()
            lists = await client.get_lists()
    """

    def __init__(
        self,
        email: str | None = None,
        password: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self._email = email or settings.anylist_email
        self._password = password or settings.anylist_password
        self._base_url = base_url or settings.anylist_api_base_url
        self._session_token: str | None = None
        self._http: httpx.AsyncClient | None = None

    # ── Context manager ───────────────────────────────────────────────────────

    async def __aenter__(self) -> "AnyListClient":
        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            headers={"Content-Type": "application/json"},
            timeout=10.0,
        )
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._http:
            await self._http.aclose()

    # ── Auth ──────────────────────────────────────────────────────────────────

    async def authenticate(self) -> None:
        """
        Authenticate with AnyList and store the session token.

        TODO: Replace with actual AnyList authentication endpoint and payload.
        """
        if not self._http:
            raise RuntimeError("Client not started. Use async with AnyListClient().")

        logger.info("Authenticating with AnyList as %s", self._email)

        # ── Placeholder ────────────────────────────────────────────────────
        # response = await self._http.post(
        #     "/auth/login",
        #     json={"email": self._email, "password": self._password},
        # )
        # response.raise_for_status()
        # self._session_token = response.json()["token"]
        # ──────────────────────────────────────────────────────────────────

        raise NotImplementedError(
            "AnyList authentication endpoint is not yet implemented. "
            "See anylist_client.py for the placeholder."
        )

    # ── Lists ─────────────────────────────────────────────────────────────────

    async def get_lists(self) -> list[GroceryList]:
        """
        Fetch all grocery lists for the authenticated user.

        TODO: Map actual AnyList API response to GroceryList models.
        """
        self._require_auth()
        logger.info("Fetching grocery lists")

        # ── Placeholder ────────────────────────────────────────────────────
        # response = await self._http.get("/user/lists")
        # response.raise_for_status()
        # raw_lists = response.json()["lists"]
        # return [GroceryList(**lst) for lst in raw_lists]
        # ──────────────────────────────────────────────────────────────────

        raise NotImplementedError("get_lists is not yet implemented.")

    async def get_list(self, list_id: str) -> GroceryList:
        """Fetch a single grocery list by ID."""
        self._require_auth()
        raise NotImplementedError("get_list is not yet implemented.")

    async def add_item(self, list_id: str, item: GroceryItem) -> GroceryItem:
        """Add an item to an existing list."""
        self._require_auth()
        raise NotImplementedError("add_item is not yet implemented.")

    async def remove_item(self, list_id: str, item_id: str) -> None:
        """Remove an item from a list."""
        self._require_auth()
        raise NotImplementedError("remove_item is not yet implemented.")

    async def update_item(self, list_id: str, item: GroceryItem) -> GroceryItem:
        """Update an existing item on a list."""
        self._require_auth()
        raise NotImplementedError("update_item is not yet implemented.")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _require_auth(self) -> None:
        if not self._session_token:
            raise AnyListAuthError(
                "Not authenticated. Call authenticate() before making API requests."
            )
