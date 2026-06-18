from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable

import csv

from .oto import OtoLine, parse_oto_file, write_oto_copy


CSV_FIELDS = (
    "line_number",
    "old_order_id",
    "new_order_id",
    "old_wav",
    "new_wav",
    "old_alias",
    "new_alias",
    "source_alias",
    "note",
    "frequency",
    "status",
    "changed",
    "reason",
)

REQUIRED_CSV_FIELDS = {"old_wav", "old_alias"}


@dataclass(frozen=True)
# 変更行情報を保持する
class ChangeRow:
    line_number: int | None
    old_wav: str
    new_wav: str
    old_alias: str
    new_alias: str
    old_order_id: int | None = None
    new_order_id: int | None = None
    source_alias: str = ""
    note: str = ""
    frequency: float | None = None
    status: str = ""
    origin_status: str = ""
    diagnostics: tuple[str, ...] = ()
    changed: bool = True
    reason: str = ""
    severity: str = "ok"
    warnings: tuple[str, ...] = ()
    warning_cells: tuple[str, ...] = ()
    auto_edit_fields: tuple[str, ...] = ()
    manual_edit_fields: tuple[str, ...] = ()
    usage_count: int = 0


@dataclass(frozen=True)
# CSVApply結果を保持する
class CsvApplyResult:
    change: ChangeRow
    status: str
    line_number: int | None = None
    message: str = ""


# 変更一覧Preview行情報一覧を処理する
def changes_from_preview_rows(rows: Iterable[object]) -> list[ChangeRow]:
    changes: list[ChangeRow] = []
    for row in rows:
        changes.append(
            ChangeRow(
                line_number=getattr(row, "line_number"),
                old_order_id=getattr(row, "line_number"),
                new_order_id=getattr(row, "line_number"),
                old_wav=getattr(row, "wav_name"),
                new_wav=getattr(row, "wav_name"),
                old_alias=getattr(row, "old_alias"),
                new_alias=getattr(row, "new_alias"),
                source_alias=getattr(row, "source_alias"),
                note=getattr(row, "note") or "",
                frequency=getattr(row, "frequency"),
                status=getattr(row, "pitch_status"),
                changed=bool(getattr(row, "changed")),
                reason=getattr(row, "rewrite_reason"),
            )
        )
    return changes


# CSVを処理する
def _bool_to_csv(value: bool) -> str:
    return "true" if value else "false"


# CSVを処理する
def _bool_from_csv(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


# 値を処理する
def _optional_int(value: str) -> int | None:
    value = value.strip()
    return int(value) if value else None


# 値を処理する
def _optional_float(value: str) -> float | None:
    value = value.strip()
    return float(value) if value else None


# 変更一覧をCSVへ書き出す
def write_changes_csv(
    changes: Iterable[ChangeRow],
    csv_path: str | Path,
    *,
    changed_only: bool = False,
    encoding: str = "utf-8-sig",
) -> Path:
    csv_path = Path(csv_path)
    rows = [change for change in changes if change.changed or not changed_only]
    with csv_path.open("w", newline="", encoding=encoding) as fp:
        writer = csv.DictWriter(fp, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for change in rows:
            writer.writerow(
                {
                    "line_number": "" if change.line_number is None else change.line_number,
                    "old_order_id": "" if change.old_order_id is None else change.old_order_id,
                    "new_order_id": "" if change.new_order_id is None else change.new_order_id,
                    "old_wav": change.old_wav,
                    "new_wav": change.new_wav,
                    "old_alias": change.old_alias,
                    "new_alias": change.new_alias,
                    "source_alias": change.source_alias,
                    "note": change.note,
                    "frequency": "" if change.frequency is None else change.frequency,
                    "status": change.status,
                    "changed": _bool_to_csv(change.changed),
                    "reason": change.reason,
                }
            )
    return csv_path


# 変更CSVを読み込む
def read_changes_csv(csv_path: str | Path, *, encoding: str = "utf-8-sig") -> list[ChangeRow]:
    csv_path = Path(csv_path)
    with csv_path.open("r", newline="", encoding=encoding) as fp:
        reader = csv.DictReader(fp)
        missing = REQUIRED_CSV_FIELDS - set(reader.fieldnames or ())
        if missing:
            raise ValueError(f"Missing required CSV columns: {sorted(missing)}")
        changes = []
        for row in reader:
            old_wav = row.get("old_wav", "")
            new_wav = row.get("new_wav", "") or old_wav
            old_alias = row.get("old_alias", "")
            new_alias = row.get("new_alias", "") or old_alias
            changes.append(
                ChangeRow(
                    line_number=_optional_int(row.get("line_number", "")),
                    old_order_id=_optional_int(row.get("old_order_id", "")),
                    new_order_id=_optional_int(row.get("new_order_id", "")),
                    old_wav=old_wav,
                    new_wav=new_wav,
                    old_alias=old_alias,
                    new_alias=new_alias,
                    source_alias=row.get("source_alias", ""),
                    note=row.get("note", ""),
                    frequency=_optional_float(row.get("frequency", "")),
                    status=row.get("status", ""),
                    changed=_bool_from_csv(row.get("changed", "true"))
                    if "changed" in row
                    else (
                        old_wav != new_wav
                        or old_alias != new_alias
                    ),
                    reason=row.get("reason", ""),
                )
            )
    return changes


# 変更一覧を反転する
def invert_changes(changes: Iterable[ChangeRow]) -> list[ChangeRow]:
    return [
        replace(
            change,
            old_wav=change.new_wav,
            new_wav=change.old_wav,
            old_alias=change.new_alias,
            new_alias=change.old_alias,
            old_order_id=change.new_order_id,
            new_order_id=change.old_order_id,
            changed=(
                change.old_wav != change.new_wav
                or change.old_alias != change.new_alias
            ),
            reason="inverted",
        )
        for change in changes
    ]


# 状態キーを処理する
def _state_key(wav: str, alias: str) -> tuple[str, str]:
    return wav, alias


# 値を判定する
def _is_noop(change: ChangeRow) -> bool:
    return (
        change.old_wav == change.new_wav
        and change.old_alias == change.new_alias
        and change.old_order_id == change.new_order_id
    )


# 変更一覧を統合する
def merge_changes(existing: Iterable[ChangeRow], new: Iterable[ChangeRow]) -> list[ChangeRow]:
    """Compress sequential changes into initial-state -> latest-state rows."""
    merged: list[ChangeRow] = [change for change in existing if not _is_noop(change)]

    for incoming in new:
        if _is_noop(incoming):
            continue
        incoming_old = _state_key(incoming.old_wav, incoming.old_alias)
        chained_index = next(
            (
                index
                for index, change in enumerate(merged)
                if _state_key(change.new_wav, change.new_alias) == incoming_old
            ),
            None,
        )
        if chained_index is not None:
            original = merged[chained_index]
            combined = replace(
                original,
                new_wav=incoming.new_wav,
                new_alias=incoming.new_alias,
                new_order_id=incoming.new_order_id
                if incoming.new_order_id is not None
                else original.new_order_id,
                note=incoming.note or original.note,
                frequency=incoming.frequency if incoming.frequency is not None else original.frequency,
                status=incoming.status or original.status,
                changed=True,
                reason="merged",
            )
            if _is_noop(combined):
                del merged[chained_index]
            else:
                merged[chained_index] = combined
            continue

        same_original_index = next(
            (
                index
                for index, change in enumerate(merged)
                if _state_key(change.old_wav, change.old_alias) == incoming_old
            ),
            None,
        )
        if same_original_index is not None:
            original = merged[same_original_index]
            overridden = replace(
                original,
                new_wav=incoming.new_wav,
                new_alias=incoming.new_alias,
                new_order_id=incoming.new_order_id
                if incoming.new_order_id is not None
                else original.new_order_id,
                note=incoming.note or original.note,
                frequency=incoming.frequency if incoming.frequency is not None else original.frequency,
                status=incoming.status or original.status,
                changed=True,
                reason="merged_override",
            )
            if _is_noop(overridden):
                del merged[same_original_index]
            else:
                merged[same_original_index] = overridden
            continue

        merged.append(incoming)

    return merged


# 対象行を探す
def _find_target_line(lines: list[OtoLine], change: ChangeRow, *, prefer_line_number: bool = True) -> CsvApplyResult:
    if prefer_line_number and change.line_number is not None:
        for line in lines:
            if line.entry and line.entry.line_number == change.line_number:
                if line.entry.wav_name == change.old_wav and line.entry.alias == change.old_alias:
                    return CsvApplyResult(change, "matched", line.entry.line_number)
                return CsvApplyResult(
                    change,
                    "mismatch",
                    line.entry.line_number,
                    "line_number exists, but old_wav/old_alias do not match current oto.ini",
                )
        return CsvApplyResult(change, "not_found", None, "line_number was not found")

    matches = [
        line.entry.line_number
        for line in lines
        if line.entry and line.entry.wav_name == change.old_wav and line.entry.alias == change.old_alias
    ]
    if len(matches) == 1:
        return CsvApplyResult(change, "matched", matches[0])
    if not matches:
        return CsvApplyResult(change, "not_found", None, "old_wav/old_alias pair was not found")
    return CsvApplyResult(change, "ambiguous", None, "old_wav/old_alias pair matched multiple rows")


# oto.ini対応表変更一覧を作る
def build_oto_update_maps_from_changes(
    lines: list[OtoLine],
    changes: Iterable[ChangeRow],
    *,
    prefer_line_number: bool = True,
    skip_noop: bool = True,
) -> tuple[dict[int, str], dict[int, str], list[CsvApplyResult]]:
    alias_by_line: dict[int, str] = {}
    wav_by_line: dict[int, str] = {}
    results: list[CsvApplyResult] = []

    for change in changes:
        if skip_noop and not change.changed and _is_noop(change):
            results.append(CsvApplyResult(change, "skipped", None, "change row is marked unchanged"))
            continue
        result = _find_target_line(lines, change, prefer_line_number=prefer_line_number)
        results.append(result)
        if result.status != "matched" or result.line_number is None:
            continue
        if change.old_alias != change.new_alias:
            alias_by_line[result.line_number] = change.new_alias
        if change.old_wav != change.new_wav:
            wav_by_line[result.line_number] = change.new_wav

    return alias_by_line, wav_by_line, results


# 変更一覧をoto.iniへ反映する
def apply_changes_to_oto_file(
    oto_path: str | Path,
    changes: Iterable[ChangeRow],
    output_path: str | Path,
    *,
    encoding: str | None = None,
    prefer_line_number: bool = True,
) -> tuple[Path, list[CsvApplyResult]]:
    lines, detected_encoding = parse_oto_file(oto_path, encoding=encoding)
    changes = list(changes)
    alias_by_line, wav_by_line, results = build_oto_update_maps_from_changes(
        lines,
        changes,
        prefer_line_number=prefer_line_number,
    )
    order_by_line = {
        result.line_number: result.change.new_order_id
        for result in results
        if result.status == "matched"
        and result.line_number is not None
        and result.change.new_order_id is not None
        and result.change.old_order_id != result.change.new_order_id
    }
    written = write_oto_copy(
        lines,
        alias_by_line,
        output_path,
        encoding=detected_encoding,
        wav_by_line_number=wav_by_line,
        order_by_line_number=order_by_line,
    )
    return written, results


# 変更CSVをoto.iniへ反映する
def apply_changes_csv_to_oto_file(
    oto_path: str | Path,
    csv_path: str | Path,
    output_path: str | Path,
    *,
    csv_encoding: str = "utf-8-sig",
    oto_encoding: str | None = None,
) -> tuple[Path, list[CsvApplyResult]]:
    changes = read_changes_csv(csv_path, encoding=csv_encoding)
    return apply_changes_to_oto_file(oto_path, changes, output_path, encoding=oto_encoding, prefer_line_number=False)
