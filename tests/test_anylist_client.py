"""Tests for the AnyList API client."""

from __future__ import annotations

import base64
import json
from decimal import Decimal

import httpx
import pytest

from grocery_helper.anylist_client import (
    AnyListAPIError,
    AnyListAuthError,
    AnyListClient,
    _decode_user_data_lists_with_meta,
    _encode_field_bytes,
    _encode_field_str,
    _encode_field_varint,
    _iter_proto_fields,
    normalize_category_name,
)
from grocery_helper.models import GroceryItem, OptimizedCart, PriceComparison, StorePrice, Unit


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
            assert b"update-list-item" in uploaded_bodies[1]
            assert b"remove-shopping-list-item" in uploaded_bodies[2]
            assert b"new-shopping-list" in uploaded_bodies[3]
            for body in uploaded_bodies:
                assert b'name="operations"' in body
                assert b"filename=" not in body

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


def _extract_multipart_part(body: bytes) -> bytes:
    """Extract the binary ``operations`` part from an HTTP multipart/form-data payload."""
    marker = b"\r\n\r\n"
    start = body.index(marker) + len(marker)
    end = body.rfind(b"\r\n--")
    return body[start:end]


def _build_pb_user_data_with_meta(
    list_id: str,
    list_name: str,
    *,
    stores: list[tuple[str, str]] | None = None,
    categories: list[tuple[str, str, str]] | None = None,
    group_id: str = "cg_default",
    extra_item_bytes: list[bytes] | None = None,
) -> bytes:
    """Build a ``PBUserDataResponse`` including ``PBListResponse`` stores & category groups."""
    encoded_items = b"".join(_encode_field_bytes(4, ib) for ib in (extra_item_bytes or []))
    shopping_list_pb = (
        _encode_field_str(1, list_id) + _encode_field_str(3, list_name) + encoded_items
    )

    cat_group_pb = _encode_field_str(1, group_id) + _encode_field_str(3, list_id)
    for cid, cname, sys_cat in categories or []:
        cat_pb = (
            _encode_field_str(1, cid)
            + _encode_field_str(3, group_id)
            + _encode_field_str(4, list_id)
            + _encode_field_str(5, cname)
            + _encode_field_str(7, sys_cat)
        )
        cat_group_pb += _encode_field_bytes(5, cat_pb)

    list_resp_pb = _encode_field_str(1, list_id)
    if categories:
        cat_resp_pb = _encode_field_bytes(1, cat_group_pb)
        list_resp_pb += _encode_field_bytes(7, cat_resp_pb)
    for sid, sname in stores or []:
        store_pb = (
            _encode_field_str(1, sid) + _encode_field_str(3, list_id) + _encode_field_str(4, sname)
        )
        list_resp_pb += _encode_field_bytes(9, store_pb)

    lists_resp_pb = _encode_field_bytes(1, shopping_list_pb) + _encode_field_bytes(6, list_resp_pb)
    return _encode_field_bytes(1, lists_resp_pb)


def _decode_first_list_item_fields(ops_pb: bytes) -> tuple[str, dict[int, list[int | bytes]]]:
    """Extract ``(handler_id, item_fields)`` from the first ``PBListOperation``."""
    handler_id = ""
    item_bytes = b""
    for fn, wt, val in _iter_proto_fields(ops_pb):
        if fn == 1 and wt == 2 and isinstance(val, bytes):
            for opf, opw, opv in _iter_proto_fields(val):
                if opf == 1 and opw == 2 and isinstance(opv, bytes):
                    for mf, mw, mv in _iter_proto_fields(opv):
                        if mf == 2 and mw == 2 and isinstance(mv, bytes):
                            handler_id = mv.decode("utf-8", errors="replace")
                elif opf == 6 and opw == 2 and isinstance(opv, bytes):
                    item_bytes = opv
            break
    fields: dict[int, list[int | bytes]] = {}
    for ifn, _, ival in _iter_proto_fields(item_bytes):
        fields.setdefault(ifn, []).append(ival)
    return handler_id, fields


class TestItemSubfields:
    def test_normalize_category_name_disambiguates_departments(self) -> None:
        assert normalize_category_name("Dairy & Frozen Foods", "Chobani Greek Yogurt") == "Dairy"
        assert (
            normalize_category_name("Dairy & Frozen Foods", "Ben & Jerry's Ice Cream")
            == "Frozen Foods"
        )
        assert normalize_category_name("Meat & Seafood", "Chicken Breast") == "Meat"
        assert normalize_category_name("Meat & Seafood", "Wild Caught Salmon") == "Seafood"
        assert normalize_category_name(None, "Hass Avocados") == "Produce"

    @pytest.mark.asyncio
    async def test_add_item_encodes_category_quantity_store_and_price_and_decodes_roundtrip(
        self,
    ) -> None:
        token = _make_jwt("user_rich")
        user_data_pb = _build_pb_user_data_with_meta(
            "list_grocery",
            "My Grocery List",
            stores=[("store_don", "Donelans")],
            categories=[
                ("cat_dairy", "Dairy", "dairy"),
                ("cat_other", "Other", "other"),
            ],
            group_id="grp_1",
        )
        v2_bodies: list[bytes] = []
        v1_bodies: list[bytes] = []

        async with AnyListClient(email="user@test.com", password="pw") as client:
            assert client._http is not None

            def handler(request: httpx.Request) -> httpx.Response:
                if request.url.path == "/auth/token":
                    return httpx.Response(200, json={"access_token": token})
                if request.url.path == "/data/user-data/get":
                    return httpx.Response(200, content=user_data_pb)
                if request.url.path == "/data/shopping-lists/update-v2":
                    v2_bodies.append(_extract_multipart_part(request.content))
                    return httpx.Response(200, content=b"")
                if request.url.path == "/data/shopping-lists/update":
                    v1_bodies.append(_extract_multipart_part(request.content))
                    return httpx.Response(200, content=b"")
                return httpx.Response(404)

            client._http._transport = httpx.MockTransport(handler)
            await client.authenticate()

            yogurt = GroceryItem(
                id="item_yogurt",
                name="Chobani Greek Yogurt 4 Pack",
                quantity=2.0,
                unit=Unit.PACK,
                category="Dairy & Frozen Foods",
                notes="Save up to $2.50",
                price=Decimal("2.99"),
                price_details="/pack",
                store="Market Basket",
            )
            await client.add_item("list_grocery", yogurt)

            # New store ("Market Basket") was created via update-v2 before adding item
            assert len(v2_bodies) == 1
            assert b"new-store" in v2_bodies[0]
            assert b"Market Basket" in v2_bodies[0]

            assert len(v1_bodies) == 1
            handler_id, item_fields = _decode_first_list_item_fields(v1_bodies[0])
            assert handler_id == "add-shopping-list-item"
            assert item_fields[4] == [b"Chobani Greek Yogurt 4 Pack"]
            assert item_fields[5] == [b"Save up to $2.50"]
            assert item_fields[11] == [b"dairy"]
            assert item_fields[13] == [b"dairy"]
            assert item_fields[18] == [b"2 pack"]
            assert len(item_fields[16]) == 1  # resolved/created Market Basket store ID
            created_store_id = item_fields[16][0]
            assert isinstance(created_store_id, bytes)

            # Quantity protobuf carries amount, unit, and rawQuantity
            qty_sub = dict((fn, val) for fn, _, val in _iter_proto_fields(item_fields[21][0]))  # type: ignore[arg-type]
            assert qty_sub[1] == b"2"
            assert qty_sub[2] == b"pack"
            assert qty_sub[3] == b"2 pack"

            # Category assignment links directly to grp_1 / cat_dairy
            assign_sub = dict((fn, val) for fn, _, val in _iter_proto_fields(item_fields[20][0]))  # type: ignore[arg-type]
            assert assign_sub[2] == b"grp_1"
            assert assign_sub[3] == b"cat_dairy"

            # Encode back into PBUserDataResponse and verify round-trip decoding
            raw_item_bytes = b"".join(
                _opv
                for _, _, _opv in _iter_proto_fields(v1_bodies[0])
                if isinstance(_opv, bytes) and b"Chobani" in _opv
            )
            # Extract listItem payload directly (field 6 of first PBListOperation)
            for fn, _, val in _iter_proto_fields(v1_bodies[0]):
                if fn == 1 and isinstance(val, bytes):
                    for opf, _, opv in _iter_proto_fields(val):
                        if opf == 6 and isinstance(opv, bytes):
                            raw_item_bytes = opv
            roundtrip_pb = _build_pb_user_data_with_meta(
                "list_grocery",
                "My Grocery List",
                stores=[
                    ("store_don", "Donelans"),
                    (created_store_id.decode("utf-8"), "Market Basket"),
                ],
                categories=[("cat_dairy", "Dairy", "dairy")],
                group_id="grp_1",
                extra_item_bytes=[raw_item_bytes],
            )
            decoded_lists, _, _ = _decode_user_data_lists_with_meta(roundtrip_pb)
            decoded_item = decoded_lists[0].items[0]
            assert decoded_item.name == "Chobani Greek Yogurt 4 Pack"
            assert decoded_item.quantity == 2.0
            assert decoded_item.unit == Unit.PACK
            assert decoded_item.category == "Dairy"
            assert decoded_item.store == "Market Basket"
            assert decoded_item.price == Decimal("2.99")
            assert decoded_item.price_details == "/pack"
            assert decoded_item.notes == "Save up to $2.50"

            # Existing store matching reuses existing store ID without another new-store call
            existing_store_item = GroceryItem(
                id="item_don",
                name="Milk",
                quantity=1.0,
                unit=Unit.GALLON,
                category="Dairy",
                store="donelans",
                price=Decimal("4.29"),
            )
            await client.add_item("list_grocery", existing_store_item)
            assert len(v2_bodies) == 1
            _, don_fields = _decode_first_list_item_fields(v1_bodies[1])
            assert don_fields[16] == [b"store_don"]

    @pytest.mark.asyncio
    async def test_update_item_preserves_unmanaged_fields_and_sync_cart(self) -> None:
        token = _make_jwt("user_upd")
        # Include unmanaged field 27 (ingredients) and field 17 (manualSortIndex) on existing item
        existing_pb = (
            _encode_field_str(1, "item_existing")
            + _encode_field_str(3, "list_sync")
            + _encode_field_str(4, "Salmon Fillets")
            + _encode_field_str(11, "seafood")
            + _encode_field_varint(17, 42)
            + _encode_field_str(27, "recipe_ingredient_blob")
        )
        user_data_pb = _build_pb_user_data_with_meta(
            "list_sync",
            "Dinner List",
            stores=[("store_mb", "Market Basket")],
            categories=[("cat_seafood", "Seafood", "seafood")],
            group_id="grp_sync",
            extra_item_bytes=[existing_pb],
        )
        v1_bodies: list[bytes] = []

        async with AnyListClient(email="user@test.com", password="pw") as client:
            assert client._http is not None

            def handler(request: httpx.Request) -> httpx.Response:
                if request.url.path == "/auth/token":
                    return httpx.Response(200, json={"access_token": token})
                if request.url.path == "/data/user-data/get":
                    return httpx.Response(200, content=user_data_pb)
                if request.url.path == "/data/shopping-lists/update":
                    v1_bodies.append(_extract_multipart_part(request.content))
                    return httpx.Response(200, content=b"")
                return httpx.Response(404)

            client._http._transport = httpx.MockTransport(handler)
            await client.authenticate()
            lists = await client.get_lists()
            target_list = lists[0]

            cart = OptimizedCart(
                list_id="list_sync",
                store_assignments={"item_existing": "Market Basket"},
                total_cost=Decimal("7.99"),
                estimated_savings=Decimal("2.00"),
                comparisons=[
                    PriceComparison(
                        item=GroceryItem(
                            id="item_existing",
                            name="Salmon Fillets",
                            quantity=2.0,
                            unit=Unit.LB,
                            category="Meat & Seafood",
                        ),
                        prices=[
                            StorePrice(
                                item_name="Salmon Fillets",
                                store_name="Market Basket",
                                price=Decimal("9.99"),
                                sale_price=Decimal("7.99"),
                                unit=Unit.LB,
                                deal_details="Weekly flyer",
                            )
                        ],
                    )
                ],
            )
            updated = await client.sync_cart_to_list(cart, target_list)
            assert len(updated) == 1
            assert updated[0].store == "Market Basket"
            assert updated[0].price == Decimal("7.99")
            assert updated[0].category == "Seafood"

            handler_id, upd_fields = _decode_first_list_item_fields(v1_bodies[0])
            assert handler_id == "update-list-item"
            assert upd_fields[16] == [b"store_mb"]
            assert upd_fields[17] == [42]
            assert upd_fields[27] == [b"recipe_ingredient_blob"]
            assert upd_fields[18] == [b"2 lb"]
