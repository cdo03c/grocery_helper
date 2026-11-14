"""
anylist_client.py
-----------------
Async HTTP client for the AnyList API, supporting real endpoints and demo mode.

AnyList uses multipart/form-data authentication at ``/auth/token`` and
Protocol Buffers payloads on ``/data/`` endpoints with custom headers:
- ``X-AnyLeaf-API-Version: 3``
- ``X-AnyLeaf-Client-Identifier: <32-hex UUID>``
- ``Authorization: Bearer <access_token>``
"""

from __future__ import annotations

import base64
import json
import logging
import uuid
from collections.abc import Iterator
from typing import Any

import httpx

from grocery_helper.config import settings
from grocery_helper.matcher import parse_quantity_and_unit
from grocery_helper.models import GroceryItem, GroceryList, OptimizedCart, Unit

logger = logging.getLogger(__name__)

# Sample demo data used when demo_mode=True or when operating offline
DEMO_LISTS: list[GroceryList] = [
    GroceryList(
        id="list_001",
        name="Weekly Shop",
        items=[
            GroceryItem(
                id="item_001", name="Milk", quantity=1.0, unit=Unit.GALLON, category="Dairy"
            ),
            GroceryItem(
                id="item_002",
                name="Sourdough Bread",
                quantity=1.0,
                unit=Unit.EACH,
                category="Bakery",
            ),
            GroceryItem(
                id="item_003",
                name="Free Range Eggs",
                quantity=1.0,
                unit=Unit.EACH,
                category="Dairy",
            ),
            GroceryItem(
                id="item_004",
                name="Honeycrisp Apples",
                quantity=3.0,
                unit=Unit.LB,
                category="Produce",
            ),
            GroceryItem(
                id="item_005", name="Chicken Breast", quantity=2.0, unit=Unit.LB, category="Meat"
            ),
            GroceryItem(
                id="item_006", name="Spinach", quantity=1.0, unit=Unit.EACH, category="Produce"
            ),
        ],
    ),
    GroceryList(
        id="list_002",
        name="Party Supplies",
        items=[
            GroceryItem(
                id="item_007",
                name="Tortilla Chips",
                quantity=2.0,
                unit=Unit.PACK,
                category="Snacks",
            ),
            GroceryItem(
                id="item_008", name="Salsa", quantity=2.0, unit=Unit.EACH, category="Snacks"
            ),
            GroceryItem(
                id="item_009", name="Guacamole", quantity=1.0, unit=Unit.EACH, category="Deli"
            ),
            GroceryItem(
                id="item_010", name="Soda 12pk", quantity=2.0, unit=Unit.PACK, category="Beverages"
            ),
            GroceryItem(
                id="item_011", name="Limes", quantity=4.0, unit=Unit.EACH, category="Produce"
            ),
        ],
    ),
    GroceryList(
        id="list_003",
        name="Brunch & Coffee",
        items=[
            GroceryItem(
                id="item_012", name="Pancake Mix", quantity=1.0, unit=Unit.PACK, category="Pantry"
            ),
            GroceryItem(
                id="item_013", name="Maple Syrup", quantity=1.0, unit=Unit.EACH, category="Pantry"
            ),
            GroceryItem(id="item_014", name="Bacon", quantity=2.0, unit=Unit.PACK, category="Meat"),
            GroceryItem(
                id="item_015",
                name="Orange Juice",
                quantity=1.0,
                unit=Unit.EACH,
                category="Beverages",
            ),
            GroceryItem(
                id="item_016", name="Coffee Beans", quantity=1.0, unit=Unit.PACK, category="Coffee"
            ),
        ],
    ),
]


class AnyListAuthError(Exception):
    """Raised when authentication with AnyList fails."""


class AnyListAPIError(Exception):
    """Raised for unexpected API errors."""


# ---------------------------------------------------------------------------
# Minimal Protocol Buffers Wire Codec for AnyList API Messages
# ---------------------------------------------------------------------------


def _encode_varint(value: int) -> bytes:
    """Encode an unsigned integer as a base-128 varint."""
    v = value & 0xFFFFFFFFFFFFFFFF
    out = bytearray()
    while True:
        byte = v & 0x7F
        v >>= 7
        if v:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            break
    return bytes(out)


def _decode_varint(buf: bytes, pos: int) -> tuple[int, int]:
    """Decode a base-128 varint starting at *pos*, returning (value, next_pos)."""
    result = 0
    shift = 0
    while pos < len(buf):
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, pos
        shift += 7
    raise ValueError("Truncated protobuf varint")


def _encode_field_bytes(field_no: int, payload: bytes) -> bytes:
    """Encode a length-delimited protobuf field (wire type 2)."""
    tag = _encode_varint((field_no << 3) | 2)
    return tag + _encode_varint(len(payload)) + payload


def _encode_field_str(field_no: int, value: str | None) -> bytes:
    """Encode an optional UTF-8 string field when non-None."""
    if value is None:
        return b""
    return _encode_field_bytes(field_no, value.encode("utf-8"))


def _encode_field_varint(field_no: int, value: int | None) -> bytes:
    """Encode an optional integer/bool varint field when non-None."""
    if value is None:
        return b""
    return _encode_varint((field_no << 3) | 0) + _encode_varint(value)


def _iter_proto_fields(buf: bytes) -> Iterator[tuple[int, int, int | bytes]]:
    """Yield ``(field_no, wire_type, value)`` tuples from a serialized protobuf buffer."""
    pos = 0
    end = len(buf)
    while pos < end:
        key, pos = _decode_varint(buf, pos)
        field_no = key >> 3
        wire_type = key & 0x07
        if wire_type == 0:
            val, pos = _decode_varint(buf, pos)
            yield field_no, wire_type, val
        elif wire_type == 1:
            val_bytes = buf[pos : pos + 8]
            pos += 8
            yield field_no, wire_type, val_bytes
        elif wire_type == 2:
            length, pos = _decode_varint(buf, pos)
            val_bytes = buf[pos : pos + length]
            pos += length
            yield field_no, wire_type, val_bytes
        elif wire_type == 5:
            val_bytes = buf[pos : pos + 4]
            pos += 4
            yield field_no, wire_type, val_bytes
        else:
            raise ValueError(f"Unsupported protobuf wire type: {wire_type}")


def _decode_jwt_user_id(token: str) -> str | None:
    """Extract ``sub`` / ``user_id`` from a JWT access token without verifying signature."""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    payload_b64 = parts[1]
    padding = "=" * (-len(payload_b64) % 4)
    try:
        decoded = base64.urlsafe_b64decode(payload_b64 + padding)
        claims = json.loads(decoded.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    uid = claims.get("sub") or claims.get("user_id") or claims.get("userId")
    return str(uid) if uid else None


def _decode_item_quantity(buf: bytes) -> tuple[float, Unit]:
    """Decode ``PBItemQuantity`` into ``(quantity, Unit)``."""
    amount_str = ""
    unit_str = ""
    raw_str = ""
    for field_no, wire_type, val in _iter_proto_fields(buf):
        if wire_type != 2 or not isinstance(val, bytes):
            continue
        text = val.decode("utf-8", errors="replace").strip()
        if field_no == 1:
            amount_str = text
        elif field_no == 2:
            unit_str = text
        elif field_no == 3:
            raw_str = text

    combined = f"{amount_str} {unit_str}".strip() or raw_str
    if combined:
        parsed_qty, parsed_unit, _ = parse_quantity_and_unit(combined)
        qty = 1.0
        if parsed_qty is not None:
            qty = max(0.0, parsed_qty)
        elif amount_str:
            try:
                qty = max(0.0, float(amount_str))
            except ValueError:
                qty = 1.0
        if parsed_unit is not None:
            return qty, parsed_unit
        if unit_str:
            try:
                return qty, Unit(unit_str.lower())
            except ValueError:
                pass
        return qty, Unit.EACH

    return 1.0, Unit.EACH


def _decode_list_item(buf: bytes) -> GroceryItem:
    """Decode a protobuf ``ListItem`` message."""
    item_id = ""
    name = "Unknown Item"
    details: str | None = None
    checked = False
    category: str | None = None
    deprecated_qty = ""
    qty_pb_bytes: bytes | None = None

    for field_no, wire_type, val in _iter_proto_fields(buf):
        if wire_type == 2 and isinstance(val, bytes):
            if field_no == 1:
                item_id = val.decode("utf-8", errors="replace")
            elif field_no == 4:
                name = val.decode("utf-8", errors="replace") or "Unknown Item"
            elif field_no == 5:
                details = val.decode("utf-8", errors="replace") or None
            elif field_no == 11:
                category = val.decode("utf-8", errors="replace") or None
            elif field_no == 18:
                deprecated_qty = val.decode("utf-8", errors="replace")
            elif field_no == 21:
                qty_pb_bytes = val
        elif wire_type == 0 and isinstance(val, int) and field_no == 6:
            checked = bool(val)

    qty = 1.0
    unit = Unit.EACH
    if qty_pb_bytes is not None:
        qty, unit = _decode_item_quantity(qty_pb_bytes)
    elif deprecated_qty:
        parsed_qty, parsed_unit, _ = parse_quantity_and_unit(deprecated_qty)
        if parsed_qty is not None:
            qty = max(0.0, parsed_qty)
        else:
            try:
                qty = max(0.0, float(deprecated_qty))
            except ValueError:
                qty = 1.0
        if parsed_unit is not None:
            unit = parsed_unit

    return GroceryItem(
        id=item_id or uuid.uuid4().hex,
        name=name,
        quantity=qty,
        unit=unit,
        category=category,
        notes=details,
        checked=checked,
    )


def _decode_shopping_list(buf: bytes) -> GroceryList:
    """Decode a protobuf ``ShoppingList`` message."""
    list_id = ""
    name = "Unnamed List"
    items: list[GroceryItem] = []

    for field_no, wire_type, val in _iter_proto_fields(buf):
        if wire_type != 2 or not isinstance(val, bytes):
            continue
        if field_no == 1:
            list_id = val.decode("utf-8", errors="replace")
        elif field_no == 3:
            name = val.decode("utf-8", errors="replace") or "Unnamed List"
        elif field_no == 4:
            items.append(_decode_list_item(val))

    return GroceryList(
        id=list_id or uuid.uuid4().hex,
        name=name,
        items=items,
    )


def _decode_user_data_lists(buf: bytes) -> list[GroceryList]:
    """Decode ``PBUserDataResponse`` bytes and extract all shopping lists."""
    lists: list[GroceryList] = []
    for field_no, wire_type, val in _iter_proto_fields(buf):
        if field_no == 1 and wire_type == 2 and isinstance(val, bytes):
            # ShoppingListsResponse: field 1 = newLists, field 2 = modifiedLists
            for sub_no, sub_wire, sub_val in _iter_proto_fields(val):
                if sub_no in (1, 2) and sub_wire == 2 and isinstance(sub_val, bytes):
                    lists.append(_decode_shopping_list(sub_val))
    return lists


def _encode_operation_metadata(handler_id: str, user_id: str | None) -> bytes:
    """Encode ``PBOperationMetadata``."""
    return (
        _encode_field_str(1, uuid.uuid4().hex)
        + _encode_field_str(2, handler_id)
        + _encode_field_str(3, user_id)
        + _encode_field_varint(4, 0)
    )


def _encode_list_item_pb(list_id: str, item: GroceryItem, user_id: str | None) -> bytes:
    """Encode a protobuf ``ListItem`` message."""
    qty_pb = _encode_field_str(1, f"{item.quantity:g}")
    if item.unit != Unit.EACH:
        qty_pb += _encode_field_str(2, item.unit.value)

    return (
        _encode_field_str(1, item.id)
        + _encode_field_str(3, list_id)
        + _encode_field_str(4, item.name)
        + _encode_field_str(5, item.notes)
        + _encode_field_varint(6, 1 if item.checked else 0)
        + _encode_field_str(11, item.category)
        + _encode_field_str(12, user_id)
        + _encode_field_bytes(21, qty_pb)
    )


def _encode_shopping_list_pb(list_id: str, name: str, user_id: str | None) -> bytes:
    """Encode a protobuf ``ShoppingList`` message for list creation."""
    return (
        _encode_field_str(1, list_id)
        + _encode_field_str(3, name)
        + _encode_field_str(5, user_id)
        + _encode_field_varint(16, 1)
        + _encode_field_varint(17, 0)
        + _encode_field_varint(18, 0)
    )


def _encode_list_operation(
    *,
    handler_id: str,
    user_id: str | None,
    list_id: str,
    list_item_id: str | None = None,
    updated_value: str | None = None,
    list_item_bytes: bytes | None = None,
    shopping_list_bytes: bytes | None = None,
) -> bytes:
    """Encode a ``PBListOperation`` message."""
    parts = [
        _encode_field_bytes(1, _encode_operation_metadata(handler_id, user_id)),
        _encode_field_str(2, list_id),
        _encode_field_str(3, list_item_id),
        _encode_field_str(4, updated_value),
    ]
    if list_item_bytes is not None:
        parts.append(_encode_field_bytes(6, list_item_bytes))
    if shopping_list_bytes is not None:
        parts.append(_encode_field_bytes(7, shopping_list_bytes))
    return b"".join(parts)


def _encode_operation_list(operations: list[bytes]) -> bytes:
    """Wrap encoded ``PBListOperation`` messages in ``PBListOperationList``."""
    return b"".join(_encode_field_bytes(1, op) for op in operations)


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class AnyListClient:
    """
    Async client for interacting with AnyList.

    Supports both live API interactions and an offline `demo_mode` for
    testing and interactive development without requiring account credentials.
    """

    def __init__(
        self,
        email: str | None = None,
        password: str | None = None,
        base_url: str | None = None,
        demo_mode: bool = False,
    ) -> None:
        self._email = email if email is not None else settings.anylist_email
        self._password = password if password is not None else settings.anylist_password
        self._base_url = (base_url or settings.anylist_api_base_url).rstrip("/")
        self._demo_mode = demo_mode
        self._session_token: str | None = None
        self._refresh_token: str | None = None
        self._user_id: str | None = None
        self._client_id: str = uuid.uuid4().hex
        self._http: httpx.AsyncClient | None = None
        self._demo_store: dict[str, GroceryList] = {
            lst.id: lst.model_copy(deep=True) for lst in DEMO_LISTS
        }

    @property
    def is_authenticated(self) -> bool:
        """Whether the client currently has a valid session token."""
        return self._session_token is not None

    @property
    def is_demo(self) -> bool:
        """Whether the client is running in mock/demo mode."""
        return self._demo_mode

    # ── Context manager ───────────────────────────────────────────────────────

    async def __aenter__(self) -> AnyListClient:
        headers = {
            "X-AnyLeaf-API-Version": "3",
            "X-AnyLeaf-Client-Identifier": self._client_id,
            "User-Agent": "AnyList/5.32 (iPhone; iOS 16.0; Scale/3.00)",
        }
        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            headers=headers,
            timeout=15.0,
        )
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._http:
            await self._http.aclose()
            self._http = None

    # ── Auth ──────────────────────────────────────────────────────────────────

    async def authenticate(self) -> None:
        """
        Authenticate with AnyList and store the session token.
        In demo mode, authenticates instantly.
        """
        if self._demo_mode:
            self._session_token = "demo_session_token"
            self._user_id = "demo_user"
            logger.info("Authenticated AnyListClient in demo mode")
            return

        if not self._http:
            raise RuntimeError("Client not started. Use async with AnyListClient().")

        email = self._email.strip()
        if not email or not self._password:
            raise AnyListAuthError(
                "AnyList credentials missing. Provide email and password, or run in demo mode."
            )

        logger.info("Authenticating with AnyList as %s", email)

        try:
            response = await self._http.post(
                "/auth/token",
                files={"email": (None, email), "password": (None, self._password)},
            )
        except httpx.RequestError as exc:
            raise AnyListAPIError(f"Network error connecting to AnyList: {exc}") from exc

        if response.status_code == 401:
            raise AnyListAuthError("Invalid AnyList email or password.")
        if response.status_code != 200:
            raise AnyListAPIError(
                f"AnyList login failed with status {response.status_code}: {response.text}"
            )

        data = response.json()
        token = data.get("access_token") or data.get("token")
        if not token:
            token = response.cookies.get("anyleaf_token") or "session_active"

        self._session_token = str(token)
        refresh = data.get("refresh_token")
        self._refresh_token = str(refresh) if refresh else None
        uid = data.get("user_id") or data.get("userId") or _decode_jwt_user_id(self._session_token)
        self._user_id = str(uid) if uid else None
        self._http.headers["Authorization"] = f"Bearer {self._session_token}"

    async def _try_refresh_token(self) -> bool:
        """Attempt to refresh the AnyList access token using ``/auth/token/refresh``."""
        if not self._http or not self._refresh_token:
            return False
        try:
            res = await self._http.post(
                "/auth/token/refresh",
                files={"refresh_token": (None, self._refresh_token)},
            )
        except httpx.RequestError:
            return False

        if res.status_code != 200:
            return False

        try:
            data = res.json()
        except ValueError:
            return False

        token = data.get("access_token") or data.get("token")
        if not token:
            return False

        self._session_token = str(token)
        new_refresh = data.get("refresh_token")
        if new_refresh:
            self._refresh_token = str(new_refresh)
        self._http.headers["Authorization"] = f"Bearer {self._session_token}"
        return True

    # ── Lists ─────────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_json_lists(data: dict[str, Any]) -> list[GroceryList]:
        """Parse JSON shopping-list payloads (used if an intermediary/mock returns JSON)."""
        raw_lists = data.get("shoppingLists") or data.get("lists") or []
        lists: list[GroceryList] = []
        for raw in raw_lists:
            list_id = str(raw.get("identifier") or raw.get("id") or uuid.uuid4().hex)
            name = str(raw.get("name") or "Unnamed List")
            items: list[GroceryItem] = []

            for raw_item in raw.get("items", []):
                item_id = str(raw_item.get("identifier") or raw_item.get("id") or uuid.uuid4().hex)
                item_name = str(raw_item.get("name") or "Unknown Item")
                raw_qty = raw_item.get("quantity")
                try:
                    qty = max(0.0, float(raw_qty)) if raw_qty is not None else 1.0
                except (TypeError, ValueError):
                    qty = 1.0
                raw_unit = raw_item.get("unit")
                try:
                    unit = Unit(raw_unit) if raw_unit else Unit.EACH
                except ValueError:
                    unit = Unit.EACH
                category = raw_item.get("category")
                notes = raw_item.get("details") or raw_item.get("notes")
                checked = bool(raw_item.get("checked", False))

                items.append(
                    GroceryItem(
                        id=item_id,
                        name=item_name,
                        quantity=qty,
                        unit=unit,
                        category=category,
                        notes=notes,
                        checked=checked,
                    )
                )

            lists.append(GroceryList(id=list_id, name=name, items=items))
        return lists

    async def get_lists(self) -> list[GroceryList]:
        """Fetch all grocery lists for the authenticated user."""
        self._require_auth()

        if self._demo_mode:
            return list(self._demo_store.values())

        response = await self._post("/data/user-data/get", action="fetch lists")
        content_type = (response.headers.get("content-type") or "").lower()
        if "json" in content_type:
            return self._parse_json_lists(response.json())
        return _decode_user_data_lists(response.content)

    async def get_list(self, list_id: str) -> GroceryList:
        """Fetch a single grocery list by ID."""
        self._require_auth()

        if self._demo_mode:
            if list_id in self._demo_store:
                return self._demo_store[list_id]
            raise AnyListAPIError(f"List with ID '{list_id}' not found.")

        lists = await self.get_lists()
        for lst in lists:
            if lst.id == list_id:
                return lst
        raise AnyListAPIError(f"List with ID '{list_id}' not found.")

    async def add_item(self, list_id: str, item: GroceryItem) -> GroceryItem:
        """Add an item to an existing list."""
        self._require_auth()

        if self._demo_mode:
            lst = await self.get_list(list_id)
            lst.items.append(item)
            return item

        op = _encode_list_operation(
            handler_id="add-shopping-list-item",
            user_id=self._user_id,
            list_id=list_id,
            list_item_id=item.id,
            list_item_bytes=_encode_list_item_pb(list_id, item, self._user_id),
        )
        await self._post_operations(_encode_operation_list([op]), action="add item")
        return item

    async def remove_item(self, list_id: str, item_id: str) -> None:
        """Remove an item from a list."""
        self._require_auth()

        if self._demo_mode:
            lst = await self.get_list(list_id)
            lst.items = [i for i in lst.items if i.id != item_id]
            return

        stub_item = _encode_field_str(1, item_id) + _encode_field_str(3, list_id)
        op = _encode_list_operation(
            handler_id="remove-shopping-list-item",
            user_id=self._user_id,
            list_id=list_id,
            list_item_id=item_id,
            list_item_bytes=stub_item,
        )
        await self._post_operations(_encode_operation_list([op]), action="remove item")

    async def update_item(self, list_id: str, item: GroceryItem) -> GroceryItem:
        """Update an existing item on a list."""
        self._require_auth()

        if self._demo_mode:
            lst = await self.get_list(list_id)
            for idx, existing in enumerate(lst.items):
                if existing.id == item.id:
                    lst.items[idx] = item
                    return item
            raise AnyListAPIError(f"Item '{item.id}' not found on list '{list_id}'.")

        ops = [
            _encode_list_operation(
                handler_id="set-list-item-name",
                user_id=self._user_id,
                list_id=list_id,
                list_item_id=item.id,
                updated_value=item.name,
            ),
            _encode_list_operation(
                handler_id="set-list-item-quantity",
                user_id=self._user_id,
                list_id=list_id,
                list_item_id=item.id,
                updated_value=f"{item.quantity:g}",
            ),
            _encode_list_operation(
                handler_id="set-list-item-details",
                user_id=self._user_id,
                list_id=list_id,
                list_item_id=item.id,
                updated_value=item.notes or "",
            ),
            _encode_list_operation(
                handler_id="set-list-item-checked",
                user_id=self._user_id,
                list_id=list_id,
                list_item_id=item.id,
                updated_value="y" if item.checked else "n",
            ),
        ]
        await self._post_operations(_encode_operation_list(ops), action="update item")
        return item

    async def create_list(self, name: str) -> GroceryList:
        """Create a new grocery list."""
        self._require_auth()

        if self._demo_mode:
            new_id = f"list_{uuid.uuid4().hex[:8]}"
            new_list = GroceryList(id=new_id, name=name, items=[])
            self._demo_store[new_id] = new_list
            return new_list

        list_id = uuid.uuid4().hex
        op = _encode_list_operation(
            handler_id="new-shopping-list",
            user_id=self._user_id,
            list_id=list_id,
            shopping_list_bytes=_encode_shopping_list_pb(list_id, name, self._user_id),
        )
        await self._post_operations(_encode_operation_list([op]), action="create list")
        return GroceryList(id=list_id, name=name, items=[])

    async def export_split_lists(
        self,
        cart: OptimizedCart,
        base_name: str = "Shop",
    ) -> list[GroceryList]:
        """
        Create store-specific grocery lists based on the optimized cart assignments.

        For example, if items are assigned to ALDI and Kroger, this creates:
        - "[base_name] (ALDI)"
        - "[base_name] (Kroger)"
        and populates each with its assigned items.
        """
        self._require_auth()

        items_by_store: dict[str, list[GroceryItem]] = {}
        item_lookup = {comp.item.id: comp.item for comp in cart.comparisons}

        for item_id, store_name in cart.store_assignments.items():
            if item_id in item_lookup:
                items_by_store.setdefault(store_name, []).append(item_lookup[item_id])

        created_lists: list[GroceryList] = []
        for store_name, items in items_by_store.items():
            list_name = f"{base_name} ({store_name})"
            new_list = await self.create_list(list_name)
            for item in items:
                await self.add_item(new_list.id, item.model_copy())
            full_list = await self.get_list(new_list.id)
            created_lists.append(full_list)

        return created_lists

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _post_operations(self, ops_bytes: bytes, *, action: str) -> httpx.Response:
        """Send a protobuf ``PBListOperationList`` payload to ``/data/shopping-lists/update``."""
        return await self._post(
            "/data/shopping-lists/update",
            files={"operations": ("operations", ops_bytes, "application/octet-stream")},
            action=action,
        )

    async def _post(
        self,
        path: str,
        *,
        files: dict[str, Any] | None = None,
        action: str,
    ) -> httpx.Response:
        """Send an authenticated POST request with token-refresh retry and error handling."""
        if not self._http:
            raise RuntimeError("Client not started.")

        try:
            response = (
                await self._http.post(path, files=files) if files else await self._http.post(path)
            )
            if response.status_code == 401 and await self._try_refresh_token():
                response = (
                    await self._http.post(path, files=files)
                    if files
                    else await self._http.post(path)
                )
        except httpx.RequestError as exc:
            raise AnyListAPIError(f"Network error during {action}: {exc}") from exc

        if response.status_code in (401, 403):
            raise AnyListAuthError("Session expired or unauthorised.")
        if response.status_code != 200:
            raise AnyListAPIError(f"Failed to {action}: {response.text}")

        return response

    def _require_auth(self) -> None:
        if not self._session_token:
            raise AnyListAuthError(
                "Not authenticated. Call authenticate() before making API requests."
            )
