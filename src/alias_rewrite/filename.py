from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from .changes import ChangeRow
from .rewrite import preview_rewrite
from .wav_names import hidden_wav_name


@dataclass(frozen=True)
# 設定を保持する
class FilenameRewriteConfig:
    rename_empty_alias_wav: bool = True
    add_alias_for_empty_alias: bool = True
    hidden_source_prefix: str = "_"
    rename_related_files: bool = True
    related_file_patterns: tuple[str, ...] = ("{stem}_wav.frq", "{stem}.wav.llsm", "{stem}_wav.pmk", "{stem}*.hifi.npz")
    rename_sidecar_files: bool = False
    sidecar_suffixes: tuple[str, ...] = ()
    kana_conversion: bool = False


# 変更一覧を作る
def build_filename_rewrite_changes(
    oto_path: str | Path,
    mrq_path: str | Path,
    alias_config=None,
    note_config=None,
    config: FilenameRewriteConfig | None = None,
) -> list[ChangeRow]:
    """Build ChangeRows for empty-alias entries, optionally moving them to alias management."""
    config = config or FilenameRewriteConfig()
    rows, _ = preview_rewrite(oto_path, mrq_path, alias_config, note_config)
    changes: list[ChangeRow] = []
    for row in rows:
        if row.old_alias != "":
            continue
        new_wav = row.wav_name
        new_alias = row.new_alias if config.add_alias_for_empty_alias else row.old_alias
        if config.rename_empty_alias_wav:
            new_wav = hidden_wav_name(row.wav_name, config.hidden_source_prefix)
        changed = row.wav_name != new_wav or row.old_alias != new_alias
        changes.append(
            ChangeRow(
                line_number=row.line_number,
                old_wav=row.wav_name,
                new_wav=new_wav,
                old_alias=row.old_alias,
                new_alias=new_alias,
                source_alias=row.source_alias,
                note=row.note or "",
                frequency=row.frequency,
                status=row.pitch_status,
                changed=changed,
                reason="filename_rewrite_empty_alias" if changed else "filename_rewrite_noop",
            )
        )
    return changes


# wav名前一覧を付与する
def with_hidden_wav_names(changes: list[ChangeRow], prefix: str = "_") -> list[ChangeRow]:
    return [
        replace(
            change,
            new_wav=hidden_wav_name(change.old_wav, prefix),
            changed=change.old_wav != hidden_wav_name(change.old_wav, prefix)
            or change.old_alias != change.new_alias,
        )
        for change in changes
    ]
