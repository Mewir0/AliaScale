from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import math

from .notes import freq_to_note
from .oto import iter_entries, parse_oto_file
from .pitch import build_frequency_index, load_frequency_records, estimate_pitch_for_entry


@dataclass(frozen=True)
# 音高を保持する
class PitchDistributionBin:
    label: str
    min_frequency: float
    max_frequency: float
    count: int


@dataclass(frozen=True)
# 音源情報を保持する
class VoiceInformation:
    voice_name: str
    wav_file_count: int
    oto_entry_count: int
    alias_count: int
    empty_alias_count: int
    frequency_table_type: str = ""
    frequency_min: float | None = None
    frequency_max: float | None = None
    frequency_average: float | None = None
    pitch_distribution: tuple[PitchDistributionBin, ...] = ()


# 音符周波数を処理する
def _note_frequency(note_index: int, octave: int) -> float:
    midi = (octave + 1) * 12 + note_index
    return 440.0 * (2 ** ((midi - 69) / 12))


# 値を作る
def _build_c1_b6_bins(frequencies: list[float]) -> tuple[PitchDistributionBin, ...]:
    note_names = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
    counts = {f"{name}{octave}": 0 for octave in range(1, 7) for name in note_names}
    for frequency in frequencies:
        note = freq_to_note(frequency)
        if note in counts:
            counts[note] += 1

    bins: list[PitchDistributionBin] = []
    for octave in range(1, 7):
        for index, name in enumerate(note_names):
            label = f"{name}{octave}"
            center = _note_frequency(index, octave)
            half_step = 2 ** (1 / 24)
            bins.append(
                PitchDistributionBin(
                    label=label,
                    min_frequency=center / half_step,
                    max_frequency=center * half_step,
                    count=counts[label],
                )
            )
    return tuple(bins)


# 音源情報を作る
def build_voice_information(
    voice_dir: str | Path,
    oto_path: str | Path | None = None,
    *,
    mrq_path: str | Path | None = None,
    frequency_source: str = "mrq",
) -> VoiceInformation:
    voice_dir = Path(voice_dir)
    oto_path = Path(oto_path) if oto_path else voice_dir / "oto.ini"
    lines, _ = parse_oto_file(oto_path)
    entries = iter_entries(lines)
    wav_file_count = sum(1 for path in voice_dir.rglob("*.wav") if path.is_file())
    alias_count = sum(1 for entry in entries if entry.alias)
    empty_alias_count = sum(1 for entry in entries if not entry.alias)

    frequencies: list[float] = []
    frequency_table_type = ""
    if mrq_path or frequency_source in {"frq", "utau_frq", "pmk", "utau_pmk", "auto", "auto_f0", "estimated"}:
        records, frequency_table_type = load_frequency_records(
            source=frequency_source,
            voice_dir=voice_dir,
            entries=entries,
            table_path=mrq_path,
        )
        mrq_index = build_frequency_index(records)
        for entry in entries:
            pitch = estimate_pitch_for_entry(entry, mrq_index)
            frequency = pitch.get("frequency")
            if isinstance(frequency, (int, float)):
                frequencies.append(float(frequency))

    finite_frequencies = [freq for freq in frequencies if math.isfinite(freq) and freq > 0]
    return VoiceInformation(
        voice_name=voice_dir.name,
        wav_file_count=wav_file_count,
        oto_entry_count=len(entries),
        alias_count=alias_count,
        empty_alias_count=empty_alias_count,
        frequency_table_type=frequency_table_type,
        frequency_min=min(finite_frequencies) if finite_frequencies else None,
        frequency_max=max(finite_frequencies) if finite_frequencies else None,
        frequency_average=(sum(finite_frequencies) / len(finite_frequencies)) if finite_frequencies else None,
        pitch_distribution=_build_c1_b6_bins(finite_frequencies),
    )
