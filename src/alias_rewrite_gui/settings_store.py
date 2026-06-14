from __future__ import annotations

from dataclasses import asdict, fields, is_dataclass
import json
from pathlib import Path
from typing import TypeVar

from .dto import AppSettings, ExcludeSettings, ReplacementRuleDto, RewriteSettings, SortSpec
from .runtime_paths import default_settings_dir


T = TypeVar("T")


# Return the settings directory
def default_config_dir(app_name: str = "AliaScale") -> Path:
    return default_settings_dir()


# Return the legacy settings directory
def legacy_config_dir(app_name: str = "AliaScale") -> Path:
    return Path.home() / "AppData" / "Roaming" / app_name


# Normalize legacy settings values
def _normalize_app_settings_data(data: dict) -> dict:
    data = dict(data)
    if data.get("theme") == "dark":
        data["theme"] = "defoko_dark"
    elif data.get("theme") == "light":
        data["theme"] = "defoko_light"
    if data.get("wav_edit_mode") in {"representative", "empty_alias_only"}:
        data["wav_edit_mode"] = "allow"
    if "numbering_order_mode" not in data and data.get("number_alias_before_wav") is True:
        data["numbering_order_mode"] = "alias_wav"
    if str(data.get("backup_root") or "").strip().lower() == "backups":
        data["backup_root"] = "backup"
    legacy_related = ("{stem}.frq", "{name}.llsm", "{stem}.hifi.npz")
    if tuple(data.get("related_file_patterns") or ()) == legacy_related:
        data["related_file_patterns"] = ("{stem}_wav.frq", "{stem}.wav.llsm", "{stem}_wav.pmk", "{stem}*.hifi.npz")
    for field_name in ("excluded_call_key_moras", "auto_wav_excluded_moras", "related_file_patterns"):
        value = data.get(field_name)
        if isinstance(value, list):
            data[field_name] = tuple(str(item) for item in value)
    return data


# Filter dataclass constructor values
def _filter_dataclass_kwargs(cls: type[T], data: dict) -> dict:
    names = {field.name for field in fields(cls)}
    return {key: value for key, value in data.items() if key in names}


# Load a JSON object
def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fp:
        loaded = json.load(fp)
    return loaded if isinstance(loaded, dict) else {}


# Write a JSON object
def _write_json(path: Path, data: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False, indent=2, sort_keys=True)
    return path


# Convert a dataclass to a dict
def _dataclass_to_dict(value) -> dict:
    if not is_dataclass(value):
        raise TypeError("value must be a dataclass instance")
    return asdict(value)


# Load app settings
def load_app_settings(config_dir: str | Path | None = None) -> AppSettings:
    root = Path(config_dir) if config_dir else default_config_dir()
    data = _load_json(root / "settings.json")
    if not data and config_dir is None:
        data = _load_json(legacy_config_dir() / "settings.json")
    data = _normalize_app_settings_data(data)
    return AppSettings(**_filter_dataclass_kwargs(AppSettings, data))


# Save app settings
def save_app_settings(settings: AppSettings, config_dir: str | Path | None = None) -> Path:
    root = Path(config_dir) if config_dir else default_config_dir()
    return _write_json(root / "settings.json", _dataclass_to_dict(settings))


# Load a preset
def load_preset(name: str, config_dir: str | Path | None = None) -> RewriteSettings:
    root = Path(config_dir) if config_dir else default_config_dir()
    data = _load_json(root / "presets" / f"{name}.json")
    if not data and config_dir is None:
        data = _load_json(legacy_config_dir() / "presets" / f"{name}.json")
    kwargs = _filter_dataclass_kwargs(RewriteSettings, data)
    if isinstance(kwargs.get("exclude"), dict):
        kwargs["exclude"] = ExcludeSettings(**_filter_dataclass_kwargs(ExcludeSettings, kwargs["exclude"]))
    if isinstance(kwargs.get("replacement_rules"), list):
        kwargs["replacement_rules"] = tuple(
            ReplacementRuleDto(**_filter_dataclass_kwargs(ReplacementRuleDto, rule))
            for rule in kwargs["replacement_rules"]
            if isinstance(rule, dict)
        )
    if isinstance(kwargs.get("sort"), list):
        kwargs["sort"] = tuple(
            SortSpec(**_filter_dataclass_kwargs(SortSpec, spec))
            for spec in kwargs["sort"]
            if isinstance(spec, dict)
        )
    return RewriteSettings(**kwargs)


# Save a preset
def save_preset(name: str, settings: RewriteSettings, config_dir: str | Path | None = None) -> Path:
    if not name.strip():
        raise ValueError("preset name is required")
    root = Path(config_dir) if config_dir else default_config_dir()
    safe_name = "".join(char for char in name.strip() if char not in '\\/:*?"<>|')
    return _write_json(root / "presets" / f"{safe_name}.json", _dataclass_to_dict(settings))


# List presets
def list_presets(config_dir: str | Path | None = None) -> list[str]:
    root = Path(config_dir) if config_dir else default_config_dir()
    preset_dir = root / "presets"
    names = {path.stem for path in preset_dir.glob("*.json") if path.is_file()} if preset_dir.exists() else set()
    if config_dir is None:
        legacy_preset_dir = legacy_config_dir() / "presets"
        if legacy_preset_dir.exists():
            names.update(path.stem for path in legacy_preset_dir.glob("*.json") if path.is_file())
    return sorted(names)
