from __future__ import annotations

from pathlib import Path
from statistics import median
from typing import Optional

import math

from .auto_f0 import estimate_f0_for_wavs
from .frq import parse_frq_directory, parse_pmk_directory
from .mrq import MrqRecord, valid_f0_values
from .notes import NoteMappingConfig, freq_to_note
from .oto import OtoEntry


FREQUENCY_ERROR_KEY = "__aliascale_frequency_error__"


# MRQ索引を作る
def build_mrq_index(records: list[MrqRecord]) -> dict[str, MrqRecord]:
    return build_frequency_index(records)


# 周波数索引を作る
def build_frequency_index(records) -> dict[str, object]:
    index: dict[str, MrqRecord] = {}
    for record in records:
        keys = {
            record.wav_name,
            record.wav_name.replace("/", "\\"),
            Path(record.wav_name).name,
        }
        for key in keys:
            if key:
                index.setdefault(key.lower(), record)
    return index


# MRQ記録を探す
def find_mrq_record(entry: OtoEntry, mrq_index: dict[str, MrqRecord]) -> Optional[MrqRecord]:
    return find_frequency_record(entry, mrq_index)


# 周波数記録を探す
def find_frequency_record(entry: OtoEntry, record_index: dict[str, object]):
    candidates = (
        entry.wav_name,
        entry.wav_name.replace("/", "\\"),
        Path(entry.wav_name).name,
    )
    for candidate in candidates:
        record = record_index.get(candidate.lower())
        if record is not None:
            return record
    return None


# 値を処理する
def _time_ms_to_frame(time_ms: float, record: MrqRecord) -> int:
    if not math.isfinite(time_ms):
        return 0
    return int(math.floor(time_ms / 1000.0 * record.sample_rate / record.hop_size))


# 記録長さを処理する
def _record_duration_ms(record) -> float:
    if record.sample_rate <= 0 or record.hop_size <= 0:
        return 0.0
    return len(record.f0_values) * record.hop_size / record.sample_rate * 1000.0


# 行を処理する
def _entry_end_ms(entry: OtoEntry, record) -> float:
    duration = _record_duration_ms(record)
    if math.isfinite(entry.cutoff) and entry.cutoff < 0:
        return entry.offset + abs(entry.cutoff)
    if math.isfinite(entry.cutoff) and entry.cutoff > 0 and duration > 0:
        return max(entry.offset, duration - entry.cutoff)
    if math.isfinite(entry.consonant) and entry.consonant > 0:
        return entry.offset + entry.consonant
    return duration


# 音高行を処理する
def estimate_pitch_for_entry(
    entry: OtoEntry,
    mrq_records: list[MrqRecord] | dict[str, MrqRecord],
    min_valid_frames: int = 1,
    note_config: NoteMappingConfig | None = None,
) -> dict[str, object]:
    """Estimate representative pitch for one oto entry."""
    mrq_index = mrq_records if isinstance(mrq_records, dict) else build_frequency_index(mrq_records)
    error_status = mrq_index.get(FREQUENCY_ERROR_KEY)
    if error_status:
        return {
            "wav": entry.wav_name,
            "alias": entry.alias,
            "frequency": None,
            "note": None,
            "valid_frame_count": 0,
            "status": str(error_status),
        }
    record = find_frequency_record(entry, mrq_index)
    if record is None:
        return {
            "wav": entry.wav_name,
            "alias": entry.alias,
            "frequency": None,
            "note": None,
            "valid_frame_count": 0,
            "status": "no_freq_src",
        }

    start_frame = max(0, _time_ms_to_frame(entry.offset, record))
    end_ms = _entry_end_ms(entry, record)
    end_frame = max(start_frame + 1, _time_ms_to_frame(end_ms, record))
    end_frame = min(end_frame, len(record.f0_values))

    window_values = valid_f0_values(record.f0_values[start_frame:end_frame])
    status = "system"
    if len(window_values) < min_valid_frames:
        status = "no_valid_f0" if window_values else "no_f0"
        window_values = []

    frequency = float(median(window_values)) if window_values else None
    return {
        "wav": entry.wav_name,
        "alias": entry.alias,
        "frequency": frequency,
        "note": freq_to_note(frequency, note_config),
        "valid_frame_count": len(window_values),
        "status": status,
    }


# 周波数記録一覧を読み込む
def load_frequency_records(
    *,
    source: str,
    voice_dir: str | Path,
    entries: list[OtoEntry],
    table_path: str | Path | None = None,
):
    normalized = (source or "mrq").lower()
    if normalized in {"mrq", "moresampler_mrq"}:
        if table_path is None:
            raise ValueError("MRQ frequency source requires a table path")
        from .mrq import parse_mrq

        return parse_mrq(table_path), "moresampler MRQ"
    if normalized in {"frq", "utau_frq"}:
        return parse_frq_directory(voice_dir), "UTAU FRQ"
    if normalized in {"pmk", "utau_pmk"}:
        return parse_pmk_directory(voice_dir), "UTAU PMK"
    if normalized in {"auto", "auto_f0", "estimated"}:
        wav_names = {entry.wav_name for entry in entries if entry.wav_name}
        return estimate_f0_for_wavs(voice_dir, wav_names), "Auto F0"
    raise ValueError(f"Unsupported frequency source: {source}")
