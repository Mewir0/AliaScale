from __future__ import annotations

from pathlib import Path


# 隠しwav名を作る
def hidden_wav_name(wav_name: str, prefix: str = "_") -> str:
    path = Path(wav_name)
    name = path.name
    new_name = name if name.startswith(prefix) else prefix + name
    parent = str(path.parent)
    if parent in {"", "."}:
        return new_name
    return str(Path(parent) / new_name)
