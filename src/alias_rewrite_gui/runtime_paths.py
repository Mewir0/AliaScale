from __future__ import annotations

import os
from pathlib import Path
import sys


# Return the app base directory
def app_base_dir() -> Path:
    override = os.environ.get("ALIASCALE_BASE_DIR")
    if override:
        return Path(override).expanduser().resolve()

    try:
        return Path(__compiled__.containing_dir).resolve()  # type: ignore[name-defined]
    except NameError:
        pass
    except Exception:
        pass

    argv0 = Path(sys.argv[0]).expanduser()
    if argv0.suffix.lower() in {".exe", ".bin"}:
        return argv0.resolve().parent

    return Path(__file__).resolve().parents[2]


# Return the settings directory
def default_settings_dir() -> Path:
    return app_base_dir() / "settings"


# Return the backup directory
def default_backup_root() -> Path:
    return app_base_dir() / "backup"


# Return the log directory
def default_logs_dir() -> Path:
    return app_base_dir() / "logs"


# Resolve an app relative path
def resolve_app_path(path: str | Path | None, default: str | Path) -> Path:
    candidate = Path(path) if path else Path(default)
    if candidate.is_absolute():
        return candidate
    return app_base_dir() / candidate
