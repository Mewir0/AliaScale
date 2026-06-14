"""Detailed logging for apply operations."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from collections import Counter
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .apply import ApplyResult


# USTLyric音符を読み込む
def _read_ust_lyric_at_note_zero(ust_path: Path) -> str | None:
    """Read Lyric= value from [#0000] section in UST file."""
    try:
        with open(ust_path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return None
    
    lines = text.split("\n")
    in_note_zero = False
    for line in lines:
        if line.strip() == "[#0000]":
            in_note_zero = True
            continue
        if in_note_zero:
            if line.startswith("[#") or line.startswith("["):
                break
            if line.startswith("Lyric="):
                return line[len("Lyric="):].strip()
    return None


# バックアップUSTを探す
def _find_backup_ust(ust_path: Path, backup_results: list) -> Path | None:
    """Find corresponding UST backup file from backup results."""
    ust_name = ust_path.name
    for backup in backup_results:
        if backup.source_path == ust_path or backup.source_path.name == ust_name:
            backup_path = backup.backup_path / ust_name
            if backup_path.exists():
                return backup_path
    return None


# Applyログ本文を作る
def generate_apply_log(result: ApplyResult, voice_dir: Path | None = None) -> str:
    """Generate detailed apply operation log with UST lyric changes.
    
    Args:
        result: ApplyResult from apply_changes_direct()
        voice_dir: Optional voice directory path for context
    
    Returns:
        Formatted log string
    """
    lines: list[str] = []
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    lines.append("=" * 80)
    lines.append(f"AliaScale Apply Log - {timestamp}")
    lines.append("=" * 80)
    lines.append("")
    
    # 概要を追加する
    lines.append("SUMMARY")
    lines.append("-" * 80)
    lines.append(f"Output oto.ini: {result.oto_path}")
    if voice_dir:
        lines.append(f"Voice directory: {voice_dir}")
    lines.append(f"Total rows processed: {sum(r.line_number is not None for r in result.oto_results)}")
    lines.append(f"Successful oto changes: {len([r for r in result.oto_results if r.status == 'matched'])}")
    lines.append(f"Written files: {len(result.written_files)}")
    lines.append(f"Backups created: {len(result.backups)}")
    lines.append(f"UST files updated: {len(result.ust_results)}")
    lines.append(f"File renames: {len([r for r in result.file_results if r.status == 'renamed'])}")
    lines.append(f"Warnings: {len(result.warnings)}")
    lines.append(f"Skipped: {len(result.skipped)}")
    lines.append(f"Errors: {len(result.errors)}")
    lines.append("")

    status_counts = Counter(result_item.change.status or result_item.status for result_item in result.oto_results)
    severity_counts = Counter(result_item.change.severity for result_item in result.oto_results)
    diagnostic_counts = Counter(
        diagnostic
        for result_item in result.oto_results
        for diagnostic in result_item.change.diagnostics
    )
    if status_counts or severity_counts or diagnostic_counts:
        lines.append("PREVIEW / APPLY COUNTS")
        lines.append("-" * 80)
        if status_counts:
            lines.append(f"  Status: {dict(sorted(status_counts.items()))}")
        if severity_counts:
            lines.append(f"  Severity: {dict(sorted(severity_counts.items()))}")
        if diagnostic_counts:
            lines.append(f"  Diagnostics: {dict(sorted(diagnostic_counts.items()))}")
        lines.append("")

    important_rows = [
        result_item.change
        for result_item in result.oto_results
        if result_item.change.severity in {"warning", "danger"} or result_item.change.diagnostics
    ]
    if important_rows:
        lines.append("WARNING / DANGER ROWS")
        lines.append("-" * 80)
        for change in important_rows:
            lines.append(
                "  "
                f"line={change.line_number} severity={change.severity} "
                f"status={change.status or change.origin_status or '-'} "
                f"diagnostics={','.join(change.diagnostics) or '-'} "
                f"wav={change.old_wav}->{change.new_wav} "
                f"alias={change.old_alias}->{change.new_alias}"
            )
        lines.append("")
    
    # バックアップ一覧を追加する
    if result.backups:
        lines.append("BACKUPS")
        lines.append("-" * 80)
        for backup in result.backups:
            lines.append(f"  Source: {backup.source_path}")
            lines.append(f"  Backup: {backup.backup_path}")
        lines.append("")
    
    # 更新ファイル一覧を追加する
    if result.written_files:
        lines.append("WRITTEN FILES")
        lines.append("-" * 80)
        for fpath in result.written_files:
            lines.append(f"  {fpath}")
        lines.append("")
    
    # USTのLyric変更詳細を追加する
    if result.ust_results:
        lines.append("UST FILE CHANGES")
        lines.append("-" * 80)
        for ust_result in result.ust_results:
            lines.append(f"File: {ust_result.ust_path}")
            lines.append(f"  Output: {ust_result.output_path}")
            lines.append(f"  Replacements: {ust_result.replacements}")
            
            # 変更前後の先頭Lyricを読み込む
            lyric_before = None
            backup_ust = _find_backup_ust(ust_result.ust_path, result.backups)
            if backup_ust and backup_ust.exists():
                lyric_before = _read_ust_lyric_at_note_zero(backup_ust)
            
            lyric_after = None
            if ust_result.output_path.exists():
                lyric_after = _read_ust_lyric_at_note_zero(ust_result.output_path)
            
            if lyric_before or lyric_after:
                lines.append(f"  [#0000] Lyric Before:  {lyric_before or '(not found)'}")
                lines.append(f"  [#0000] Lyric After:   {lyric_after or '(not found)'}")
            
            if ust_result.warnings:
                for warning in ust_result.warnings:
                    lines.append(f"  Warning: {warning}")
        lines.append("")
    
    # CSV出力結果を追加する
    if result.csv_path:
        lines.append("CSV OUTPUT")
        lines.append("-" * 80)
        lines.append(f"  {result.csv_path}")
        lines.append("")
    
    # ファイルrename結果を追加する
    if result.file_results:
        renamed_count = len([r for r in result.file_results if r.status == "renamed"])
        if renamed_count > 0:
            lines.append("FILE RENAMES")
            lines.append("-" * 80)
            for fres in result.file_results:
                if fres.status == "renamed":
                    lines.append(f"  {fres.plan.old_path} -> {fres.plan.new_path}")
            lines.append("")
        failed_file_results = [r for r in result.file_results if r.status != "renamed"]
        if failed_file_results:
            lines.append("FILE RENAME WARNINGS")
            lines.append("-" * 80)
            for fres in failed_file_results:
                lines.append(f"  [{fres.status}] {fres.plan.old_path} -> {fres.plan.new_path} {fres.message}")
            lines.append("")
    
    # 警告一覧を追加する
    if result.warnings:
        lines.append("WARNINGS")
        lines.append("-" * 80)
        for warning in result.warnings:
            lines.append(f"  {warning}")
        lines.append("")
    
    # スキップ一覧を追加する
    if result.skipped:
        lines.append("SKIPPED ITEMS")
        lines.append("-" * 80)
        for item in result.skipped:
            lines.append(f"  {item}")
        lines.append("")
    
    # エラー一覧を追加する
    if result.errors:
        lines.append("ERRORS")
        lines.append("-" * 80)
        for error in result.errors:
            lines.append(f"  {error}")
        lines.append("")
    
    lines.append("=" * 80)
    return "\n".join(lines)


# Applyログを書き出す
def save_apply_log(result: ApplyResult, log_path: str | Path, voice_dir: Path | None = None) -> Path:
    """Generate and save apply operation log to file.
    
    Args:
        result: ApplyResult from apply_changes_direct()
        log_path: Destination file path for the log
        voice_dir: Optional voice directory path for context
    
    Returns:
        Path to the saved log file
    """
    log_path = Path(log_path)
    log_content = generate_apply_log(result, voice_dir)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(log_content)
    return log_path
