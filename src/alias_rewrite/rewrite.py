from __future__ import annotations

from dataclasses import dataclass, field, replace
from functools import cmp_to_key
from pathlib import Path
import math
import re

from .aliases import AliasRewriteConfig, build_alias_change, normalize_alias_body, source_alias_for_entry, split_alias
from .changes import (
    ChangeRow,
    CsvApplyResult,
    apply_changes_csv_to_oto_file,
    build_oto_update_maps_from_changes,
    changes_from_preview_rows,
    invert_changes,
    merge_changes,
    read_changes_csv,
    write_changes_csv,
)
from .keys import (
    KeyWarningConfig,
    apply_changes_to_entries,
    detect_call_key_collisions,
    detect_resolution_warnings,
    pronunciation_mora,
    resolve_call_key,
    warning_summary_by_line,
    wav_key,
)
from .mrq import parse_mrq
from .notes import NoteMappingConfig
from .options import (
    SortDirection,
    WavEditMode,
    normalize_sort_direction,
    normalize_wav_edit_mode,
    wav_auto_edit_enabled,
    wav_representative_edit_enabled,
)
from .oto import OtoEntry, iter_entries, parse_oto_file, write_oto_copy
from .pitch import FREQUENCY_ERROR_KEY, build_frequency_index, build_mrq_index, estimate_pitch_for_entry, load_frequency_records
from .sort_order import SortTextOrder, load_otolist_order
from .warnings import warning_rank
from .wav_names import hidden_wav_name


@dataclass(frozen=True)
# Preview行情報を保持する
class RewritePreviewRow:
    line_number: int
    wav_name: str
    old_alias: str
    source_alias: str
    new_alias: str
    frequency: float | None
    note: str | None
    valid_frame_count: int
    pitch_status: str
    rewrite_reason: str
    changed: bool


@dataclass(frozen=True)
# ルールを保持する
class ReplacementRule:
    old: str
    new: str
    target: str = "alias"  # alias / wav / all / call_key
    use_regex: bool = False


@dataclass(frozen=True)
# 除外設定を保持する
class ExcludeConfig:
    exclude_unvoiced: bool = False
    exclude_no_f0: bool = False
    exclude_no_freq_src: bool = False
    exclude_empty_params: bool = True
    mode: str = "none"  # none / string_list / regex / mora
    patterns: tuple[str, ...] = ()


@dataclass(frozen=True)
# 並べ替えキー順序を保持する
class SortKeyOrder:
    key: str
    direction: str = SortDirection.ASC.value


@dataclass(frozen=True)
# Preview設定を保持する
class PreviewOptions:
    mode: str = "pitch_append"  # pitch_append / replace / numbering / csv / none / rule_based
    alias_config: AliasRewriteConfig = field(default_factory=AliasRewriteConfig)
    note_config: NoteMappingConfig = field(default_factory=NoteMappingConfig)
    frequency_source: str = "mrq"  # mrq / frq / pmk / auto_f0
    replacement_rules: tuple[ReplacementRule, ...] = ()
    csv_invert: bool = False
    csv_read_columns: tuple[str, ...] = ("new_wav", "new_alias", "new_order_id")
    rule_scope: str = "alias_wav"  # alias_wav / call_key
    rule_alias_template: str = ""
    rule_wav_template: str = ""
    rule_call_key_template: str = ""
    exclude_config: ExcludeConfig = field(default_factory=ExcludeConfig)
    edit_scope: str = "call_key"  # call_key / alias_wav
    alias_target: str = "call_key"  # legacy compatibility
    add_alias_for_unused_wav: bool = False
    edit_mismatched_wav_mora: bool = False
    key_warning_config: KeyWarningConfig = field(default_factory=KeyWarningConfig)
    number_first_alias: bool = False
    sort_keys: tuple[str, ...] = ()  # filename / alias / pitch
    sort_descending: bool = False
    sort_orders: tuple[SortKeyOrder, ...] = ()
    sort_order_path: str | Path | None = None
    allow_wav_edit: bool = True
    wav_edit_mode: str = WavEditMode.ALLOW.value
    allow_alias_edit: bool = True
    alias_edit_mode: str = WavEditMode.ALLOW.value
    prefix_underscore_for_new_alias: bool = False
    auto_wav_excluded_moras: tuple[str, ...] = ("_",)
    numbering_order_mode: str = "separate"  # separate / alias_wav / wav_alias
    renumber_after_order_change: bool = True
    usage_count_by_line: dict[int, int] = field(default_factory=dict)
    usage_counts_available: bool = False
    relax_cannotcall_for_unused_ust_entries: bool = False


# 値を生成する
def preview_rewrite(
    oto_path: str | Path,
    mrq_path: str | Path,
    alias_config: AliasRewriteConfig | None = None,
    note_config: NoteMappingConfig | None = None,
) -> tuple[list[RewritePreviewRow], str]:
    alias_config = alias_config or AliasRewriteConfig()
    lines, encoding = parse_oto_file(oto_path)
    records = parse_mrq(mrq_path)
    mrq_index = build_mrq_index(records)

    pending_changes = []
    pitch_results = {}
    for entry in iter_entries(lines):
        pitch = estimate_pitch_for_entry(entry, mrq_index, note_config=note_config)
        pitch_results[entry.line_number] = pitch
        pending_changes.append(build_alias_change(entry, pitch.get("note"), alias_config))

    changes = pending_changes
    rows: list[RewritePreviewRow] = []
    for change in changes:
        pitch = pitch_results[change.line_number]
        rows.append(
            RewritePreviewRow(
                line_number=change.line_number,
                wav_name=change.wav_name,
                old_alias=change.old_alias,
                source_alias=change.source_alias,
                new_alias=change.new_alias,
                frequency=pitch.get("frequency"),
                note=pitch.get("note"),
                valid_frame_count=int(pitch.get("valid_frame_count") or 0),
                pitch_status=str(pitch.get("status")),
                rewrite_reason=change.reason,
                changed=change.changed,
            )
        )
    return rows, encoding


# 行順序を処理する
def _entry_order_id(entry: OtoEntry) -> int:
    return entry.line_number


# MRQパスを返す
def _default_mrq_path(oto_path: str | Path, explicit_path: str | Path | None) -> Path | None:
    if explicit_path:
        path = Path(explicit_path)
        if path.exists():
            return path
    voice_dir = Path(oto_path).parent
    desc = voice_dir / "desc.mrq"
    if desc.exists():
        return desc
    mrqs = sorted(voice_dir.glob("*.mrq"))
    return mrqs[0] if mrqs else None


# 音高索引を読み込む
def _load_pitch_index(
    oto_path: str | Path,
    entries: list[OtoEntry],
    table_path: str | Path | None,
    options: PreviewOptions,
):
    source = options.frequency_source or "mrq"
    resolved_path = _default_mrq_path(oto_path, table_path) if source in {"mrq", "moresampler_mrq"} else table_path
    if source in {"mrq", "moresampler_mrq"} and resolved_path is None:
        return {FREQUENCY_ERROR_KEY: "no_freq_src"}, "moresampler MRQ"
    try:
        records, label = load_frequency_records(
            source=source,
            voice_dir=Path(oto_path).parent,
            entries=entries,
            table_path=resolved_path,
        )
    except Exception:
        return {FREQUENCY_ERROR_KEY: "invalid_freq"}, source
    return build_frequency_index(records), label


# 変更を処理する
def _noop_change(entry: OtoEntry, reason: str) -> ChangeRow:
    order_id = _entry_order_id(entry)
    status = "empty" if reason == "empty_params" else ""
    return ChangeRow(
        line_number=entry.line_number,
        old_wav=entry.wav_name,
        new_wav=entry.wav_name,
        old_alias=entry.alias,
        new_alias=entry.alias,
        old_order_id=order_id,
        new_order_id=order_id,
        source_alias=entry.alias,
        status=status,
        origin_status=status,
        changed=False,
        reason=reason,
    )


# 除外対象変更を処理する
def _excluded_change(entry: OtoEntry, *, note: str = "", frequency: float | None = None) -> ChangeRow:
    return replace(_noop_change(entry, "excluded"), status="exclude", origin_status="exclude", note=note, frequency=frequency)


# 周波数なし除外行を保持する
def _no_frequency_change(entry: OtoEntry, status: str, *, note: str = "", frequency: float | None = None) -> ChangeRow:
    normalized = _normalized_status(status)
    return replace(_noop_change(entry, "excluded_unvoiced"), status=normalized, origin_status=normalized, note=note, frequency=frequency)


# 行を処理する
def _single_line(value: str) -> str:
    return " ".join(str(value or "").splitlines())


# 行本文を処理する
def _entry_text_for_exclusion(entry: OtoEntry) -> str:
    return "\n".join((entry.wav_name, entry.alias, Path(entry.wav_name).stem))


# 行を処理する
def _entry_params_all_zero_or_blank(entry: OtoEntry) -> bool:
    values = (entry.offset, entry.consonant, entry.cutoff, entry.preutterance, entry.overlap)
    return all(math.isnan(value) or value == 0 for value in values)


# 発音集合を処理する
def _mora_set(values: tuple[str, ...]) -> set[str]:
    return {value.strip() for value in values if value.strip()}


# 発音の有効性を判定する
def _mora_is_allowed(mora: str, excluded_moras: tuple[str, ...]) -> bool:
    return bool(mora) and mora not in _mora_set(excluded_moras)


# 通し番号順序設定を正規化する
def _numbering_order_mode(options: PreviewOptions) -> str:
    if options.numbering_order_mode in {"separate", "alias_wav", "wav_alias"}:
        return options.numbering_order_mode
    return "separate"


# 通し番号のフィールド優先順位を返す
def _numbering_field_priority(options: PreviewOptions) -> dict[str, int]:
    return {"alias": 0, "wav": 1} if _numbering_order_mode(options) == "alias_wav" else {"wav": 0, "alias": 1}


# 編集スコープを正規化する
def _edit_scope(options: PreviewOptions) -> str:
    if options.edit_scope in {"call_key", "alias_wav"}:
        return options.edit_scope
    if options.alias_target == "include_empty":
        return "alias_wav"
    return "call_key"


# フィールド自動編集可否を判定する
def _field_auto_edit_enabled(field: str, options: PreviewOptions) -> bool:
    if field == "alias":
        return options.allow_alias_edit and normalize_wav_edit_mode(options.alias_edit_mode) == WavEditMode.ALLOW
    if field == "wav":
        return options.allow_wav_edit and wav_auto_edit_enabled(options.wav_edit_mode)
    return False


# 変更対象フィールド一覧を処理する
def _auto_edit_fields(entry: OtoEntry, options: PreviewOptions, *, seeded_alias: str = "") -> tuple[str, ...]:
    invalid_moras = options.key_warning_config.excluded_moras
    alias_value = seeded_alias or entry.alias
    alias_mora = pronunciation_mora(alias_value)
    wav_mora = pronunciation_mora(wav_key(entry.wav_name))
    alias_valid = bool(alias_value) and _mora_is_allowed(alias_mora, invalid_moras)
    wav_valid = _mora_is_allowed(wav_mora, invalid_moras)

    logical: list[str] = []
    if _edit_scope(options) == "alias_wav":
        if alias_valid:
            logical.append("alias")
        if wav_valid:
            logical.append("wav")
    else:
        if alias_valid:
            logical.append("alias")
            if wav_valid and alias_mora != wav_mora:
                logical.append("wav")
        elif wav_valid:
            logical.append("wav")

    fields = [field for field in logical if _field_auto_edit_enabled(field, options)]
    if (
        _edit_scope(options) == "call_key"
        and logical == ["alias"]
        and "alias" not in fields
        and wav_valid
        and _field_auto_edit_enabled("wav", options)
    ):
        fields.append("wav")
    return tuple(dict.fromkeys(fields))


# 呼び出しキー対象一覧を処理する
def _call_key_edit_targets(entry: OtoEntry, options: PreviewOptions) -> tuple[str, ...]:
    """Return fields to edit when a mode targets UTAU-callable keys."""
    return _auto_edit_fields(entry, options)


# wavalias名前を処理する
def _wav_has_alias_by_name(entries: list[OtoEntry]) -> dict[str, bool]:
    result: dict[str, bool] = {}
    for entry in entries:
        result.setdefault(entry.wav_name, False)
        if entry.alias:
            result[entry.wav_name] = True
    return result


# 行を処理する
def _should_auto_edit_entry(
    entry: OtoEntry,
    options: PreviewOptions,
    wav_has_alias: dict[str, bool],
) -> tuple[bool, str]:
    if options.exclude_config.exclude_empty_params and _entry_params_all_zero_or_blank(entry):
        return False, "empty_params"
    return True, ""


# wavを処理する
def _should_auto_edit_wav(entry: OtoEntry, options: PreviewOptions, wav_has_alias: dict[str, bool]) -> bool:
    return "wav" in _auto_edit_fields(entry, options)


# 除外対象を判定する
def _is_excluded(
    entry: OtoEntry,
    config: ExcludeConfig,
    *,
    pitch_status: str = "",
    note: str | None = None,
    frequency: float | None = None,
) -> bool:
    return bool(_exclusion_kind(entry, config, pitch_status=pitch_status, note=note, frequency=frequency))


def _is_unvoiced_excluded(
    config: ExcludeConfig,
    *,
    pitch_status: str = "",
    note: str | None = None,
    frequency: float | None = None,
) -> bool:
    if not config.exclude_unvoiced:
        if not (config.exclude_no_f0 or config.exclude_no_freq_src):
            return False
    normalized = _normalized_status(pitch_status)
    if not normalized and note is None and frequency is None:
        return False
    exclude_no_f0 = config.exclude_unvoiced or config.exclude_no_f0
    exclude_no_freq_src = config.exclude_unvoiced or config.exclude_no_freq_src
    if normalized == "no_f0":
        return exclude_no_f0
    if normalized in {"no_freq_src", "invalid_freq"}:
        return exclude_no_freq_src
    return False


def _is_explicitly_excluded(entry: OtoEntry, config: ExcludeConfig) -> bool:
    if config.mode == "none":
        return False

    text = _entry_text_for_exclusion(entry)
    patterns = tuple(pattern for pattern in config.patterns if pattern)
    if config.mode == "string_list":
        return any(pattern in text for pattern in patterns)
    if config.mode == "regex":
        return any(re.search(pattern, text) for pattern in patterns)
    if config.mode == "mora":
        moras = {pattern.strip() for pattern in patterns if pattern.strip()}
        return pronunciation_mora(entry.alias) in moras or pronunciation_mora(wav_key(entry.wav_name)) in moras
    raise ValueError(f"Unsupported exclude mode: {config.mode}")


def _exclusion_kind(
    entry: OtoEntry,
    config: ExcludeConfig,
    *,
    pitch_status: str = "",
    note: str | None = None,
    frequency: float | None = None,
) -> str:
    if _is_explicitly_excluded(entry, config):
        return "explicit"
    if _is_unvoiced_excluded(config, pitch_status=pitch_status, note=note, frequency=frequency):
        return "unvoiced"
    return ""


def _change_is_auto_excluded(change: ChangeRow) -> bool:
    return change.reason in {"excluded", "excluded_unvoiced"} or change.status == "exclude" or change.origin_status == "exclude"


def _field_is_manual(change: ChangeRow, field: str) -> bool:
    if change.manual_edit_fields:
        return field in change.manual_edit_fields
    return change.origin_status == "manual" or change.status == "manual"


def _all_auto_fields_manual(change: ChangeRow) -> bool:
    fields = tuple(field for field in ("alias", "wav") if field in change.auto_edit_fields)
    return bool(fields) and all(_field_is_manual(change, field) for field in fields)


# 音符wav名前を処理する
def _note_wav_name(wav_name: str, note: str | None, config: AliasRewriteConfig) -> str:
    if not note:
        return wav_name
    path = Path(wav_name)
    mora = pronunciation_mora(path.stem)
    if not mora:
        return wav_name
    new_name = f"{mora}{config.separator}{note}{path.suffix}"
    parent = str(path.parent)
    if parent in {"", "."}:
        return new_name
    return str(Path(parent) / new_name)


# 音階名付きaliasを生成する
def _pitch_alias_name(entry: OtoEntry, note: str, options: PreviewOptions, *, seeded_alias: str = "") -> str:
    source = seeded_alias or source_alias_for_entry(entry)
    parts = split_alias(source)
    base = parts.mora if options.alias_config.strip_suffix else normalize_alias_body(parts.body)
    if options.alias_config.keep_prefix and parts.prefix:
        base = parts.prefix + base
    if not base:
        return entry.alias
    return f"{base}{options.alias_config.separator}{note}"


# 音階名付きwav名を生成する
def _pitch_wav_name(entry: OtoEntry, note: str, options: PreviewOptions) -> str:
    path = Path(entry.wav_name)
    parts = split_alias(path.stem)
    base = parts.mora if options.alias_config.strip_suffix else normalize_alias_body(parts.body)
    if options.alias_config.keep_prefix and parts.prefix:
        base = parts.prefix + base
    if not base:
        return entry.wav_name
    new_name = f"{base}{options.alias_config.separator}{note}{path.suffix}"
    parent = str(path.parent)
    if parent in {"", "."}:
        return new_name
    return str(Path(parent) / new_name)


# キー警告一覧を付与する
def _with_key_warnings(
    changes: list[ChangeRow],
    lines: list,
    options: PreviewOptions,
) -> list[ChangeRow]:
    original_entries = iter_entries(lines)
    modified_entries = apply_changes_to_entries(original_entries, changes)
    warnings = (
        detect_call_key_collisions(original_entries, options.key_warning_config)
        + detect_call_key_collisions(modified_entries, options.key_warning_config)
        + detect_resolution_warnings(original_entries, modified_entries, options.key_warning_config)
    )
    summaries = warning_summary_by_line(warnings)
    # セル一覧行を処理する
    def cells_for_line(raw_cells: tuple[str, ...], line_number: int) -> tuple[str, ...]:
        result: list[str] = []
        for cell in raw_cells:
            if cell in {"new_wav", "new_alias"}:
                result.append(cell)
                continue
            if cell == f"wav:{line_number}":
                result.append("new_wav")
            elif cell == f"alias:{line_number}":
                result.append("new_alias")
        return tuple(dict.fromkeys(result))

    result: list[ChangeRow] = []
    for change in changes:
        if change.line_number is None or change.line_number not in summaries:
            result.append(change)
            continue
        severity, status, messages, cells = summaries[change.line_number]
        if (
            status == "cannotcall"
            and options.relax_cannotcall_for_unused_ust_entries
            and options.usage_counts_available
            and options.usage_count_by_line.get(change.line_number, change.usage_count) == 0
        ):
            severity = "warning"
            status = "unused_cannotcall"
            messages = messages + ("cannotcall was relaxed because the row is unused in the selected UST set",)
        diagnostics = change.diagnostics + ((status,) if status and status not in change.diagnostics else ())
        result.append(
            replace(
                change,
                severity=severity,
                status=status or change.status,
                diagnostics=diagnostics,
                warnings=messages,
                warning_cells=cells_for_line(cells, change.line_number),
            )
        )
    return result


# Attach UST usage counts to preview rows
def _apply_usage_counts(changes: list[ChangeRow], options: PreviewOptions) -> list[ChangeRow]:
    if not options.usage_count_by_line:
        return changes
    return [
        replace(change, usage_count=options.usage_count_by_line.get(change.line_number or -1, 0))
        for change in changes
    ]


NUMBERED_NAME_RE = re.compile(r"^(?P<head>.*?)(?P<number>[2-9][0-9]*)(?P<sep>[_-].*)$")


# 重複名前を番号を付ける
def _number_duplicate_name(name: str, number: int) -> str:
    match = NUMBERED_NAME_RE.match(name)
    if match:
        return f"{match.group('head')}{number}{match.group('sep')}"
    if "_" in name:
        head, sep, tail = name.rpartition("_")
        return f"{head}{number}{sep}{tail}"
    return f"{name}{number}"


# wav名前を番号を付ける
def _number_wav_name(wav_name: str, number: int) -> str:
    path = Path(wav_name)
    numbered_name = _number_duplicate_name(path.stem, number) + path.suffix
    parent = str(path.parent)
    if parent in {"", "."}:
        return numbered_name
    return str(Path(parent) / numbered_name)


# 変更一覧を処理する
def _ordered_changes_for_numbering(changes: list[ChangeRow]) -> list[ChangeRow]:
    return sorted(
        changes,
        key=lambda change: (
            change.new_order_id if change.new_order_id is not None else change.old_order_id if change.old_order_id is not None else change.line_number or 0,
            change.line_number or 0,
        ),
    )


# 項目重複を処理する
def _cross_field_duplicate_items(changes: list[ChangeRow]) -> list[tuple[str, int, str, ChangeRow, bool]]:
    items: list[tuple[str, int, str, ChangeRow, bool]] = []
    for change in changes:
        order = (
            change.new_order_id
            if change.new_order_id is not None
            else change.old_order_id
            if change.old_order_id is not None
            else change.line_number
            or 0
        )
        if change.new_wav:
            items.append((Path(change.new_wav).stem, order, "wav", change, (change.new_wav != change.old_wav) and not _field_is_manual(change, "wav")))
        if change.new_alias:
            items.append((change.new_alias, order, "alias", change, (change.new_alias != change.old_alias) and not _field_is_manual(change, "alias")))
    return items


# 項目を処理する
def _group_cross_field_items(items: list[tuple[str, int, str, ChangeRow, bool]]) -> dict[str, list[tuple[int, str, ChangeRow, bool]]]:
    grouped: dict[str, list[tuple[int, str, ChangeRow, bool]]] = {}
    for key, order, field, change, editable in items:
        grouped.setdefault(key, []).append((order, field, change, editable))
    return grouped


# 項目重複を処理する
def _cross_field_duplicate_updates(
    grouped: dict[str, list[tuple[int, str, ChangeRow, bool]]],
    options: PreviewOptions,
) -> dict[tuple[int | None, str], str]:
    field_priority = _numbering_field_priority(options)
    updates: dict[tuple[int | None, str], str] = {}
    reserved_keys = set(grouped)
    for key, group in grouped.items():
        editable_items = [item for item in group if item[3]]
        if len(group) <= 1 or not editable_items:
            continue
        fixed_count = len(group) - len(editable_items)
        editable_items.sort(key=lambda item: (field_priority[item[1]], item[0], item[2].line_number or 0))
        used_names: set[str] = set()
        for offset, (_order, field, change, _editable) in enumerate(editable_items, start=1):
            index = fixed_count + offset
            if fixed_count == 0 and index == 1 and not options.number_first_alias:
                numbered = key
            else:
                numbered = _number_duplicate_name(key, index)
            while numbered in used_names or (numbered != key and numbered in reserved_keys):
                index += 1
                numbered = _number_duplicate_name(key, index)
            used_names.add(numbered)
            reserved_keys.add(numbered)
            if field == "wav":
                updates[(change.line_number, "wav")] = _number_wav_name(change.new_wav, index) if numbered != Path(change.new_wav).stem else change.new_wav
            else:
                updates[(change.line_number, "alias")] = numbered
    return updates


# 同一列重複番号表を作成する
def _same_field_duplicate_updates(
    items: list[tuple[str, int, str, ChangeRow, bool]],
    options: PreviewOptions,
) -> dict[tuple[int | None, str], str]:
    grouped: dict[tuple[str, str], list[tuple[int, ChangeRow, bool]]] = {}
    reserved_by_field: dict[str, set[str]] = {}
    for key, order, field, change, editable in items:
        grouped.setdefault((field, key), []).append((order, change, editable))
        reserved_by_field.setdefault(field, set()).add(key)

    updates: dict[tuple[int | None, str], str] = {}
    for (field, key), group in grouped.items():
        editable_items = [item for item in group if item[2]]
        if len(group) <= 1 or not editable_items:
            continue
        fixed_count = len(group) - len(editable_items)
        editable_items.sort(key=lambda item: (item[0], item[1].line_number or 0))
        used_names: set[str] = set()
        for offset, (_order, change, _editable) in enumerate(editable_items, start=1):
            index = fixed_count + offset
            if fixed_count == 0 and index == 1 and not options.number_first_alias:
                numbered = key
            else:
                numbered = _number_duplicate_name(key, index)
            reserved_keys = reserved_by_field.setdefault(field, set())
            while numbered in used_names or (numbered != key and numbered in reserved_keys):
                index += 1
                numbered = _number_duplicate_name(key, index)
            used_names.add(numbered)
            reserved_keys.add(numbered)
            if field == "wav":
                updates[(change.line_number, "wav")] = _number_wav_name(change.new_wav, index) if numbered != Path(change.new_wav).stem else change.new_wav
            else:
                updates[(change.line_number, "alias")] = numbered
    return updates


# 項目重複を反映する
def _apply_cross_field_duplicate_updates(
    changes: list[ChangeRow],
    updates: dict[tuple[int | None, str], str],
) -> list[ChangeRow]:
    result: list[ChangeRow] = []
    for change in changes:
        new_wav = updates.get((change.line_number, "wav"), change.new_wav)
        new_alias = updates.get((change.line_number, "alias"), change.new_alias)
        reason = change.reason
        if new_wav != change.new_wav or new_alias != change.new_alias:
            reason = "duplicate_numbered"
        result.append(
            replace(
                change,
                new_wav=new_wav,
                new_alias=new_alias,
                changed=(new_wav != change.old_wav or new_alias != change.old_alias),
                reason=reason,
            )
        )
    return result


# 同一old_wavの自動wav名を同期する
def _sync_auto_wav_groups(changes: list[ChangeRow]) -> list[ChangeRow]:
    groups: dict[str, list[ChangeRow]] = {}
    for change in changes:
        if change.old_wav:
            groups.setdefault(_norm_wav_name(change.old_wav), []).append(change)

    representative_by_old_wav: dict[str, str] = {}
    for key, group in groups.items():
        candidates = [
            change
            for change in _ordered_changes_for_numbering(group)
            if "wav" in change.auto_edit_fields
            and change.new_wav
            and change.new_wav != change.old_wav
            and not _field_is_manual(change, "wav")
        ]
        if candidates:
            representative_by_old_wav[key] = candidates[0].new_wav

    if not representative_by_old_wav:
        return changes

    result: list[ChangeRow] = []
    for change in changes:
        representative = representative_by_old_wav.get(_norm_wav_name(change.old_wav))
        if not representative or representative == change.new_wav or _field_is_manual(change, "wav"):
            result.append(change)
            continue
        result.append(
            replace(
                change,
                new_wav=representative,
                changed=(representative != change.old_wav or change.new_alias != change.old_alias),
                reason="same_old_wav_synced" if representative != change.old_wav else change.reason,
            )
        )
    return result


# 音階追記の番号再集計用ベース名を復元する
def _pitch_append_base_values_for_renumbering(
    changes: list[ChangeRow],
    entries_by_line: dict[int, OtoEntry],
    options: PreviewOptions,
) -> list[ChangeRow]:
    if options.mode != "pitch_append":
        return changes
    result: list[ChangeRow] = []
    for change in changes:
        if change.line_number is None or _all_auto_fields_manual(change):
            result.append(change)
            continue
        entry = entries_by_line.get(change.line_number)
        if entry is None:
            result.append(change)
            continue
        new_alias = change.new_alias
        new_wav = change.new_wav
        if "alias" in change.auto_edit_fields and not _field_is_manual(change, "alias"):
            if not entry.alias and not options.add_alias_for_unused_wav:
                new_alias = entry.alias
            else:
                new_alias = _pitch_alias_name(entry, change.note or "", options)
        if "wav" in change.auto_edit_fields and not _field_is_manual(change, "wav"):
            new_wav = _pitch_wav_name(entry, change.note or "", options)
        result.append(
            replace(
                change,
                new_wav=new_wav,
                new_alias=new_alias,
                changed=(new_wav != change.old_wav or new_alias != change.old_alias),
            )
        )
    return result


# 項目重複番号表を付与する
def _with_cross_field_duplicate_numbers(changes: list[ChangeRow], options: PreviewOptions) -> list[ChangeRow]:
    if options.mode != "pitch_append":
        return changes
    ordered_changes = _ordered_changes_for_numbering(changes)
    items = _cross_field_duplicate_items(ordered_changes)
    if _numbering_order_mode(options) == "separate":
        updates = _same_field_duplicate_updates(items, options)
    else:
        grouped = _group_cross_field_items(items)
        updates = _cross_field_duplicate_updates(grouped, options)
    if not updates:
        return changes
    return _apply_cross_field_duplicate_updates(changes, updates)


# 変更警告を統合する
def _merge_change_warning(change: ChangeRow, *, level: str, message: str, cells: tuple[str, ...], status: str = "") -> ChangeRow:
    severity = level if warning_rank(level) > warning_rank(change.severity) else change.severity
    warnings = change.warnings + ((message,) if message and message not in change.warnings else ())
    warning_cells = tuple(dict.fromkeys(change.warning_cells + cells))
    diagnostics = change.diagnostics
    if status and status not in diagnostics:
        diagnostics = diagnostics + (status,)
    return replace(
        change,
        severity=severity,
        status=status or change.status,
        diagnostics=diagnostics,
        warnings=warnings,
        warning_cells=warning_cells,
    )


# wavを反映する
def _apply_wav_edit_policy(changes: list[ChangeRow], options: PreviewOptions) -> list[ChangeRow]:
    mode = normalize_wav_edit_mode(options.wav_edit_mode)
    if options.allow_wav_edit and wav_auto_edit_enabled(mode):
        return changes
    return [
            replace(
                change,
                new_wav=change.old_wav,
                changed=(change.new_alias != change.old_alias),
                reason="wav_edit_disabled" if change.new_wav != change.old_wav else change.reason,
                auto_edit_fields=tuple(field for field in change.auto_edit_fields if field != "wav"),
            )
        if change.new_wav != change.old_wav
        else change
        for change in changes
    ]


# aliasを反映する
def _apply_alias_edit_policy(changes: list[ChangeRow], options: PreviewOptions) -> list[ChangeRow]:
    mode = normalize_wav_edit_mode(options.alias_edit_mode)
    if options.allow_alias_edit and mode == WavEditMode.ALLOW:
        return changes
    return [
            replace(
                change,
                new_alias=change.old_alias,
                changed=(change.new_wav != change.old_wav),
                reason="alias_edit_disabled" if change.new_alias != change.old_alias else change.reason,
                auto_edit_fields=tuple(field for field in change.auto_edit_fields if field != "alias"),
            )
        if change.new_alias != change.old_alias
        else change
        for change in changes
    ]


# wav名前を処理する
def _norm_wav_name(wav_name: str) -> str:
    return str(Path(wav_name)).casefold()


WINDOWS_INVALID_FILENAME_CHARS = set('<>:"|?*')
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


# 不正wav名前理由を処理する
def _invalid_wav_name_reason(wav_name: str) -> str | None:
    name = str(wav_name or "").strip()
    if not name:
        return "wav name is empty"
    if "/" in name or "\\" in name:
        return "wav name must not contain path separators"
    path = Path(name)
    if path.is_absolute() or ".." in path.parts:
        return "wav name must be a relative path inside the voice folder"
    if path.suffix.lower() != ".wav":
        return "wav name must end with .wav"
    if not path.stem:
        return "wav basename is empty"
    for part in path.parts:
        if part in {"", ".", ".."}:
            return "wav name contains an invalid path segment"
        if any(char in WINDOWS_INVALID_FILENAME_CHARS for char in part):
            return "wav name contains characters that cannot be used on Windows"
        stem = Path(part).stem.upper()
        if stem in WINDOWS_RESERVED_NAMES:
            return "wav name uses a reserved Windows device name"
        if part.endswith((" ", ".")):
            return "wav name cannot end with a space or period"
    return None


# 不正wav名前警告一覧を付与する
def _with_invalid_wav_name_warnings(changes: list[ChangeRow]) -> list[ChangeRow]:
    result: list[ChangeRow] = []
    for change in changes:
        reason = _invalid_wav_name_reason(change.new_wav)
        if reason:
            result.append(
                _merge_change_warning(
                    replace(change, status="invalid_wav_name"),
                    level="danger",
                    message=f"invalid wav name {change.new_wav!r}: {reason}",
                    cells=("new_wav",),
                    status="invalid_wav_name",
                )
            )
        else:
            result.append(change)
    return result


# wav衝突警告一覧を付与する
def _with_wav_collision_warnings(changes: list[ChangeRow]) -> list[ChangeRow]:
    changed_wav_rows = [change for change in changes if change.old_wav and change.new_wav and _norm_wav_name(change.old_wav) != _norm_wav_name(change.new_wav)]
    if not changed_wav_rows:
        return changes

    moving_sources = {_norm_wav_name(change.old_wav) for change in changed_wav_rows}
    old_wavs = {_norm_wav_name(change.old_wav) for change in changes if change.old_wav}
    dest_to_sources: dict[str, set[str]] = {}
    for change in changed_wav_rows:
        dest_to_sources.setdefault(_norm_wav_name(change.new_wav), set()).add(_norm_wav_name(change.old_wav))

    danger_by_dest: dict[str, list[str]] = {}
    for change in changed_wav_rows:
        dest = _norm_wav_name(change.new_wav)
        sources = dest_to_sources.get(dest, set())
        messages: list[str] = []
        if len(sources) > 1:
            messages.append(f"wav destination collision: multiple old wav files map to {change.new_wav!r}")
        if dest in old_wavs and dest not in moving_sources and dest != _norm_wav_name(change.old_wav):
            messages.append(f"wav destination already exists and is not moving: {change.new_wav!r}")
        if messages:
            danger_by_dest.setdefault(dest, []).extend(messages)

    if not danger_by_dest:
        return changes
    result = []
    for change in changes:
        updated = change
        for message in danger_by_dest.get(_norm_wav_name(change.new_wav), ()):
            updated = _merge_change_warning(updated, level="danger", message=message, cells=("new_wav",), status="wav_conflict")
        result.append(updated)
    return result


# wav警告一覧を付与する
def _with_same_old_wav_consistency_warnings(changes: list[ChangeRow]) -> list[ChangeRow]:
    groups: dict[str, list[ChangeRow]] = {}
    for change in changes:
        if change.old_wav:
            groups.setdefault(_norm_wav_name(change.old_wav), []).append(change)

    danger_lines: dict[int | None, str] = {}
    for group in groups.values():
        new_wavs = {_norm_wav_name(change.new_wav) for change in group if change.new_wav}
        if len(new_wavs) <= 1:
            continue
        old_wav = group[0].old_wav
        values = ", ".join(sorted({change.new_wav for change in group}))
        message = f"same old wav {old_wav!r} maps to multiple new wav names: {values}"
        for change in group:
            danger_lines[change.line_number] = message

    if not danger_lines:
        return changes
    result = []
    for change in changes:
        message = danger_lines.get(change.line_number)
        if message:
            result.append(_merge_change_warning(change, level="danger", message=message, cells=("new_wav",), status="old_wav_split"))
        else:
            result.append(change)
    return result


# aliasを処理する
def _alias_from_source(entry: OtoEntry, options: PreviewOptions) -> str:
    source = source_alias_for_entry(entry)
    parts = split_alias(source)
    base = parts.mora if options.alias_config.strip_suffix else normalize_alias_body(parts.body)
    prefix = parts.prefix if options.alias_config.keep_prefix else ""
    return f"{prefix}{base}" if base else entry.alias


# wav名をprefix/suffix設定に従って整える
def _wav_from_source(entry: OtoEntry, options: PreviewOptions) -> str:
    path = Path(entry.wav_name)
    parts = split_alias(path.stem)
    base = parts.mora if options.alias_config.strip_suffix else normalize_alias_body(parts.body)
    prefix = parts.prefix if options.alias_config.keep_prefix else ""
    if not base:
        return entry.wav_name
    new_name = f"{prefix}{base}{path.suffix}"
    parent = str(path.parent)
    if parent in {"", "."}:
        return new_name
    return str(Path(parent) / new_name)


# 設定を反映する
def _apply_common_secondary_options(
    changes: list[ChangeRow],
    entries_by_line: dict[int, OtoEntry],
    options: PreviewOptions,
) -> list[ChangeRow]:
    alias_mode = normalize_wav_edit_mode(options.alias_edit_mode)
    alias_auto_allowed = options.allow_alias_edit and alias_mode == WavEditMode.ALLOW
    result: list[ChangeRow] = []
    for change in changes:
        entry = entries_by_line.get(change.line_number or -1)
        if entry is None:
            result.append(change)
            continue

        updated = change
        seeded_alias = wav_key(entry.wav_name) if not entry.alias and options.add_alias_for_unused_wav else ""
        fields = _auto_edit_fields(entry, options, seeded_alias=seeded_alias)
        if options.mode == "none":
            if entry.alias and "alias" in fields:
                new_alias = _alias_from_source(entry, options)
                if new_alias != updated.new_alias:
                    updated = replace(
                        updated,
                        new_alias=new_alias,
                        changed=(updated.new_wav != updated.old_wav or new_alias != updated.old_alias),
                        reason="option_alias_normalized",
                        origin_status="system",
                        auto_edit_fields=tuple(dict.fromkeys(updated.auto_edit_fields + ("alias",))),
                    )
            if "wav" in fields:
                new_wav = _wav_from_source(entry, options)
                if new_wav != updated.new_wav:
                    updated = replace(
                        updated,
                        new_wav=new_wav,
                        changed=(new_wav != updated.old_wav or updated.new_alias != updated.old_alias),
                        reason="option_wav_normalized",
                        origin_status="system",
                        auto_edit_fields=tuple(dict.fromkeys(updated.auto_edit_fields + ("wav",))),
                    )
        if not entry.alias and options.add_alias_for_unused_wav and alias_auto_allowed and "alias" in fields and not updated.new_alias:
            new_alias = _alias_from_source(entry, options)
            updated = replace(
                updated,
                new_alias=new_alias,
                source_alias=source_alias_for_entry(entry),
                changed=(updated.new_wav != updated.old_wav or new_alias != updated.old_alias),
                reason="empty_alias_added" if new_alias else updated.reason,
                origin_status="system" if new_alias else updated.origin_status,
                auto_edit_fields=tuple(dict.fromkeys(updated.auto_edit_fields + ("alias",))),
            )

        if (
            options.prefix_underscore_for_new_alias
            and _field_auto_edit_enabled("wav", options)
            and not entry.alias
            and updated.new_alias
            and updated.new_alias != entry.alias
        ):
            new_wav = hidden_wav_name(updated.new_wav)
            updated = replace(
                updated,
                new_wav=new_wav,
                changed=(new_wav != updated.old_wav or updated.new_alias != updated.old_alias),
                reason="empty_alias_added_hidden_wav" if new_wav != updated.new_wav else updated.reason,
                origin_status="system" if new_wav != updated.new_wav else updated.origin_status,
                auto_edit_fields=tuple(dict.fromkeys(updated.auto_edit_fields + ("wav",))),
            )
        result.append(updated)
    return result


# 新規alias付与行のwav名を隠し名にする
def _apply_hidden_wav_prefix_for_new_aliases(
    changes: list[ChangeRow],
    entries_by_line: dict[int, OtoEntry],
    options: PreviewOptions,
) -> list[ChangeRow]:
    if not options.prefix_underscore_for_new_alias or not _field_auto_edit_enabled("wav", options):
        return changes
    result: list[ChangeRow] = []
    for change in changes:
        entry = entries_by_line.get(change.line_number or -1)
        if (
            entry is None
            or entry.alias
            or not change.new_alias
            or change.new_alias == entry.alias
            or _field_is_manual(change, "wav")
            or change.origin_status == "exclude"
            or change.status == "exclude"
        ):
            result.append(change)
            continue
        new_wav = hidden_wav_name(change.new_wav)
        if new_wav == change.new_wav:
            result.append(change)
            continue
        result.append(
            replace(
                change,
                new_wav=new_wav,
                changed=(new_wav != change.old_wav or change.new_alias != change.old_alias),
                reason="empty_alias_added_hidden_wav",
                origin_status="system",
                auto_edit_fields=tuple(dict.fromkeys(change.auto_edit_fields + ("wav",))),
            )
        )
    return result


# wav名前一覧を反映する
def _apply_representative_wav_names(
    changes: list[ChangeRow],
    entries: list[OtoEntry],
    options: PreviewOptions,
) -> list[ChangeRow]:
    if not options.allow_wav_edit or not wav_representative_edit_enabled(options.wav_edit_mode):
        return changes

    by_line = {change.line_number: change for change in changes if change.line_number is not None}
    entries_by_old_wav: dict[str, list[OtoEntry]] = {}
    for entry in entries:
        entries_by_old_wav.setdefault(entry.wav_name, []).append(entry)

    representative_wav_by_old_wav: dict[str, str] = {}
    for old_wav in entries_by_old_wav:
        representative = resolve_call_key(wav_key(old_wav), entries)
        if representative is None or representative.wav_name != old_wav:
            representative_wav_by_old_wav[old_wav] = old_wav
            continue
        representative_change = by_line.get(representative.line_number)
        representative_wav_by_old_wav[old_wav] = representative_change.new_wav if representative_change else old_wav

    result: list[ChangeRow] = []
    for change in changes:
        representative_wav = representative_wav_by_old_wav.get(change.old_wav, change.new_wav)
        if representative_wav == change.new_wav:
            result.append(change)
            continue
        result.append(
            replace(
                change,
                new_wav=representative_wav,
                changed=(representative_wav != change.old_wav or change.new_alias != change.old_alias),
                reason="representative_wav" if representative_wav != change.old_wav else change.reason,
            )
        )
    return result


# Preview変更一覧を処理する
def _finalize_preview_changes(
    changes: list[ChangeRow],
    lines: list,
    options: PreviewOptions,
    *,
    oto_path: str | Path | None = None,
    mrq_path: str | Path | None = None,
) -> list[ChangeRow]:
    entries = iter_entries(lines)
    entries_by_line = {entry.line_number: entry for entry in entries}
    changes = _sanitize_preview_values(changes)
    changes = _apply_representative_wav_names(changes, entries, options)
    changes = _apply_common_secondary_options(changes, entries_by_line, options)
    changes = _apply_alias_edit_policy(changes, options)
    changes = _apply_wav_edit_policy(changes, options)
    changes = _apply_usage_counts(changes, options)
    changes = _with_pitch_sort_metadata(changes, entries, options, oto_path=oto_path, mrq_path=mrq_path)
    changes = _apply_sort(changes, options)
    changes = _apply_numbering_values(changes, entries_by_line, options)
    changes = _apply_rule_based_dynamic_templates(changes, entries, options)
    changes = _apply_hidden_wav_prefix_for_new_aliases(changes, entries_by_line, options)
    changes = _with_cross_field_duplicate_numbers(changes, options)
    changes = _sync_auto_wav_groups(changes)
    changes = _with_key_warnings(changes, lines, options)
    changes = _with_invalid_wav_name_warnings(changes)
    changes = _with_wav_collision_warnings(changes)
    return _with_same_old_wav_consistency_warnings(changes)


# CSVPreview変更一覧を処理する
def _finalize_csv_preview_changes(
    changes: list[ChangeRow],
    lines: list,
    options: PreviewOptions,
    *,
    oto_path: str | Path | None = None,
    mrq_path: str | Path | None = None,
) -> list[ChangeRow]:
    """Finalize CSV mode without applying REWRITE name-generation options."""
    entries = iter_entries(lines)
    changes = _sanitize_preview_values(changes)
    changes = _apply_usage_counts(changes, options)
    changes = _with_pitch_sort_metadata(changes, entries, options, oto_path=oto_path, mrq_path=mrq_path)
    if options.sort_orders and not any(change.new_order_id is not None and change.new_order_id != change.old_order_id for change in changes):
        changes = _apply_sort(changes, options)
    changes = _with_key_warnings(changes, lines, options)
    changes = _with_invalid_wav_name_warnings(changes)
    changes = _with_wav_collision_warnings(changes)
    return _with_same_old_wav_consistency_warnings(changes)


# Preview値一覧を整える
def _sanitize_preview_values(changes: list[ChangeRow]) -> list[ChangeRow]:
    result: list[ChangeRow] = []
    for change in changes:
        new_wav = _single_line(change.new_wav)
        new_alias = _single_line(change.new_alias)
        if new_wav == change.new_wav and new_alias == change.new_alias:
            result.append(change)
            continue
        result.append(
            replace(
                change,
                new_wav=new_wav,
                new_alias=new_alias,
                changed=(new_wav != change.old_wav or new_alias != change.old_alias),
            )
        )
    return result


# テンプレートを展開する
def _expand_replacement_template(
    template: str,
    entry: OtoEntry,
    *,
    source_text: str | None = None,
    note: str = "",
    oldnum: int | None = None,
    newnum: int | None = None,
    oldmoranum: int | None = None,
    newmoranum: int | None = None,
    newprefixmoranum: int | None = None,
    newdupnum: str = "",
    number_first: bool = True,
) -> str:
    source = source_alias_for_entry(entry) if source_text is None else source_text
    parts = split_alias(source)
    prefix_mora = parts.prefix + parts.mora

    def number(value: int | None, *, blank_first: bool = True) -> str:
        if value is None:
            return ""
        if value == 1 and blank_first and not number_first:
            return ""
        return str(value)

    variables = {
        "p": parts.prefix,
        "prefix": parts.prefix,
        "mora": parts.mora,
        "m": parts.mora,
        "pm": prefix_mora,
        "suffix": parts.suffix,
        "s": parts.suffix,
        "alias": entry.alias,
        "a": entry.alias,
        "wav": wav_key(entry.wav_name),
        "wavstem": wav_key(entry.wav_name),
        "f": wav_key(entry.wav_name),
        "note": note,
        "n": note,
        "oldnum": "" if oldnum is None else str(oldnum),
        "l": "" if newnum is None else str(newnum),
        "newnum": "" if newnum is None else str(newnum),
        "oldmoranum": number(oldmoranum),
        "newmoranum": number(newmoranum),
        "mr": number(newmoranum),
        "pmr": number(newprefixmoranum),
        "newdupnum": newdupnum,
        "d": newdupnum,
    }
    expanded = template
    for name in sorted(variables, key=len, reverse=True):
        expanded = expanded.replace(f"${name}", variables[name])
    return _single_line(expanded)


# 値を反映する
def _apply_replacement(text: str, rule: ReplacementRule, entry: OtoEntry, *, note: str = "") -> str:
    if rule.use_regex:
        return _single_line(re.sub(rule.old, rule.new, text))
    return _single_line(text.replace(rule.old, rule.new))


# ルールwav名前を処理する
def _rule_wav_name(
    entry: OtoEntry,
    template: str,
    *,
    source_text: str | None = None,
    note: str,
    oldnum: int | None,
    newnum: int | None,
    oldmoranum: int | None = None,
    newmoranum: int | None = None,
    newprefixmoranum: int | None = None,
    newdupnum: str = "",
    number_first: bool = True,
) -> str:
    expanded = _expand_replacement_template(
        template,
        entry,
        source_text=wav_key(entry.wav_name) if source_text is None else source_text,
        note=note,
        oldnum=oldnum,
        newnum=newnum,
        oldmoranum=oldmoranum,
        newmoranum=newmoranum,
        newprefixmoranum=newprefixmoranum,
        newdupnum=newdupnum,
        number_first=number_first,
    )
    if not expanded:
        return entry.wav_name
    path = Path(entry.wav_name)
    suffix = path.suffix
    candidate = expanded if Path(expanded).suffix else expanded + suffix
    parent = str(path.parent)
    if parent in {"", "."} or any(sep in expanded for sep in ("/", "\\")):
        return candidate
    return str(Path(parent) / candidate)


# 発音番号対応表を処理する
def _mora_number_maps(entries: list[OtoEntry], changes: list[ChangeRow]) -> tuple[dict[int, int], dict[int, int]]:
    old_counts: dict[str, int] = {}
    old_by_line: dict[int, int] = {}
    for entry in sorted(entries, key=lambda item: item.line_number):
        mora = pronunciation_mora(source_alias_for_entry(entry))
        if not mora:
            continue
        old_counts[mora] = old_counts.get(mora, 0) + 1
        old_by_line[entry.line_number] = old_counts[mora]

    new_counts: dict[str, int] = {}
    new_by_line: dict[int, int] = {}
    ordered = sorted(
        changes,
        key=lambda change: (
            change.new_order_id if change.new_order_id is not None else change.old_order_id if change.old_order_id is not None else change.line_number or 0,
            change.line_number or 0,
        ),
    )
    for change in ordered:
        if change.line_number is None:
            continue
        key = change.new_alias or wav_key(change.new_wav)
        mora = pronunciation_mora(key)
        if not mora:
            continue
        new_counts[mora] = new_counts.get(mora, 0) + 1
        new_by_line[change.line_number] = new_counts[mora]
    return old_by_line, new_by_line


# prefix+発音番号対応表を処理する
def _new_prefix_mora_number_map(changes: list[ChangeRow]) -> dict[int, int]:
    counts: dict[str, int] = {}
    by_line: dict[int, int] = {}
    ordered = sorted(
        changes,
        key=lambda change: (
            change.new_order_id if change.new_order_id is not None else change.old_order_id if change.old_order_id is not None else change.line_number or 0,
            change.line_number or 0,
        ),
    )
    for change in ordered:
        if change.line_number is None:
            continue
        key = change.new_alias or wav_key(change.new_wav)
        parts = split_alias(key)
        prefix_mora = parts.prefix + parts.mora
        if not prefix_mora:
            continue
        counts[prefix_mora] = counts.get(prefix_mora, 0) + 1
        by_line[change.line_number] = counts[prefix_mora]
    return by_line


# 重複番号表値一覧を処理する
def _rule_numbering_items(
    ordered_changes: list[ChangeRow],
    entries_by_line: dict[int, OtoEntry],
    options: PreviewOptions,
) -> list[tuple[str, int, int, str]]:
    items: list[tuple[str, int, int, str]] = []
    field_priority = _numbering_field_priority(options)
    for fallback_order, change in enumerate(ordered_changes, start=1):
        if change.line_number is None or _change_is_auto_excluded(change) or _all_auto_fields_manual(change):
            continue
        entry = entries_by_line.get(change.line_number)
        if entry is None:
            continue
        order = change.new_order_id if change.new_order_id is not None else fallback_order
        for field in ("alias", "wav"):
            if _field_is_manual(change, field):
                continue
            if not _rule_template_for_field(entry, field, options):
                continue
            source = change.new_alias if field == "alias" else wav_key(change.new_wav)
            items.append((field, change.line_number, order * 10 + field_priority[field], source))
    return items


def _rule_number_group_key(kind: str, field: str, value: str, options: PreviewOptions) -> str:
    if kind == "prefix_mora":
        parts = split_alias(value)
        group_value = parts.prefix + parts.mora
    else:
        group_value = pronunciation_mora(value)
    if _numbering_order_mode(options) == "separate":
        return f"{field}\0{group_value}"
    return group_value


def _rule_number_map_by_field(
    items: list[tuple[str, int, int, str]],
    *,
    kind: str,
    options: PreviewOptions,
) -> dict[tuple[int, str], int]:
    groups: dict[str, list[tuple[str, int, int]]] = {}
    field_priority = _numbering_field_priority(options)
    for field, line_number, order, value in items:
        group_key = _rule_number_group_key(kind, field, value, options)
        if not group_key.split("\0")[-1]:
            continue
        groups.setdefault(group_key, []).append((field, line_number, order))

    result: dict[tuple[int, str], int] = {}
    for group in groups.values():
        group.sort(key=lambda item: (field_priority[item[0]], item[2], item[1]))
        for index, (field, line_number, _order) in enumerate(group, start=1):
            result[(line_number, field)] = index
    return result


def _duplicate_numbers_for_values(values_by_line: dict[int, str], options: PreviewOptions) -> dict[int, str]:
    groups: dict[str, list[int]] = {}
    for line_number, value in values_by_line.items():
        groups.setdefault(value, []).append(line_number)
    result: dict[int, str] = {}
    for line_numbers in groups.values():
        if len(line_numbers) <= 1:
            if options.number_first_alias:
                result[line_numbers[0]] = "1"
            else:
                result[line_numbers[0]] = ""
            continue
        for index, line_number in enumerate(line_numbers, start=1):
            result[line_number] = str(index) if index > 1 or options.number_first_alias else ""
    return result


# 呼び出しキー重複番号表を処理する
def _call_key_duplicate_numbers(
    *,
    alias_base: dict[int, str],
    wav_base: dict[int, str],
    ordered_changes: list[ChangeRow],
    options: PreviewOptions,
) -> dict[tuple[int, str], str]:
    order_by_line = {
        change.line_number: index
        for index, change in enumerate(ordered_changes, start=1)
        if change.line_number is not None
    }
    field_priority = _numbering_field_priority(options)
    groups: dict[str, list[tuple[str, int, int]]] = {}
    for line_number, value in alias_base.items():
        key = f"alias\0{value}" if _numbering_order_mode(options) == "separate" else value
        groups.setdefault(key, []).append(("alias", line_number, order_by_line.get(line_number, line_number)))
    for line_number, value in wav_base.items():
        stem = Path(value).stem
        key = f"wav\0{stem}" if _numbering_order_mode(options) == "separate" else stem
        groups.setdefault(key, []).append(("wav", line_number, order_by_line.get(line_number, line_number)))

    result: dict[tuple[int, str], str] = {}
    for items in groups.values():
        items.sort(key=lambda item: (field_priority[item[0]], item[2], item[1]))
        if len(items) <= 1:
            field, line_number, _order = items[0]
            result[(line_number, field)] = "1" if options.number_first_alias else ""
            continue
        for index, (field, line_number, _order) in enumerate(items, start=1):
            result[(line_number, field)] = str(index) if index > 1 or options.number_first_alias else ""
    return result


# ルールテンプレート項目を処理する
def _rule_template_for_field(entry: OtoEntry | None, field: str, options: PreviewOptions) -> str:
    if entry is None:
        return ""
    seeded_alias = wav_key(entry.wav_name) if not entry.alias and options.add_alias_for_unused_wav else ""
    if field not in _auto_edit_fields(entry, options, seeded_alias=seeded_alias):
        return ""
    if options.rule_scope == "call_key":
        return options.rule_call_key_template
    if field == "alias":
        return options.rule_alias_template
    return options.rule_wav_template


# ルールテンプレート一覧を処理する
def _uses_rule_based_dynamic_templates(options: PreviewOptions) -> bool:
    return options.mode == "rule_based" and bool(options.rule_alias_template or options.rule_wav_template or options.rule_call_key_template)


# 変更一覧ルールテンプレート一覧を処理する
def _ordered_changes_for_rule_templates(changes: list[ChangeRow]) -> list[ChangeRow]:
    return sorted(
        changes,
        key=lambda change: (
            change.new_order_id if change.new_order_id is not None else change.old_order_id if change.old_order_id is not None else change.line_number or 0,
            change.line_number or 0,
        ),
    )


# ルールを処理する
def _rule_dynamic_common(
    change: ChangeRow,
    entry: OtoEntry,
    field: str,
    newnum_by_line: dict[int, int],
    old_mora_by_line: dict[int, int],
    new_mora_by_field: dict[tuple[int, str], int],
    new_prefix_mora_by_field: dict[tuple[int, str], int],
) -> dict:
    return dict(
        note=change.note,
        oldnum=entry.line_number,
        newnum=newnum_by_line.get(entry.line_number),
        oldmoranum=old_mora_by_line.get(entry.line_number),
        newmoranum=new_mora_by_field.get((entry.line_number, field)),
        newprefixmoranum=new_prefix_mora_by_field.get((entry.line_number, field)),
    )


# ルール値を処理する
def _rule_dynamic_base_value(
    change: ChangeRow,
    field: str,
    entries_by_line: dict[int, OtoEntry],
    newnum_by_line: dict[int, int],
    old_mora_by_line: dict[int, int],
    new_mora_by_field: dict[tuple[int, str], int],
    new_prefix_mora_by_field: dict[tuple[int, str], int],
    options: PreviewOptions,
) -> str:
    entry = entries_by_line.get(change.line_number or -1)
    if entry is None:
        return change.new_alias if field == "alias" else change.new_wav
    template = _rule_template_for_field(entry, field, options)
    if not template:
        return change.new_alias if field == "alias" else change.new_wav
    common = _rule_dynamic_common(change, entry, field, newnum_by_line, old_mora_by_line, new_mora_by_field, new_prefix_mora_by_field)
    if field == "alias":
        return _expand_replacement_template(template, entry, source_text=entry.alias, **common, newdupnum="", number_first=options.number_first_alias)
    return _rule_wav_name(entry, template, source_text=wav_key(entry.wav_name), **common, newdupnum="", number_first=options.number_first_alias)


# ルール対応表を処理する
def _rule_dynamic_base_maps(
    ordered_changes: list[ChangeRow],
    entries_by_line: dict[int, OtoEntry],
    newnum_by_line: dict[int, int],
    old_mora_by_line: dict[int, int],
    new_mora_by_field: dict[tuple[int, str], int],
    new_prefix_mora_by_field: dict[tuple[int, str], int],
    options: PreviewOptions,
) -> tuple[dict[int, str], dict[int, str]]:
    alias_base = {
        change.line_number: _rule_dynamic_base_value(change, "alias", entries_by_line, newnum_by_line, old_mora_by_line, new_mora_by_field, new_prefix_mora_by_field, options)
        for change in ordered_changes
        if change.line_number is not None
        and _rule_template_for_field(entries_by_line.get(change.line_number), "alias", options)
        and not _change_is_auto_excluded(change)
        and not _field_is_manual(change, "alias")
    }
    wav_base = {
        change.line_number: _rule_dynamic_base_value(change, "wav", entries_by_line, newnum_by_line, old_mora_by_line, new_mora_by_field, new_prefix_mora_by_field, options)
        for change in ordered_changes
        if change.line_number is not None
        and _rule_template_for_field(entries_by_line.get(change.line_number), "wav", options)
        and not _change_is_auto_excluded(change)
        and not _field_is_manual(change, "wav")
    }
    return alias_base, wav_base


# ルール重複対応表を処理する
def _rule_dynamic_duplicate_maps(
    alias_base: dict[int, str],
    wav_base: dict[int, str],
    ordered_changes: list[ChangeRow],
    options: PreviewOptions,
) -> tuple[dict[int, str], dict[int, str], dict[tuple[int, str], str]]:
    alias_dup = _duplicate_numbers_for_values(alias_base, options)
    wav_dup = _duplicate_numbers_for_values({line: Path(value).stem for line, value in wav_base.items()}, options)
    call_key_dup = (
        _call_key_duplicate_numbers(
            alias_base=alias_base,
            wav_base=wav_base,
            ordered_changes=ordered_changes,
            options=options,
        )
        if options.rule_scope == "call_key" or _numbering_order_mode(options) != "separate"
        else {}
    )
    return alias_dup, wav_dup, call_key_dup


# ルール変更を反映する
def _apply_rule_dynamic_change(
    change: ChangeRow,
    entry: OtoEntry,
    newnum_by_line: dict[int, int],
    old_mora_by_line: dict[int, int],
    new_mora_by_field: dict[tuple[int, str], int],
    new_prefix_mora_by_field: dict[tuple[int, str], int],
    alias_dup: dict[int, str],
    wav_dup: dict[int, str],
    call_key_dup: dict[tuple[int, str], str],
    options: PreviewOptions,
) -> ChangeRow:
    new_alias = change.new_alias
    new_wav = change.new_wav
    alias_template = _rule_template_for_field(entry, "alias", options)
    wav_template = _rule_template_for_field(entry, "wav", options)
    if alias_template and not _field_is_manual(change, "alias"):
        common = _rule_dynamic_common(change, entry, "alias", newnum_by_line, old_mora_by_line, new_mora_by_field, new_prefix_mora_by_field)
        new_alias = _expand_replacement_template(
            alias_template,
            entry,
            source_text=entry.alias,
            **common,
            newdupnum=(
                call_key_dup.get((entry.line_number, "alias"), "")
                if options.rule_scope == "call_key" or _numbering_order_mode(options) != "separate"
                else alias_dup.get(entry.line_number, "")
            ),
            number_first=options.number_first_alias,
        )
    if wav_template and not _field_is_manual(change, "wav"):
        common = _rule_dynamic_common(change, entry, "wav", newnum_by_line, old_mora_by_line, new_mora_by_field, new_prefix_mora_by_field)
        new_wav = _rule_wav_name(
            entry,
            wav_template,
            source_text=wav_key(entry.wav_name),
            **common,
            newdupnum=(
                call_key_dup.get((entry.line_number, "wav"), "")
                if options.rule_scope == "call_key" or _numbering_order_mode(options) != "separate"
                else wav_dup.get(entry.line_number, "")
            ),
            number_first=options.number_first_alias,
        )
    return replace(
        change,
        new_alias=new_alias,
        new_wav=new_wav,
        changed=(new_wav != change.old_wav or new_alias != change.old_alias),
        reason="rule_based" if new_wav != change.old_wav or new_alias != change.old_alias else change.reason,
    )


# ルールテンプレート一覧を反映する
def _apply_rule_based_dynamic_templates(changes: list[ChangeRow], entries: list[OtoEntry], options: PreviewOptions) -> list[ChangeRow]:
    if not _uses_rule_based_dynamic_templates(options):
        return changes
    entries_by_line = {entry.line_number: entry for entry in entries}
    old_mora_by_line, _new_mora_by_line = _mora_number_maps(entries, changes)
    ordered_changes = _ordered_changes_for_rule_templates(changes)
    newnum_by_line = {change.line_number: index for index, change in enumerate(ordered_changes, start=1) if change.line_number is not None}
    numbering_items = _rule_numbering_items(ordered_changes, entries_by_line, options)
    new_mora_by_field = _rule_number_map_by_field(numbering_items, kind="mora", options=options)
    new_prefix_mora_by_field = _rule_number_map_by_field(numbering_items, kind="prefix_mora", options=options)
    alias_base, wav_base = _rule_dynamic_base_maps(ordered_changes, entries_by_line, newnum_by_line, old_mora_by_line, new_mora_by_field, new_prefix_mora_by_field, options)
    alias_dup, wav_dup, call_key_dup = _rule_dynamic_duplicate_maps(alias_base, wav_base, ordered_changes, options)
    result: list[ChangeRow] = []
    for change in changes:
        entry = entries_by_line.get(change.line_number or -1)
        if entry is None or _change_is_auto_excluded(change) or _all_auto_fields_manual(change):
            result.append(change)
            continue
        result.append(_apply_rule_dynamic_change(change, entry, newnum_by_line, old_mora_by_line, new_mora_by_field, new_prefix_mora_by_field, alias_dup, wav_dup, call_key_dup, options))
    return result


# 順序一覧設定を並べ替える
def _sort_orders_from_options(options: PreviewOptions) -> tuple[SortKeyOrder, ...]:
    allowed_keys = {"filename", "alias", "pitch", "mora", "prefix", "suffix", "old_order", "usage"}
    if options.sort_orders:
        return tuple(
            SortKeyOrder(order.key, normalize_sort_direction(order.direction).value)
            for order in options.sort_orders
            if order.key in allowed_keys
        )
    fallback_direction = SortDirection.DESC.value if options.sort_descending else SortDirection.ASC.value
    return tuple(
        SortKeyOrder(key, fallback_direction)
        for key in options.sort_keys
        if key in allowed_keys
    )


# 音階並べ替えが必要か判定する
def _sort_uses_pitch(options: PreviewOptions) -> bool:
    return any(order.key == "pitch" for order in _sort_orders_from_options(options))


# 音階並べ替え用の音高情報を補完する
def _with_pitch_sort_metadata(
    changes: list[ChangeRow],
    entries: list[OtoEntry],
    options: PreviewOptions,
    *,
    oto_path: str | Path | None,
    mrq_path: str | Path | None,
) -> list[ChangeRow]:
    if not _sort_uses_pitch(options) or oto_path is None:
        return changes
    if all(change.frequency is not None for change in changes if change.line_number is not None):
        return changes

    entries_by_line = {entry.line_number: entry for entry in entries}
    try:
        pitch_index, _label = _load_pitch_index(oto_path, entries, mrq_path, options)
    except Exception:
        return changes

    result: list[ChangeRow] = []
    pitch_by_line: dict[int, dict] = {}
    for change in changes:
        if change.line_number is None or change.frequency is not None:
            result.append(change)
            continue
        entry = entries_by_line.get(change.line_number)
        if entry is None:
            result.append(change)
            continue
        pitch = pitch_by_line.setdefault(
            change.line_number,
            estimate_pitch_for_entry(entry, pitch_index, note_config=options.note_config),
        )
        frequency = pitch.get("frequency")
        result.append(
            replace(
                change,
                frequency=frequency if isinstance(frequency, (int, float)) else None,
                note=str(pitch.get("note") or change.note or ""),
                status=change.status or _normalized_status(str(pitch.get("status") or "")),
            )
        )
    return result


# 呼び出しキー候補を並べ替え用に取り出す
def _call_key_sort_candidates(change: ChangeRow) -> list[str]:
    candidates = []
    if change.new_alias:
        candidates.append(change.new_alias)
    wav = wav_key(change.new_wav or change.old_wav)
    if wav:
        candidates.append(wav)
    return candidates


# 呼び出しキーの分解値を取り出す
def _call_key_part_sort_value(change: ChangeRow, part: str, excluded_sort_moras: set[str]) -> str:
    for candidate in _call_key_sort_candidates(change):
        parts = split_alias(candidate)
        mora = parts.mora
        if mora and mora not in excluded_sort_moras:
            if part == "mora":
                return mora
            if part == "prefix":
                return parts.prefix
            if part == "suffix":
                return parts.suffix
    return ""


# 文字列の並べ替え値を変換する
def _sort_comparable_value(value, text_order: SortTextOrder | None):
    if isinstance(value, str) and text_order is not None:
        if value == "":
            return ((2, ""),)
        return text_order.key(value)
    if isinstance(value, str):
        if value == "":
            return "\U0010ffff"
        return value.casefold()
    return value


# 値を並べ替える
def _sort_value_for(change: ChangeRow, key: str, excluded_sort_moras: set[str]):
    if key == "filename":
        return wav_key(change.new_wav or change.old_wav)
    if key == "alias":
        return change.new_alias or change.old_alias
    if key == "pitch":
        return float("inf") if change.frequency is None else change.frequency
    if key == "mora":
        return _call_key_part_sort_value(change, "mora", excluded_sort_moras)
    if key == "prefix":
        return _call_key_part_sort_value(change, "prefix", excluded_sort_moras)
    if key == "suffix":
        return _call_key_part_sort_value(change, "suffix", excluded_sort_moras)
    if key == "old_order":
        return change.old_order_id if change.old_order_id is not None else change.line_number or 0
    if key == "usage":
        return change.usage_count
    return ""


# 変更一覧並べ替えを処理する
def _compare_changes_for_sort(
    left: ChangeRow,
    right: ChangeRow,
    sort_orders: tuple[SortKeyOrder, ...],
    excluded_sort_moras: set[str],
    text_order: SortTextOrder | None,
) -> int:
    for order in sort_orders:
        left_value = _sort_comparable_value(_sort_value_for(left, order.key, excluded_sort_moras), text_order)
        right_value = _sort_comparable_value(_sort_value_for(right, order.key, excluded_sort_moras), text_order)
        if left_value == right_value:
            continue
        direction = -1 if normalize_sort_direction(order.direction) == SortDirection.DESC else 1
        return direction * (-1 if left_value < right_value else 1)
    left_order = left.old_order_id if left.old_order_id is not None else left.line_number or 0
    right_order = right.old_order_id if right.old_order_id is not None else right.line_number or 0
    return -1 if left_order < right_order else (1 if left_order > right_order else 0)


# 順序を反映する
def _apply_sorted_order_ids(changes: list[ChangeRow], sort_orders: tuple[SortKeyOrder, ...], options: PreviewOptions) -> list[ChangeRow]:
    excluded_sort_moras = _mora_set(options.key_warning_config.excluded_moras)
    text_order = load_otolist_order(options.sort_order_path)
    sorted_changes = sorted(
        changes,
        key=cmp_to_key(lambda left, right: _compare_changes_for_sort(left, right, sort_orders, excluded_sort_moras, text_order)),
    )
    return [
        replace(
            change,
            new_order_id=index,
            changed=change.changed,
            reason="sorted" if not change.changed and change.old_order_id != index else change.reason,
        )
        for index, change in enumerate(sorted_changes, start=1)
    ]


# 並べ替えを反映する
def _apply_sort(changes: list[ChangeRow], options: PreviewOptions) -> list[ChangeRow]:
    sort_orders = _sort_orders_from_options(options)
    if not sort_orders:
        return changes
    return _apply_sorted_order_ids(changes, sort_orders, options)


# 変更音高行情報を処理する
def _change_from_pitch_row(row: RewritePreviewRow) -> ChangeRow:
    normalized_status = _normalized_status(row.pitch_status)
    origin_status = normalized_status if normalized_status in {"no_freq_src", "invalid_freq", "no_f0"} else ("system" if row.changed else "")
    return ChangeRow(
        line_number=row.line_number,
        old_wav=row.wav_name,
        new_wav=row.wav_name,
        old_alias=row.old_alias,
        new_alias=row.new_alias,
        old_order_id=row.line_number,
        new_order_id=row.line_number,
        source_alias=row.source_alias,
        note=row.note or "",
        frequency=row.frequency,
        status=normalized_status,
        origin_status=origin_status,
        changed=row.changed,
        reason=row.rewrite_reason,
    )


# 状態を処理する
def _normalized_status(status: str) -> str:
    labels = {
        "": "",
        "window": "system",
        "system": "system",
        "system_fallback": "system",
        "fallback_full_wav": "no_f0",
        "no_valid_f0": "no_f0",
        "no_freq": "no_f0",
        "missing": "no_freq_src",
        "missing_mrq_record": "no_freq_src",
        "missing_frequency": "no_freq_src",
        "EXCLUDE": "exclude",
        "excluded": "exclude",
    }
    return labels.get(status, status)


# 対象一覧を処理する
def _replacement_targets(entry: OtoEntry, rule: ReplacementRule, options: PreviewOptions) -> tuple[str, ...]:
    target = "all" if rule.target == "both" else rule.target
    invalid_moras = options.key_warning_config.excluded_moras
    alias_valid = bool(entry.alias) and _mora_is_allowed(pronunciation_mora(entry.alias), invalid_moras)
    wav_valid = _mora_is_allowed(pronunciation_mora(wav_key(entry.wav_name)), invalid_moras)
    if target == "all":
        return tuple(field for field, valid in (("alias", alias_valid), ("wav", wav_valid)) if valid)
    if target == "call_key":
        return _call_key_edit_targets(entry, options)
    if target == "alias":
        return ("alias",) if alias_valid else ()
    if target == "wav":
        return ("wav",) if wav_valid else ()
    raise ValueError(f"Unsupported replacement target: {rule.target}")


# ルール一覧を反映する
def _apply_replacement_rules(entry: OtoEntry, rules: tuple[ReplacementRule, ...], options: PreviewOptions) -> ChangeRow:
    new_wav = entry.wav_name
    new_alias = entry.alias
    for rule in rules:
        targets = _replacement_targets(entry, rule, options)
        if "alias" in targets:
            new_alias = _apply_replacement(new_alias, rule, entry)
        if "wav" in targets:
            new_wav = _apply_replacement(new_wav, rule, entry)

    changed = new_wav != entry.wav_name or new_alias != entry.alias
    order_id = _entry_order_id(entry)
    return ChangeRow(
        line_number=entry.line_number,
        old_wav=entry.wav_name,
        new_wav=new_wav,
        old_alias=entry.alias,
        new_alias=new_alias,
        old_order_id=order_id,
        new_order_id=order_id,
        source_alias=entry.alias,
        origin_status="system" if changed else "",
        auto_edit_fields=tuple(
            dict.fromkeys(
                field
                for rule in rules
                for field in _replacement_targets(entry, rule, options)
                if _field_auto_edit_enabled(field, options)
            )
        ),
        changed=changed,
        reason="replace" if changed else "replace_noop",
    )


# 音高行情報を処理する
def _pitch_append_row(entry: OtoEntry, pitch: dict, options: PreviewOptions) -> RewritePreviewRow:
    note = pitch.get("note")
    frequency = pitch.get("frequency")
    alias_change = build_alias_change(entry, note, options.alias_config)
    return RewritePreviewRow(
        line_number=entry.line_number,
        wav_name=entry.wav_name,
        old_alias=entry.alias,
        source_alias=alias_change.source_alias,
        new_alias=alias_change.new_alias,
        frequency=frequency if isinstance(frequency, (int, float)) else None,
        note=str(note or ""),
        valid_frame_count=int(pitch.get("valid_frame_count") or 0),
        pitch_status=str(pitch.get("status") or ""),
        rewrite_reason=alias_change.reason,
        changed=alias_change.changed,
    )


# 音高変更を処理する
def _pitch_append_skipped_change(row: RewritePreviewRow, reason: str) -> ChangeRow:
    return replace(
        _change_from_pitch_row(row),
        new_alias=row.old_alias,
        changed=False,
        reason=reason,
    )


# 音高除外対象変更を処理する
def _pitch_append_excluded_change(row: RewritePreviewRow) -> ChangeRow:
    return replace(
        _change_from_pitch_row(row),
        new_alias=row.old_alias,
        changed=False,
        status="exclude",
        origin_status="exclude",
        reason="excluded",
    )


# 音高変更を処理する
def _pitch_append_edit_change(entry: OtoEntry, row: RewritePreviewRow, options: PreviewOptions, wav_has_alias: dict[str, bool]) -> ChangeRow:
    change = _change_from_pitch_row(row)
    new_wav = entry.wav_name
    seeded_alias = wav_key(entry.wav_name) if not entry.alias and options.add_alias_for_unused_wav else ""
    fields = set(_auto_edit_fields(entry, options, seeded_alias=seeded_alias))
    no_frequency_note = not row.note and _normalized_status(row.pitch_status) in {"no_freq_src", "invalid_freq", "no_f0"}
    should_edit_wav = bool((row.note or no_frequency_note) and "wav" in fields)
    if should_edit_wav:
        new_wav = _pitch_wav_name(entry, row.note or "", options)
    if not entry.alias and not options.add_alias_for_unused_wav:
        return replace(
            change,
            new_alias=entry.alias,
            new_wav=new_wav,
            changed=(new_wav != entry.wav_name),
            reason="empty_alias_wav_note" if new_wav != entry.wav_name else "empty_alias_no_alias_added",
            auto_edit_fields=tuple(field for field in ("alias", "wav") if field in fields),
            origin_status="system" if new_wav != entry.wav_name else change.origin_status,
        )
    if row.note or no_frequency_note:
        change = replace(change, new_alias=_pitch_alias_name(entry, row.note or "", options, seeded_alias=seeded_alias))
    if "alias" not in fields:
        change = replace(change, new_alias=entry.alias, changed=(new_wav != entry.wav_name))
    return replace(
        change,
        new_wav=new_wav,
        changed=(change.changed or new_wav != entry.wav_name or change.new_alias != entry.alias),
        reason="mismatched_wav_mora" if new_wav != entry.wav_name else change.reason,
        auto_edit_fields=tuple(field for field in ("alias", "wav") if field in fields),
        origin_status="system" if change.changed or new_wav != entry.wav_name or change.new_alias != entry.alias else change.origin_status,
    )


# 音高変更行を処理する
def _pitch_append_change_for_entry(
    entry: OtoEntry,
    pitch: dict,
    options: PreviewOptions,
    wav_has_alias: dict[str, bool],
) -> ChangeRow:
    row = _pitch_append_row(entry, pitch, options)
    should_edit, reason = _should_auto_edit_entry(entry, options, wav_has_alias)
    if not should_edit:
        return _pitch_append_skipped_change(row, reason)
    exclusion_kind = _exclusion_kind(
        entry,
        options.exclude_config,
        pitch_status=row.pitch_status,
        note=row.note or None,
        frequency=row.frequency,
    )
    if exclusion_kind == "explicit":
        return _pitch_append_excluded_change(row)
    if exclusion_kind == "unvoiced":
        return _no_frequency_change(entry, row.pitch_status, note=row.note, frequency=row.frequency)
    return _pitch_append_edit_change(entry, row, options, wav_has_alias)


# 音高変更一覧を生成する
def _preview_pitch_append_changes(
    oto_path: str | Path,
    mrq_path: str | Path | None,
    options: PreviewOptions,
) -> tuple[list[ChangeRow], str]:
    lines, encoding = parse_oto_file(oto_path)
    entry_list = iter_entries(lines)
    pitch_index, _label = _load_pitch_index(oto_path, entry_list, mrq_path, options)
    wav_has_alias = _wav_has_alias_by_name(entry_list)
    changes = [
        _pitch_append_change_for_entry(
            entry,
            estimate_pitch_for_entry(entry, pitch_index, note_config=options.note_config),
            options,
            wav_has_alias,
        )
        for entry in entry_list
    ]
    return _finalize_preview_changes(changes, lines, options, oto_path=oto_path, mrq_path=mrq_path), encoding


# 番号付き値を処理する
def _numbered_value_for_source(source: str, index: int, options: PreviewOptions) -> str:
    parts = split_alias(source)
    if options.alias_config.strip_suffix:
        base = parts.mora
        if options.alias_config.keep_prefix and parts.prefix:
            base = parts.prefix + base
    else:
        base = source
    if not base:
        return source
    if index == 1 and not options.number_first_alias:
        return base
    return f"{base}{options.alias_config.separator}{index}"


# 番号付きwav行を処理する
def _numbered_wav_for_entry(entry: OtoEntry, source: str, index: int, options: PreviewOptions) -> str:
    path = Path(entry.wav_name)
    numbered_stem = _numbered_value_for_source(source, index, options)
    parent = str(path.parent)
    numbered_name = numbered_stem + path.suffix
    if parent in {"", "."}:
        return numbered_name
    return str(Path(parent) / numbered_name)


# 番号付け用の元名を返す
def _numbering_source_for_field(entry: OtoEntry, field: str, seeded_alias: str) -> str:
    if field == "wav":
        return wav_key(entry.wav_name)
    return seeded_alias or source_alias_for_entry(entry)


# 通し番号を反映する
def _apply_numbering_values(
    changes: list[ChangeRow],
    entries_by_line: dict[int, OtoEntry],
    options: PreviewOptions,
) -> list[ChangeRow]:
    if options.mode != "numbering":
        return changes

    ordered = _ordered_changes_for_numbering(changes)
    field_priority = _numbering_field_priority(options)
    order_by_line = {change.line_number: index for index, change in enumerate(ordered, start=1) if change.line_number is not None}
    items: list[tuple[str, int, int, str, str, ChangeRow, OtoEntry]] = []

    for change in ordered:
        if change.line_number is None:
            continue
        entry = entries_by_line.get(change.line_number)
        if entry is None:
            continue
        seeded_alias = wav_key(entry.wav_name) if not entry.alias and options.add_alias_for_unused_wav else ""
        for field in change.auto_edit_fields:
            if _field_is_manual(change, field):
                continue
            source = _numbering_source_for_field(entry, field, seeded_alias)
            pronunciation = pronunciation_mora(source)
            if not pronunciation:
                continue
            items.append((pronunciation, field_priority[field], order_by_line.get(change.line_number, 0), field, source, change, entry))

    count_by_key: dict[tuple[str, ...], int] = {}
    updates: dict[tuple[int | None, str], str] = {}
    wav_group_updates: dict[str, str] = {}
    for pronunciation, _field_rank, _order, field, source, change, entry in sorted(
        items,
        key=lambda item: (item[0], item[1], item[2], item[3], item[4]),
    ):
        count_key = (pronunciation,) if _numbering_order_mode(options) != "separate" else (field, pronunciation)
        count_by_key[count_key] = count_by_key.get(count_key, 0) + 1
        index = count_by_key[count_key]
        if field == "alias":
            updates[(change.line_number, "alias")] = _numbered_value_for_source(source, index, options)
        elif field == "wav":
            wav_group_updates.setdefault(_norm_wav_name(entry.wav_name), _numbered_wav_for_entry(entry, source, index, options))

    result: list[ChangeRow] = []
    for change in changes:
        new_alias = updates.get((change.line_number, "alias"), change.new_alias)
        new_wav = wav_group_updates.get(_norm_wav_name(change.old_wav), change.new_wav)
        result.append(
            replace(
                change,
                new_alias=new_alias,
                new_wav=new_wav,
                changed=(new_wav != change.old_wav or new_alias != change.old_alias),
                reason="numbering" if new_wav != change.old_wav or new_alias != change.old_alias else change.reason,
                origin_status="system" if new_wav != change.old_wav or new_alias != change.old_alias else change.origin_status,
            )
        )
    return result


# 変更一覧を生成する
def _preview_numbering_changes(
    oto_path: str | Path,
    options: PreviewOptions,
) -> tuple[list[ChangeRow], str]:
    lines, encoding = parse_oto_file(oto_path)
    entry_list = iter_entries(lines)
    wav_has_alias = _wav_has_alias_by_name(entry_list)
    changes: list[ChangeRow] = []
    for entry in entry_list:
        should_edit, reason = _should_auto_edit_entry(entry, options, wav_has_alias)
        order_id = _entry_order_id(entry)
        if not should_edit:
            changes.append(_noop_change(entry, reason))
            continue
        if _is_explicitly_excluded(entry, options.exclude_config):
            changes.append(_excluded_change(entry))
            continue
        seeded_alias = wav_key(entry.wav_name) if not entry.alias and options.add_alias_for_unused_wav else ""
        targets = _auto_edit_fields(entry, options, seeded_alias=seeded_alias)
        if not targets:
            changes.append(_noop_change(entry, "numbering_no_target"))
            continue
        new_alias = (seeded_alias or entry.alias) if "alias" in targets else entry.alias
        new_wav = entry.wav_name
        changes.append(
            ChangeRow(
                line_number=entry.line_number,
                old_wav=entry.wav_name,
                new_wav=new_wav,
                old_alias=entry.alias,
                new_alias=new_alias,
                old_order_id=order_id,
                new_order_id=order_id,
                source_alias=seeded_alias or source_alias_for_entry(entry),
                origin_status="system" if new_alias != entry.alias else "",
                auto_edit_fields=targets,
                changed=(new_wav != entry.wav_name or new_alias != entry.alias),
                reason="numbering_seed" if new_alias != entry.alias else "numbering_noop",
            )
        )
    return _finalize_preview_changes(changes, lines, options, oto_path=oto_path), encoding


# 変更一覧を生成する
def _preview_replace_changes(
    oto_path: str | Path,
    options: PreviewOptions,
) -> tuple[list[ChangeRow], str]:
    lines, encoding = parse_oto_file(oto_path)
    entry_list = iter_entries(lines)
    wav_has_alias = _wav_has_alias_by_name(entry_list)
    changes = []
    for entry in entry_list:
        should_edit, reason = _should_auto_edit_entry(entry, options, wav_has_alias)
        if not should_edit:
            changes.append(_noop_change(entry, reason))
            continue
        if _is_explicitly_excluded(entry, options.exclude_config):
            changes.append(_excluded_change(entry))
            continue
        working_entry = (
            replace(entry, alias=wav_key(entry.wav_name))
            if not entry.alias and options.add_alias_for_unused_wav
            else entry
        )
        change = _apply_replacement_rules(working_entry, options.replacement_rules, options)
        if working_entry is not entry:
            change = replace(
                change,
                old_alias=entry.alias,
                source_alias=source_alias_for_entry(working_entry),
                changed=(change.new_wav != entry.wav_name or change.new_alias != entry.alias),
                reason="replace" if change.new_wav != entry.wav_name or change.new_alias != entry.alias else "replace_noop",
            )
        changes.append(change)
    return _finalize_preview_changes(changes, lines, options, oto_path=oto_path), encoding


# ルール音高行を処理する
def _rule_based_pitch_by_line(
    oto_path: str | Path,
    entries: list[OtoEntry],
    mrq_path: str | Path | None,
    options: PreviewOptions,
) -> dict[int, dict]:
    pitch_by_line: dict[int, dict] = {}
    if mrq_path or options.frequency_source in {"frq", "utau_frq", "pmk", "utau_pmk", "auto", "auto_f0", "estimated"}:
        mrq_index, _label = _load_pitch_index(oto_path, entries, mrq_path, options)
        for entry in entries:
            pitch_by_line[entry.line_number] = estimate_pitch_for_entry(entry, mrq_index, note_config=options.note_config)
    return pitch_by_line


# ルール値一覧行を処理する
def _rule_based_values_for_entry(entry: OtoEntry, note: str, options: PreviewOptions) -> tuple[str, str]:
    order_id = _entry_order_id(entry)
    new_alias = entry.alias
    new_wav = entry.wav_name
    alias_template = _rule_template_for_field(entry, "alias", options)
    wav_template = _rule_template_for_field(entry, "wav", options)
    if alias_template:
        new_alias = _expand_replacement_template(
            alias_template,
            entry,
            source_text=entry.alias,
            note=note,
            oldnum=order_id,
            newnum=order_id,
            number_first=options.number_first_alias,
        )
    if wav_template:
        new_wav = _rule_wav_name(
            entry,
            wav_template,
            source_text=wav_key(entry.wav_name),
            note=note,
            oldnum=order_id,
            newnum=order_id,
            number_first=options.number_first_alias,
        )
    return new_alias, new_wav


# ルール変更行を処理する
def _rule_based_change_for_entry(
    entry: OtoEntry,
    pitch: dict,
    options: PreviewOptions,
    wav_has_alias: dict[str, bool],
) -> ChangeRow:
    note = str(pitch.get("note") or "")
    frequency = pitch.get("frequency")
    status = str(pitch.get("status") or "")
    should_edit, reason = _should_auto_edit_entry(entry, options, wav_has_alias)
    if not should_edit:
        return _noop_change(entry, reason)
    exclusion_kind = _exclusion_kind(entry, options.exclude_config, pitch_status=status, note=note or None, frequency=frequency)
    if exclusion_kind == "explicit":
        return _excluded_change(entry, note=note, frequency=frequency)
    if exclusion_kind == "unvoiced":
        return _no_frequency_change(entry, status, note=note, frequency=frequency)
    order_id = _entry_order_id(entry)
    new_alias, new_wav = _rule_based_values_for_entry(entry, note, options)
    fields = tuple(
        field
        for field in ("alias", "wav")
        if _rule_template_for_field(entry, field, options)
    )
    return ChangeRow(
        line_number=entry.line_number,
        old_wav=entry.wav_name,
        new_wav=new_wav,
        old_alias=entry.alias,
        new_alias=new_alias,
        old_order_id=order_id,
        new_order_id=order_id,
        source_alias=source_alias_for_entry(entry),
        note=note,
        frequency=frequency,
        status=_normalized_status(status),
        origin_status="system" if new_wav != entry.wav_name or new_alias != entry.alias else _normalized_status(status),
        auto_edit_fields=fields,
        changed=(new_wav != entry.wav_name or new_alias != entry.alias),
        reason="rule_based" if new_wav != entry.wav_name or new_alias != entry.alias else "rule_based_noop",
    )


# ルール変更一覧を生成する
def _preview_rule_based_changes(
    oto_path: str | Path,
    mrq_path: str | Path | None,
    options: PreviewOptions,
) -> tuple[list[ChangeRow], str]:
    lines, encoding = parse_oto_file(oto_path)
    entry_list = iter_entries(lines)
    wav_has_alias = _wav_has_alias_by_name(entry_list)
    pitch_by_line = _rule_based_pitch_by_line(oto_path, entry_list, mrq_path, options)
    changes = [
        _rule_based_change_for_entry(
            entry,
            pitch_by_line.get(entry.line_number, {}),
            options,
            wav_has_alias,
        )
        for entry in entry_list
    ]
    return _finalize_preview_changes(changes, lines, options, oto_path=oto_path, mrq_path=mrq_path), encoding


# 変更一覧を生成する
def _preview_none_changes(oto_path: str | Path, options: PreviewOptions) -> tuple[list[ChangeRow], str]:
    lines, encoding = parse_oto_file(oto_path)
    changes = []
    for entry in iter_entries(lines):
        reason = "excluded" if _is_explicitly_excluded(entry, options.exclude_config) else "noop"
        changes.append(_excluded_change(entry) if reason == "excluded" else _noop_change(entry, reason))
    return _finalize_preview_changes(changes, lines, options, oto_path=oto_path), encoding


# CSV変更一覧を生成する
def _preview_csv_changes(oto_path: str | Path, csv_path: str | Path, options: PreviewOptions) -> tuple[list[ChangeRow], str]:
    csv_changes = read_changes_csv(csv_path)
    if options.csv_invert:
        csv_changes = invert_changes(csv_changes)
    csv_changes = _apply_csv_read_columns(csv_changes, options.csv_read_columns)
    lines, encoding = parse_oto_file(oto_path)
    alias_by_line, wav_by_line, results = build_oto_update_maps_from_changes(
        lines,
        csv_changes,
        prefer_line_number=False,
        skip_noop=False,
    )
    matched_by_line = {result.line_number: result for result in results if result.line_number is not None}

    changes = []
    for entry in iter_entries(lines):
        result = matched_by_line.get(entry.line_number)
        order_id = _entry_order_id(entry)
        if not result or result.status != "matched":
            changes.append(_noop_change(entry, "csv_noop"))
            continue
        if _is_explicitly_excluded(entry, options.exclude_config):
            changes.append(_excluded_change(entry))
            continue
        new_alias = alias_by_line.get(entry.line_number, entry.alias)
        new_wav = wav_by_line.get(entry.line_number, entry.wav_name)
        incoming = result.change
        changes.append(
            ChangeRow(
                line_number=entry.line_number,
                old_wav=entry.wav_name,
                new_wav=new_wav,
                old_alias=entry.alias,
                new_alias=new_alias,
                old_order_id=incoming.old_order_id if incoming.old_order_id is not None else order_id,
                new_order_id=incoming.new_order_id if incoming.new_order_id is not None else order_id,
                source_alias=incoming.source_alias or entry.alias,
                note=incoming.note,
                frequency=incoming.frequency,
                status=result.status,
                changed=(
                    new_wav != entry.wav_name
                    or new_alias != entry.alias
                ),
                reason=incoming.reason or "csv",
            )
        )

    unmatched = [result for result in results if result.status != "matched"]
    for result in unmatched:
        changes.append(replace(result.change, changed=False, status=result.status, reason=result.message or result.status))
    return _finalize_csv_preview_changes(changes, lines, options, oto_path=oto_path), encoding


# CSV読み込み対象列を反映する
def _apply_csv_read_columns(changes: list[ChangeRow], columns: tuple[str, ...]) -> list[ChangeRow]:
    selected = set(columns)
    result: list[ChangeRow] = []
    for change in changes:
        new_wav = change.new_wav if "new_wav" in selected else change.old_wav
        new_alias = change.new_alias if "new_alias" in selected else change.old_alias
        new_order_id = change.new_order_id if "new_order_id" in selected else change.old_order_id
        result.append(
            replace(
                change,
                new_wav=new_wav,
                new_alias=new_alias,
                new_order_id=new_order_id,
                changed=(
                    change.old_wav != new_wav
                    or change.old_alias != new_alias
                ),
            )
        )
    return result


# 処理モードに応じたPreviewを生成する
def preview_changes(
    oto_path: str | Path,
    *,
    options: PreviewOptions | None = None,
    mrq_path: str | Path | None = None,
    csv_path: str | Path | None = None,
) -> tuple[list[ChangeRow], str]:
    options = options or PreviewOptions()
    if options.mode == "pitch_append":
        return _preview_pitch_append_changes(oto_path, mrq_path, options)
    if options.mode == "replace":
        return _preview_replace_changes(oto_path, options)
    if options.mode == "numbering":
        return _preview_numbering_changes(oto_path, options)
    if options.mode == "csv":
        if csv_path is None:
            raise ValueError("csv_path is required for csv mode")
        return _preview_csv_changes(oto_path, csv_path, options)
    if options.mode == "none":
        return _preview_none_changes(oto_path, options)
    if options.mode == "rule_based":
        return _preview_rule_based_changes(oto_path, mrq_path, options)
    raise ValueError(f"Unsupported preview mode: {options.mode}")


# Preview行を再検証する
def validate_preview_changes(
    oto_path: str | Path,
    changes: list[ChangeRow],
    *,
    options: PreviewOptions | None = None,
) -> tuple[list[ChangeRow], str]:
    options = options or PreviewOptions()
    lines, encoding = parse_oto_file(oto_path)
    changes = [
        replace(change, severity="ok", diagnostics=(), warnings=(), warning_cells=())
        for change in changes
    ]
    entries = iter_entries(lines)
    entries_by_line = {entry.line_number: entry for entry in entries}
    if options.mode == "pitch_append" and options.renumber_after_order_change:
        changes = _pitch_append_base_values_for_renumbering(changes, entries_by_line, options)
        changes = _with_cross_field_duplicate_numbers(changes, options)
    if options.mode == "numbering" and options.renumber_after_order_change:
        changes = _apply_numbering_values(changes, entries_by_line, options)
    if options.mode == "rule_based":
        changes = _apply_rule_based_dynamic_templates(changes, entries, options)
        changes = _sanitize_preview_values(changes)
    changes = _apply_hidden_wav_prefix_for_new_aliases(changes, entries_by_line, options)
    changes = _apply_usage_counts(changes, options)
    changes = _sync_auto_wav_groups(changes)
    changes = _with_key_warnings(changes, lines, options)
    changes = _with_invalid_wav_name_warnings(changes)
    changes = _with_wav_collision_warnings(changes)
    changes = _with_same_old_wav_consistency_warnings(changes)
    return changes, encoding


# oto.iniファイルを処理する
def rewrite_oto_file(
    oto_path: str | Path,
    mrq_path: str | Path,
    output_path: str | Path,
    alias_config: AliasRewriteConfig | None = None,
    note_config: NoteMappingConfig | None = None,
) -> tuple[Path, list[RewritePreviewRow]]:
    rows, encoding = preview_rewrite(oto_path, mrq_path, alias_config, note_config)
    lines, _ = parse_oto_file(oto_path, encoding=encoding)
    alias_by_line = {row.line_number: row.new_alias for row in rows if row.changed}
    written_path = write_oto_copy(lines, alias_by_line, output_path, encoding=encoding)
    return written_path, rows


# PreviewCSVを書き出す
def export_rewrite_preview_csv(
    oto_path: str | Path,
    mrq_path: str | Path,
    csv_path: str | Path,
    alias_config: AliasRewriteConfig | None = None,
    note_config: NoteMappingConfig | None = None,
    *,
    changed_only: bool = False,
) -> tuple[Path, list[ChangeRow]]:
    rows, _ = preview_rewrite(oto_path, mrq_path, alias_config, note_config)
    changes = changes_from_preview_rows(rows)
    written_path = write_changes_csv(changes, csv_path, changed_only=changed_only)
    return written_path, changes


# oto.iniファイルCSVを処理する
def rewrite_oto_file_from_csv(
    oto_path: str | Path,
    csv_path: str | Path,
    output_path: str | Path,
) -> tuple[Path, list[CsvApplyResult]]:
    return apply_changes_csv_to_oto_file(oto_path, csv_path, output_path)


# 変更CSVファイル一覧を統合する
def merge_change_csv_files(
    existing_csv_path: str | Path,
    new_csv_path: str | Path,
    output_csv_path: str | Path,
    *,
    changed_only: bool = False,
) -> tuple[Path, list[ChangeRow]]:
    existing = read_changes_csv(existing_csv_path)
    new = read_changes_csv(new_csv_path)
    merged = merge_changes(existing, new)
    written_path = write_changes_csv(merged, output_csv_path, changed_only=changed_only)
    return written_path, merged
