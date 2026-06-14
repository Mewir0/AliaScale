from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .changes import ChangeRow
from .options import WarningSeverity


WARNING_RANK = {
    "": 0,
    WarningSeverity.OK.value: 0,
    WarningSeverity.WARNING.value: 2,
    WarningSeverity.DANGER.value: 3,
}


@dataclass(frozen=True)
# 警告を保持する
class WarningMessage:
    severity: WarningSeverity
    message: str
    line_number: int | None = None
    cells: tuple[str, ...] = ()


# 警告警告レベルを正規化する
def normalize_warning_severity(value: str | WarningSeverity | None) -> WarningSeverity:
    if isinstance(value, WarningSeverity):
        return value
    if value in {None, "", "info"}:
        return WarningSeverity.OK
    return WarningSeverity(str(value))


# 値を作る
def warning_rank(value: str | WarningSeverity | None) -> int:
    severity = value.value if isinstance(value, WarningSeverity) else value or ""
    return WARNING_RANK.get(severity, 0)


# 変更一覧を作る
def warnings_from_changes(changes: Iterable[ChangeRow]) -> list[WarningMessage]:
    result: list[WarningMessage] = []
    for change in changes:
        if not change.warnings:
            continue
        severity = normalize_warning_severity(change.severity or WarningSeverity.OK.value)
        for message in change.warnings:
            result.append(
                WarningMessage(
                    severity=severity,
                    message=message,
                    line_number=change.line_number,
                    cells=change.warning_cells,
                )
            )
    return result


# 警告一覧を判定する
def has_blocking_warnings(warnings: Iterable[WarningMessage]) -> bool:
    return any(warning.severity == WarningSeverity.DANGER for warning in warnings)
