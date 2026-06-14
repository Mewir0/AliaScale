from __future__ import annotations

from dataclasses import asdict, replace
import math
from pathlib import Path
import wave

from alias_rewrite import (
    AliasRewriteConfig,
    ApplyOptions,
    ExcludeConfig,
    FilenameRewriteConfig,
    KeyWarningConfig,
    NoteMappingConfig,
    PreviewOptions,
    ReplacementRule,
    SortKeyOrder,
    apply_changes_direct,
    build_voice_information,
    collect_ust_usage,
    freq_to_note,
    preview_changes,
    preview_ust_sync_for_folder,
    pronunciation_mora,
    validate_preview_changes,
    warnings_from_changes,
    wav_key,
)
from alias_rewrite.apply import _apply_edit_policies
from alias_rewrite.files import build_file_rename_plan
from alias_rewrite.log_apply import save_apply_log
from alias_rewrite.options import WavEditMode, normalize_wav_edit_mode
from alias_rewrite.pitch import FREQUENCY_ERROR_KEY, build_frequency_index, estimate_pitch_for_entry, find_frequency_record, load_frequency_records
from alias_rewrite.warnings import has_blocking_warnings

from .dto import (
    ApplyRequest,
    ApplyResponse,
    PreviewRequest,
    PreviewResponse,
    PreviewRowDto,
    PreviewSummary,
    UstListItem,
    WarningDto,
)
from .runtime_paths import default_logs_dir, default_settings_dir, resolve_app_path
from .wav_utils import wav_duration_ms


# Load original oto entries for UST usage
def _oto_entries(request: PreviewRequest | ApplyRequest):
    if not getattr(request, "oto_path", ""):
        return []
    from alias_rewrite.oto import iter_entries, parse_oto_file

    lines, _encoding = parse_oto_file(request.oto_path)
    return iter_entries(lines)


# Split comma separated candidates
def _split_candidates(values: tuple[str, ...]) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        result.extend(part.strip() for part in value.split(",") if part.strip())
    return tuple(result)


# Build note mapping config
def _note_config(request: PreviewRequest) -> NoteMappingConfig:
    rewrite = request.rewrite
    candidates = _split_candidates(rewrite.rounding_candidates)
    mode = rewrite.rounding_mode
    if mode == "whole_tone":
        return NoteMappingConfig(mode="whole_tone")
    if mode in {"key", "pitch_classes", "allowed_notes"}:
        return NoteMappingConfig(mode="pitch_classes", allowed_pitch_classes=candidates)
    if mode in {"free", "explicit_notes"}:
        return NoteMappingConfig(mode="explicit_notes", explicit_notes=candidates)
    return NoteMappingConfig(mode="semitone")


# Build preview options
def _preview_options(request: PreviewRequest) -> PreviewOptions:
    rewrite = request.rewrite
    alias_config = AliasRewriteConfig(
        separator=rewrite.separator,
        strip_suffix=rewrite.strip_suffix,
        keep_prefix=rewrite.keep_prefix,
        missing_pitch=rewrite.missing_pitch,
    )
    exclude = ExcludeConfig(
        exclude_unvoiced=rewrite.exclude.exclude_unvoiced,
        exclude_no_f0=rewrite.exclude.exclude_no_f0,
        exclude_no_freq_src=rewrite.exclude.exclude_no_freq_src,
        exclude_empty_params=rewrite.exclude.exclude_empty_params,
        mode=rewrite.exclude.mode,
        patterns=rewrite.exclude.patterns,
    )
    replacement_rules = tuple(
        ReplacementRule(rule.old, rule.new, target=rule.target, use_regex=rule.use_regex)
        for rule in rewrite.replacement_rules
    )
    sort_orders = tuple(SortKeyOrder(spec.key, spec.direction) for spec in rewrite.sort)
    wav_edit_mode = normalize_wav_edit_mode(rewrite.wav_edit_mode)
    alias_edit_mode = normalize_wav_edit_mode(rewrite.alias_edit_mode)
    return PreviewOptions(
        mode=rewrite.mode,
        frequency_source=rewrite.frequency_source or request.frequency_source or "mrq",
        alias_config=alias_config,
        note_config=_note_config(request),
        replacement_rules=replacement_rules,
        csv_invert=rewrite.csv_invert,
        csv_read_columns=tuple(rewrite.csv_read_columns),
        rule_scope=rewrite.rule_scope,
        rule_alias_template=rewrite.rule_alias_template,
        rule_wav_template=rewrite.rule_wav_template,
        rule_call_key_template=rewrite.rule_call_key_template,
        edit_scope=rewrite.edit_scope,
        exclude_config=exclude,
        alias_target=rewrite.alias_target,
        add_alias_for_unused_wav=rewrite.add_alias_for_unused_wav,
        number_first_alias=rewrite.number_first_alias,
        sort_orders=sort_orders,
        allow_wav_edit=wav_edit_mode != WavEditMode.DISABLED,
        wav_edit_mode=wav_edit_mode.value,
        allow_alias_edit=alias_edit_mode != WavEditMode.DISABLED,
        alias_edit_mode=alias_edit_mode.value,
        prefix_underscore_for_new_alias=rewrite.prefix_underscore_for_new_alias,
        key_warning_config=KeyWarningConfig(excluded_moras=request.settings.excluded_call_key_moras),
        auto_wav_excluded_moras=request.settings.excluded_call_key_moras,
        number_alias_before_wav=request.settings.number_alias_before_wav,
        numbering_order_mode=request.settings.numbering_order_mode,
        renumber_after_order_change=request.settings.renumber_after_order_change,
        relax_cannotcall_for_unused_ust_entries=request.settings.relax_cannotcall_for_unused_ust_entries,
        sort_order_path=resolve_app_path(None, Path("settings") / "otolist.txt"),
    )


# Convert preview rows to DTOs
def _row_dtos(request: PreviewRequest | ApplyRequest, rows) -> tuple[PreviewRowDto, ...]:
    voice_dir = Path(request.voice_dir) if getattr(request, "voice_dir", "") else None
    entries_by_line = {}
    if getattr(request, "oto_path", ""):
        try:
            from alias_rewrite.oto import iter_entries, parse_oto_file

            lines, _encoding = parse_oto_file(request.oto_path)
            entries_by_line = {entry.line_number: entry for entry in iter_entries(lines)}
        except (OSError, ValueError):
            entries_by_line = {}
    existing_by_wav: dict[str, bool] = {}
    pitch_by_line = _pitch_by_line_for_display(request, entries_by_line)
    result: list[PreviewRowDto] = []
    for row in rows:
        dto = row if isinstance(row, PreviewRowDto) else PreviewRowDto.from_change(row)
        exists = True
        if voice_dir is not None and dto.old_wav:
            key = dto.old_wav
            if key not in existing_by_wav:
                existing_by_wav[key] = (voice_dir / key).exists()
            exists = existing_by_wav[key]
        if not dto.auto_new_wav:
            dto = replace(dto, auto_new_wav=dto.new_wav)
        if not dto.auto_new_alias:
            dto = replace(dto, auto_new_alias=dto.new_alias)
        if dto.frequency is None and dto.line_number in pitch_by_line:
            pitch = pitch_by_line[dto.line_number]
            dto = replace(
                dto,
                frequency=pitch.get("frequency") if isinstance(pitch.get("frequency"), (int, float)) else None,
                note=str(pitch.get("note") or dto.note or ""),
                status=dto.status or str(pitch.get("status") or ""),
            )
        entry = entries_by_line.get(dto.line_number)
        if entry is not None:
            start, end = _play_range_ms(voice_dir / entry.wav_name if voice_dir else None, entry.offset, entry.cutoff)
            dto = replace(
                dto,
                offset_ms=_safe_float(entry.offset),
                consonant_ms=_safe_float(entry.consonant),
                cutoff_ms=_safe_float(entry.cutoff),
                preutterance_ms=_safe_float(entry.preutterance),
                overlap_ms=_safe_float(entry.overlap),
                play_start_ms=start,
                play_end_ms=end,
            )
        result.append(replace(dto, old_wav_exists=exists))
    return tuple(result)


# 表示用の周波数推定を作る
def _pitch_by_line_for_display(request: PreviewRequest | ApplyRequest, entries_by_line: dict[int, object]) -> dict[int, dict]:
    if not entries_by_line:
        return {}
    source = getattr(request, "frequency_source", "") or getattr(getattr(request, "rewrite", None), "frequency_source", "") or "mrq"
    voice_dir = Path(request.voice_dir) if getattr(request, "voice_dir", "") else None
    if voice_dir is None:
        return {}
    table_path = getattr(request, "mrq_path", "") or None
    try:
        if source in {"mrq", "moresampler_mrq"} and not table_path:
            table = voice_dir / "desc.mrq"
            table_path = str(table) if table.exists() else None
        if source in {"mrq", "moresampler_mrq"} and table_path is None:
            index = {FREQUENCY_ERROR_KEY: "no_freq_src"}
        else:
            records, _label = load_frequency_records(source=source, voice_dir=voice_dir, entries=list(entries_by_line.values()), table_path=table_path)
            index = build_frequency_index(records)
    except Exception:
        index = {FREQUENCY_ERROR_KEY: "invalid_freq"}
    return {line_number: estimate_pitch_for_entry(entry, index) for line_number, entry in entries_by_line.items()}


# Convert a numeric value to safe float
def _safe_float(value: object) -> float:
    if not isinstance(value, (int, float)):
        return 0.0
    if not math.isfinite(float(value)):
        return 0.0
    return float(value)


# Calculate row playback range
def _play_range_ms(path: Path | None, offset_ms: float, blank_ms: float) -> tuple[int, int]:
    offset_ms = _safe_float(offset_ms)
    blank_ms = _safe_float(blank_ms)
    start = max(0, int(offset_ms))
    duration = wav_duration_ms(path)
    if duration <= 0:
        return start, 0
    if blank_ms < 0:
        end = int(start + abs(blank_ms))
    elif blank_ms > 0:
        end = int(duration - blank_ms)
    else:
        end = duration
    end = max(start + 1, min(duration, end))
    return start, end


# 音源表示名を取得する
def _voice_display_name(voice_dir: Path | None) -> str:
    if voice_dir is None:
        return "-"
    character_path = voice_dir / "character.txt"
    if character_path.exists():
        for encoding in ("cp932", "utf-8-sig", "utf-8"):
            try:
                text = character_path.read_text(encoding=encoding)
            except (OSError, UnicodeDecodeError):
                continue
            for line in text.splitlines():
                if line.startswith("name="):
                    name = line.removeprefix("name=").strip()
                    if name:
                        return name
            break
    return voice_dir.name


# 原音関連ファイル数を取得する
def _related_voice_file_count(voice_dir: Path | None) -> int:
    if voice_dir is None:
        return 0
    patterns = ("*.frq", "*.llsm", "*.pmk", "*.hifi.npz")
    seen: set[Path] = set()
    try:
        for pattern in patterns:
            for path in voice_dir.rglob(pattern):
                if path.is_file():
                    seen.add(path.resolve(strict=False))
    except OSError:
        return len(seen)
    return len(seen)


# 波形の棒数を表示範囲から決める
def _waveform_bin_count(duration_ms: int) -> int:
    return max(1, int(round(max(1, duration_ms))))


# フォールバック波形を作る
def _fallback_waveform_peaks(seed: str, count: int) -> list[float]:
    base = sum(ord(char) for char in seed) % 23
    peaks: list[float] = []
    for index in range(max(1, count)):
        value = (
            0.38
            + 0.28 * abs(math.sin((index + 1 + base) * 0.43))
            + 0.18 * abs(math.cos((index + 3 + base) * 0.19))
        )
        peaks.append(max(0.08, min(1.0, value)))
    return peaks


# サンプル値を正規化する
def _normalized_wav_sample(raw: bytes, sample_width: int) -> float:
    if sample_width == 1:
        return (raw[0] - 128) / 128.0
    maximum = float(1 << (sample_width * 8 - 1))
    return int.from_bytes(raw, byteorder="little", signed=True) / maximum


# wavから表示範囲のピーク列を作る
def _wav_waveform_peaks(path: Path, start_ms: int, end_ms: int, count: int) -> list[float]:
    if count <= 0:
        return []
    if not path.exists():
        return _fallback_waveform_peaks(path.name, count)
    try:
        with wave.open(str(path), "rb") as wav:
            rate = wav.getframerate()
            channels = max(1, wav.getnchannels())
            sample_width = wav.getsampwidth()
            total_frames = wav.getnframes()
            if rate <= 0 or sample_width not in {1, 2, 3, 4} or total_frames <= 0:
                return _fallback_waveform_peaks(path.name, count)
            start_frame = max(0, min(total_frames - 1, int(rate * max(0, start_ms) / 1000)))
            end_frame = int(rate * end_ms / 1000) if end_ms > 0 else total_frames
            end_frame = max(start_frame + 1, min(total_frames, end_frame))
            frame_count = end_frame - start_frame
            peaks: list[float] = []
            frame_size = channels * sample_width
            for index in range(count):
                bin_start = start_frame + int(frame_count * index / count)
                bin_end = start_frame + int(frame_count * (index + 1) / count)
                bin_end = max(bin_start + 1, min(end_frame, bin_end))
                wav.setpos(bin_start)
                data = wav.readframes(bin_end - bin_start)
                peak = 0.0
                for pos in range(0, len(data), frame_size):
                    frame = data[pos : pos + frame_size]
                    if len(frame) < frame_size:
                        break
                    for channel in range(channels):
                        start = channel * sample_width
                        sample = frame[start : start + sample_width]
                        peak = max(peak, abs(_normalized_wav_sample(sample, sample_width)))
                peaks.append(max(0.02, min(1.0, peak)))
    except (OSError, wave.Error, ValueError):
        return _fallback_waveform_peaks(path.name, count)
    if not any(peaks):
        return _fallback_waveform_peaks(path.name, count)
    return peaks


# 周波数表から選択エントリのピッチ点を作る
def _f0_points_for_entry(request: PreviewRequest | ApplyRequest, entry, start_ms: int, end_ms: int) -> list[dict]:
    voice_dir = Path(request.voice_dir) if getattr(request, "voice_dir", "") else None
    if voice_dir is None:
        return []
    source = getattr(request, "frequency_source", "") or getattr(getattr(request, "rewrite", None), "frequency_source", "") or "mrq"
    table_path = getattr(request, "mrq_path", "") or None
    try:
        if source in {"mrq", "moresampler_mrq"} and not table_path:
            candidate = voice_dir / "desc.mrq"
            table_path = str(candidate) if candidate.exists() else None
        if source in {"mrq", "moresampler_mrq"} and table_path is None:
            return []
        records, _label = load_frequency_records(source=source, voice_dir=voice_dir, entries=[entry], table_path=table_path)
        record = find_frequency_record(entry, build_frequency_index(records))
    except Exception:
        return []
    if record is None or record.sample_rate <= 0 or record.hop_size <= 0:
        return []
    points: list[dict] = []
    frame_duration = record.hop_size / record.sample_rate * 1000.0
    start_frame = max(0, int(math.floor(start_ms / frame_duration))) if frame_duration > 0 else 0
    end_frame = min(len(record.f0_values), max(start_frame + 1, int(math.ceil(end_ms / frame_duration)))) if frame_duration > 0 else 0
    frame_total = max(1, end_frame - start_frame)
    step = max(1, math.ceil(frame_total / 240))
    for frame_index in range(start_frame, end_frame, step):
        frequency = float(record.f0_values[frame_index])
        if not math.isfinite(frequency) or frequency <= 0:
            continue
        points.append({"time_ms": frame_index * frame_duration, "frequency": frequency})
    return points


# 選択行の波形表示データを作る
def _selected_waveform_data(
    request: PreviewRequest | ApplyRequest,
    row: PreviewRowDto | None,
    entry,
) -> dict | None:
    if row is None or entry is None or not getattr(request, "voice_dir", ""):
        return None
    voice_dir = Path(request.voice_dir)
    wav_path = voice_dir / row.old_wav
    duration = wav_duration_ms(wav_path)
    offset_ms = _safe_float(entry.offset)
    overlap_ms = _safe_float(entry.overlap)
    base_start = max(0, int(row.play_start_ms or offset_ms))
    if overlap_ms < 0:
        base_start = min(base_start, max(0, int(offset_ms + overlap_ms)))
    display_start = base_start
    if row.play_end_ms:
        display_end = int(row.play_end_ms)
    else:
        _start, display_end = _play_range_ms(wav_path, entry.offset, entry.cutoff)
    if duration > 0:
        display_end = max(display_start + 1, min(duration, display_end))
    else:
        display_end = max(display_start + 1, display_end)
    display_duration = max(1, display_end - display_start)
    bar_count = _waveform_bin_count(display_duration)
    no_pitch_curve = (
        row.frequency is None
        or row.status in {"no_freq_src", "invalid_freq", "no_f0", "no_valid_f0", "no_freq", "fallback_full_wav"}
    )
    return {
        "wav_name": row.old_wav,
        "duration_ms": duration,
        "display_start_ms": display_start,
        "display_end_ms": display_end,
        "offset_ms": offset_ms,
        "consonant_ms": _safe_float(entry.consonant),
        "cutoff_ms": _safe_float(entry.cutoff),
        "preutterance_ms": _safe_float(entry.preutterance),
        "overlap_ms": overlap_ms,
        "peaks": _wav_waveform_peaks(wav_path, display_start, display_end, bar_count),
        "f0_points": [] if no_pitch_curve else _f0_points_for_entry(request, entry, display_start, display_end),
    }


# Build voice information
def _information(request: PreviewRequest, usage_summary=None) -> dict:
    if not request.voice_dir or not request.oto_path:
        return {}
    mrq_path = request.mrq_path or None
    try:
        data = asdict(
            build_voice_information(
                request.voice_dir,
                request.oto_path,
                mrq_path=mrq_path,
                frequency_source=request.frequency_source or request.rewrite.frequency_source or "mrq",
            )
        )
        if usage_summary is not None:
            data["ust_usage_total_lyrics"] = usage_summary.total_lyrics
            data["ust_usage_resolved_lyrics"] = usage_summary.resolved_lyrics
            data["ust_usage_unresolved_lyrics"] = len(usage_summary.unresolved_lyrics)
            data["ust_usage_ust_count"] = len(usage_summary.selected_paths)
            data["ust_usage_unresolved_samples"] = tuple(
                {"ust_path": str(item.ust_path), "lyric": item.lyric, "note_id": item.note_id}
                for item in usage_summary.unresolved_lyrics[:20]
            )
        return data
    except (OSError, ValueError):
        return {}


# Collect UST usage against original oto
def _ust_usage_summary(request: PreviewRequest | ApplyRequest, *, selection_known: bool | None = None):
    if not request.ust_root or not request.voice_dir or not request.oto_path:
        return None
    try:
        entries = _oto_entries(request)
        if selection_known is None:
            selection_known = isinstance(request, ApplyRequest) or getattr(request, "ust_selection_known", False)
        selected = tuple(getattr(request, "selected_ust_paths", ()) or ())
        return collect_ust_usage(
            request.ust_root,
            request.voice_dir,
            entries,
            selected_ust_paths=selected if selection_known else None,
            excluded_ust_paths=(request.utau_plugin_temp_path,) if request.utau_plugin_temp_path else (),
            strict_voice_match=request.settings.strict_voice_match,
            utau_exe_path=request.settings.utau_exe_path or None,
        )
    except (OSError, ValueError):
        return None


# Apply UST usage values to preview options
def _apply_usage_to_options(options: PreviewOptions, usage_summary) -> PreviewOptions:
    if usage_summary is None:
        return options
    return replace(
        options,
        usage_count_by_line=dict(usage_summary.usage_by_line),
        usage_counts_available=True,
    )


NOTE_ORDER = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


# 音名を低音優先の比較値へ変換する
def _note_rank(note: str | None) -> int:
    if not note:
        return 10**9
    for name in sorted(NOTE_ORDER, key=len, reverse=True):
        if note.startswith(name):
            try:
                octave = int(note[len(name) :])
            except ValueError:
                return 10**9
            return (octave + 1) * 12 + NOTE_ORDER.index(name)
    return 10**9


# 有効周波数だけを抽出する
def _valid_row_frequencies(rows: tuple[PreviewRowDto, ...]) -> list[float]:
    result: list[float] = []
    for row in rows:
        frequency = row.frequency
        if frequency is not None and math.isfinite(frequency) and frequency > 0:
            result.append(float(frequency))
    return result


# INFORMATION詳細用の音高統計を作る
def _pitch_statistics(rows: tuple[PreviewRowDto, ...]) -> dict:
    frequencies = sorted(_valid_row_frequencies(rows))
    if not frequencies:
        return {
            "count": 0,
            "frequency_average": None,
            "note_average": "",
            "note_median": "",
            "note_mode": "",
        }
    average = sum(frequencies) / len(frequencies)
    midpoint = len(frequencies) // 2
    if len(frequencies) % 2:
        median = frequencies[midpoint]
    else:
        median = (frequencies[midpoint - 1] + frequencies[midpoint]) / 2
    note_counts: dict[str, int] = {}
    for frequency in frequencies:
        note = freq_to_note(frequency)
        if note:
            note_counts[note] = note_counts.get(note, 0) + 1
    mode_note = ""
    if note_counts:
        mode_note = min(
            (note for note, count in note_counts.items() if count == max(note_counts.values())),
            key=_note_rank,
        )
    return {
        "count": len(frequencies),
        "frequency_average": average,
        "note_average": freq_to_note(average) or "",
        "note_median": freq_to_note(median) or "",
        "note_mode": mode_note,
    }


# C1からB6までの音高分布を作る
def _pitch_distribution_from_rows(rows: tuple[PreviewRowDto, ...]) -> list[dict]:
    labels = [f"{name}{octave}" for octave in range(1, 7) for name in NOTE_ORDER]
    counts = {label: 0 for label in labels}
    for frequency in _valid_row_frequencies(rows):
        note = freq_to_note(frequency)
        if note in counts:
            counts[note] += 1
    return [{"label": label, "count": counts[label]} for label in labels]


# 設定された関連ファイル名を展開する
def _format_related_pattern(pattern: str, wav_name: str) -> str:
    path = Path(wav_name)
    stem = path.stem
    try:
        return pattern.format(stem=stem)
    except (KeyError, ValueError):
        return pattern.replace("{stem}", stem)


# 関連ファイル表示候補を展開する
def _expand_related_display_candidates(voice_dir: Path, pattern: str, old_wav: str, new_wav: str) -> list[tuple[str, str]]:
    old_pattern = _format_related_pattern(pattern, old_wav)
    old_stem = Path(old_wav).stem
    new_stem = Path(new_wav).stem if new_wav else old_stem
    if "*" not in old_pattern:
        new_name = _format_related_pattern(pattern, new_wav) if new_wav else ""
        return [(old_pattern, new_name if new_name != old_pattern else "")]
    result: list[tuple[str, str]] = []
    for path in sorted(voice_dir.glob(old_pattern)):
        if path.name.startswith(old_stem):
            new_name = new_stem + path.name[len(old_stem):] if new_wav else ""
        else:
            new_name = path.name.replace(old_stem, new_stem, 1) if new_wav else ""
        result.append((path.name, new_name if new_name != path.name else ""))
    return result


# 選択行に関係する実在ファイルを集める
def _related_file_rows(request: ApplyRequest, row: PreviewRowDto | None) -> list[dict]:
    if row is None or not request.voice_dir:
        return []
    voice_dir = Path(request.voice_dir)
    candidates: list[tuple[str, str]] = [(row.old_wav, row.new_wav if row.new_wav != row.old_wav else "")]
    for pattern in request.settings.related_file_patterns:
        candidates.extend(_expand_related_display_candidates(voice_dir, pattern, row.old_wav, row.new_wav if row.new_wav != row.old_wav else ""))

    result: list[dict] = []
    seen: set[str] = set()
    for old_name, new_name in candidates:
        if not old_name or old_name in seen:
            continue
        seen.add(old_name)
        if (voice_dir / old_name).exists():
            result.append({"old_name": old_name, "new_name": new_name})
    return result


# 選択行を参照するUST一覧を作る
def _using_ust_rows(request: ApplyRequest, usage_summary, row: PreviewRowDto | None) -> list[dict]:
    if row is None or usage_summary is None or row.line_number is None:
        return []
    result: list[dict] = []
    usages = usage_summary.usage_by_line_ust.get(row.line_number, ())
    for item in usages:
        label = str(item.ust_path) if request.settings.show_full_ust_path else item.ust_path.name
        result.append({"path": str(item.ust_path), "label": label, "count": item.count})
    return result


# 未解決Lyric一覧を画面用に変換する
def _unresolved_lyric_rows(request: ApplyRequest, usage_summary) -> list[dict]:
    if usage_summary is None:
        return []
    rows: list[dict] = []
    for item in usage_summary.unresolved_lyrics:
        label = str(item.ust_path) if request.settings.show_full_ust_path else item.ust_path.name
        rows.append(
            {
                "ust_path": str(item.ust_path),
                "ust_label": label,
                "lyric": item.lyric,
                "note_id": item.note_id,
            }
        )
    return rows


# ユーザー定義五十音表を読み込む
def _otolist_text() -> str:
    path = default_settings_dir() / "otolist.txt"
    try:
        return path.read_text(encoding="cp932")
    except UnicodeDecodeError:
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return ""
    except OSError:
        return ""


# 発音別の件数を集計する
def _pronunciation_statistics(rows: tuple[PreviewRowDto, ...], usage_by_line: dict[int, int]) -> dict:
    stats: dict[str, dict[str, int]] = {}
    entries: list[dict] = []
    for row in rows:
        key = row.new_alias or wav_key(row.new_wav)
        mora = pronunciation_mora(key) or ""
        if not mora:
            continue
        usage = usage_by_line.get(row.line_number or -1, row.usage_count or 0)
        bucket = stats.setdefault(mora, {"alias": 0, "usage": 0})
        bucket["alias"] += 1
        bucket["usage"] += usage
        entries.append(
            {
                "mora": mora,
                "new_alias": row.new_alias,
                "new_wav": row.new_wav,
                "note": row.note or (freq_to_note(row.frequency) if row.frequency else "") or "",
                "usage": usage,
                "line_number": row.line_number,
            }
        )
    return {"stats": stats, "entries": entries}


# INFORMATION詳細ウィンドウ用データを生成する
def information_detail(request: ApplyRequest, *, selected_line_number: int | None = None, usage_stale: bool = False) -> dict:
    rows = tuple(request.rows)
    if not rows:
        return {
            "state": "empty",
            "rows": 0,
            "usage_stale": usage_stale,
            "theme": request.settings.theme,
            "ui_scale": request.settings.ui_scale,
        }

    usage_summary = _ust_usage_summary(request, selection_known=True)
    try:
        entries_by_line = {entry.line_number: entry for entry in _oto_entries(request)}
    except (OSError, ValueError):
        entries_by_line = {}
    selected_row = next((row for row in rows if row.line_number == selected_line_number), None)
    selected_entry = entries_by_line.get(selected_row.line_number) if selected_row is not None and selected_row.line_number is not None else None
    pitch_stats = _pitch_statistics(rows)
    usage_by_line = dict(usage_summary.usage_by_line) if usage_summary is not None else {}
    pronunciation = _pronunciation_statistics(rows, usage_by_line)
    wav_count = 0
    related_count = 0
    voice_dir = Path(request.voice_dir) if request.voice_dir else None
    if request.voice_dir:
        try:
            wav_count = sum(1 for path in Path(request.voice_dir).rglob("*.wav") if path.is_file())
        except OSError:
            wav_count = 0
        related_count = _related_voice_file_count(voice_dir)
    unresolved = _unresolved_lyric_rows(request, usage_summary)
    total_lyrics = usage_summary.total_lyrics if usage_summary is not None else 0
    resolved_lyrics = usage_summary.resolved_lyrics if usage_summary is not None else 0
    return {
        "state": "ready",
        "usage_stale": usage_stale,
        "theme": request.settings.theme,
        "ui_scale": request.settings.ui_scale,
        "voice_dir": request.voice_dir,
        "summary": {
            "voice_name": _voice_display_name(voice_dir),
            "folder_name": voice_dir.name if voice_dir is not None else "-",
            "voice_folder_path": request.voice_dir,
            "wav_file_count": wav_count,
            "related_file_count": related_count,
            "oto_entry_count": len(entries_by_line),
            "preview_rows": len(rows),
            "alias_count": sum(1 for row in rows if row.new_alias),
            "empty_alias_count": sum(1 for row in rows if not row.new_alias),
            "frequency_source": request.rewrite.frequency_source or "mrq",
            "frequency_count": pitch_stats["count"],
            "frequency_average": pitch_stats["frequency_average"],
            "note_average": pitch_stats["note_average"],
            "note_median": pitch_stats["note_median"],
            "note_mode": pitch_stats["note_mode"],
            "ust_count": len(usage_summary.selected_paths) if usage_summary is not None else 0,
            "total_lyrics": total_lyrics,
            "resolved_lyrics": resolved_lyrics,
            "unresolved_lyrics": len(unresolved),
        },
        "pitch_distribution": _pitch_distribution_from_rows(rows),
        "selected_entry": asdict(selected_row) if selected_row is not None else None,
        "waveform": _selected_waveform_data(request, selected_row, selected_entry),
        "related_files": _related_file_rows(request, selected_row),
        "using_ust": _using_ust_rows(request, usage_summary, selected_row),
        "unresolved_lyrics": unresolved,
        "otolist_text": _otolist_text(),
        "pronunciation": pronunciation,
    }


# Build UST preview list
def _ust_list(request: PreviewRequest, usage_summary=None) -> tuple[UstListItem, ...]:
    if not request.ust_root or not request.voice_dir or usage_summary is None:
        return ()
    selected = {str(Path(path).resolve(strict=False)) for path in request.selected_ust_paths}
    selection_known = request.ust_selection_known
    result: list[UstListItem] = []
    for path in usage_summary.matched_paths:
        resolved = str(Path(path).resolve(strict=False))
        label = str(path) if request.settings.show_full_ust_path else path.name
        result.append(
            UstListItem(
                path=str(path),
                label=label,
                checked=(resolved in selected) if selection_known else True,
                replacements=0,
                warnings=(),
            )
        )
    return tuple(result)


# Generate preview rows
def preview(request: PreviewRequest) -> PreviewResponse:
    options = _preview_options(request)
    usage_summary = _ust_usage_summary(request)
    options = _apply_usage_to_options(options, usage_summary)
    rows, encoding = preview_changes(
        request.oto_path,
        options=options,
        mrq_path=request.mrq_path or None,
        csv_path=request.csv_path or None,
    )
    warnings = tuple(WarningDto.from_warning(warning) for warning in warnings_from_changes(rows))
    danger_count = sum(1 for warning in warnings if warning.severity == "danger")
    summary = PreviewSummary(
        rows=len(rows),
        edits=sum(1 for row in rows if row.changed),
        warnings=len(warnings),
        danger=danger_count,
        can_apply=danger_count == 0,
    )
    row_dtos = _row_dtos(request, rows)
    return PreviewResponse(
        rows=row_dtos,
        summary=summary,
        warnings=warnings,
        ust_list=_ust_list(request, usage_summary),
        information=_information(request, usage_summary),
        encoding=encoding,
    )


# Search UST files without regenerating preview names
def search_ust(request: PreviewRequest) -> PreviewResponse:
    usage_summary = _ust_usage_summary(request)
    return PreviewResponse(
        rows=(),
        summary=PreviewSummary(rows=0, edits=0, warnings=0, danger=0, can_apply=False),
        warnings=(),
        ust_list=_ust_list(request, usage_summary),
        information=_information(request, usage_summary),
        encoding="",
    )


# Validate edited preview rows
def validate_preview_rows(request: ApplyRequest) -> PreviewResponse:
    settings = request.settings
    rows = []
    for row in request.rows:
        change = row.to_change()
        manual_fields = tuple(
            field
            for field, current, automatic in (
                ("wav", row.new_wav, row.auto_new_wav),
                ("alias", row.new_alias, row.auto_new_alias),
            )
            if current != automatic
        )
        if manual_fields:
            change = replace(change, manual_edit_fields=manual_fields)
        elif change.status == "manual" or change.origin_status == "manual" or change.manual_edit_fields:
            change = replace(
                change,
                status="" if change.status == "manual" else change.status,
                origin_status="" if change.origin_status == "manual" else change.origin_status,
                manual_edit_fields=(),
            )
        rows.append(change)
    options = _preview_options(
        PreviewRequest(
            voice_dir=request.voice_dir,
            oto_path=request.oto_path,
            ust_root=request.ust_root,
            rewrite=request.rewrite,
            settings=settings,
        )
    )
    usage_summary = _ust_usage_summary(request)
    options = _apply_usage_to_options(options, usage_summary)
    validated, encoding = validate_preview_changes(request.oto_path, rows, options=options)
    warnings = tuple(WarningDto.from_warning(warning) for warning in warnings_from_changes(validated))
    danger_count = sum(1 for warning in warnings if warning.severity == "danger")
    summary = PreviewSummary(
        rows=len(validated),
        edits=sum(1 for row in validated if row.changed),
        warnings=len(warnings),
        danger=danger_count,
        can_apply=danger_count == 0,
    )
    existing_by_line = {row.line_number: row for row in request.rows}
    row_dtos = []
    for row in validated:
        dto = PreviewRowDto.from_change(row)
        previous = existing_by_line.get(dto.line_number)
        if previous is not None:
            manual_fields = set(dto.manual_edit_fields)
            dto = replace(
                dto,
                auto_new_wav=previous.auto_new_wav if "wav" in manual_fields else dto.new_wav,
                auto_new_alias=previous.auto_new_alias if "alias" in manual_fields else dto.new_alias,
            )
        row_dtos.append(dto)
    return PreviewResponse(
        rows=_row_dtos(request, row_dtos),
        summary=summary,
        warnings=warnings,
        information=_information(
            PreviewRequest(
                voice_dir=request.voice_dir,
                oto_path=request.oto_path,
                ust_root=request.ust_root,
                rewrite=request.rewrite,
                settings=settings,
            ),
            usage_summary,
        ),
        encoding=encoding,
    )


# Build apply options
def _apply_options(request: ApplyRequest) -> ApplyOptions:
    settings = request.settings
    wav_edit_mode = normalize_wav_edit_mode(settings.wav_edit_mode)
    alias_edit_mode = normalize_wav_edit_mode(settings.alias_edit_mode)
    selected_ust_paths = tuple(Path(path) for path in request.selected_ust_paths) if settings.update_ust else None
    return ApplyOptions(
        backup=settings.backup,
        backup_root=resolve_app_path(settings.backup_root, "backup"),
        backup_mode=settings.backup_mode,
        write_csv=settings.write_csv,
        csv_path=settings.csv_path or None,
        merge_csv=settings.merge_csv,
        update_ust=settings.update_ust,
        ust_root=request.ust_root or None,
        selected_ust_paths=selected_ust_paths,
        rename_files=settings.rename_files,
        allow_wav_edit=wav_edit_mode != WavEditMode.DISABLED,
        wav_edit_mode=wav_edit_mode.value,
        allow_alias_edit=alias_edit_mode != WavEditMode.DISABLED,
        alias_edit_mode=alias_edit_mode.value,
        block_on_danger=settings.block_on_danger,
        backup_max_count_enabled=settings.backup_max_count_enabled,
        backup_max_count=settings.backup_max_count,
        utau_plugin_temp_path=request.utau_plugin_temp_path or None,
        strict_voice_match=settings.strict_voice_match,
        utau_exe_path=settings.utau_exe_path or None,
        filename_config=FilenameRewriteConfig(
            rename_related_files=True,
            related_file_patterns=tuple(settings.related_file_patterns),
        ),
        excluded_call_key_moras=settings.excluded_call_key_moras,
    )


# Apply確認用のUST件数を数える
def _apply_ust_write_count(request: ApplyRequest, changes, options: ApplyOptions) -> int:
    if not options.update_ust or not options.ust_root:
        return 0
    try:
        original_entries = _oto_entries(request)
        previews = preview_ust_sync_for_folder(
            options.ust_root,
            request.voice_dir,
            changes,
            entries_before=original_entries,
            strict_voice_match=options.strict_voice_match,
            utau_exe_path=options.utau_exe_path,
            excluded_moras=options.excluded_call_key_moras,
        )
    except (OSError, ValueError):
        return len(request.selected_ust_paths)
    selected = {Path(path).resolve(strict=False) for path in options.selected_ust_paths} if options.selected_ust_paths is not None else None
    if selected is not None:
        previews = [preview for preview in previews if preview.ust_path.resolve(strict=False) in selected]
    return sum(1 for preview in previews if preview.replacements > 0)


# Apply確認用の件数を作る
def apply_summary(request: ApplyRequest) -> dict:
    options = _apply_options(request)
    changes, wav_edit_mode, _alias_edit_mode = _apply_edit_policies([row.to_change() for row in request.rows], options)
    changed_rows = [
        change
        for change in changes
        if change.old_wav != change.new_wav or change.old_alias != change.new_alias
    ]
    file_plans = []
    if (
        options.rename_files
        and options.allow_wav_edit
        and wav_edit_mode != WavEditMode.DISABLED
        and request.voice_dir
    ):
        file_plans = build_file_rename_plan(request.voice_dir, changes, options.filename_config)
    wav_file_count = len({_path.resolve(strict=False) for _path in (plan.old_path for plan in file_plans if plan.kind == "wav")})
    related_file_count = sum(1 for plan in file_plans if plan.kind == "related")
    mrq_file_count = 0
    if request.voice_dir and any(change.old_wav != change.new_wav for change in changes):
        mrq_file_count = int((Path(request.voice_dir) / "desc.mrq").is_file())
    return {
        "voice_dir": request.voice_dir,
        "oto_changed_rows": len(changed_rows),
        "ust_write_count": _apply_ust_write_count(request, changes, options),
        "wav_file_count": wav_file_count,
        "related_file_count": related_file_count,
        "mrq_file_count": mrq_file_count,
    }


# Build apply log path
def _apply_log_path(request: ApplyRequest) -> Path:
    from datetime import datetime

    voice_name = Path(request.voice_dir).name if request.voice_dir else "voice"
    safe_voice_name = "".join(char if char not in '\\/:*?"<>|' else "_" for char in voice_name) or "voice"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return default_logs_dir() / f"aliascale_apply_{safe_voice_name}_{timestamp}.log"


# Execute apply request
def apply(request: ApplyRequest) -> ApplyResponse:
    rows = [row.to_change() for row in request.rows]
    warnings = warnings_from_changes(rows)
    if request.settings.block_on_danger and has_blocking_warnings(warnings):
        return ApplyResponse(errors=("danger warnings remain",))
    options = _apply_options(request)
    result = apply_changes_direct(request.voice_dir, request.oto_path, rows, options)

    log_path_str = ""
    if request.settings.write_debug_log:
        try:
            log_path = _apply_log_path(request)
            save_apply_log(result, log_path, Path(request.voice_dir) if request.voice_dir else None)
            log_path_str = str(log_path)
        except Exception:
            pass

    return ApplyResponse(
        written_files=tuple(str(path) for path in result.written_files),
        moved_to_conflict_folder=tuple(str(path) for path in result.moved_to_conflict_folder),
        backups=tuple(str(backup.backup_path) for backup in result.backups),
        csv_path="" if result.csv_path is None else str(result.csv_path),
        log_path=log_path_str,
        warnings=tuple(result.warnings),
        skipped=tuple(result.skipped),
        errors=tuple(result.errors),
    )
