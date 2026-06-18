from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path


# 指定日数より古いファイルを削除する
def prune_files_older_than(
    root: str | Path,
    pattern: str,
    max_age_days: int,
    *,
    now: datetime | None = None,
) -> list[Path]:
    root = Path(root)
    if max_age_days < 1 or not root.exists():
        return []

    current_time = now or datetime.now()
    cutoff = current_time - timedelta(days=max_age_days)
    deleted: list[Path] = []

    for path in root.glob(pattern):
        if not path.is_file():
            continue
        modified = datetime.fromtimestamp(path.stat().st_mtime)
        if modified >= cutoff:
            continue
        path.unlink()
        deleted.append(path)

    return deleted
