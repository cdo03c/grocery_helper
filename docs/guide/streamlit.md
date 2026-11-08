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
- **Store ZIP Code**: Set your local ZIP code (e.g. `90210`) to look up nearby grocers.
- **Demo Mode Toggle**: When enabled, the app uses built-in sample lists and mock pricing.
- **Store Filters (`Allowed Stores`)**: Multiselect grocers to include or exclude (ALDI, Trader Joe's, Kroger, Whole Foods).
- **Max Stores Slider**: Limit your route to at most $N$ stops (e.g. `max_stores=1` for single-trip shopping).

### 2. In-App Item Management
Above the results tabs, expand **"➕ Add an Item to this List"**:
- Enter an item name (e.g. *"Greek Yogurt"* or *"Avocados"*).
- Specify quantity and unit (each, lb, oz, gallon, pack, liter).
- Click **"Add to List"** to immediately update the list and re-calculate optimal pricing!

### 3. Price Comparison Tab
A comprehensive data table detailing:
- Every grocery item on your list and its quantity.
- Prices across all considered stores (highlighting promotional sales).
- The identified cheapest store and best unit price.
- Potential savings compared to the highest-priced store.

### 4. Optimised Cart Tab
- **Metric Cards**:
  - **Optimised Basket Total**: Total cost when buying each item at its cheapest store.
  - **Estimated Total Savings**: Dollar amount saved across the basket.
  - **Best Single Store**: The cheapest single store if you only make one stop, along with the cost delta.
- **Cost Comparison Bar Chart**: Visual comparison of the optimized split vs. shopping exclusively at ALDI, Kroger, Trader Joe's, or Whole Foods.
- **Store Route Expanders**: Itemized shopping lists grouped by store with subtotals.
- **Export Store Lists to AnyList**: A one-click button that creates store-specific sub-lists (e.g. *"Weekly Shop (ALDI)"*) directly in your AnyList account.

