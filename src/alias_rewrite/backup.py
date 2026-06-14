from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re
from shutil import copy2, copytree, rmtree


@dataclass(frozen=True)
# バックアップ結果を保持する
class BackupResult:
    source_path: Path
    backup_path: Path


BACKUP_DIR_RE = re.compile(r".+_\d{8}_\d{6}(?:_\d+)?$")


# バックアップパスを作る
def make_backup_path(source_path: str | Path, backup_root: str | Path, timestamp: str | None = None) -> Path:
    source_path = Path(source_path)
    backup_root = Path(backup_root)
    timestamp = timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    return backup_root / timestamp / source_path.name


# Apply単位のバックアップフォルダを作る
def make_backup_session_path(backup_root: str | Path, timestamp: str) -> Path:
    backup_root = Path(backup_root)
    session_path = backup_root / timestamp
    if not session_path.exists():
        return session_path
    suffix = 1
    while True:
        candidate = backup_root / f"{timestamp}_{suffix}"
        if not candidate.exists():
            return candidate
        suffix += 1


# バックアップフォルダ内の重複しない保存先を作る
def _unique_child_path(parent: Path, name: str) -> Path:
    candidate = parent / name
    if not candidate.exists():
        return candidate
    stem = Path(name).stem
    suffix = Path(name).suffix
    index = 1
    while True:
        next_candidate = parent / f"{stem}_{index}{suffix}"
        if not next_candidate.exists():
            return next_candidate
        index += 1


# ファイルをApply単位バックアップへ保存する
def backup_file_to_session(source_path: str | Path, session_path: str | Path) -> BackupResult:
    source_path = Path(source_path)
    session_path = Path(session_path)
    session_path.mkdir(parents=True, exist_ok=True)
    backup_path = _unique_child_path(session_path, source_path.name)
    copy2(source_path, backup_path)
    return BackupResult(source_path=source_path, backup_path=backup_path)


# 音源フォルダをApply単位バックアップへ保存する
def backup_directory_to_session(source_path: str | Path, session_path: str | Path) -> BackupResult:
    source_path = Path(source_path)
    session_path = Path(session_path)
    if not source_path.is_dir():
        raise NotADirectoryError(source_path)
    backup_path = _unique_child_path(session_path, source_path.name)
    try:
        backup_path.resolve().relative_to(source_path.resolve())
        inside_source = True
    except ValueError:
        inside_source = False
    if inside_source:
        raise ValueError("backup directory must not be inside the source directory")
    session_path.mkdir(parents=True, exist_ok=True)
    copytree(source_path, backup_path)
    return BackupResult(source_path=source_path, backup_path=backup_path)


# ファイルをバックアップする
def backup_file(source_path: str | Path, backup_root: str | Path, timestamp: str | None = None) -> BackupResult:
    source_path = Path(source_path)
    backup_path = make_backup_path(source_path, backup_root, timestamp)
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    copy2(source_path, backup_path)
    return BackupResult(source_path=source_path, backup_path=backup_path)


# フォルダをバックアップする
def backup_directory(source_path: str | Path, backup_root: str | Path, timestamp: str | None = None) -> BackupResult:
    source_path = Path(source_path)
    backup_root = Path(backup_root)
    timestamp = timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_root / timestamp
    if not source_path.is_dir():
        raise NotADirectoryError(source_path)
    try:
        backup_path.resolve().relative_to(source_path.resolve())
        inside_source = True
    except ValueError:
        inside_source = False
    if inside_source:
        raise ValueError("backup directory must not be inside the source directory")
    if backup_path.exists():
        suffix = 1
        base_path = backup_path
        while backup_path.exists():
            backup_path = base_path.with_name(f"{base_path.name}_{suffix}")
            suffix += 1
    copytree(source_path, backup_path)
    return BackupResult(source_path=source_path, backup_path=backup_path)


# バックアップフォルダ一覧を整理する
def prune_backup_directories(backup_root: str | Path, max_count: int) -> list[Path]:
    """Delete old AliaScale backup folders when the configured limit is exceeded."""
    backup_root = Path(backup_root)
    if max_count < 1 or not backup_root.exists():
        return []

    candidates = [
        path
        for path in backup_root.iterdir()
        if path.is_dir() and BACKUP_DIR_RE.match(path.name)
    ]
    if len(candidates) <= max_count:
        return []

    candidates.sort(key=lambda path: (path.stat().st_mtime, path.name))
    deleted: list[Path] = []
    for path in candidates[: len(candidates) - max_count]:
        rmtree(path)
        deleted.append(path)
    return deleted
