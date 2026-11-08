# 🛠️ Contributing & Development

## Local Setup

Ensure you have [uv](https://docs.astral.sh/uv/) installed.

```bash
# Clone the repository
git clone https://github.com/cuyler/grocery_helper.git
cd grocery_helper

# Install all runtime, dev, and documentation dependencies
uv sync --all-groups
```

---

## Running Quality Checks

### 1. Test Suite & Coverage
```bash
uv run pytest
```

### 2. Code Formatting & Linting
```bash
# Check formatting
uv run ruff format --check .

# Automatically format code
uv run ruff format .

# Run linter
uv run ruff check .
```

### 3. Static Type Checking
```bash
uv run mypy src/ tests/
```

---

## Building the Documentation Locally

To preview the documentation site locally with hot-reloading:

```bash
uv run mkdocs serve
```

Then visit `http://127.0.0.1:8000` in your browser.

To build the static HTML site for publishing:

```bash
uv run mkdocs build
```
The compiled static assets will be located in the `site/` directory.

