from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .changes import ChangeRow
from .keys import call_key_candidates_for_entry, pronunciation_mora, resolve_call_key, wav_key
from .oto import iter_entries, parse_oto_file, OtoEntry
from .ust import UstDocument, UstLine, parse_ust_file, replace_lyrics, write_ust_document
from .voice_scan import scan_ust_files


@dataclass(frozen=True)
# USTPreviewを保持する
class UstSyncPreview:
    ust_path: Path
    output_path: Path
    replacements: int
    matched_voice: bool = True
    warnings: tuple[str, ...] = ()


# UST更新用のalias対応表を作る
def build_ust_alias_map(changes: list[ChangeRow]) -> dict[str, str]:
    alias_map: dict[str, str] = {}
    for change in changes:
        if not change.changed or not change.old_alias or not change.new_alias:
            continue
        alias_map.setdefault(change.old_alias, change.new_alias)
    return alias_map


# 変更行番号を処理する
def _change_by_line_number(changes: list[ChangeRow]) -> dict[int, ChangeRow]:
    return {
        change.line_number: change
        for change in changes
        if change.line_number is not None and change.changed
    }


# 無効発音ではない候補キーを判定する
def _is_valid_replacement_candidate(candidate, excluded_moras: tuple[str, ...]) -> bool:
    excluded = {mora.strip() for mora in excluded_moras if mora.strip()}
    return bool(candidate.key) and not (candidate.mora and candidate.mora in excluded)


# 無効発音ではない文字列キーを判定する
def _is_valid_replacement_key(key: str, excluded_moras: tuple[str, ...]) -> bool:
    if not key:
        return False
    excluded = {mora.strip() for mora in excluded_moras if mora.strip()}
    mora = pronunciation_mora(key)
    return not (mora and mora in excluded)


# キー変更を処理する
def _replacement_key_for_change(
    *,
    old_key: str,
    change: ChangeRow,
    entries_after: list,
    excluded_moras: tuple[str, ...] = (),
) -> str:
    from .oto import OtoEntry

    entry_after = OtoEntry(
        line_number=change.line_number or 0,
        wav_name=change.new_wav,
        alias=change.new_alias,
        offset=0,
        consonant=0,
        cutoff=0,
        preutterance=0,
        overlap=0,
    )
    old_mora = pronunciation_mora(old_key)
    candidates = []
    for candidate in call_key_candidates_for_entry(entry_after):
        if not _is_valid_replacement_candidate(candidate, excluded_moras):
            continue
        resolved = resolve_call_key(candidate.key, entries_after)
        if resolved is not None and resolved.line_number == change.line_number:
            candidates.append(candidate)
    if not candidates:
        if _is_valid_replacement_key(change.new_alias, excluded_moras):
            return change.new_alias
        new_wav_key = wav_key(change.new_wav)
        if _is_valid_replacement_key(new_wav_key, excluded_moras):
            return new_wav_key
        return ""
    matching = [candidate for candidate in candidates if candidate.mora == old_mora]
    pool = matching or candidates
    for candidate in pool:
        if candidate.kind == "alias":
            return candidate.key
    return pool[0].key


# 行一覧を置換する
def _replace_lyrics_by_resolved_entries(
    document: UstDocument,
    changes: list[ChangeRow],
    entries_before: list[OtoEntry],
    excluded_moras: tuple[str, ...] = (),
) -> tuple[UstDocument, int, tuple[str, ...]]:
    entries = entries_before
    changes_by_line = _change_by_line_number(changes)
    from .keys import apply_changes_to_entries

    entries_after = apply_changes_to_entries(entries, changes)
    new_lines: list[UstLine] = []
    replacements = 0
    warnings: list[str] = []

    for line in document.lines:
        if not line.body.startswith("Lyric="):
            new_lines.append(line)
            continue

        old_key = line.body[len("Lyric=") :]
        entry = resolve_call_key(old_key, entries)
        if entry is None:
            new_lines.append(line)
            continue

        change = changes_by_line.get(entry.line_number)
        if change is None:
            new_lines.append(line)
            continue

        replacement_key = _replacement_key_for_change(
            old_key=old_key,
            change=change,
            entries_after=entries_after,
            excluded_moras=excluded_moras,
        )
        if replacement_key and replacement_key != old_key:
            new_lines.append(UstLine("Lyric=" + replacement_key, line.line_ending))
            replacements += 1
            continue

        if not replacement_key:
            warnings.append(
                f"{document.path}: Lyric '{old_key}' was not updated because new alias and wav key are empty or invalid pronunciation"
            )
            new_lines.append(line)
            continue

        new_lines.append(line)

    return (
        UstDocument(path=document.path, lines=tuple(new_lines), encoding=document.encoding),
        replacements,
        tuple(warnings),
    )


# USTファイルの更新Previewを生成する
def preview_ust_sync_for_file(
    ust_path: str | Path,
    changes: list[ChangeRow],
    output_path: str | Path | None = None,
    *,
    oto_path: str | Path | None = None,
    entries_before: list[OtoEntry] | None = None,
    excluded_moras: tuple[str, ...] = (),
) -> UstSyncPreview:
    ust_path = Path(ust_path)
    document = parse_ust_file(ust_path)

    if entries_before is not None:
        _, replacements, warnings = _replace_lyrics_by_resolved_entries(
            document, changes, entries_before, excluded_moras
        )
    elif oto_path is not None:
        lines, _ = parse_oto_file(oto_path)
        _, replacements, warnings = _replace_lyrics_by_resolved_entries(
            document, changes, iter_entries(lines), excluded_moras
        )
    else:
        alias_map = build_ust_alias_map(changes)
        _, replacements = replace_lyrics(document, alias_map)
        warnings = ()

    output = Path(output_path) if output_path else ust_path.with_name(
        ust_path.stem + "_alias_rewrite.ust"
    )

    return UstSyncPreview(
        ust_path=ust_path,
        output_path=output,
        replacements=replacements,
        warnings=warnings,
    )


# USTファイルへLyric変更を反映する
def apply_ust_sync_for_file( 
    ust_path: str | Path, 
    changes: list[ChangeRow], 
    output_path: str | Path | None = None, 
    *, 
    overwrite: bool = False, 
    oto_path: str | Path | None = None, 
    entries_before: list[OtoEntry] | None = None, 
    excluded_moras: tuple[str, ...] = (),
) -> UstSyncPreview: 
    ust_path = Path(ust_path) 
    document = parse_ust_file(ust_path) 

    if entries_before is not None:
        updated_document, replacements, warnings = _replace_lyrics_by_resolved_entries(
            document, changes, entries_before, excluded_moras
        )

    elif oto_path is not None:
        lines, _ = parse_oto_file(oto_path)
        updated_document, replacements, warnings = _replace_lyrics_by_resolved_entries(
            document, changes, iter_entries(lines), excluded_moras
        )

    else:
        # 簡易モードではalias対応表を使う
        alias_map = build_ust_alias_map(changes) 
        updated_document, replacements = replace_lyrics(document, alias_map) 
        warnings: tuple[str, ...] = () 

    output = ust_path if overwrite else (
        Path(output_path) if output_path else ust_path.with_name(ust_path.stem + "_alias_rewrite.ust")
    ) 

    write_ust_document(updated_document, output) 

    return UstSyncPreview(
        ust_path=ust_path,
        output_path=output,
        replacements=replacements,
        warnings=warnings
    )


# USTフォルダの更新Previewを生成する
def preview_ust_sync_for_folder(
    ust_root: str | Path,
    voice_dir: str | Path,
    changes: list[ChangeRow],
    *,
    oto_path: str | Path | None = None,
    entries_before: list[OtoEntry] | None = None,
    strict_voice_match: bool = False,
    utau_exe_path: str | Path | None = None,
    excluded_moras: tuple[str, ...] = (),
) -> list[UstSyncPreview]:
    previews: list[UstSyncPreview] = []

    for scan in scan_ust_files(
        ust_root,
        voice_dir,
        strict_voice_match=strict_voice_match,
        utau_exe_path=utau_exe_path,
    ):
        if not scan.matched:
            continue

        previews.append(
            preview_ust_sync_for_file(
                scan.path,
                changes,
                oto_path=oto_path,
                entries_before=entries_before,
                excluded_moras=excluded_moras,
            )
        )

    return previews
