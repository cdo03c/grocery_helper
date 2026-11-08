# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
- Test suite with 90+ tests spanning unit tests, model tests, mock HTTP transport tests, and Streamlit `AppTest` UI flow tests.
