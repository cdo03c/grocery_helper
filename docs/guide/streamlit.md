# 🖥️ Streamlit Web App Guide

The Streamlit interface provides an interactive, visual dashboard for managing grocery lists and analyzing multi-store shopping routes.

---

## Launching the Web App

```bash
uv run grocery-helper serve
```

---

## Features Walkthrough

### 1. Settings & Sidebar
- **Credentials**: Enter your AnyList email and password to sync your live account.
- **Store ZIP Code**: Set your local ZIP code (e.g. `02139`) to discover nearby store circulars and grocers.
- **Demo Mode Toggle**: When enabled, the app uses built-in sample lists and baseline pricing.
- **Store Filters (`Allowed Stores`)**: Multiselect grocers to include or exclude (**ALDI**, **Market Basket**, **Trader Joe's**, **Kroger**, **Whole Foods**).
- **Max Stores Slider**: Limit your route to at most $N$ stops (e.g. `max_stores=1` for single-trip shopping).
- **Weekly Flyers (MB, Whole Foods & Flipp)**:
  - **Enable Live Weekly Flyers**: Overlay live weekly sale prices from Market Basket, Whole Foods, and nearby Flipp stores onto comparisons.
  - **Include Nearby Flipp Stores (by ZIP)**: Discover and compare circulars across nearby grocers (e.g. Star Market, Shaw's, Stop & Shop, Hannaford, Wegmans, Big Y, ALDI, Costco) by ZIP code.
  - **Advertised flyer prices only (MB & Whole Foods)**: Omit baseline estimates for items not in the Market Basket or Whole Foods weekly flyers.
  - **Whole Foods Store ID**: Set your local Whole Foods store ID (e.g. `10010` Cambridge, `10380` Medford, `10156` Framingham).
  - **Whole Foods Prime Pricing**: Apply Amazon Prime member discounts when listed in the Whole Foods flyer.

### 2. In-App Item Management
Above the results tabs, expand **"➕ Add an Item to this List"**:
- Enter an item name (e.g. *"Greek Yogurt"* or *"Avocados"*).
- Specify quantity and unit (each, lb, oz, gallon, pack, liter).
- Click **"Add to List"** to immediately update the list and re-calculate optimal pricing!

### 3. Price Comparison Tab
A comprehensive data table detailing:
- Every grocery item on your list and its quantity.
- Prices across all considered stores (highlighting promotional sales and live weekly flyer deals with `🏷️`).
- The identified cheapest store and best unit price.
- Potential savings compared to the highest-priced store.

### 4. Optimised Cart Tab
- **Metric Cards**:
  - **Optimised Basket Total**: Total cost when buying each item at its cheapest store.
  - **Estimated Total Savings**: Dollar amount saved across the basket.
  - **Best Single Store**: The cheapest single store if you only make one stop, along with the cost delta.
- **Cost Comparison Bar Chart**: Visual comparison of the optimized split vs. shopping exclusively at a single store.
- **Store Route Expanders**: Itemized shopping lists grouped by store with subtotals.
- **Export Store Lists to AnyList**: A one-click button that creates store-specific sub-lists (e.g. *"Weekly Shop (ALDI)"*) directly in your AnyList account.

### 5. 🏷️ Weekly Sales & Flyers Tab
Browse and search active weekly sale items directly in the app:
- Click **"🔄 Load / Refresh Live Weekly Flyers"** (or enable **Live Weekly Flyers** in the sidebar) to fetch circulars from **Market Basket**, **Whole Foods**, and nearby **Flipp** stores.
- **🎯 On-Sale Matches for Your Current List**: Automatically highlights items from your active grocery list that are currently on sale, showing matched flyer titles, sale prices, regular prices, and deal terms.
- **📋 Browse All Weekly On-Sale Items**: Filter active weekly deals by store, search by keyword, or toggle **Featured Only**.

