"""
preferences.py
--------------
Persistent non-secret user preferences for the Streamlit UI.

Stores selections (allowed stores, max stores, ZIP code, live flyer toggles,
Whole Foods store ID, selected list ID) in a local JSON file so choices survive
between runs without ever writing credentials (email/password) to disk.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from grocery_helper.config import settings

logger = logging.getLogger(__name__)

# Strictly non-secret settings safe to persist on disk
PERSISTED_SETTING_KEYS: frozenset[str] = frozenset(
    {
        "store_zip",
        "allowed_stores",
        "max_stores",
        "enable_live_flyers",
        "enable_flipp",
        "advertised_only",
        "whole_foods_store_id",
        "whole_foods_prime",
        "enable_open_prices",
        "selected_list_id",
    }
)


def resolve_preferences_path(path_override: str | Path | None = None) -> Path | None:
    """Resolve the preferences JSON file path, or None when persistence is disabled."""
    raw = settings.ui_settings_file if path_override is None else str(path_override)
    raw_clean = raw.strip()
    if not raw_clean:
        return None
    return Path(raw_clean).expanduser()


def _sanitize_preferences(raw: dict[str, Any]) -> dict[str, Any]:
    """Filter and normalize loaded preference values to expected safe types."""
    clean: dict[str, Any] = {}
    for key in PERSISTED_SETTING_KEYS:
        if key not in raw:
            continue
        val = raw[key]
        if key == "allowed_stores":
            if isinstance(val, list):
                clean[key] = [str(s).strip() for s in val if str(s).strip()]
        elif key == "max_stores":
            if val is None:
                clean[key] = None
            elif isinstance(val, int) and not isinstance(val, bool) and val >= 1:
                clean[key] = val
        elif key in {
            "enable_live_flyers",
            "enable_flipp",
            "advertised_only",
            "whole_foods_prime",
            "enable_open_prices",
        }:
            if isinstance(val, bool):
                clean[key] = val
        elif key in {"store_zip", "whole_foods_store_id"}:
            if isinstance(val, str):
                clean[key] = val.strip()
        elif key == "selected_list_id":
            if val is None or isinstance(val, str):
                clean[key] = val
    return clean


def load_ui_settings(path: str | Path | None = None) -> dict[str, Any]:
    """Load saved non-secret UI settings from disk, returning {} if absent or invalid."""
    target = resolve_preferences_path(path)
    if target is None or not target.exists():
        return {}

    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("Could not read saved UI preferences from %s: %s", target, exc)
        return {}

    if not isinstance(raw, dict):
        return {}
    return _sanitize_preferences(raw)


def save_ui_settings(
    updates: dict[str, Any],
    path: str | Path | None = None,
    *,
    merge_existing: bool = True,
) -> Path | None:
    """Persist non-secret UI settings atomically to disk."""
    target = resolve_preferences_path(path)
    if target is None:
        return None

    filtered = _sanitize_preferences(updates)
    payload: dict[str, Any] = load_ui_settings(target) if merge_existing else {}
    payload.update(filtered)

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(target.parent, 0o700)
        except OSError:
            pass
        tmp_path = target.with_suffix(f"{target.suffix}.tmp")
        tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp_path.replace(target)
    except OSError as exc:
        logger.warning("Could not save UI preferences to %s: %s", target, exc)
        return None

    return target


def clear_ui_settings(path: str | Path | None = None) -> bool:
    """Delete the saved UI preferences file if present."""
    target = resolve_preferences_path(path)
    if target is None or not target.exists():
        return False
    try:
        target.unlink()
        return True
    except OSError as exc:
        logger.warning("Could not remove saved UI preferences at %s: %s", target, exc)
        return False
