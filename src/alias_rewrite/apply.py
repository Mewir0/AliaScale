from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from .backup import (
    BackupResult,
    backup_directory_to_session,
    backup_file_to_session,
    make_backup_session_path,
    prune_backup_directories,
)
from .changes import (
    ChangeRow,
    CsvApplyResult,
    apply_changes_to_oto_file,
    merge_changes,
    read_changes_csv,
    write_changes_csv,
)
from .filename import FilenameRewriteConfig
from .files import FileRenamePlan, FileRenameResult, apply_file_rename_plan, build_file_rename_plan
from .mrq import rewrite_mrq_wav_names
from .options import WavEditMode, normalize_wav_edit_mode
from .utau_plugin import load_utau_plugin_context, same_voice_dir
from .ust_sync import UstSyncPreview, apply_ust_sync_for_file, preview_ust_sync_for_folder
from .oto import iter_entries, parse_oto_file


@dataclass(frozen=True)
# Apply設定を保持する
class ApplyOptions:
    backup: bool = True
    backup_root: str | Path = "backup"
    backup_mode: str = "voice_dir"
    write_csv: bool = True
    csv_path: str | Path | None = None
    merge_csv: bool = False
    update_ust: bool = False
    ust_root: str | Path | None = None
    selected_ust_paths: tuple[str | Path, ...] | None = None
    rename_files: bool = False
    allow_wav_edit: bool = True
    wav_edit_mode: str = WavEditMode.ALLOW.value
    allow_alias_edit: bool = True
    alias_edit_mode: str = WavEditMode.ALLOW.value
    block_on_danger: bool = True
    backup_max_count_enabled: bool = False
    backup_max_count: int = 10
    utau_plugin_temp_path: str | Path | None = None
    strict_voice_match: bool = False
    utau_exe_path: str | Path | None = None
    filename_config: FilenameRewriteConfig = field(default_factory=FilenameRewriteConfig)
    excluded_call_key_moras: tuple[str, ...] = ("_",)


@dataclass(frozen=True)
# Apply結果を保持する
class ApplyResult:
    oto_path: Path
    oto_results: list[CsvApplyResult]
    backups: list[BackupResult] = field(default_factory=list)
    written_files: list[Path] = field(default_factory=list)
    csv_path: Path | None = None
    ust_results: list[UstSyncPreview] = field(default_factory=list)
    file_results: list[FileRenameResult] = field(default_factory=list)
    moved_to_conflict_folder: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


# CSV出力先の既定パスを作る
def _default_csv_path(voice_dir: Path, oto_path: Path, timestamp: str, *, merge_csv: bool) -> Path:
    if merge_csv:
        return oto_path.with_name(f"{voice_dir.name}.csv")
    return oto_path.with_name(f"{voice_dir.name}_{timestamp}.csv")


# oto.ini更新用の一時ファイルパスを作る
def _temporary_oto_path(oto_path: Path) -> Path:
    while True:
        candidate = oto_path.with_name(f".aliascale_tmp_{uuid4().hex}_{oto_path.name}")
        if not candidate.exists():
            return candidate


# 指定された一時ファイルを削除する
def _cleanup_path(path: Path | None) -> None:
    if path is None:
        return
    with suppress(OSError):
        path.unlink()


# 古いoto.ini一時ファイルを削除する
def _cleanup_stale_oto_temp_files(oto_path: Path) -> None:
    for path in oto_path.parent.glob(f".aliascale_tmp_*_{oto_path.name}"):
        _cleanup_path(path)


# rename済みファイルを可能な範囲で戻す
def _rollback_file_results(results: list[FileRenameResult]) -> list[FileRenameResult]:
    reverse_plans = [
        FileRenamePlan(
            old_path=result.plan.new_path,
            new_path=result.plan.old_path,
            kind=result.plan.kind,
            change=result.plan.change,
        )
        for result in results
        if result.status == "renamed"
    ]
    if not reverse_plans:
        return []
    return apply_file_rename_plan(reverse_plans, dry_run=False)


# Apply対象ファイルのバックアップを作る
def _backup_apply_targets(
    *,
    voice_dir: Path,
    oto_path: Path,
    affected_ust: list[UstSyncPreview],
    extra_files: list[Path],
    options: ApplyOptions,
    timestamp: str,
) -> list[BackupResult]:
    if not options.backup:
        return []

    backups: list[BackupResult] = []
    backup_root = Path(options.backup_root)
    session_path = make_backup_session_path(backup_root, timestamp)

    if options.backup_mode == "voice_dir":
        backups.append(backup_directory_to_session(voice_dir, session_path))
    elif options.backup_mode == "oto_only":
        backups.append(backup_file_to_session(oto_path, session_path))
    else:
        raise ValueError("backup_mode must be 'voice_dir' or 'oto_only'")

    for preview in affected_ust:
        backups.append(backup_file_to_session(preview.ust_path, session_path))

    if options.backup_mode == "oto_only":
        for path in extra_files:
            if path.exists():
                backups.append(backup_file_to_session(path, session_path))

    return backups


# CSV統合後も現在Previewの全行を残す
def _merge_changes_with_current_rows(existing: list[ChangeRow], current: list[ChangeRow]) -> list[ChangeRow]:
    merged = merge_changes(existing, current)
    represented_orders = {change.old_order_id for change in merged if change.old_order_id is not None}
    represented_keys = {(change.old_wav, change.old_alias) for change in merged}
    complete = list(merged)
    for change in current:
        if change.old_order_id is not None and change.old_order_id in represented_orders:
            continue
        if change.old_order_id is None and (change.old_wav, change.old_alias) in represented_keys:
            continue
        complete.append(change)
    return sorted(
        complete,
        key=lambda change: (
            change.old_order_id is None,
            change.old_order_id if change.old_order_id is not None else change.line_number if change.line_number is not None else 0,
        ),
    )


# Apply内容をCSVへ書き出す
def _write_apply_csv(
    changes: list[ChangeRow],
    *,
    voice_dir: Path,
    oto_path: Path,
    timestamp: str,
    options: ApplyOptions,
) -> tuple[Path | None, list[str]]:
    if not options.write_csv:
        return None, []

    warnings: list[str] = []
    csv_path = Path(options.csv_path) if options.csv_path else _default_csv_path(voice_dir, oto_path, timestamp, merge_csv=options.merge_csv)
    if options.csv_path and not options.merge_csv and csv_path.exists():
        warnings.append(f"{csv_path} already exists and will be overwritten")
    if options.merge_csv and csv_path.exists():
        existing = read_changes_csv(csv_path)
        changes = _merge_changes_with_current_rows(existing, changes)
    return write_changes_csv(changes, csv_path, changed_only=False), warnings


# wav編集を無効化した変更一覧を作る
def _without_wav_edits(changes: list[ChangeRow]) -> list[ChangeRow]:
    cleaned_changes: list[ChangeRow] = []
    for change in changes:
        if change.old_wav == change.new_wav:
            cleaned_changes.append(change)
            continue

        warnings = tuple(warning for warning in change.warnings if not warning.startswith("wav "))
        warning_cells = tuple(cell for cell in change.warning_cells if cell != "new_wav")
        severity = "ok" if not warnings else change.severity

        cleaned_changes.append(
            ChangeRow(
            line_number=change.line_number,
            old_order_id=change.old_order_id,
            new_order_id=change.new_order_id,
            old_wav=change.old_wav,
            new_wav=change.old_wav,
            old_alias=change.old_alias,
            new_alias=change.new_alias,
            source_alias=change.source_alias,
            note=change.note,
            frequency=change.frequency,
            status=change.status,
            changed=(change.old_alias != change.new_alias),
            reason="wav_edit_disabled" if change.old_wav != change.new_wav else change.reason,
            severity=severity,
            warnings=warnings,
            warning_cells=warning_cells,
        )
        )
    return cleaned_changes


# alias編集を無効化した変更一覧を作る
def _without_alias_edits(changes: list[ChangeRow]) -> list[ChangeRow]:
    cleaned_changes: list[ChangeRow] = []
    for change in changes:
        if change.old_alias == change.new_alias:
            cleaned_changes.append(change)
            continue

        warnings = tuple(warning for warning in change.warnings if "alias" not in warning.lower())
        warning_cells = tuple(cell for cell in change.warning_cells if cell != "new_alias")
        severity = "ok" if not warnings else change.severity

        cleaned_changes.append(
            ChangeRow(
                line_number=change.line_number,
                old_order_id=change.old_order_id,
                new_order_id=change.new_order_id,
                old_wav=change.old_wav,
                new_wav=change.new_wav,
                old_alias=change.old_alias,
                new_alias=change.old_alias,
                source_alias=change.source_alias,
                note=change.note,
                frequency=change.frequency,
                status=change.status,
                changed=(change.old_wav != change.new_wav),
                reason="alias_edit_disabled",
                severity=severity,
                warnings=warnings,
                warning_cells=warning_cells,
            )
        )
    return cleaned_changes


# danger警告の文面だけを集める
def _danger_messages(changes: list[ChangeRow]) -> list[str]:
    return [
        warning
        for change in changes
        if change.severity == "danger"
        for warning in change.warnings
    ]


@dataclass
# Apply処理中の作業状態を保持する
class _ApplyWork:
    written_files: list[Path] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    backups: list[BackupResult] = field(default_factory=list)
    ust_results: list[UstSyncPreview] = field(default_factory=list)
    file_results: list[FileRenameResult] = field(default_factory=list)
    moved_to_conflict_folder: list[Path] = field(default_factory=list)
    csv_path: Path | None = None


# 設定に従ってwavとaliasの編集可否を反映する
def _apply_edit_policies(changes: list[ChangeRow], options: ApplyOptions) -> tuple[list[ChangeRow], WavEditMode, WavEditMode]:
    wav_edit_mode = normalize_wav_edit_mode(options.wav_edit_mode)
    alias_edit_mode = normalize_wav_edit_mode(options.alias_edit_mode)
    if not options.allow_wav_edit or wav_edit_mode == WavEditMode.DISABLED:
        changes = _without_wav_edits(changes)
    if not options.allow_alias_edit or alias_edit_mode == WavEditMode.DISABLED:
        changes = _without_alias_edits(changes)
    return changes, wav_edit_mode, alias_edit_mode


# danger警告でApplyを止める結果を作る
def _blocked_by_danger_result(oto_path: Path, changes: list[ChangeRow], options: ApplyOptions) -> ApplyResult | None:
    danger_messages = _danger_messages(changes)
    if not options.block_on_danger or not danger_messages:
        return None
    return ApplyResult(
        oto_path=oto_path,
        oto_results=[],
        warnings=danger_messages,
        errors=["apply blocked because danger warnings remain"],
    )


# renameが有効な場合にファイルrename計画を作る
def _build_file_plans(
    voice_dir: Path,
    changes: list[ChangeRow],
    options: ApplyOptions,
    wav_edit_mode: WavEditMode,
) -> list[FileRenamePlan]:
    if options.rename_files and options.allow_wav_edit and wav_edit_mode != WavEditMode.DISABLED:
        return build_file_rename_plan(voice_dir, changes, options.filename_config)
    return []


# desc.mrqの更新対象パスを返す
def _desc_mrq_path(voice_dir: Path) -> Path | None:
    path = voice_dir / "desc.mrq"
    return path if path.is_file() else None


# MRQ内wav名更新用の対応表を作る
def _mrq_wav_name_map(changes: list[ChangeRow]) -> dict[str, str]:
    result: dict[str, str] = {}
    for change in changes:
        if not change.old_wav or not change.new_wav or change.old_wav == change.new_wav:
            continue
        result.setdefault(change.old_wav, change.new_wav)
        result.setdefault(change.old_wav.replace("/", "\\"), change.new_wav.replace("/", "\\"))
        result.setdefault(Path(change.old_wav).name, Path(change.new_wav).name)
    return result


# Apply対象になるUST一覧をPreview結果から絞り込む
def _affected_ust_previews(
    voice_dir: Path,
    changes: list[ChangeRow],
    original_entries: list,
    options: ApplyOptions,
    work: _ApplyWork,
) -> list[UstSyncPreview]:
    if not options.update_ust or not options.ust_root:
        return []
    ust_previews = preview_ust_sync_for_folder(
        options.ust_root,
        voice_dir,
        changes,
        entries_before=original_entries,
        strict_voice_match=options.strict_voice_match,
        utau_exe_path=options.utau_exe_path,
        excluded_moras=options.excluded_call_key_moras,
    )
    selected_ust_paths = {Path(path).resolve() for path in options.selected_ust_paths} if options.selected_ust_paths is not None else None
    if selected_ust_paths is not None:
        ust_previews = [preview for preview in ust_previews if preview.ust_path.resolve() in selected_ust_paths]
    work.skipped.extend(warning for preview in ust_previews for warning in preview.warnings)
    return [preview for preview in ust_previews if preview.replacements > 0]


# バックアップ作成と古いバックアップ整理を行う
def _prepare_backups(
    voice_dir: Path,
    oto_path: Path,
    affected_ust: list[UstSyncPreview],
    extra_files: list[Path],
    options: ApplyOptions,
    timestamp: str,
    work: _ApplyWork,
) -> ApplyResult | None:
    try:
        work.backups = _backup_apply_targets(
            voice_dir=voice_dir,
            oto_path=oto_path,
            affected_ust=affected_ust,
            extra_files=extra_files,
            options=options,
            timestamp=timestamp,
        )
    except Exception as exc:
        return ApplyResult(
            oto_path=oto_path,
            oto_results=[],
            warnings=work.warnings,
            skipped=work.skipped,
            errors=[f"backup failed: {exc}"],
        )
    if options.backup and options.backup_max_count_enabled:
        try:
            deleted_backups = prune_backup_directories(options.backup_root, max(1, min(int(options.backup_max_count), 999999)))
            work.warnings.extend(f"deleted old backup: {path}" for path in deleted_backups)
        except OSError as exc:
            work.warnings.append(f"backup cleanup failed: {exc}")
    return None


# oto.ini一時ファイルを書き出す
def _write_oto_temp_file(
    oto_path: Path,
    changes: list[ChangeRow],
    work: _ApplyWork,
) -> tuple[Path | None, list[CsvApplyResult], ApplyResult | None]:
    _cleanup_stale_oto_temp_files(oto_path)
    temp_oto_path = _temporary_oto_path(oto_path)
    try:
        _written_oto, oto_results = apply_changes_to_oto_file(oto_path, changes, temp_oto_path)
    except Exception as exc:
        _cleanup_path(temp_oto_path)
        return None, [], ApplyResult(
            oto_path=oto_path,
            oto_results=[],
            backups=work.backups,
            warnings=work.warnings,
            skipped=work.skipped,
            errors=[f"{oto_path}: {exc}"],
        )
    for result in oto_results:
        if result.status not in {"matched", "skipped"}:
            work.skipped.append(f"oto.ini line: {result.status}: {result.message}")
    return temp_oto_path, oto_results, None


# ファイルを反映する
def _apply_file_renames(file_plans: list[FileRenamePlan], work: _ApplyWork) -> list[Path]:
    renamed_files: list[Path] = []
    if not file_plans:
        return renamed_files
    try:
        work.file_results = apply_file_rename_plan(file_plans, dry_run=False)
    except Exception as exc:
        work.warnings.append(f"file rename failed unexpectedly: {exc}")
        work.file_results = []
    for result in work.file_results:
        if result.status == "renamed":
            renamed_files.append(result.plan.new_path)
        elif result.status == "moved_to_conflict_folder" and result.message:
            work.moved_to_conflict_folder.append(Path(result.message))
        elif result.status == "missing" and result.plan.kind == "related":
            continue
        elif result.status not in {"dry_run", "destination_conflict"}:
            work.skipped.append(f"{result.plan.old_path}: {result.status}: {result.message}")
    return renamed_files


# 本体oto.iniを置換する
def _replace_live_oto(
    oto_path: Path,
    temp_oto_path: Path,
    oto_results: list[CsvApplyResult],
    work: _ApplyWork,
) -> ApplyResult | None:
    try:
        temp_oto_path.replace(oto_path)
    except OSError as exc:
        _cleanup_path(temp_oto_path)
        return ApplyResult(
            oto_path=oto_path,
            oto_results=oto_results,
            backups=work.backups,
            file_results=work.file_results,
            warnings=work.warnings,
            skipped=work.skipped,
            errors=[f"{oto_path}: {exc}"],
        )
    work.written_files.append(oto_path)
    return None


# 音源全体型周波数表を反映する
def _apply_global_frequency_tables(voice_dir: Path, changes: list[ChangeRow], work: _ApplyWork) -> None:
    desc_mrq = _desc_mrq_path(voice_dir)
    if desc_mrq is None:
        return
    wav_name_map = _mrq_wav_name_map(changes)
    if not wav_name_map:
        return
    try:
        result = rewrite_mrq_wav_names(desc_mrq, wav_name_map)
    except Exception as exc:
        work.warnings.append(f"{desc_mrq}: MRQ update failed: {exc}")
        return
    if result.rewritten:
        work.written_files.append(result.path)


# USTを反映する
def _apply_ust_previews(
    affected_ust: list[UstSyncPreview],
    changes: list[ChangeRow],
    original_entries: list,
    options: ApplyOptions,
    work: _ApplyWork,
) -> None:
    for preview in affected_ust:
        try:
            result = apply_ust_sync_for_file(
                preview.ust_path,
                changes,
                overwrite=True,
                entries_before=original_entries,
                excluded_moras=options.excluded_call_key_moras,
            )
        except OSError as exc:
            work.errors.append(f"{preview.ust_path}: {exc}")
            continue
        work.ust_results.append(result)
        work.written_files.append(result.output_path)
        work.skipped.extend(result.warnings)


# UTAU一時を反映する
def _apply_utau_plugin_temp(
    voice_dir: Path,
    changes: list[ChangeRow],
    original_entries: list,
    options: ApplyOptions,
    work: _ApplyWork,
) -> None:
    if not options.utau_plugin_temp_path:
        return
    plugin_context = load_utau_plugin_context(options.utau_plugin_temp_path)
    if plugin_context is None:
        work.warnings.append(f"UTAU plugin temp file was not readable: {options.utau_plugin_temp_path}")
    elif plugin_context.note_count <= 0:
        work.warnings.append(f"UTAU plugin temp file has no selected notes: {plugin_context.temp_path}")
    elif same_voice_dir(plugin_context.voice_dir, voice_dir):
        try:
            result = apply_ust_sync_for_file(
                plugin_context.temp_path,
                changes,
                overwrite=True,
                entries_before=original_entries,
                excluded_moras=options.excluded_call_key_moras,
            )
        except OSError as exc:
            work.errors.append(f"{plugin_context.temp_path}: {exc}")
        else:
            if result.replacements > 0:
                work.written_files.append(result.output_path)
            work.skipped.extend(result.warnings)
    else:
        work.warnings.append(
            f"UTAU plugin temp file skipped because VoiceDir differs: {plugin_context.voice_dir}"
        )


# CSV結果を追加する
def _append_csv_result(
    voice_dir: Path,
    oto_path: Path,
    changes: list[ChangeRow],
    timestamp: str,
    options: ApplyOptions,
    work: _ApplyWork,
) -> None:
    try:
        csv_path, csv_warnings = _write_apply_csv(
            changes,
            voice_dir=voice_dir,
            oto_path=oto_path,
            timestamp=timestamp,
            options=options,
        )
        work.csv_path = csv_path
        work.warnings.extend(csv_warnings)
        if csv_path:
            work.written_files.append(csv_path)
    except Exception as exc:
        work.errors.append(f"CSV output failed: {exc}")


# Preview結果を実ファイルへ反映する
def apply_changes_direct(
    voice_dir: str | Path,
    oto_path: str | Path,
    changes: list[ChangeRow],
    options: ApplyOptions | None = None,
) -> ApplyResult:
    """Apply a preview result to the live voice folder.

    The default safety model backs up the whole voice folder plus affected USTs
    before writing. `backup_mode="oto_only"` is available for the future
    settings dialog.
    """
    options = options or ApplyOptions()
    voice_dir = Path(voice_dir)
    oto_path = Path(oto_path)
    original_oto_lines, _ = parse_oto_file(oto_path)
    original_entries = iter_entries(original_oto_lines)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_timestamp = f"{voice_dir.name}_{timestamp}"
    work = _ApplyWork()
    changes, wav_edit_mode, _alias_edit_mode = _apply_edit_policies(list(changes), options)

    blocked = _blocked_by_danger_result(oto_path, changes, options)
    if blocked is not None:
        return blocked

    file_plans = _build_file_plans(voice_dir, changes, options, wav_edit_mode)
    affected_ust = _affected_ust_previews(voice_dir, changes, original_entries, options, work)
    extra_backup_files = [path for path in (_desc_mrq_path(voice_dir),) if path is not None and _mrq_wav_name_map(changes)]

    backup_error = _prepare_backups(voice_dir, oto_path, affected_ust, extra_backup_files, options, backup_timestamp, work)
    if backup_error is not None:
        return backup_error

    temp_oto_path, oto_results, oto_error = _write_oto_temp_file(oto_path, changes, work)
    if oto_error is not None:
        return oto_error
    assert temp_oto_path is not None

    renamed_files = _apply_file_renames(file_plans, work)
    replace_error = _replace_live_oto(oto_path, temp_oto_path, oto_results, work)
    if replace_error is not None:
        return replace_error
    work.written_files.extend(renamed_files)

    _apply_global_frequency_tables(voice_dir, changes, work)
    _apply_ust_previews(affected_ust, changes, original_entries, options, work)
    _apply_utau_plugin_temp(voice_dir, changes, original_entries, options, work)
    _append_csv_result(voice_dir, oto_path, changes, timestamp, options, work)

    return ApplyResult(
        oto_path=oto_path,
        oto_results=oto_results,
        backups=work.backups,
        written_files=work.written_files,
        csv_path=work.csv_path,
        ust_results=work.ust_results,
        file_results=work.file_results,
        moved_to_conflict_folder=work.moved_to_conflict_folder,
        warnings=work.warnings,
        skipped=work.skipped,
        errors=work.errors,
    )
