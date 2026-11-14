# 🚀 Getting Started

## Prerequisites

- [uv](https://docs.astral.sh/uv/) (Fast Python package and environment manager)
- Python 3.11+

---

## Installation

Clone the repository and install dependencies with `uv`:

```bash
git clone https://github.com/cuyler/grocery_helper.git
cd grocery_helper

# Install dependencies into virtualenv managed by uv
uv sync
```

---

## Configuration

Copy the example environment template:

```bash
cp .env.example .env
```

Open `.env` in your editor:

```ini
# ── AnyList credentials (Optional for Demo Mode) ───────────────────────────
ANYLIST_EMAIL=you@example.com
ANYLIST_PASSWORD=your_password

# ── Store settings ────────────────────────────────────────────────────────────
DEFAULT_STORE_ZIP=90210

# ── Cache ─────────────────────────────────────────────────────────────────────
PRICE_CACHE_TTL_SECONDS=3600

# ── Kroger Developer API (Optional live data) ─────────────────────────────────
KROGER_CLIENT_ID=
KROGER_CLIENT_SECRET=

# ── Weekly Flyer Sources (Market Basket, Whole Foods & Flipp) ───────────────
WHOLE_FOODS_STORE_ID=10010
WHOLE_FOODS_PRIME=true
ENABLE_LIVE_FLYERS=false
ENABLE_FLIPP=true
```

!!! tip "Demo Mode Works Out of the Box"
    If you don't provide AnyList credentials or Kroger API keys, Grocery Helper automatically runs in **Demo Mode**. You can immediately explore sample grocery lists and mock pricing without creating any accounts.

---

## Launching the Application

### 1. Interactive Streamlit Web UI

Run the programmatic serve command:

```bash
uv run grocery-helper serve
```

Or via Streamlit directly:

```bash
uv run streamlit run src/grocery_helper/app.py
```

Then open `http://localhost:8501` in your browser.

### 2. Command-Line Interface (CLI)

To optimize a list directly in the terminal:

```bash
uv run grocery-helper optimise list_001 --demo
```

To list live weekly on-sale items from Market Basket, Whole Foods, and nearby stores in your ZIP code:

```bash
uv run grocery-helper flyers --demo --list-id list_001 --zip 02139
```

