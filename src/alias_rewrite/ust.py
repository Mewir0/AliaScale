from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from .oto import decode_text


@dataclass(frozen=True)
# UST行を保持する
class UstLine:
    body: str
    line_ending: str


@dataclass(frozen=True)
# UST文書を保持する
class UstDocument:
    path: Path
    lines: tuple[UstLine, ...]
    encoding: str


# 行を分割する
def _split_line_ending(line: str) -> tuple[str, str]:
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith("\n") or line.endswith("\r"):
        return line[:-1], line[-1]
    return line, ""


# USTファイルを読み込む
def parse_ust_file(path: str | Path, encoding: str | None = None) -> UstDocument:
    path = Path(path)
    text, detected_encoding = decode_text(path.read_bytes(), encoding)
    lines = tuple(UstLine(*_split_line_ending(line)) for line in text.splitlines(keepends=True))
    return UstDocument(path=path, lines=lines, encoding=detected_encoding)


# UST文書を文字列へ戻す
def render_ust_document(document: UstDocument) -> str:
    return "".join(line.body + line.line_ending for line in document.lines)


# UST文書を書き出す
def write_ust_document(document: UstDocument, output_path: str | Path | None = None) -> Path:
    output_path = Path(output_path) if output_path else document.path
    with output_path.open("w", encoding=document.encoding, errors="replace", newline="") as fp:
        fp.write(render_ust_document(document))
    return output_path


# 設定値を取得する
def get_setting_value(document: UstDocument, key: str) -> str | None:
    prefix = key + "="
    for line in document.lines:
        if line.body.startswith(prefix):
            return line.body[len(prefix) :]
    return None


# USTのLyricを置換する
def replace_lyrics(document: UstDocument, alias_map: dict[str, str]) -> tuple[UstDocument, int]:
    new_lines = []
    count = 0
    for line in document.lines:
        if line.body.startswith("Lyric="):
            old_lyric = line.body[len("Lyric=") :]
            new_lyric = alias_map.get(old_lyric)
            if new_lyric is not None and new_lyric != old_lyric:
                new_lines.append(replace(line, body="Lyric=" + new_lyric))
                count += 1
                continue
        new_lines.append(line)
    return replace(document, lines=tuple(new_lines)), count
