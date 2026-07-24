"""Tests for the AnyList API client skeleton."""
from __future__ import annotations

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
        client = AnyListClient(email="a@b.com", password="s3cr3t", base_url="https://example.com")
        assert client._email == "a@b.com"
        assert client._password == "s3cr3t"
        assert client._base_url == "https://example.com"

    def test_defaults_to_settings(self) -> None:
        """Without explicit args the client falls back to settings (empty strings in tests)."""
        client = AnyListClient()
        # Just check types; actual values come from env / settings defaults
        assert isinstance(client._email, str)
        assert isinstance(client._base_url, str)

    def test_initial_state(self) -> None:
        client = AnyListClient()
        assert client._session_token is None
        assert client._http is None


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
        # After exit the client is closed; httpx marks it as closed
        assert http.is_closed

    @pytest.mark.asyncio
    async def test_aexit_handles_none_http(self) -> None:
        """__aexit__ should not raise if _http was never set."""
        client = AnyListClient()
        # Call __aexit__ directly without __aenter__
        await client.__aexit__(None, None, None)  # should not raise


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
        client._require_auth()  # should not raise


# ---------------------------------------------------------------------------
# authenticate
# ---------------------------------------------------------------------------


class TestAuthenticate:
    @pytest.mark.asyncio
    async def test_raises_not_implemented(self) -> None:
        async with AnyListClient() as client:
            with pytest.raises(NotImplementedError, match="authentication endpoint"):
                await client.authenticate()

    @pytest.mark.asyncio
    async def test_raises_runtime_error_without_context_manager(self) -> None:
        client = AnyListClient()
        # _http is None because we didn't use the context manager
        with pytest.raises(RuntimeError, match="not started"):
            await client.authenticate()


# ---------------------------------------------------------------------------
# Stub methods — unauthenticated → AnyListAuthError
# ---------------------------------------------------------------------------


_ITEM = GroceryItem(id="item_x", name="Milk", unit=Unit.GALLON)


class TestUnauthenticatedStubs:
    """All CRUD methods should raise AnyListAuthError when not authenticated."""

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
# Stub methods — authenticated → NotImplementedError
# ---------------------------------------------------------------------------


class TestAuthenticatedStubs:
    """Once a token is injected, all stubs should raise NotImplementedError."""

    def _authenticated_client(self) -> AnyListClient:
        client = AnyListClient()
        client._session_token = "tok_fake"
        return client

    @pytest.mark.asyncio
    async def test_get_lists_not_implemented(self) -> None:
        async with self._authenticated_client() as client:
            with pytest.raises(NotImplementedError, match="get_lists"):
                await client.get_lists()

    @pytest.mark.asyncio
    async def test_get_list_not_implemented(self) -> None:
        async with self._authenticated_client() as client:
            with pytest.raises(NotImplementedError, match="get_list"):
                await client.get_list("list_001")

    @pytest.mark.asyncio
    async def test_add_item_not_implemented(self) -> None:
        async with self._authenticated_client() as client:
            with pytest.raises(NotImplementedError, match="add_item"):
                await client.add_item("list_001", _ITEM)

    @pytest.mark.asyncio
    async def test_remove_item_not_implemented(self) -> None:
        async with self._authenticated_client() as client:
            with pytest.raises(NotImplementedError, match="remove_item"):
                await client.remove_item("list_001", "item_x")

    @pytest.mark.asyncio
    async def test_update_item_not_implemented(self) -> None:
        async with self._authenticated_client() as client:
            with pytest.raises(NotImplementedError, match="update_item"):
                await client.update_item("list_001", _ITEM)
