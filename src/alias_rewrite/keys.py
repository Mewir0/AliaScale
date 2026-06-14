from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from .aliases import extract_leading_mora, split_alias_prefix
from .changes import ChangeRow
from .oto import OtoEntry


@dataclass(frozen=True)
# 呼び出しキーを保持する
class CallKey:
    key: str
    line_number: int
    kind: str
    mora: str


@dataclass(frozen=True)
# キー警告を保持する
class KeyWarning:
    code: str
    level: str
    key: str
    line_numbers: tuple[int, ...]
    cells: tuple[str, ...]
    message: str


@dataclass(frozen=True)
# キー警告設定を保持する
class KeyWarningConfig:
    warn_on_resolution_change: bool = True
    excluded_moras: tuple[str, ...] = ("_",)


# wavキーを処理する
def wav_key(wav_name: str) -> str:
    return Path(wav_name).stem


# 発音を処理する
def pronunciation_mora(key: str) -> str:
    _, body = split_alias_prefix(key)
    mora, _ = extract_leading_mora(body)
    return mora


# 呼び出しキー一覧行を処理する
def call_keys_for_entry(entry: OtoEntry) -> tuple[CallKey, ...]:
    keys: list[CallKey] = []
    if entry.alias:
        keys.append(CallKey(entry.alias, entry.line_number, "alias", pronunciation_mora(entry.alias)))
    wav = wav_key(entry.wav_name)
    if wav:
        keys.append(CallKey(wav, entry.line_number, "wav", pronunciation_mora(wav)))
    return tuple(keys)


# 呼び出しキー行を処理する
def effective_call_key_for_entry(entry: OtoEntry) -> str:
    if entry.alias:
        return entry.alias
    return wav_key(entry.wav_name)


# 呼び出しキー候補一覧行を処理する
def call_key_candidates_for_entry(entry: OtoEntry) -> tuple[CallKey, ...]:
    candidates: list[CallKey] = []
    if entry.alias:
        candidates.append(CallKey(entry.alias, entry.line_number, "alias", pronunciation_mora(entry.alias)))
    wav = wav_key(entry.wav_name)
    if wav:
        candidates.append(CallKey(wav, entry.line_number, "wav", pronunciation_mora(wav)))
    return tuple(candidates)


# 除外対象発音を処理する
def _excluded_mora_set(excluded_moras: tuple[str, ...] = ()) -> set[str]:
    return {mora.strip() for mora in excluded_moras if mora.strip()}


# 除外対象呼び出しキーを判定する
def _is_excluded_call_key(call_key: CallKey, excluded_moras: set[str]) -> bool:
    return bool(call_key.mora and call_key.mora in excluded_moras)


# キー対応表を処理する
def callable_key_map(entries: list[OtoEntry], excluded_moras: tuple[str, ...] = ()) -> dict[str, CallKey]:
    """Return keys that can actually call entries, matching UTAU priority.

    UTAU resolves aliases before wav basenames. To reproduce that priority as a
    stable map, collect all non-empty aliases in oto order first, then collect
    all wav basenames in oto order. The first occurrence of each key wins.
    """
    result: dict[str, CallKey] = {}
    excluded = _excluded_mora_set(excluded_moras)
    for entry in entries:
        if not entry.alias:
            continue
        call_key = CallKey(entry.alias, entry.line_number, "alias", pronunciation_mora(entry.alias))
        if not _is_excluded_call_key(call_key, excluded):
            result.setdefault(entry.alias, call_key)
    for entry in entries:
        wav = wav_key(entry.wav_name)
        if not wav:
            continue
        call_key = CallKey(wav, entry.line_number, "wav", pronunciation_mora(wav))
        if not _is_excluded_call_key(call_key, excluded):
            result.setdefault(wav, call_key)
    return result


# 呼び出しキーを解決する
def resolve_call_key(key: str, entries: list[OtoEntry]) -> OtoEntry | None:
    for entry in entries:
        if entry.alias == key:
            return entry
    for entry in entries:
        if wav_key(entry.wav_name) == key:
            return entry
    return None


# 変更一覧行一覧を反映する
def apply_changes_to_entries(entries: list[OtoEntry], changes: list[ChangeRow]) -> list[OtoEntry]:
    by_line = {change.line_number: change for change in changes if change.line_number is not None}
    updated: list[OtoEntry] = []
    for entry in entries:
        change = by_line.get(entry.line_number)
        if change is None:
            updated.append(entry)
            continue
        updated.append(replace(entry, wav_name=change.new_wav, alias=change.new_alias))

    order_by_line = {
        change.line_number: change.new_order_id
        for change in changes
        if change.line_number is not None
        and change.new_order_id is not None
        and change.old_order_id != change.new_order_id
    }
    if order_by_line:
        return sorted(updated, key=lambda entry: (order_by_line.get(entry.line_number, entry.line_number), entry.line_number))
    return updated


# 呼び出しキー衝突一覧を検出する
def detect_call_key_collisions(entries: list[OtoEntry], config: KeyWarningConfig | None = None) -> list[KeyWarning]:
    config = config or KeyWarningConfig()
    excluded = _excluded_mora_set(config.excluded_moras)
    key_lines: dict[str, dict[int, set[str]]] = {}
    for entry in entries:
        for call_key in call_keys_for_entry(entry):
            if _is_excluded_call_key(call_key, excluded):
                continue
            key_lines.setdefault(call_key.key, {}).setdefault(call_key.line_number, set()).add(call_key.kind)

    warnings: list[KeyWarning] = []
    for key, line_kinds in key_lines.items():
        if len(line_kinds) <= 1:
            continue
        line_numbers = tuple(sorted(line_kinds))
        cells = tuple(
            sorted(
                f"{kind}:{line_number}"
                for line_number, kinds in line_kinds.items()
                for kind in kinds
            )
        )
        warnings.append(
            KeyWarning(
                code="key_collision",
                level="warning",
                key=key,
                line_numbers=line_numbers,
                cells=cells,
                message=f"call key '{key}' is shared by multiple oto entries",
            )
        )
    return warnings


# 警告一覧を検出する
def detect_resolution_warnings(
    original_entries: list[OtoEntry],
    modified_entries: list[OtoEntry],
    config: KeyWarningConfig | None = None,
) -> list[KeyWarning]:
    config = config or KeyWarningConfig()
    if not config.warn_on_resolution_change:
        return []

    warnings: list[KeyWarning] = []
    before_keys = callable_key_map(original_entries, config.excluded_moras)
    after_keys = callable_key_map(modified_entries, config.excluded_moras)
    modified_by_line = {entry.line_number: entry for entry in modified_entries}

    for old_key, call_key in before_keys.items():
        modified_entry = modified_by_line.get(call_key.line_number)
        if modified_entry is None:
            warnings.append(
                KeyWarning(
                    code="key_updated_row_missing",
                    level="danger",
                    key=old_key,
                    line_numbers=(call_key.line_number,),
                    cells=(f"{call_key.kind}:{call_key.line_number}",),
                    message=f"old call key '{old_key}' cannot be checked because its oto row is missing after preview",
                )
            )
            continue

        safe_candidates = [
            candidate
            for candidate in call_key_candidates_for_entry(modified_entry)
            if after_keys.get(candidate.key) is not None
            and after_keys[candidate.key].line_number == call_key.line_number
        ]
        if not safe_candidates:
            candidate_text = ", ".join(candidate.key for candidate in call_key_candidates_for_entry(modified_entry)) or "(none)"
            warnings.append(
                KeyWarning(
                    code="updated_key_not_callable",
                    level="danger",
                    key=old_key,
                    line_numbers=(call_key.line_number,),
                    cells=(f"{call_key.kind}:{call_key.line_number}",),
                    message=(
                        f"old call key '{old_key}' cannot call line {call_key.line_number} after preview; "
                        f"checked candidates: {candidate_text}"
                    ),
                )
            )
            continue
        if call_key.mora and not any(candidate.mora == call_key.mora for candidate in safe_candidates):
            cells = tuple(f"{candidate.kind}:{candidate.line_number}" for candidate in safe_candidates)
            warnings.append(
                KeyWarning(
                    code="same_mora_key_unavailable",
                    level="warning",
                    key=old_key,
                    line_numbers=(call_key.line_number,),
                    cells=cells,
                    message=(
                        f"old call key '{old_key}' can still call line {call_key.line_number}, "
                        f"but no updated callable key keeps the same pronunciation mora '{call_key.mora}'"
                    ),
                )
            )
    return warnings


# 状態警告を処理する
def _status_for_warning_code(code: str) -> str:
    if code == "key_collision":
        return "duplication"
    if code == "same_mora_key_unavailable":
        return "changed_mora"
    if code in {"updated_key_not_callable", "key_updated_row_missing"}:
        return "cannotcall"
    return code


# 概要行を作る
def warning_summary_by_line(warnings: list[KeyWarning]) -> dict[int, tuple[str, str, tuple[str, ...], tuple[str, ...]]]:
    severity = {"": 0, "ok": 0, "warning": 2, "danger": 3}
    result: dict[int, tuple[str, str, tuple[str, ...], tuple[str, ...]]] = {}
    for warning in warnings:
        for line_number in warning.line_numbers:
            current_level, current_status, current_messages, current_cells = result.get(line_number, ("ok", "", (), ()))
            level = warning.level if severity[warning.level] > severity[current_level] else current_level
            status = _status_for_warning_code(warning.code) if severity[warning.level] >= severity[current_level] else current_status
            result[line_number] = (level, status, current_messages + (warning.message,), current_cells + warning.cells)
    return result
