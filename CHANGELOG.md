# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added
- **Live Weekly Flyers & Generic Aggregator (`grocery_helper.flyers`)**:
  - Added `MarketBasketFlyerProvider` fetching weekly circular JSON from `shopmarketbasket.com/wp-json/mb/v1/weekly-flyer` with multi-buy, per-lb, and savings parsing.
  - Added `WholeFoodsFlyerProvider` extracting store-specific weekly sales and Prime member pricing from `wholefoodsmarket.com/sales-flyer?store-id=<id>`.
  - Added `FlippFlyerProvider` (`backflipp.wishabi.com/flipp`) for generic ZIP-code circular discovery and cross-store sale item search (e.g. ALDI, Star Market, Shaw's, Stop & Shop, Hannaford, Wegmans, Big Y, Costco, Target, Walmart).
  - Added `FlyerDeal` model and extended `StorePrice` with `is_live`, `matched_product_name`, and `deal_details`.
- **Pricing, UI & CLI Support (`grocery_helper.pricing`, `grocery_helper.app`, `grocery_helper.cli`)**:
  - Added `"Market Basket"` to `MockPricingProvider.STORE_PROFILES`.
  - Updated `PricingService` to pre-warm and overlay Flipp, Market Basket, and Whole Foods live flyers (with `enable_live_flyers`, `enable_flipp`, `flyer_only_for_live_stores`, `whole_foods_store_id`, `whole_foods_prime`, and `flipp_merchants`).
  - Added sidebar live flyer controls and a **🏷️ Weekly Sales & Flyers** tab in Streamlit for matching deals to your grocery list and searching/filtering all active weekly on-sale items.
  - Added the `grocery-helper flyers` CLI subcommand and `--live-flyers`, `--advertised-only`, `--wf-store-id`, `--no-prime`, `--no-flipp`, and `--flipp-merchants` flags to `optimise`.
- **Open Prices Crowdsourced Retail Pricing (`grocery_helper.open_prices`)**:
  - Added `OpenPricesProvider` querying Open Food Facts' Open Prices REST API (`prices.openfoodfacts.org/api/v1`) with product-code search, category-tag staple fallback, ZIP proximity ranking, per-kilogram unit conversion, and cache isolation.
  - Wired `enable_open_prices` (`Mock → Open Prices → Flipp → Kroger → Market Basket & Whole Foods direct flyers` precedence) into `PricingService`, `.env` config (`ENABLE_OPEN_PRICES`, `OPEN_PRICES_BASE_URL`), Streamlit sidebar (`Include Open Prices (Crowdsourced)`), and CLI (`--open-prices` / `--no-open-prices`).

### Fixed
- **Live AnyList Authentication & Protobuf Sync (`grocery_helper.anylist_client`)**:
  - Replaced the legacy `/data/users/login` placeholder (which returned HTTP `403` and was misreported as an invalid password) with AnyList's real multipart `POST /auth/token` login and `/auth/token/refresh` token-renewal flow.
  - Added pure-Python Protocol Buffer serialization/deserialization for `POST /data/user-data/get` (`PBUserDataResponse`) and multipart `operations` uploads to `POST /data/shopping-lists/update` (`PBListOperationList`).
- **Streamlit Session Reload Safety (`grocery_helper.models`, `grocery_helper.app`, `grocery_helper.pricing`)**:
  - Added `_ReloadSafeModel` coercion across core Pydantic models and JSON serialization for Streamlit `session_state` caches so hot module reloads never trigger `ValidationError` when filtering stores or recalculating baskets.

---

## [0.1.0] - 2026-11-14

### Fixed
- **Streamlit UI & Route Consistency (`grocery_helper.app`, `grocery_helper.cli`)**:
  - Fixed `StreamlitAPIException` crash when selecting 0 or 1 allowed stores in the sidebar (`max_stores` slider bounds).
  - Updated Shopping Route UI and CLI text table to render assigned stores and prices from `cart.store_assignments` instead of unconstrained `comp.cheapest`, using `Decimal` arithmetic for subtotals.
  - Persisted demo-mode AnyList state and stable list-ID selection across Streamlit reruns so added items and exported sub-lists are retained.
  - Replaced unsafe `run_async` fallback so coroutine `RuntimeError`s are not masked and active event loops are handled cleanly.
  - Added unified error handling around item creation and split-list exports.
- **Pricing Optimizer & Cache Safety (`grocery_helper.pricing`, `grocery_helper.utils`)**:
  - Calculated `estimated_savings` relative to each item's assigned store price under `max_stores` constraints.
  - Enforced `max_stores` even when items lack prices or subsets provide partial coverage; validated `max_stores >= 1`.
  - Isolated in-memory price cache keys by unit and provider configuration, added `threading.RLock` synchronization, and exposed `clear_price_cache()`.
  - Guarded `KrogerPricingProvider` against missing tokens/locations and added token expiry tracking and 401 refresh retries.
  - Fixed `truncate(..., max_len=0)` edge case and prevented punctuation-only strings or hyphenated prefixes (e.g. `L-Carnitine`) from false-matching in `matcher.py`.
- **AnyList Live Client (`grocery_helper.anylist_client`)**:
  - Unified network/auth error handling (`httpx.RequestError` and `401/403`) across `add_item`, `remove_item`, `update_item`, and `create_list`.
  - Preserved `0` item quantities and item units when parsing `/data/shopping-lists/get` responses, and cleared `_http` on `__aexit__`.

### Improved
- **Unit-Aware Price Comparison (`grocery_helper.models`, `grocery_helper.pricing`)**:
  - Added `convert_unit_price()`, `PriceComparison.normalized_price()`, and `PriceComparison.has_unit_mismatch` to normalize comparable volume (`gallon` ↔ `liter`) and weight (`lb` ↔ `oz`) prices.
  - Improved catalog matching for pack-suffixed items (e.g. `Soda 12pk`) and descriptive names via containment-weighted token scoring in `matcher.py`.
- **Performance & Tooling**:
  - Parallelized item price fetches via `asyncio.gather` and added shared `httpx.AsyncClient` connection pooling for `KrogerPricingProvider` during `PricingService.optimise()`.
  - Cached live AnyList list fetches in Streamlit `session_state` with a manual refresh button and extracted pure view-model helpers (`build_comparison_rows`, `group_cart_by_store`).
  - Consolidated duplicate `dev` dependency definitions in `pyproject.toml` and optimized `.github/workflows/docs.yml` with `concurrency` and `--no-install-project --only-group docs`.

---

## [0.0.0] - 2026-11-08

Initial release of **Grocery Helper** — a Python library, CLI, and Streamlit application that connects to AnyList, compares grocery prices across stores, and computes optimal shopping routes.

### Added

#### Core & Architecture
- **Data Models (`grocery_helper.models`)**:
  - `GroceryItem`: ID, name, quantity, unit, category, notes, and checked status.
  - `GroceryList`: Container with timestamps and list metadata.
  - `StorePrice`: Price per unit, sale price, store identifiers, and effective price computation.
  - `PriceComparison`: Multi-store item comparison, identifying cheapest grocer and potential savings.
  - `OptimizedCart`: Multi-store split assignments, total basket cost, estimated savings, single-store comparison totals, and route constraint metadata.
- **Centralized Configuration (`grocery_helper.config`)**:
  - Pydantic Settings integration reading environment variables and `.env` files.
  - Settings for AnyList credentials, Kroger Developer API credentials, cache TTL, and default ZIP code.
- **Shared Utilities (`grocery_helper.utils`)**:
  - Normalized in-memory TTL caching (`TTLCache`) for price queries.
  - Currency and savings string formatters (`format_currency`, `format_savings`).
  - `@timed` execution profiling decorator for sync and async functions.

#### AnyList Integration (`grocery_helper.anylist_client`)
- Async HTTP client with internal AnyList API endpoints (`/data/users/login`, `/data/shopping-lists/get`, `/data/shopping-lists/add-item`, etc.).
- Mobile client header support (`X-AnyLeaf-API-Version: 3`, `X-AnyLeaf-Client-Identifier`, custom User-Agent).
- **Offline Demo Mode**:
  - Provides pre-populated sample lists (*"Weekly Shop"*, *"Party Supplies"*, *"Brunch & Coffee"*).
  - Enables zero-configuration testing and local UI preview without active AnyList credentials.
- **Sub-List Export**:
  - `export_split_lists()` method to generate store-specific shopping lists (e.g. *"Weekly Shop (ALDI)"*, *"Weekly Shop (Kroger)"*) directly in AnyList.

#### Pricing & Basket Optimization (`grocery_helper.pricing`)
- **Pluggable Provider Protocol (`BasePricingProvider`)**:
  - Standardized interface for querying grocery item pricing by location.
- **Mock Pricing Provider (`MockPricingProvider`)**:
  - Generates realistic, deterministic pricing profiles for ALDI, Trader Joe's, Kroger, and Whole Foods.
  - Handles regular price variations and occasional promotional sale discounts.
- **Live Kroger Developer API Provider (`KrogerPricingProvider`)**:
  - OAuth2 client credentials authentication against `api.kroger.com`.
  - Automatic nearest store location discovery using ZIP code.
  - Live product search and real-time regular and promotional price extraction.
- **Optimization Algorithms**:
  - Unconstrained multi-store split optimization for maximum savings.
  - **Constrained Route Optimization**:
    - `allowed_stores`: Restrict shopping exclusively to selected stores.
    - `max_stores`: Limit stops to at most $N$ stores (e.g. `max_stores=1` for single-trip shopping, `max_stores=2` for two-store split).
  - Single-store basket totals comparison to weigh travel inconvenience against multi-store savings.

#### Item Normalization & Matching (`grocery_helper.matcher`)
- Regex-based embedded quantity and unit extractor (e.g. *"Honeycrisp Apples 3 lbs"* $\to$ 3.0, LB).
- Text normalizer stripping punctuation, abbreviations, and case variations.
- Token-overlap and sequence-matching similarity scoring to match user item names to store SKUs.

#### User Interfaces
- **Streamlit Web Application (`grocery_helper.app`)**:
  - Interactive sidebar with credential configuration, ZIP code lookup, and demo mode toggle.
  - Optimization controls: Allowed stores multiselect and max stores trip limit slider.
  - In-app **"➕ Add Item"** form to append items to lists directly in the web UI.
  - Comprehensive price comparison data table with sale indicators and best store badges.
  - Metrics cards (Optimized Total, Estimated Savings, Best Single Store recommendation).
  - Total basket cost comparison bar chart (`st.bar_chart`).
  - Expandable shopping route per store with subtotal breakdowns.
  - One-click **"📤 Export Store Lists to AnyList"** button.
- **CLI Interface (`grocery_helper.cli`)**:
  - `grocery-helper serve`: Launches Streamlit application with configurable `--port` and `--host`.
  - `grocery-helper optimise <list_id>`: Headless CLI optimization with `--demo`, `--zip`, `--max-stores`, `--stores`, and `--json` export flags.

#### CI/CD & Testing
- GitHub Actions CI workflow (`.github/workflows/ci.yml`) testing formatting, linting, type-checking, and coverage on push and pull requests.
- Test suite with 100 tests spanning unit tests, model tests, mock HTTP transport tests, and Streamlit `AppTest` UI flow tests.

#### Documentation
- **Non-Developer Shopper's Guide**: Comprehensive, step-by-step user guide explaining the app workflow, store selection, "max stores" trip slider, AnyList sub-list export, and item naming tips for everyday shoppers.
- **GitHub Pages Documentation Site**: MkDocs Material site deployed automatically via GitHub Actions (`.github/workflows/docs.yml`) covering architecture, Streamlit UI, CLI reference, configuration, and developer setup.
