"""
anylist_client.py
-----------------
Async HTTP client for the AnyList API, supporting real endpoints and demo mode.

AnyList uses internal endpoints under /data/ with custom headers:
- X-AnyLeaf-API-Version: 3
- X-AnyLeaf-Client-Identifier: <32-hex UUID>
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import httpx

from grocery_helper.config import settings
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
            "Content-Type": "application/x-www-form-urlencoded",
            "X-AnyLeaf-API-Version": "3",
            "X-AnyLeaf-Client-Identifier": self._client_id,
            "User-Agent": "AnyList/5.32 (iPhone; iOS 16.0; Scale/3.00)",
        }
        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            headers=headers,
            timeout=10.0,
        )
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._http:
            await self._http.aclose()

    # ── Auth ────────────────────────────────----------------──────────────────

    async def authenticate(self) -> None:
        """
        Authenticate with AnyList and store the session token.
        In demo mode, authenticates instantly.
        """
        if self._demo_mode:
            self._session_token = "demo_session_token"
            logger.info("Authenticated AnyListClient in demo mode")
            return

        if not self._http:
            raise RuntimeError("Client not started. Use async with AnyListClient().")

        if not self._email or not self._password:
            raise AnyListAuthError(
                "AnyList credentials missing. Provide email and password, or run in demo mode."
            )

        logger.info("Authenticating with AnyList as %s", self._email)

        try:
            response = await self._http.post(
                "/data/users/login",
                data={"email": self._email, "password": self._password},
            )
        except httpx.RequestError as exc:
            raise AnyListAPIError(f"Network error connecting to AnyList: {exc}") from exc

        if response.status_code in (401, 403):
            raise AnyListAuthError("Invalid AnyList email or password.")
        if response.status_code != 200:
            raise AnyListAPIError(
                f"AnyList login failed with status {response.status_code}: {response.text}"
            )

        data = response.json()
        token = data.get("token") or data.get("access_token")
        if not token:
            # Fall back to session cookie if returned
            token = response.cookies.get("anyleaf_token") or "session_active"

        self._session_token = str(token)
        self._http.headers["Authorization"] = f"Bearer {self._session_token}"

    # ── Lists ─────────────────────────────────────────────────────────────────

    async def get_lists(self) -> list[GroceryList]:
        """Fetch all grocery lists for the authenticated user."""
        self._require_auth()

        if self._demo_mode:
            return list(self._demo_store.values())

        if not self._http:
            raise RuntimeError("Client not started.")

        try:
            response = await self._http.post("/data/shopping-lists/get", data={})
        except httpx.RequestError as exc:
            raise AnyListAPIError(f"Network error fetching lists: {exc}") from exc

        if response.status_code in (401, 403):
            raise AnyListAuthError("Session expired or unauthorised.")
        if response.status_code != 200:
            raise AnyListAPIError(f"Failed to fetch lists: {response.text}")

        data = response.json()
        raw_lists = data.get("shoppingLists") or data.get("lists") or []

        lists: list[GroceryList] = []
        for raw in raw_lists:
            list_id = str(raw.get("identifier") or raw.get("id") or uuid.uuid4().hex)
            name = str(raw.get("name") or "Unnamed List")
            items: list[GroceryItem] = []

            for raw_item in raw.get("items", []):
                item_id = str(raw_item.get("identifier") or raw_item.get("id") or uuid.uuid4().hex)
                item_name = str(raw_item.get("name") or "Unknown Item")
                qty = float(raw_item.get("quantity") or 1.0)
                category = raw_item.get("category")
                notes = raw_item.get("details") or raw_item.get("notes")
                checked = bool(raw_item.get("checked", False))

                items.append(
                    GroceryItem(
                        id=item_id,
                        name=item_name,
                        quantity=qty,
                        unit=Unit.EACH,
                        category=category,
                        notes=notes,
                        checked=checked,
                    )
                )

            lists.append(GroceryList(id=list_id, name=name, items=items))

        return lists

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

        if not self._http:
            raise RuntimeError("Client not started.")

        payload = {
            "listId": list_id,
            "name": item.name,
            "quantity": str(item.quantity),
            "details": item.notes or "",
        }
        response = await self._http.post("/data/shopping-lists/add-item", data=payload)
        if response.status_code != 200:
            raise AnyListAPIError(f"Failed to add item: {response.text}")

        return item

    async def remove_item(self, list_id: str, item_id: str) -> None:
        """Remove an item from a list."""
        self._require_auth()

        if self._demo_mode:
            lst = await self.get_list(list_id)
            lst.items = [i for i in lst.items if i.id != item_id]
            return

        if not self._http:
            raise RuntimeError("Client not started.")

        payload = {"listId": list_id, "itemId": item_id}
        response = await self._http.post("/data/shopping-lists/delete-item", data=payload)
        if response.status_code != 200:
            raise AnyListAPIError(f"Failed to remove item: {response.text}")

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

        if not self._http:
            raise RuntimeError("Client not started.")

        payload = {
            "listId": list_id,
            "itemId": item.id,
            "name": item.name,
            "quantity": str(item.quantity),
            "details": item.notes or "",
            "checked": "1" if item.checked else "0",
        }
        response = await self._http.post("/data/shopping-lists/update-item", data=payload)
        if response.status_code != 200:
            raise AnyListAPIError(f"Failed to update item: {response.text}")

        return item

    async def create_list(self, name: str) -> GroceryList:
        """Create a new grocery list."""
        self._require_auth()

        if self._demo_mode:
            new_id = f"list_{uuid.uuid4().hex[:8]}"
            new_list = GroceryList(id=new_id, name=name, items=[])
            self._demo_store[new_id] = new_list
            return new_list

        if not self._http:
            raise RuntimeError("Client not started.")

        payload = {"name": name}
        response = await self._http.post("/data/shopping-lists/create", data=payload)
        if response.status_code != 200:
            raise AnyListAPIError(f"Failed to create list: {response.text}")

        data = response.json()
        list_id = str(data.get("identifier") or data.get("id") or uuid.uuid4().hex)
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

        # Group items by assigned store
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

    def _require_auth(self) -> None:
        if not self._session_token:
            raise AnyListAuthError(
                "Not authenticated. Call authenticate() before making API requests."
            )
