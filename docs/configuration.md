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
| `ENABLE_LIVE_FLYERS` | Boolean | `false` | Fetch live weekly flyer sale items during pricing and optimization. |
| `ENABLE_FLIPP` | Boolean | `true` | Include generic nearby store flyers via Flipp when `ENABLE_LIVE_FLYERS=true`. |
| `ENABLE_OPEN_PRICES` | Boolean | `false` | Overlay crowdsourced retail receipt/shelf prices from Open Prices. |
| `OPEN_PRICES_BASE_URL` | String | `https://prices.openfoodfacts.org/api/v1` | Base URL for Open Food Facts Open Prices REST API. |
| `WHOLE_FOODS_STORE_ID` | String | `"10010"` | Whole Foods store ID for local weekly sales flyer (e.g. `10010` Cambridge). |
| `WHOLE_FOODS_PRIME` | Boolean | `true` | Prefer Amazon Prime member sale prices for Whole Foods deals. |
| `MARKET_BASKET_FLYER_URL` | String | `https://www.shopmarketbasket.com/wp-json/mb/v1/weekly-flyer` | JSON endpoint for Market Basket weekly flyer items. |
| `WHOLE_FOODS_BASE_URL` | String | `https://www.wholefoodsmarket.com` | Base URL for Whole Foods Market sales flyers. |
| `FLIPP_BASE_URL` | String | `https://backflipp.wishabi.com/flipp` | Base URL for Flipp circular discovery and item search. |
| `APP_TITLE` | String | `Grocery Helper` | Title displayed in Streamlit browser tabs. |
| `DEBUG` | Boolean | `false` | Enable verbose debug logging output. |

---

## Live Weekly Flyers Setup (Optional)

Grocery Helper can fetch live weekly advertised sale prices from three sources without requiring API keys:

1. **Market Basket** (`shopmarketbasket.com/wp-json/mb/v1/weekly-flyer`): Automatically fetches active weekly circular deals across all Market Basket stores.
2. **Whole Foods Market** (`wholefoodsmarket.com/sales-flyer?store-id=...`): Fetches store-specific weekly promotions and Amazon Prime member pricing. Set `WHOLE_FOODS_STORE_ID` to your preferred store (for example: `10010` Cambridge, `10380` Medford, `10090` Bedford, `10156` Framingham, `10371` Andover).
3. **Flipp** (`backflipp.wishabi.com/flipp`): Automatically discovers nearby grocery store circulars for your `DEFAULT_STORE_ZIP` (such as ALDI, Star Market, Shaw's, Stop & Shop, Hannaford, Wegmans, Big Y, Costco, Target, and Walmart).

To enable live flyer overlays by default in both the CLI and Streamlit UI, add the following to `.env`:

```ini
DEFAULT_STORE_ZIP=02139
WHOLE_FOODS_STORE_ID=10010
WHOLE_FOODS_PRIME=true
ENABLE_LIVE_FLYERS=true
ENABLE_FLIPP=true
```

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

