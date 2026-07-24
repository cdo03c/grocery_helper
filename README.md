# 🛒 Grocery Helper

A Python library + Streamlit app that integrates with **AnyList** to find optimal pricing on groceries across stores.

## Project Structure

```
grocery_helper/
├── src/
│   └── grocery_helper/
│       ├── __init__.py          # Package version
│       ├── app.py               # Streamlit UI entry point
│       ├── cli.py               # CLI (serve / optimise commands)
│       ├── config.py            # Settings via pydantic-settings + .env
│       ├── models.py            # Pydantic data models
│       ├── anylist_client.py    # Async AnyList API client (skeleton)
│       ├── pricing.py           # Price comparison & optimisation logic
│       └── utils.py             # Formatting, caching, logging helpers
├── tests/
│   ├── test_models.py
│   ├── test_pricing.py
│   └── test_utils.py
├── .env.example                 # Copy to .env and fill in credentials
└── pyproject.toml
```

## Quick Start

### Prerequisites
- [uv](https://docs.astral.sh/uv/) (installed automatically by setup script)
- Python 3.11+

### Setup

```bash
# 1. Copy and fill in environment variables
cp .env.example .env

# 2. Install dependencies
uv sync

# 3. Launch the Streamlit app
uv run streamlit run src/grocery_helper/app.py

# — or via the CLI entry point —
uv run grocery-helper serve
```

### Running Tests

```bash
uv run pytest
```

### Linting & Type Checking

```bash
uv run ruff check .
uv run mypy src/
```

## Configuration

All settings live in `.env` (see `.env.example`):

| Variable | Description | Default |
|---|---|---|
| `ANYLIST_EMAIL` | AnyList account email | |
| `ANYLIST_PASSWORD` | AnyList account password | |
| `DEFAULT_STORE_ZIP` | ZIP code for store lookups | |
| `PRICE_CACHE_TTL_SECONDS` | Cache duration in seconds | `3600` |
| `APP_TITLE` | Streamlit page title | `Grocery Helper` |
| `DEBUG` | Enable verbose logging | `false` |

## Implementation Status

| Module | Status | Notes |
|---|---|---|
| `config.py` | ✅ Complete | Settings fully wired |
| `models.py` | ✅ Complete | All Pydantic models |
| `utils.py` | ✅ Complete | Formatting, caching, logging |
| `app.py` | 🏗 Skeleton | UI structure ready; wire up clients |
| `anylist_client.py` | 🏗 Skeleton | Auth & API stubs — needs endpoints |
| `pricing.py` | 🏗 Skeleton | `fetch_prices` needs data source |
| `cli.py` | ✅ Complete | `serve` and `optimise` commands |
