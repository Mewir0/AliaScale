from __future__ import annotations

from enum import Enum


# wavモードを保持する
class WavEditMode(str, Enum):
    ALLOW = "allow"
    REPRESENTATIVE = "representative"
    EMPTY_ALIAS_ONLY = "empty_alias_only"
    MANUAL_ONLY = "manual_only"
    DISABLED = "disabled"


# 並べ替え方向を保持する
class SortDirection(str, Enum):
    ASC = "asc"
    DESC = "desc"


# 警告警告レベルを保持する
class WarningSeverity(str, Enum):
    OK = "ok"
    WARNING = "warning"
    DANGER = "danger"


# wavモードを正規化する
def normalize_wav_edit_mode(value: str | WavEditMode | None) -> WavEditMode:
    if isinstance(value, WavEditMode):
        if value in {WavEditMode.REPRESENTATIVE, WavEditMode.EMPTY_ALIAS_ONLY}:
            return WavEditMode.ALLOW
        return value
    if value in {None, ""}:
        return WavEditMode.ALLOW
    mode = WavEditMode(str(value))
    if mode in {WavEditMode.REPRESENTATIVE, WavEditMode.EMPTY_ALIAS_ONLY}:
        return WavEditMode.ALLOW
    return mode


# wavを処理する
def wav_auto_edit_enabled(value: str | WavEditMode | None) -> bool:
    mode = normalize_wav_edit_mode(value)
    return mode == WavEditMode.ALLOW


# wavを処理する
def wav_representative_edit_enabled(value: str | WavEditMode | None) -> bool:
    return wav_auto_edit_enabled(value)


# wavaliasを処理する
def wav_empty_alias_only_edit_enabled(value: str | WavEditMode | None) -> bool:
    return normalize_wav_edit_mode(value) == WavEditMode.EMPTY_ALIAS_ONLY


# 並べ替え方向を正規化する
def normalize_sort_direction(value: str | SortDirection | None) -> SortDirection:
    if isinstance(value, SortDirection):
        return value
    if value in {None, ""}:
        return SortDirection.ASC
    return SortDirection(str(value))
