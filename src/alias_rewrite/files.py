from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from .backup import BackupResult, backup_file
from .changes import ChangeRow
from .filename import FilenameRewriteConfig


@dataclass(frozen=True)
# ファイルrenameを保持する
class FileRenamePlan:
    old_path: Path
    new_path: Path
    kind: str
    change: ChangeRow


@dataclass(frozen=True)
# ファイルrename結果を保持する
class FileRenameResult:
    plan: FileRenamePlan
    status: str
    message: str = ""
    backup: BackupResult | None = None


# パスキーを処理する
def _path_key(path: Path) -> str:
    return str(path.parent.resolve() / path.name).casefold()



# 関連ファイルパターンを文字列化する
def _format_related_pattern(pattern: str, wav_path: Path) -> str:
    return pattern.format(name=wav_path.name, stem=wav_path.stem, suffix=wav_path.suffix)


# ファイル名比較用のキーを作る
def _name_key(text: str) -> str:
    return unicodedata.normalize("NFC", text).casefold()


# 関連ファイルパターン内の指定stem位置を返す
def _related_stem_span(pattern: str, file_name: str, stem: str) -> tuple[int, int] | None:
    parts: list[str] = []
    index = 0
    stem_group_used = False
    while index < len(pattern):
        if pattern.startswith("{stem}", index):
            if stem_group_used:
                parts.append(re.escape(stem))
            else:
                parts.append(f"(?P<stem>{re.escape(stem)})")
                stem_group_used = True
            index += len("{stem}")
            continue
        if pattern.startswith("{name}", index):
            parts.append(re.escape(f"{stem}.wav"))
            index += len("{name}")
            continue
        if pattern.startswith("{suffix}", index):
            parts.append(re.escape(".wav"))
            index += len("{suffix}")
            continue
        if pattern[index] == "*":
            parts.append(".*")
        else:
            parts.append(re.escape(pattern[index]))
        index += 1
    if not stem_group_used:
        return None
    match = re.fullmatch("".join(parts), file_name, flags=re.IGNORECASE)
    return match.span("stem") if match else None


# 関連ファイルパターンが指定stemに一致するか判定する
def _related_pattern_matches_stem(pattern: str, file_name: str, stem: str) -> bool:
    return _related_stem_span(pattern, file_name, stem) is not None


# 関連ファイル名に最も長く一致するstemを選ぶ
def _best_related_stem(pattern: str, file_name: str, stems: tuple[str, ...]) -> str | None:
    matches = [stem for stem in stems if _related_pattern_matches_stem(pattern, file_name, stem)]
    if not matches:
        return None
    matches.sort(key=lambda stem: (len(_name_key(stem)), _name_key(stem)), reverse=True)
    best_length = len(_name_key(matches[0]))
    best_matches = [stem for stem in matches if len(_name_key(stem)) == best_length]
    return best_matches[0] if len(best_matches) == 1 else None


# 関連ファイル候補を展開する
def _expand_related_pattern(pattern: str, wav_path: Path, new_wav_path: Path, known_stems: tuple[str, ...]) -> list[tuple[Path, Path]]:
    old_pattern = _format_related_pattern(pattern, wav_path)
    if "*" not in old_pattern:
        old_path = wav_path.with_name(old_pattern)
        if old_path.exists() and not old_path.is_file():
            return []
        new_name = _format_related_pattern(pattern, new_wav_path)
        return [(old_path, new_wav_path.with_name(new_name))]

    candidates: list[tuple[Path, Path]] = []
    old_stem = wav_path.stem
    new_stem = new_wav_path.stem
    for old_path in sorted(wav_path.parent.glob(old_pattern)):
        if not old_path.is_file():
            continue
        if "{stem}" in pattern:
            best_stem = _best_related_stem(pattern, old_path.name, known_stems)
            if best_stem is None or _name_key(best_stem) != _name_key(old_stem):
                continue
            stem_span = _related_stem_span(pattern, old_path.name, old_stem)
            if stem_span is None:
                continue
            new_name = old_path.name[:stem_span[0]] + new_stem + old_path.name[stem_span[1]:]
        elif old_path.name.startswith(old_stem):
            new_name = new_stem + old_path.name[len(old_stem):]
        else:
            new_name = old_path.name.replace(old_stem, new_stem, 1)
        candidates.append((old_path, new_wav_path.with_name(new_name)))
    return candidates


# 候補一覧を処理する
def _related_candidates(
    wav_path: Path,
    new_wav_path: Path,
    config: FilenameRewriteConfig,
    known_stems: tuple[str, ...],
) -> list[tuple[Path, Path]]:
    candidates: list[tuple[Path, Path]] = []
    if getattr(config, "rename_related_files", False):
        for pattern in getattr(config, "related_file_patterns", ()):
            candidates.extend(_expand_related_pattern(pattern, wav_path, new_wav_path, known_stems))

    old_stem = wav_path.stem
    for suffix in getattr(config, "sidecar_suffixes", ()):
        if suffix.startswith("."):
            old_sidecar = wav_path.with_suffix(suffix)
            new_sidecar = new_wav_path.with_suffix(suffix)
        else:
            old_sidecar = wav_path.with_name(old_stem + suffix)
            new_sidecar = new_wav_path.with_name(new_wav_path.stem + suffix)
        candidates.append((old_sidecar, new_sidecar))
    return candidates


# ファイルrenameを作る
def build_file_rename_plan(
    voice_dir: str | Path,
    changes: list[ChangeRow],
    config: FilenameRewriteConfig | None = None,
) -> list[FileRenamePlan]:
    config = config or FilenameRewriteConfig()
    voice_dir = Path(voice_dir)
    plans: list[FileRenamePlan] = []
    seen_pairs: set[tuple[str, str, str]] = set()
    known_stems_by_parent: dict[Path, set[str]] = {}
    for change in changes:
        if change.old_wav:
            old_path = voice_dir / change.old_wav
            known_stems_by_parent.setdefault(old_path.parent.resolve(), set()).add(old_path.stem)
    for change in changes:
        if not change.changed or change.old_wav == change.new_wav:
            continue
        old_path = voice_dir / change.old_wav
        new_path = voice_dir / change.new_wav
        known_stems = tuple(
            sorted(
                known_stems_by_parent.get(old_path.parent.resolve(), {old_path.stem}),
                key=lambda stem: len(_name_key(stem)),
                reverse=True,
            )
        )
        pair = (_path_key(old_path), _path_key(new_path), "wav")
        if pair not in seen_pairs:
            seen_pairs.add(pair)
            plans.append(FileRenamePlan(old_path=old_path, new_path=new_path, kind="wav", change=change))
        if config.rename_related_files or config.rename_sidecar_files:
            for old_sidecar, new_sidecar in _related_candidates(old_path, new_path, config, known_stems):
                pair = (_path_key(old_sidecar), _path_key(new_sidecar), "related")
                if pair not in seen_pairs:
                    seen_pairs.add(pair)
                    plans.append(FileRenamePlan(old_path=old_sidecar, new_path=new_sidecar, kind="related", change=change))
    return plans


# ファイルrenameを検証する
def validate_file_rename_plan(plans: list[FileRenamePlan]) -> list[FileRenameResult]:
    """Return blocking and non-blocking validation results for a rename plan."""
    results: list[FileRenameResult] = []
    source_to_dest: dict[str, str] = {}
    dest_to_sources: dict[str, set[str]] = {}
    source_keys = {_path_key(plan.old_path) for plan in plans}

    for plan in plans:
        source_key = _path_key(plan.old_path)
        dest_key = _path_key(plan.new_path)
        if not plan.old_path.exists():
            results.append(FileRenameResult(plan, "missing", "source file does not exist"))
        if source_key in source_to_dest and source_to_dest[source_key] != dest_key:
            results.append(FileRenameResult(plan, "duplicate_source", "source has multiple destinations"))
        source_to_dest[source_key] = dest_key
        dest_to_sources.setdefault(dest_key, set()).add(source_key)

    for plan in plans:
        source_key = _path_key(plan.old_path)
        dest_key = _path_key(plan.new_path)
        if len(dest_to_sources.get(dest_key, set())) > 1:
            results.append(FileRenameResult(plan, "duplicate_destination", "multiple sources map to the same destination"))
        if plan.new_path.exists() and dest_key not in source_keys and source_key != dest_key:
            results.append(FileRenameResult(plan, "destination_conflict", "destination already exists outside this rename set"))

    return results


# 値を処理する
def _skipped_plan_ids(results: list[FileRenameResult]) -> set[int]:
    return {
        id(result.plan)
        for result in results
        if result.status in {"missing", "duplicate_source", "duplicate_destination"}
    }


# ルートを処理する
def _unique_conflict_root(parent: Path) -> Path:
    while True:
        candidate = parent / f"conflict_{uuid4().hex[:10]}"
        if not candidate.exists():
            return candidate


# 値を移動する
def _move_external_conflicts(
    group: list[tuple[int, FileRenamePlan]],
    *,
    source_keys: set[str],
    conflict_roots: dict[Path, Path],
) -> list[FileRenameResult]:
    results: list[FileRenameResult] = []
    moved: set[str] = set()
    for _index, plan in group:
        source_key = _path_key(plan.old_path)
        dest_key = _path_key(plan.new_path)
        if dest_key in moved:
            continue
        if not plan.new_path.exists() or dest_key in source_keys or source_key == dest_key:
            continue
        conflict_root = conflict_roots.setdefault(plan.new_path.parent, _unique_conflict_root(plan.new_path.parent))
        conflict_path = conflict_root / plan.new_path.name
        conflict_path.parent.mkdir(parents=True, exist_ok=True)
        counter = 2
        while conflict_path.exists():
            conflict_path = conflict_root / f"{plan.new_path.stem}_{counter}{plan.new_path.suffix}"
            counter += 1
        plan.new_path.rename(conflict_path)
        moved.add(dest_key)
        results.append(FileRenameResult(plan, "moved_to_conflict_folder", str(conflict_path)))
    return results


# 一時パスを処理する
def _temporary_path(path: Path, reserved: set[str]) -> Path:
    while True:
        candidate = path.with_name(f".aliascale_tmp_{uuid4().hex}_{path.name}")
        key = _path_key(candidate)
        if key not in reserved and not candidate.exists():
            reserved.add(key)
            return candidate


# 値を処理する
def _connected_groups(indexed_plans: list[tuple[int, FileRenamePlan]]) -> list[list[tuple[int, FileRenamePlan]]]:
    key_to_indexes: dict[str, set[int]] = {}
    by_index = {index: plan for index, plan in indexed_plans}
    for index, plan in indexed_plans:
        key_to_indexes.setdefault(_path_key(plan.old_path), set()).add(index)
        key_to_indexes.setdefault(_path_key(plan.new_path), set()).add(index)
        if plan.change.old_wav:
            key_to_indexes.setdefault(f"old_wav:{plan.change.old_wav.casefold()}", set()).add(index)

    groups: list[list[tuple[int, FileRenamePlan]]] = []
    visited: set[int] = set()
    for start_index, _plan in indexed_plans:
        if start_index in visited:
            continue
        stack = [start_index]
        group_indexes: list[int] = []
        visited.add(start_index)
        while stack:
            index = stack.pop()
            group_indexes.append(index)
            plan = by_index[index]
            keys = [_path_key(plan.old_path), _path_key(plan.new_path)]
            if plan.change.old_wav:
                keys.append(f"old_wav:{plan.change.old_wav.casefold()}")
            for key in keys:
                for next_index in key_to_indexes.get(key, set()):
                    if next_index not in visited:
                        visited.add(next_index)
                        stack.append(next_index)
        groups.append([(index, by_index[index]) for index in group_indexes])
    return groups


# 値を巻き戻す
def _rollback_group(
    group: list[tuple[int, FileRenamePlan]],
    *,
    reserved: set[str],
    temporary_by_index: dict[int, Path],
    completed_indexes: list[int],
) -> None:
    by_index = {index: plan for index, plan in group}
    rollback_by_index: dict[int, Path] = {}
    for index in reversed(completed_indexes):
        plan = by_index[index]
        if plan.new_path.exists():
            try:
                rollback_temporary = _temporary_path(plan.new_path, reserved)
                plan.new_path.rename(rollback_temporary)
                rollback_by_index[index] = rollback_temporary
            except OSError:
                pass
    for index, temporary in temporary_by_index.items():
        plan = by_index[index]
        if temporary.exists() and not plan.old_path.exists():
            try:
                temporary.rename(plan.old_path)
            except OSError:
                pass
    for index, temporary in rollback_by_index.items():
        plan = by_index[index]
        if temporary.exists() and not plan.old_path.exists():
            try:
                temporary.rename(plan.old_path)
            except OSError:
                pass


# ファイルrenameを反映する
def apply_file_rename_plan(
    plans: list[FileRenamePlan],
    *,
    dry_run: bool = True,
    backup_root: str | Path | None = None,
) -> list[FileRenameResult]:
    validation_results = validate_file_rename_plan(plans)
    results: list[FileRenameResult] = list(validation_results)
    skipped_ids = _skipped_plan_ids(validation_results)
    executable = [
        (index, plan)
        for index, plan in enumerate(plans)
        if id(plan) not in skipped_ids
        and plan.old_path.exists()
        and _path_key(plan.old_path) != _path_key(plan.new_path)
    ]
    if dry_run:
        return results + [FileRenameResult(plan, "dry_run") for _index, plan in executable]

    backups_by_index: dict[int, BackupResult | None] = {}
    for index, plan in executable:
        backups_by_index[index] = backup_file(plan.old_path, backup_root) if backup_root else None

    reserved = {_path_key(plan.old_path) for _index, plan in executable} | {_path_key(plan.new_path) for _index, plan in executable}
    source_keys = {_path_key(plan.old_path) for _index, plan in executable}
    conflict_roots: dict[Path, Path] = {}
    for group in _connected_groups(executable):
        temporary_by_index: dict[int, Path] = {}
        completed_indexes: list[int] = []
        try:
            results.extend(_move_external_conflicts(group, source_keys=source_keys, conflict_roots=conflict_roots))
            for index, plan in group:
                temporary = _temporary_path(plan.old_path, reserved)
                plan.old_path.rename(temporary)
                temporary_by_index[index] = temporary
            for index, plan in group:
                plan.new_path.parent.mkdir(parents=True, exist_ok=True)
                temporary_by_index[index].rename(plan.new_path)
                completed_indexes.append(index)
                results.append(FileRenameResult(plan, "renamed", backup=backups_by_index.get(index)))
        except OSError as exc:
            _rollback_group(
                group,
                reserved=reserved,
                temporary_by_index=temporary_by_index,
                completed_indexes=completed_indexes,
            )
            for _index, plan in group:
                results.append(FileRenameResult(plan, "error", str(exc)))
    return results
