from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import re
import unicodedata

from .oto import OtoEntry


SMALL_KANA = set("ゃゅょぁぃぅぇぉゎャュョァィゥェォヮ")
VOICING_MARKS = set("\u3099\u309a゛゜ﾞﾟ")
NUMBERED_ALIAS_RE = re.compile(r"^(?P<head>.*?)(?P<number>[2-9][0-9]*)(?P<sep>[_-].*)$")
PREFIX_SPLIT_RE = re.compile(r"^(?P<prefix>.*\s+)(?P<body>.*)$")


@dataclass(frozen=True)
# alias設定を保持する
class AliasRewriteConfig:
    separator: str = "_"
    strip_suffix: bool = True
    keep_prefix: bool = True
    missing_pitch: str = "keep"  # keep / append_marker
    missing_pitch_marker: str = "無"
    duplicate_strategy: str = "number"  # number


@dataclass(frozen=True)
# aliasを保持する
class AliasParts:
    original_alias: str
    source_alias: str
    prefix: str
    body: str
    mora: str
    suffix: str


@dataclass(frozen=True)
# alias変更を保持する
class AliasChange:
    line_number: int
    wav_name: str
    old_alias: str
    source_alias: str
    new_alias: str
    note: str | None
    changed: bool
    reason: str


# alias行を取得す
def source_alias_for_entry(entry: OtoEntry) -> str:
    if entry.alias:
        return entry.alias
    return Path(entry.wav_name).stem


# aliasを分割する
def split_alias_prefix(alias: str) -> tuple[str, str]:
    """Split at the last whitespace.

    UTAU-style aliases often use a leading prefix such as "a きゃ".
    AliaScale treats the last whitespace as the prefix/body boundary, so the
    pronunciation mora is extracted from the first one or two characters after
    that whitespace. Earlier whitespace stays in the prefix.
    """
    match = PREFIX_SPLIT_RE.match(alias)
    if not match:
        return "", alias
    return match.group("prefix"), match.group("body")


# aliasを正規化する
def normalize_alias_body(body: str) -> str:
    return unicodedata.normalize("NFKC", body)


# 発音を取り出す
def extract_leading_mora(body: str) -> tuple[str, str]:
    """Extract one Japanese pronunciation unit from the beginning of body.

    This intentionally stays small and table-driven: a base kana plus a following
    small kana is treated as one pronunciation unit, so strings such as きゃ, しゃ, ぢょ are kept
    together. Future language-specific rules can replace this function without
    changing the rewrite orchestration.
    """
    normalized = normalize_alias_body(body).replace(" \u3099", "\u3099").replace(" \u309a", "\u309a")
    if not normalized:
        return "", ""
    end = 1
    if len(normalized) > end and normalized[end] in VOICING_MARKS:
        end += 1
    if len(normalized) > end and normalized[end] in SMALL_KANA:
        end += 1
    return normalized[:end], normalized[end:]


# aliasを分割する
def split_alias(alias: str) -> AliasParts:
    source_alias = alias
    prefix, body = split_alias_prefix(source_alias)
    mora, suffix = extract_leading_mora(body)
    return AliasParts(
        original_alias=alias,
        source_alias=source_alias,
        prefix=prefix,
        body=body,
        mora=mora,
        suffix=suffix,
    )


# alias変更を作る
def build_alias_change(
    entry: OtoEntry,
    note: str | None,
    config: AliasRewriteConfig | None = None,
) -> AliasChange:
    config = config or AliasRewriteConfig()
    source_alias = source_alias_for_entry(entry)
    parts = split_alias(source_alias)
    if not note:
        if config.missing_pitch == "append_marker" and parts.mora:
            base = parts.mora if config.strip_suffix else normalize_alias_body(parts.body)
            prefix = parts.prefix if config.keep_prefix else ""
            new_alias = f"{prefix}{base}{config.separator}{config.missing_pitch_marker}"
            return AliasChange(entry.line_number, entry.wav_name, entry.alias, source_alias, new_alias, note, True, "missing_pitch_marker")
        return AliasChange(entry.line_number, entry.wav_name, entry.alias, source_alias, entry.alias, note, False, "missing_pitch_keep")

    base = parts.mora if config.strip_suffix else normalize_alias_body(parts.body)
    prefix = parts.prefix if config.keep_prefix else ""
    if not base:
        return AliasChange(entry.line_number, entry.wav_name, entry.alias, source_alias, entry.alias, note, False, "empty_alias_body")

    new_alias = f"{prefix}{base}{config.separator}{note}"
    return AliasChange(entry.line_number, entry.wav_name, entry.alias, source_alias, new_alias, note, new_alias != entry.alias, "rewritten")


# 重複を番号を付ける
def _number_duplicate(alias: str, number: int) -> str:
    match = NUMBERED_ALIAS_RE.match(alias)
    if match:
        return f"{match.group('head')}{number}{match.group('sep')}"
    if "_" in alias:
        head, sep, tail = alias.rpartition("_")
        return f"{head}{number}{sep}{tail}"
    return f"{alias}{number}"


# 重複を解決する
def resolve_duplicate_aliases(changes: list[AliasChange], config: AliasRewriteConfig | None = None) -> list[AliasChange]:
    config = config or AliasRewriteConfig()
    if config.duplicate_strategy != "number":
        raise ValueError(f"Unsupported duplicate_strategy: {config.duplicate_strategy}")

    used: dict[str, int] = {}
    resolved: list[AliasChange] = []
    for change in changes:
        if not change.changed:
            resolved.append(change)
            continue
        key = change.new_alias
        count = used.get(key, 0) + 1
        used[key] = count
        if count == 1:
            resolved.append(change)
            continue
        new_alias = _number_duplicate(change.new_alias, count)
        while new_alias in used:
            count += 1
            new_alias = _number_duplicate(change.new_alias, count)
        used[new_alias] = 1
        resolved.append(
            AliasChange(
                line_number=change.line_number,
                wav_name=change.wav_name,
                old_alias=change.old_alias,
                source_alias=change.source_alias,
                new_alias=new_alias,
                note=change.note,
                changed=new_alias != change.old_alias,
                reason="duplicate_numbered",
            )
        )
    return resolved
