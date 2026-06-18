from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
from pathlib import Path
from typing import Any

from alias_rewrite import ChangeRow, WarningMessage
from alias_rewrite.options import SortDirection, WarningSeverity, WavEditMode


@dataclass(frozen=True)
# 並べ替えを保持する
class SortSpec:
    key: str
    direction: str = SortDirection.ASC.value


@dataclass(frozen=True)
# ルール画面用データを保持する
class ReplacementRuleDto:
    old: str
    new: str
    target: str = "alias"
    use_regex: bool = False


@dataclass(frozen=True)
# 除外設定を保持する
class ExcludeSettings:
    exclude_unvoiced: bool = False
    exclude_no_f0: bool = False
    exclude_no_freq_src: bool = False
    exclude_empty_params: bool = True
    mode: str = "none"
    patterns: tuple[str, ...] = ()


@dataclass(frozen=True)
# 設定を保持する
class RewriteSettings:
    mode: str = "pitch_append"
    frequency_source: str = "mrq"
    separator: str = "_"
    strip_suffix: bool = True
    keep_prefix: bool = True
    missing_pitch: str = "keep"
    edit_scope: str = "call_key"
    alias_target: str = "call_key"
    add_alias_for_unused_wav: bool = False
    edit_mismatched_wav_mora: bool = False
    number_first_alias: bool = False
    rounding_mode: str = "semitone"
    rounding_candidates: tuple[str, ...] = ()
    replacement_rules: tuple[ReplacementRuleDto, ...] = ()
    csv_invert: bool = False
    csv_read_columns: tuple[str, ...] = ("new_wav", "new_alias", "new_order_id")
    rule_scope: str = "alias_wav"
    rule_alias_template: str = ""
    rule_wav_template: str = ""
    rule_call_key_template: str = ""
    exclude: ExcludeSettings = field(default_factory=ExcludeSettings)
    sort: tuple[SortSpec, ...] = ()
    wav_edit_mode: str = WavEditMode.ALLOW.value
    alias_edit_mode: str = WavEditMode.ALLOW.value
    prefix_underscore_for_new_alias: bool = False


@dataclass(frozen=True)
# 設定を保持する
class AppSettings:
    backup: bool = True
    backup_mode: str = "voice_dir"
    backup_root: str = "backup"
    backup_max_count_enabled: bool = False
    backup_max_count: int = 10
    write_csv: bool = True
    merge_csv: bool = False
    csv_path: str = ""
    update_ust: bool = False
    show_full_ust_path: bool = False
    strict_voice_match: bool = False
    utau_exe_path: str = ""
    rename_files: bool = True
    wav_edit_mode: str = WavEditMode.ALLOW.value
    alias_edit_mode: str = WavEditMode.ALLOW.value
    block_on_danger: bool = True
    theme: str = "defoko_dark"
    ui_scale: float = 1.0
    excluded_call_key_moras: tuple[str, ...] = ("_",)
    auto_wav_excluded_moras: tuple[str, ...] = ("_",)
    numbering_order_mode: str = "separate"
    renumber_after_order_change: bool = True
    relax_cannotcall_for_unused_ust_entries: bool = False
    write_debug_log: bool = True
    related_file_patterns: tuple[str, ...] = ("{stem}_wav.frq", "{stem}.wav.llsm", "{stem}_wav.pmk", "{stem}*.hifi.npz")


@dataclass(frozen=True)
# Preview要求を保持する
class PreviewRequest:
    voice_dir: str
    oto_path: str
    mrq_path: str = ""
    frequency_source: str = "mrq"
    csv_path: str = ""
    ust_root: str = ""
    selected_ust_paths: tuple[str, ...] = ()
    ust_selection_known: bool = False
    utau_plugin_temp_path: str = ""
    rewrite: RewriteSettings = field(default_factory=RewriteSettings)
    settings: AppSettings = field(default_factory=AppSettings)


@dataclass(frozen=True)
# Preview行情報画面用データを保持する
class PreviewRowDto:
    line_number: int | None
    old_order_id: int | None
    new_order_id: int | None
    old_wav: str
    new_wav: str
    old_alias: str
    new_alias: str
    source_alias: str = ""
    note: str = ""
    frequency: float | None = None
    status: str = ""
    origin_status: str = ""
    diagnostics: tuple[str, ...] = ()
    changed: bool = False
    reason: str = ""
    severity: str = "ok"
    warnings: tuple[str, ...] = ()
    warning_cells: tuple[str, ...] = ()
    auto_edit_fields: tuple[str, ...] = ()
    manual_edit_fields: tuple[str, ...] = ()
    old_wav_exists: bool = True
    auto_new_wav: str = ""
    auto_new_alias: str = ""
    offset_ms: float = 0.0
    consonant_ms: float = 0.0
    cutoff_ms: float = 0.0
    preutterance_ms: float = 0.0
    overlap_ms: float = 0.0
    play_start_ms: int = 0
    play_end_ms: int = 0
    usage_count: int = 0

    @classmethod
    # 変更を変換する
    def from_change(cls, change: ChangeRow) -> "PreviewRowDto":
        return cls(**asdict(change))

    # 変更を変換する
    def to_change(self) -> ChangeRow:
        data = asdict(self)
        data["warnings"] = tuple(data.get("warnings") or ())
        data["warning_cells"] = tuple(data.get("warning_cells") or ())
        data["diagnostics"] = tuple(data.get("diagnostics") or ())
        data["auto_edit_fields"] = tuple(data.get("auto_edit_fields") or ())
        data["manual_edit_fields"] = tuple(data.get("manual_edit_fields") or ())
        allowed = ChangeRow.__dataclass_fields__
        return ChangeRow(**{key: value for key, value in data.items() if key in allowed})


@dataclass(frozen=True)
# 警告画面用データを保持する
class WarningDto:
    severity: str
    message: str
    line_number: int | None = None
    cells: tuple[str, ...] = ()

    @classmethod
    # 警告を変換する
    def from_warning(cls, warning: WarningMessage) -> "WarningDto":
        return cls(
            severity=warning.severity.value,
            message=warning.message,
            line_number=warning.line_number,
            cells=warning.cells,
        )


@dataclass(frozen=True)
# Preview概要を保持する
class PreviewSummary:
    rows: int
    edits: int
    warnings: int
    danger: int
    can_apply: bool


@dataclass(frozen=True)
# USTを保持する
class UstListItem:
    path: str
    label: str
    checked: bool = True
    replacements: int = 0
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
# Preview応答を保持する
class PreviewResponse:
    rows: tuple[PreviewRowDto, ...]
    summary: PreviewSummary
    warnings: tuple[WarningDto, ...] = ()
    ust_list: tuple[UstListItem, ...] = ()
    information: dict[str, Any] = field(default_factory=dict)
    encoding: str = ""


@dataclass(frozen=True)
# Apply要求を保持する
class ApplyRequest:
    voice_dir: str
    oto_path: str
    rows: tuple[PreviewRowDto, ...]
    mrq_path: str = ""
    frequency_source: str = "mrq"
    ust_root: str = ""
    selected_ust_paths: tuple[str, ...] = ()
    utau_plugin_temp_path: str = ""
    rewrite: RewriteSettings = field(default_factory=RewriteSettings)
    settings: AppSettings = field(default_factory=AppSettings)


@dataclass(frozen=True)
# Apply応答を保持する
class ApplyResponse:
    written_files: tuple[str, ...] = ()
    moved_to_conflict_folder: tuple[str, ...] = ()
    backups: tuple[str, ...] = ()
    csv_path: str = ""
    log_path: str = ""
    warnings: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()


# 画面用データを処理する
def dto_to_dict(value: object) -> dict[str, Any]:
    result = asdict(value)
    return _stringify_paths(result)


# パス一覧を処理する
def _stringify_paths(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: _stringify_paths(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_stringify_paths(item) for item in value]
    if isinstance(value, (WavEditMode, SortDirection, WarningSeverity)):
        return value.value
    return value
