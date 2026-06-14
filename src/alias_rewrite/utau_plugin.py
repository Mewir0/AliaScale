from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re

from .ust import UstDocument, get_setting_value, parse_ust_file


@dataclass(frozen=True)
# UTAU連携情報を保持する
class UtauPluginContext:
    temp_path: Path
    voice_dir: str
    note_count: int
    encoding: str


# UTAU連携情報を読み込む
def load_utau_plugin_context(path: str | Path | None) -> UtauPluginContext | None:
    if not path:
        return None
    temp_path = Path(path)
    if not temp_path.is_file():
        return None
    try:
        document = parse_ust_file(temp_path)
    except OSError:
        return None
    voice_dir = get_setting_value(document, "VoiceDir") or ""
    if not voice_dir:
        return None
    return UtauPluginContext(
        temp_path=temp_path,
        voice_dir=voice_dir,
        note_count=count_note_sections(document),
        encoding=document.encoding,
    )


# 音符区画一覧を数える
def count_note_sections(document: UstDocument) -> int:
    count = 0
    in_note = False
    has_lyric = False
    section_re = re.compile(r"^\[#(\d+)\]$")
    for line in document.lines:
        body = line.body.strip()
        if body.startswith("[#") and body.endswith("]"):
            if in_note and has_lyric:
                count += 1
            in_note = bool(section_re.match(body))
            has_lyric = False
            continue
        if in_note and line.body.startswith("Lyric="):
            has_lyric = True
    if in_note and has_lyric:
        count += 1
    return count


# 音源を処理する
def same_voice_dir(left: str | Path, right: str | Path) -> bool:
    if not left or not right:
        return False
    return _normalized_path(left) == _normalized_path(right)


# パスを処理する
def _normalized_path(path: str | Path) -> str:
    text = str(path).strip().strip('"')
    try:
        resolved = Path(text).expanduser().resolve(strict=False)
        return os.path.normcase(os.path.abspath(str(resolved)))
    except OSError:
        return os.path.normcase(os.path.abspath(text))
