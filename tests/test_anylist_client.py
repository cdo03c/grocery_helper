"""Tests for the AnyList API client."""

from __future__ import annotations

import httpx
import pytest

from grocery_helper.anylist_client import (
    AnyListAPIError,
    AnyListAuthError,
    AnyListClient,
)
from grocery_helper.models import GroceryItem, Unit

# ---------------------------------------------------------------------------
# Exception classes
# ---------------------------------------------------------------------------


class TestExceptionTypes:
    def test_auth_error_is_exception(self) -> None:
        err = AnyListAuthError("not authenticated")
        assert isinstance(err, Exception)
        assert str(err) == "not authenticated"

    def test_api_error_is_exception(self) -> None:
        err = AnyListAPIError("bad response")
        assert isinstance(err, Exception)
        assert str(err) == "bad response"


# ---------------------------------------------------------------------------
# Constructor / init
# ---------------------------------------------------------------------------


class TestAnyListClientInit:
    def test_explicit_credentials(self) -> None:
        client = AnyListClient(
            email="a@b.com",
            password="s3cr3t",
            base_url="https://example.com",
            demo_mode=True,
        )
        assert client._email == "a@b.com"
        assert client._password == "s3cr3t"
        assert client._base_url == "https://example.com"
        assert client.is_demo is True

    def test_defaults_to_settings(self) -> None:
        client = AnyListClient()
        assert isinstance(client._email, str)
        assert isinstance(client._base_url, str)
        assert client.is_demo is False

    def test_initial_state(self) -> None:
        client = AnyListClient()
        assert client._session_token is None
        assert client._http is None
        assert client.is_authenticated is False


# ---------------------------------------------------------------------------
# Context manager
# ---------------------------------------------------------------------------


class TestContextManager:
    @pytest.mark.asyncio
    async def test_aenter_creates_http_client(self) -> None:
        async with AnyListClient() as client:
            assert client._http is not None

    @pytest.mark.asyncio
    async def test_aexit_closes_http_client(self) -> None:
        client = AnyListClient()
        async with client:
            http = client._http
            assert http is not None
        assert http.is_closed

    @pytest.mark.asyncio
    async def test_aexit_handles_none_http(self) -> None:
        client = AnyListClient()
        await client.__aexit__(None, None, None)


# ---------------------------------------------------------------------------
# _require_auth
# ---------------------------------------------------------------------------


class TestRequireAuth:
    def test_raises_when_no_token(self) -> None:
        client = AnyListClient()
        with pytest.raises(AnyListAuthError, match="Not authenticated"):
            client._require_auth()

    def test_passes_when_token_set(self) -> None:
        client = AnyListClient()
        client._session_token = "tok_abc123"
        client._require_auth()


# ---------------------------------------------------------------------------
# authenticate
# ---------------------------------------------------------------------------


class TestAuthenticate:
    @pytest.mark.asyncio
    async def test_raises_runtime_error_without_context_manager(self) -> None:
        client = AnyListClient()
        with pytest.raises(RuntimeError, match="not started"):
            await client.authenticate()

    @pytest.mark.asyncio
    async def test_raises_auth_error_if_credentials_missing(self) -> None:
        async with AnyListClient(email="", password="") as client:
            with pytest.raises(AnyListAuthError, match="credentials missing"):
                await client.authenticate()

    @pytest.mark.asyncio
    async def test_demo_mode_authenticates_instantly(self) -> None:
        client = AnyListClient(demo_mode=True)
        await client.authenticate()
        assert client.is_authenticated is True
        assert client._session_token == "demo_session_token"

    @pytest.mark.asyncio
    async def test_authenticate_success_with_mock_http(self) -> None:
        async with AnyListClient(email="test@example.com", password="pw") as client:
            assert client._http is not None

            def mock_handler(request: httpx.Request) -> httpx.Response:
                assert request.url.path == "/data/users/login"
                return httpx.Response(200, json={"token": "live_token_xyz"})

            client._http._transport = httpx.MockTransport(mock_handler)
            await client.authenticate()
            assert client.is_authenticated is True
            assert client._session_token == "live_token_xyz"
            assert "Bearer live_token_xyz" in client._http.headers["Authorization"]

    @pytest.mark.asyncio
    async def test_authenticate_invalid_credentials_raises_auth_error(self) -> None:
        async with AnyListClient(email="bad@example.com", password="bad") as client:
            assert client._http is not None

            def mock_handler(_: httpx.Request) -> httpx.Response:
                return httpx.Response(401, json={"error": "unauthorized"})

            client._http._transport = httpx.MockTransport(mock_handler)
            with pytest.raises(AnyListAuthError, match="Invalid"):
                await client.authenticate()

    @pytest.mark.asyncio
    async def test_authenticate_server_error_raises_api_error(self) -> None:
        async with AnyListClient(email="err@example.com", password="pw") as client:
            assert client._http is not None

            def mock_handler(_: httpx.Request) -> httpx.Response:
                return httpx.Response(500, text="Internal Server Error")

            client._http._transport = httpx.MockTransport(mock_handler)
            with pytest.raises(AnyListAPIError, match="status 500"):
                await client.authenticate()


# ---------------------------------------------------------------------------
# Unauthenticated CRUD raises AnyListAuthError
# ---------------------------------------------------------------------------


_ITEM = GroceryItem(id="item_x", name="Milk", unit=Unit.GALLON)


class TestUnauthenticatedStubs:
    @pytest.mark.asyncio
    async def test_get_lists_requires_auth(self) -> None:
        async with AnyListClient() as client:
            with pytest.raises(AnyListAuthError):
                await client.get_lists()

    @pytest.mark.asyncio
    async def test_get_list_requires_auth(self) -> None:
        async with AnyListClient() as client:
            with pytest.raises(AnyListAuthError):
                await client.get_list("list_001")

    @pytest.mark.asyncio
    async def test_add_item_requires_auth(self) -> None:
        async with AnyListClient() as client:
            with pytest.raises(AnyListAuthError):
                await client.add_item("list_001", _ITEM)

    @pytest.mark.asyncio
    async def test_remove_item_requires_auth(self) -> None:
        async with AnyListClient() as client:
            with pytest.raises(AnyListAuthError):
                await client.remove_item("list_001", "item_x")

    @pytest.mark.asyncio
    async def test_update_item_requires_auth(self) -> None:
        async with AnyListClient() as client:
            with pytest.raises(AnyListAuthError):
                await client.update_item("list_001", _ITEM)


# ---------------------------------------------------------------------------
# Demo mode full operations
# ---------------------------------------------------------------------------


class TestDemoModeOperations:
    @pytest.mark.asyncio
    async def test_demo_crud_flow(self) -> None:
        client = AnyListClient(demo_mode=True)
        await client.authenticate()

        lists = await client.get_lists()
        assert len(lists) >= 3
        list_ids = [lst.id for lst in lists]
        assert "list_001" in list_ids

        # Fetch single list
        weekly_shop = await client.get_list("list_001")
        assert weekly_shop.name == "Weekly Shop"
        initial_item_count = len(weekly_shop.items)

        # Add item
        new_item = GroceryItem(id="item_new", name="Blueberries", quantity=2.0, unit=Unit.PACK)
        added = await client.add_item("list_001", new_item)
        assert added.name == "Blueberries"
        assert len(weekly_shop.items) == initial_item_count + 1

        # Update item
        updated_item = GroceryItem(id="item_new", name="Organic Blueberries", quantity=3.0)
        await client.update_item("list_001", updated_item)
        curr = [i for i in weekly_shop.items if i.id == "item_new"][0]
        assert curr.name == "Organic Blueberries"
        assert curr.quantity == 3.0

        # Remove item
        await client.remove_item("list_001", "item_new")
        assert len(weekly_shop.items) == initial_item_count

        # Create list
        created_list = await client.create_list("Target Run")
        assert created_list.name == "Target Run"
        assert created_list.id in client._demo_store

    @pytest.mark.asyncio
    async def test_demo_export_split_lists(self) -> None:
        from grocery_helper.pricing import PricingService

        client = AnyListClient(demo_mode=True)
        pricing = PricingService(store_zip="90210")
        await client.authenticate()

        base_list = await client.get_list("list_001")
        cart = await pricing.optimise(base_list)

        exported = await client.export_split_lists(cart, base_name="Weekly Shop")
        assert len(exported) == len(cart.stores_used)
        exported_names = [lst.name for lst in exported]
        for store in cart.stores_used:
            assert any(store in name for name in exported_names)

    @pytest.mark.asyncio
    async def test_demo_get_nonexistent_list_raises(self) -> None:
        client = AnyListClient(demo_mode=True)
        await client.authenticate()
        with pytest.raises(AnyListAPIError, match="not found"):
            await client.get_list("nonexistent_id")

    @pytest.mark.asyncio
    async def test_demo_update_nonexistent_item_raises(self) -> None:
        client = AnyListClient(demo_mode=True)
        await client.authenticate()
        with pytest.raises(AnyListAPIError, match="not found"):
            await client.update_item("list_001", GroceryItem(id="missing", name="Ghost"))


# ---------------------------------------------------------------------------
# HTTP Live Mode Operations with Mock Transport
# ---------------------------------------------------------------------------


class TestHTTPOperations:
    @pytest.mark.asyncio
    async def test_get_lists_parses_json(self) -> None:
        async with AnyListClient(email="user@test.com", password="pw") as client:
            assert client._http is not None

            def handler(request: httpx.Request) -> httpx.Response:
                if request.url.path == "/data/users/login":
                    return httpx.Response(200, json={"token": "test_tok"})
                if request.url.path == "/data/shopping-lists/get":
                    return httpx.Response(
                        200,
                        json={
                            "shoppingLists": [
                                {
                                    "identifier": "live_list_1",
                                    "name": "Live Grocery List",
                                    "items": [
                                        {
                                            "identifier": "item_1",
                                            "name": "Almond Milk",
                                            "quantity": 1,
                                            "category": "Dairy",
                                        }
                                    ],
                                }
                            ]
                        },
                    )
                return httpx.Response(404)

            client._http._transport = httpx.MockTransport(handler)
            await client.authenticate()
            lists = await client.get_lists()

            assert len(lists) == 1
            assert lists[0].id == "live_list_1"
            assert lists[0].name == "Live Grocery List"
            assert len(lists[0].items) == 1
            assert lists[0].items[0].name == "Almond Milk"

    @pytest.mark.asyncio
    async def test_crud_endpoints(self) -> None:
        async with AnyListClient(email="user@test.com", password="pw") as client:
            assert client._http is not None

            def handler(request: httpx.Request) -> httpx.Response:
                if request.url.path == "/data/users/login":
                    return httpx.Response(200, json={"token": "test_tok"})
                return httpx.Response(200, json={"status": "ok"})

            client._http._transport = httpx.MockTransport(handler)
            await client.authenticate()

            item = GroceryItem(id="item_live", name="Coffee")
            await client.add_item("list_1", item)
            await client.update_item("list_1", item)
            await client.remove_item("list_1", "item_live")
