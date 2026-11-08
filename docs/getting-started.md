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

