from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import math


@dataclass(frozen=True)
# oto.ini行を保持する
class OtoEntry:
    wav_name: str
    alias: str
    offset: float
    consonant: float
    cutoff: float
    preutterance: float
    overlap: float
    line_number: int


@dataclass(frozen=True)
# oto.ini行を保持する
class OtoLine:
    raw: str
    line_ending: str
    entry: OtoEntry | None
    parts: tuple[str, ...] = ()


# 本文文字コードを検出する
def detect_text_encoding(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "cp932", "shift_jis"):
        try:
            raw.decode(encoding)
            return encoding
        except UnicodeDecodeError:
            pass
    return "utf-8"


# 本文を復号する
def decode_text(raw: bytes, encoding: str | None = None) -> tuple[str, str]:
    encoding = encoding or detect_text_encoding(raw)
    return raw.decode(encoding, errors="replace"), encoding


# 行を分割する
def _split_line_ending(line: str) -> tuple[str, str]:
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith("\n") or line.endswith("\r"):
        return line[:-1], line[-1]
    return line, ""


# 値を読み込む
def _parse_float(text: str) -> float:
    text = text.strip()
    if text == "":
        return math.nan
    try:
        return float(text)
    except ValueError:
        return math.nan


# oto.ini本文を読み込む
def parse_oto_text(text: str) -> list[OtoLine]:
    lines: list[OtoLine] = []
    for line_number, raw_line in enumerate(text.splitlines(keepends=True), start=1):
        body, line_ending = _split_line_ending(raw_line)
        stripped = body.strip()
        if not stripped or stripped.startswith("#") or "=" not in body:
            lines.append(OtoLine(raw=body, line_ending=line_ending, entry=None))
            continue

        wav_name, rest = body.split("=", 1)
        parts = tuple(rest.split(","))
        if len(parts) < 6:
            lines.append(OtoLine(raw=body, line_ending=line_ending, entry=None))
            continue

        entry = OtoEntry(
            wav_name=wav_name.strip(),
            alias=parts[0].strip(),
            offset=_parse_float(parts[1]),
            consonant=_parse_float(parts[2]),
            cutoff=_parse_float(parts[3]),
            preutterance=_parse_float(parts[4]),
            overlap=_parse_float(parts[5]),
            line_number=line_number,
        )
        lines.append(OtoLine(raw=body, line_ending=line_ending, entry=entry, parts=parts))
    return lines


# oto.iniを読み込む
def parse_oto_file(path: str | Path, encoding: str | None = None) -> tuple[list[OtoLine], str]:
    raw = Path(path).read_bytes()
    text, detected_encoding = decode_text(raw, encoding)
    return parse_oto_text(text), detected_encoding


# 行一覧を取り出す
def iter_entries(lines: list[OtoLine]) -> list[OtoEntry]:
    return [line.entry for line in lines if line.entry is not None]


# oto.ini行を書き出し用文字列へ戻す
def render_oto_line(line: OtoLine, new_alias: str | None = None, new_wav_name: str | None = None) -> str:
    if line.entry is None or (new_alias is None and new_wav_name is None):
        return line.raw + line.line_ending
    wav_name, _ = line.raw.split("=", 1)
    parts = list(line.parts)
    if new_alias is not None:
        parts[0] = new_alias
    if new_wav_name is not None:
        wav_name = new_wav_name
    return wav_name + "=" + ",".join(parts) + line.line_ending


# oto.iniのコピーを書き出す
def write_oto_copy(
    lines: list[OtoLine],
    alias_by_line_number: dict[int, str],
    output_path: str | Path,
    encoding: str = "cp932",
    wav_by_line_number: dict[int, str] | None = None,
    order_by_line_number: dict[int, int] | None = None,
) -> Path:
    output_path = Path(output_path)
    wav_by_line_number = wav_by_line_number or {}
    order_by_line_number = order_by_line_number or {}
    rendered_lines = []
    for line in lines:
        new_alias = alias_by_line_number.get(line.entry.line_number) if line.entry else None
        new_wav = wav_by_line_number.get(line.entry.line_number) if line.entry else None
        rendered_lines.append((line, render_oto_line(line, new_alias, new_wav)))

    if order_by_line_number:
        sortable_entries = [
            (order_by_line_number.get(line.entry.line_number, line.entry.line_number), index, rendered)
            for index, (line, rendered) in enumerate(rendered_lines)
            if line.entry is not None
        ]
        sorted_entries = [rendered for _, _, rendered in sorted(sortable_entries)]
        sorted_index = 0
        rendered = []
        for line, current_rendered in rendered_lines:
            if line.entry is None:
                rendered.append(current_rendered)
                continue
            rendered.append(sorted_entries[sorted_index])
            sorted_index += 1
    else:
        rendered = [rendered for _, rendered in rendered_lines]

    with output_path.open("w", encoding=encoding, errors="replace", newline="") as fp:
        fp.write("".join(rendered))
    return output_path
