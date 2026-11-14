"""Tests for the AnyList API client."""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from grocery_helper.anylist_client import (
    AnyListAPIError,
    AnyListAuthError,
    AnyListClient,
    _encode_field_bytes,
    _encode_field_str,
    _encode_field_varint,
)
from grocery_helper.models import GroceryItem, Unit


def _make_jwt(sub: str = "user_abc") -> str:
    """Build an unsigned test JWT carrying a ``sub`` claim."""
    header = base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(json.dumps({"sub": sub}).encode()).rstrip(b"=").decode()
    return f"{header}.{payload}.sig"


def _build_pb_user_data(
    list_id: str,
    list_name: str,
    items: list[tuple[str, str, str | None, str | None, str | None, bool]],
) -> bytes:
    """Encode a minimal ``PBUserDataResponse`` protobuf payload for tests."""
    encoded_items = b""
    for item_id, name, qty_amt, qty_unit, category, checked in items:
        item_pb = _encode_field_str(1, item_id) + _encode_field_str(4, name)
        if checked:
            item_pb += _encode_field_varint(6, 1)
        if category is not None:
            item_pb += _encode_field_str(11, category)
        if qty_amt is not None or qty_unit is not None:
            qty_pb = _encode_field_str(1, qty_amt) + _encode_field_str(2, qty_unit)
            item_pb += _encode_field_bytes(21, qty_pb)
        encoded_items += _encode_field_bytes(4, item_pb)

    shopping_list_pb = (
        _encode_field_str(1, list_id) + _encode_field_str(3, list_name) + encoded_items
    )
    lists_resp_pb = _encode_field_bytes(1, shopping_list_pb)
    return _encode_field_bytes(1, lists_resp_pb)


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
        assert client._http is None

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
        token = _make_jwt("user_123")
        async with AnyListClient(email="test@example.com", password="pw") as client:
            assert client._http is not None

            def mock_handler(request: httpx.Request) -> httpx.Response:
                assert request.url.path == "/auth/token"
                assert "multipart/form-data" in request.headers.get("Content-Type", "")
                body = request.content.decode("utf-8", errors="replace")
                assert "test@example.com" in body
                assert "pw" in body
                return httpx.Response(
                    200,
                    json={
                        "access_token": token,
                        "refresh_token": "refresh_tok_xyz",
                    },
                )

            client._http._transport = httpx.MockTransport(mock_handler)
            await client.authenticate()
            assert client.is_authenticated is True
            assert client._session_token == token
            assert client._refresh_token == "refresh_tok_xyz"
            assert client._user_id == "user_123"
            assert f"Bearer {token}" in client._http.headers["Authorization"]

    @pytest.mark.asyncio
    async def test_authenticate_invalid_credentials_raises_auth_error(self) -> None:
        async with AnyListClient(email="bad@example.com", password="bad") as client:
            assert client._http is not None

            def mock_handler(request: httpx.Request) -> httpx.Response:
                assert request.url.path == "/auth/token"
                return httpx.Response(401, json={"error": "unauthorized"})

            client._http._transport = httpx.MockTransport(mock_handler)
            with pytest.raises(AnyListAuthError, match="Invalid"):
                await client.authenticate()

    @pytest.mark.asyncio
    async def test_authenticate_forbidden_raises_api_error(self) -> None:
        async with AnyListClient(email="user@example.com", password="pw") as client:
            assert client._http is not None

            def mock_handler(_: httpx.Request) -> httpx.Response:
                return httpx.Response(403, text="Forbidden")

            client._http._transport = httpx.MockTransport(mock_handler)
            with pytest.raises(AnyListAPIError, match="status 403"):
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
    async def test_get_lists_decodes_protobuf(self) -> None:
        token = _make_jwt("user_proto")
        pb_payload = _build_pb_user_data(
            "live_list_pb",
            "Live PB List",
            [("item_pb_1", "Oat Milk", "2", "carton", "Dairy", True)],
        )
        async with AnyListClient(email="user@test.com", password="pw") as client:
            assert client._http is not None

            def handler(request: httpx.Request) -> httpx.Response:
                if request.url.path == "/auth/token":
                    return httpx.Response(200, json={"access_token": token})
                if request.url.path == "/data/user-data/get":
                    return httpx.Response(
                        200,
                        content=pb_payload,
                        headers={"content-type": "application/octet-stream"},
                    )
                return httpx.Response(404)

            client._http._transport = httpx.MockTransport(handler)
            await client.authenticate()
            lists = await client.get_lists()

            assert len(lists) == 1
            assert lists[0].id == "live_list_pb"
            assert lists[0].name == "Live PB List"
            assert len(lists[0].items) == 1
            assert lists[0].items[0].name == "Oat Milk"
            assert lists[0].items[0].quantity == 2.0
            assert lists[0].items[0].category == "Dairy"
            assert lists[0].items[0].checked is True

    @pytest.mark.asyncio
    async def test_get_lists_parses_json_fallback(self) -> None:
        token = _make_jwt()
        async with AnyListClient(email="user@test.com", password="pw") as client:
            assert client._http is not None

            def handler(request: httpx.Request) -> httpx.Response:
                if request.url.path == "/auth/token":
                    return httpx.Response(200, json={"access_token": token})
                if request.url.path == "/data/user-data/get":
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
    async def test_get_lists_refreshes_expired_token_on_401(self) -> None:
        old_token = _make_jwt("user_1")
        new_token = _make_jwt("user_1") + "_refreshed"
        pb_payload = _build_pb_user_data("list_refreshed", "Refreshed", [])
        calls: list[str] = []
        async with AnyListClient(email="user@test.com", password="pw") as client:
            assert client._http is not None

            def handler(request: httpx.Request) -> httpx.Response:
                calls.append(request.url.path)
                if request.url.path == "/auth/token":
                    return httpx.Response(
                        200,
                        json={
                            "access_token": old_token,
                            "refresh_token": "refresh_1",
                        },
                    )
                if request.url.path == "/auth/token/refresh":
                    body = request.content.decode("utf-8", errors="replace")
                    assert "refresh_1" in body
                    return httpx.Response(
                        200,
                        json={
                            "access_token": new_token,
                            "refresh_token": "refresh_2",
                        },
                    )
                if request.url.path == "/data/user-data/get":
                    if calls.count("/data/user-data/get") == 1:
                        return httpx.Response(401, text="Expired")
                    assert f"Bearer {new_token}" == request.headers["Authorization"]
                    return httpx.Response(200, content=pb_payload)
                return httpx.Response(404)

            client._http._transport = httpx.MockTransport(handler)
            await client.authenticate()
            lists = await client.get_lists()
            assert [lst.id for lst in lists] == ["list_refreshed"]
            assert client._session_token == new_token
            assert client._refresh_token == "refresh_2"

    @pytest.mark.asyncio
    async def test_crud_endpoints(self) -> None:
        token = _make_jwt("user_crud")
        uploaded_bodies: list[bytes] = []
        async with AnyListClient(email="user@test.com", password="pw") as client:
            assert client._http is not None

            def handler(request: httpx.Request) -> httpx.Response:
                if request.url.path == "/auth/token":
                    return httpx.Response(200, json={"access_token": token})
                if request.url.path == "/data/shopping-lists/update":
                    assert "multipart/form-data" in request.headers.get("Content-Type", "")
                    uploaded_bodies.append(request.content)
                    return httpx.Response(200, content=b"")
                return httpx.Response(404)

            client._http._transport = httpx.MockTransport(handler)
            await client.authenticate()

            item = GroceryItem(id="item_live", name="Coffee", quantity=2, category="Beverages")
            await client.add_item("list_1", item)
            await client.update_item("list_1", item)
            await client.remove_item("list_1", "item_live")
            new_list = await client.create_list("Party Supplies")

            assert new_list.name == "Party Supplies"
            assert len(uploaded_bodies) == 4
            assert b"add-shopping-list-item" in uploaded_bodies[0]
            assert b"set-list-item-name" in uploaded_bodies[1]
            assert b"remove-shopping-list-item" in uploaded_bodies[2]
            assert b"new-shopping-list" in uploaded_bodies[3]

    @pytest.mark.asyncio
    async def test_crud_unauthorized_raises_auth_error(self) -> None:
        token = _make_jwt()
        async with AnyListClient(email="user@test.com", password="pw") as client:
            assert client._http is not None

            def handler(request: httpx.Request) -> httpx.Response:
                if request.url.path == "/auth/token":
                    return httpx.Response(200, json={"access_token": token})
                return httpx.Response(401, text="Unauthorized")

            client._http._transport = httpx.MockTransport(handler)
            await client.authenticate()

            item = GroceryItem(id="item_live", name="Coffee")
            with pytest.raises(AnyListAuthError):
                await client.add_item("list_1", item)
            with pytest.raises(AnyListAuthError):
                await client.remove_item("list_1", "item_live")
            with pytest.raises(AnyListAuthError):
                await client.update_item("list_1", item)
            with pytest.raises(AnyListAuthError):
                await client.create_list("New List")

    @pytest.mark.asyncio
    async def test_crud_network_error_raises_api_error(self) -> None:
        token = _make_jwt()
        async with AnyListClient(email="user@test.com", password="pw") as client:
            assert client._http is not None

            def handler(request: httpx.Request) -> httpx.Response:
                if request.url.path == "/auth/token":
                    return httpx.Response(200, json={"access_token": token})
                raise httpx.ConnectError("connection lost", request=request)

            client._http._transport = httpx.MockTransport(handler)
            await client.authenticate()

            item = GroceryItem(id="item_live", name="Coffee")
            with pytest.raises(AnyListAPIError, match="Network error"):
                await client.add_item("list_1", item)

    @pytest.mark.asyncio
    async def test_get_lists_preserves_zero_quantity_and_unit(self) -> None:
        token = _make_jwt()
        async with AnyListClient(email="user@test.com", password="pw") as client:
            assert client._http is not None

            def handler(request: httpx.Request) -> httpx.Response:
                if request.url.path == "/auth/token":
                    return httpx.Response(200, json={"access_token": token})
                return httpx.Response(
                    200,
                    json={
                        "shoppingLists": [
                            {
                                "identifier": "l1",
                                "name": "Test",
                                "items": [
                                    {
                                        "identifier": "i1",
                                        "name": "Milk",
                                        "quantity": 0,
                                        "unit": "gallon",
                                    }
                                ],
                            }
                        ]
                    },
                )

            client._http._transport = httpx.MockTransport(handler)
            await client.authenticate()
            lists = await client.get_lists()
            assert lists[0].items[0].quantity == 0.0
            assert lists[0].items[0].unit == Unit.GALLON
