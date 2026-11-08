# ⌨️ Command-Line Interface (CLI) Reference

The `grocery-helper` CLI allows automated, headless execution for shell scripts, scheduled tasks, and CI/CD pipelines.

---

## Subcommands Overview

```bash
grocery-helper [-h] {serve,optimise} ...
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
- `--json`: Output raw JSON data instead of formatted terminal text.

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

