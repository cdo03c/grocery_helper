# ⚙️ Configuration Reference

Grocery Helper manages its configuration using [pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/), reading from environment variables and `.env` files.

---

## Environment Variables

| Variable | Type | Default | Description |
|---|---|---|---|
| `ANYLIST_EMAIL` | String | `""` | AnyList account login email. |
| `ANYLIST_PASSWORD` | String | `""` | AnyList account login password. |
| `ANYLIST_API_BASE_URL` | String | `https://www.anylist.com` | Base URL for AnyList internal API routes. |
| `DEFAULT_STORE_ZIP` | String | `""` | Default ZIP code for grocery store lookups. |
| `PRICE_CACHE_TTL_SECONDS` | Integer | `3600` | In-memory price result caching duration in seconds. |
| `KROGER_CLIENT_ID` | String | `""` | OAuth2 Client ID from Kroger Developer Portal. |
| `KROGER_CLIENT_SECRET` | String | `""` | OAuth2 Client Secret from Kroger Developer Portal. |
| `KROGER_API_BASE_URL` | String | `https://api.kroger.com/v1` | Base URL for Kroger Developer API. |
| `APP_TITLE` | String | `Grocery Helper` | Title displayed in Streamlit browser tabs. |
| `DEBUG` | Boolean | `false` | Enable verbose debug logging output. |

---

## Kroger Developer API Setup (Optional)

To enable live grocery prices from Kroger:
1. Register for a free developer account at [developer.kroger.com](https://developer.kroger.com/).
2. Create a new App in your Kroger developer dashboard.
3. Copy your **Client ID** and **Client Secret**.
4. Add them to your `.env` file:
   ```ini
   KROGER_CLIENT_ID=your_client_id_here
   KROGER_CLIENT_SECRET=your_client_secret_here
   ```
5. When running optimizations, live Kroger SKU prices will automatically overlay onto your results!

