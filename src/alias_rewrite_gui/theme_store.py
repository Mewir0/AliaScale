from __future__ import annotations

import json
from pathlib import Path
from shutil import copy2
from typing import Any

from .runtime_paths import default_settings_dir


DEFAULT_COLOR_SCHEMES = (
    {
        "name": "defoko_dark",
        "display_name": "\u30c7\u30d5\u30a9\u5b50(\u30c0\u30fc\u30af)",
        "colors": {},
    },
    {
        "name": "defoko_light",
        "display_name": "\u30c7\u30d5\u30a9\u5b50(\u30e9\u30a4\u30c8)",
        "colors": {},
    },
)


# Return the external color scheme path
def color_scheme_path() -> Path:
    return default_settings_dir() / "color_schemes.json"


# Return the bundled color scheme path
def bundled_color_scheme_path() -> Path:
    return Path(__file__).resolve().parents[1] / "json" / "color_schemes.json"


# Ensure the external color scheme file exists
def ensure_color_scheme_file(path: str | Path | None = None) -> Path:
    target = Path(path) if path else color_scheme_path()
    if target.exists():
        return target
    source = bundled_color_scheme_path()
    if source.exists():
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            copy2(source, target)
        except OSError:
            pass
    return target


# Load color schemes
def load_color_schemes(path: str | Path | None = None) -> list[dict[str, Any]]:
    target = ensure_color_scheme_file(path)
    try:
        with target.open("r", encoding="utf-8") as fp:
            data = json.load(fp)
    except (OSError, json.JSONDecodeError):
        return [dict(item) for item in DEFAULT_COLOR_SCHEMES]
    if not isinstance(data, list):
        return [dict(item) for item in DEFAULT_COLOR_SCHEMES]
    result: list[dict[str, Any]] = []
    names: set[str] = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name or name in names:
            continue
        display_name = str(item.get("display_name") or name)
        colors = item.get("colors")
        if not isinstance(colors, dict):
            colors = {}
        result.append({"name": name, "display_name": display_name, "colors": colors})
        names.add(name)
    if not result:
        return [dict(item) for item in DEFAULT_COLOR_SCHEMES]
    dark_colors = next((item.get("colors", {}) for item in result if item.get("name") == "defoko_dark"), {})
    if dark_colors:
        result = [
            {
                **item,
                "colors": {**dark_colors, **item.get("colors", {})},
            }
            for item in result
        ]
    return result
