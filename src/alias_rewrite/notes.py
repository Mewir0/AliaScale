from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import math
import re


NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
NOTE_TO_PITCH_CLASS = {name: index for index, name in enumerate(NOTE_NAMES)}
NOTE_RE = re.compile(r"^(?P<name>[A-G](?:#|b)?)(?P<octave>-?\d+)$")
FLAT_TO_SHARP = {
    "Db": "C#",
    "Eb": "D#",
    "Gb": "F#",
    "Ab": "G#",
    "Bb": "A#",
}


@dataclass(frozen=True)
# 音符設定を保持する
class NoteMappingConfig:
    mode: str = "semitone"
    allowed_pitch_classes: tuple[str, ...] | None = None
    explicit_notes: tuple[str, ...] | None = None
    a4_frequency: float = 440.0


# MIDI音符を処理する
def _midi_to_note(midi: int) -> str:
    note = NOTE_NAMES[midi % 12]
    octave = midi // 12 - 1
    return f"{note}{octave}"


# 音高を正規化する
def _normalize_pitch_class(name: str) -> str:
    name = name.strip()
    return FLAT_TO_SHARP.get(name, name)


# 音符MIDIを処理する
def _note_to_midi(note: str) -> int:
    match = NOTE_RE.match(note.strip())
    if not match:
        raise ValueError(f"Invalid note name: {note!r}")
    pitch_class = _normalize_pitch_class(match.group("name"))
    if pitch_class not in NOTE_TO_PITCH_CLASS:
        raise ValueError(f"Unknown pitch class: {pitch_class!r}")
    octave = int(match.group("octave"))
    return (octave + 1) * 12 + NOTE_TO_PITCH_CLASS[pitch_class]


# MIDIを処理する
def _nearest_allowed_midi(raw_midi: float, allowed_pitch_classes: tuple[str, ...]) -> int:
    normalized = tuple(_normalize_pitch_class(name) for name in allowed_pitch_classes)
    unknown = set(normalized) - set(NOTE_NAMES)
    if unknown:
        raise ValueError(f"Unknown pitch classes: {sorted(unknown)}")
    allowed = {NOTE_TO_PITCH_CLASS[name] for name in normalized}
    center = int(round(raw_midi))
    candidates = range(center - 12, center + 13)
    return min(
        (midi for midi in candidates if midi % 12 in allowed),
        key=lambda midi: (abs(raw_midi - midi), midi),
    )


# 音符を処理する
def freq_to_note(freq: float | None, config: NoteMappingConfig | None = None) -> Optional[str]:
    """Convert frequency in Hz to a rounded equal-tempered note name."""
    config = config or NoteMappingConfig()
    if freq is None or not math.isfinite(freq) or freq <= 0:
        return None
    if config.a4_frequency <= 0:
        raise ValueError("a4_frequency must be positive")

    raw_midi = 69 + 12 * math.log2(freq / config.a4_frequency)
    if config.mode == "semitone":
        midi = int(round(raw_midi))
    elif config.mode == "whole_tone":
        midi = _nearest_allowed_midi(raw_midi, ("C", "D", "E", "F", "G", "A", "B"))
    elif config.mode == "allowed_notes":
        if not config.allowed_pitch_classes:
            raise ValueError("allowed_pitch_classes is required for allowed_notes mode")
        midi = _nearest_allowed_midi(raw_midi, config.allowed_pitch_classes)
    elif config.mode == "pitch_classes":
        if not config.allowed_pitch_classes:
            raise ValueError("allowed_pitch_classes is required for pitch_classes mode")
        midi = _nearest_allowed_midi(raw_midi, config.allowed_pitch_classes)
    elif config.mode == "explicit_notes":
        if not config.explicit_notes:
            raise ValueError("explicit_notes is required for explicit_notes mode")
        candidates = [_note_to_midi(note) for note in config.explicit_notes]
        midi = min(candidates, key=lambda candidate: (abs(raw_midi - candidate), candidate))
    else:
        raise ValueError(f"Unsupported note mapping mode: {config.mode}")
    return _midi_to_note(midi)
