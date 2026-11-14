# ⌨️ Command-Line Interface (CLI) Reference

The `grocery-helper` CLI allows automated, headless execution for shell scripts, scheduled tasks, and CI/CD pipelines.

---

## Subcommands Overview

```bash
grocery-helper [-h] {serve,optimise,flyers} ...
```

---

## `grocery-helper serve`

Launch the Streamlit web application.

```bash
grocery-helper serve [--port PORT] [--host HOST]
```

### Options:
- `--port PORT`: Port to bind Streamlit server to (default: `8501`).
- `--host HOST`: Address to listen on (default: `localhost`).

### Example:
```bash
grocery-helper serve --port 8080 --host 0.0.0.0
```

---

## `grocery-helper optimise`

Run the pricing optimizer for a grocery list.

```bash
grocery-helper optimise <list_id> [OPTIONS]
```

### Positional Arguments:
- `list_id`: Identifier of the list to optimize (e.g. `list_001`).

### Options:
- `--demo`: Run in offline demo mode using built-in sample lists.
- `--zip ZIP`: Specify store ZIP code for local pricing (default: from `.env` or `90210`).
- `--max-stores N`: Limit the shopping route to at most $N$ stores (e.g. `1` or `2`).
- `--stores [STORES ...]`: Space-separated list of store names to consider.
- `--live-flyers`: Overlay live weekly advertised sale prices from Market Basket, Whole Foods, and Flipp.
- `--advertised-only`: For Market Basket and Whole Foods, use only advertised weekly flyer items instead of baseline estimates.
- `--wf-store-id ID`: Whole Foods store ID for weekly sales flyer lookup (default: `10010`).
- `--no-prime`: Use regular Whole Foods sale prices instead of Prime member prices.
- `--no-flipp`: Disable generic nearby store circular lookups via Flipp when `--live-flyers` is set.
- `--flipp-merchants [STORES ...]`: Restrict Flipp lookup to specific store names (e.g. `Hannaford "Star Market" ALDI`).
- `--json`: Output raw JSON data instead of formatted terminal text.

---

## `grocery-helper flyers`

List active weekly advertised on-sale items from Market Basket, Whole Foods, and nearby stores via Flipp.

```bash
grocery-helper flyers [OPTIONS]
```

### Options:
- `--store {all,mb,wf,flipp}`: Flyer source(s) to fetch (`all` by default; `mb` Market Basket; `wf` Whole Foods; `flipp` all nearby stores by ZIP).
- `--zip ZIP`: ZIP/postal code for Flipp circular discovery (default: from `.env` or `02139`).
- `--merchants [STORES ...]`: Optional Flipp merchant allow-list (e.g. `"Star Market" Hannaford ALDI`).
- `--wf-store-id ID`: Whole Foods store ID (default: `10010`).
- `--no-prime`: Show regular Whole Foods sale prices instead of Prime member prices.
- `--search KEYWORD`: Filter on-sale items by keyword (product name, brand, description, or department).
- `--list-id LIST_ID`: Highlight weekly flyer deals matching items on an AnyList shopping list.
- `--demo`: Load `--list-id` from built-in sample lists.
- `--json`: Output raw JSON instead of a formatted table.

---

## CLI Examples

### Basic Optimization in Demo Mode
```bash
grocery-helper optimise list_001 --demo
```

### Restrict to at most 2 Stores
```bash
grocery-helper optimise list_001 --demo --max-stores 2
```

### Compare Only ALDI and Trader Joe's
```bash
grocery-helper optimise list_001 --demo --stores ALDI "Trader Joe's"
```

### Optimize with Live Weekly Flyers (Market Basket, Whole Foods & Flipp)
```bash
grocery-helper optimise list_001 --demo --live-flyers --zip 02139 --wf-store-id 10010
```

### List All Weekly On-Sale Items & Match Your List
```bash
# Market Basket + Whole Foods + nearby Flipp stores matched against demo list_001
grocery-helper flyers --demo --list-id list_001 --zip 02139

# Search Flipp deals at specific local chains
grocery-helper flyers --store flipp --zip 02139 --merchants Hannaford "Star Market" ALDI --search chicken
```

### Output JSON for Automation Scripts
```bash
grocery-helper optimise list_001 --demo --json
```

Sample JSON structure:
```json
{
  "list_id": "list_001",
  "list_name": "Weekly Shop",
  "store_assignments": {
    "item_001": "ALDI",
    "item_002": "ALDI",
    "item_005": "Kroger"
  },
  "total_cost": "23.140",
  "estimated_savings": "8.740",
  "single_store_totals": {
    "ALDI": "23.440",
    "Kroger": "26.370",
    "Trader Joe's": "26.680",
    "Whole Foods": "31.880"
  }
}
```

