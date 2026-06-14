from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import struct


@dataclass(frozen=True)
# 周波数表記録を保持する
class FrqRecord:
    wav_name: str
    sample_rate: int
    hop_size: int
    f0_values: tuple[float, ...]
    average_frequency: float | None = None


# 周波数表エラーを表す
class FrqParseError(ValueError):
    pass


# wav名前周波数表パスを処理する
def _wav_name_from_frq_path(path: Path) -> str:
    stem = path.stem
    if stem.endswith("_wav"):
        stem = stem[:-4]
    return stem + ".wav"


# wav名前ピッチマークパスを処理する
def _wav_name_from_pmk_path(path: Path) -> str:
    stem = path.stem
    if stem.endswith("_wav"):
        stem = stem[:-4]
    return stem + ".wav"


# 周波数表を読み込む
def parse_frq(path: str | Path, *, sample_rate: int = 44100) -> FrqRecord:
    """Read an UTAU FREQ0003 .frq file.

    UTAU .frq files are stored per wav. The format used by classic UTAU starts
    with FREQ0003, an int32 hop size in samples, a double average frequency, a
    frame count at byte 36, then frame pairs of two float64 values. In UTAU
    FREQ0003 files, the first value of each pair is the per-frame F0.
    """
    path = Path(path)
    data = path.read_bytes()
    if len(data) < 40 or data[:8] != b"FREQ0003":
        raise FrqParseError(f"Not an UTAU FREQ0003 frq file: {path}")

    hop_size = struct.unpack_from("<i", data, 8)[0]
    if hop_size <= 0:
        raise FrqParseError(f"Invalid frq hop size: {hop_size}")

    average_frequency = struct.unpack_from("<d", data, 12)[0]
    frame_count = struct.unpack_from("<i", data, 36)[0]
    if frame_count < 0:
        raise FrqParseError(f"Invalid frq frame count: {frame_count}")

    pos = 40
    values: list[float] = []
    for _index in range(frame_count):
        if pos + 16 > len(data):
            break
        frequency = struct.unpack_from("<d", data, pos)[0]
        values.append(float(frequency))
        pos += 16

    return FrqRecord(
        wav_name=_wav_name_from_frq_path(path),
        sample_rate=sample_rate,
        hop_size=hop_size,
        f0_values=tuple(values),
        average_frequency=float(average_frequency) if average_frequency > 0 else None,
    )


# 周波数表フォルダを読み込む
def parse_frq_directory(path: str | Path, *, sample_rate: int = 44100) -> list[FrqRecord]:
    root = Path(path)
    records: list[FrqRecord] = []
    for frq_path in sorted(root.rglob("*_wav.frq")):
        try:
            record = parse_frq(frq_path, sample_rate=sample_rate)
            relative = frq_path.relative_to(root)
            stem = relative.stem
            stem = stem[:-4]
            relative_wav = relative.with_name(stem + ".wav")
            records.append(replace(record, wav_name=str(relative_wav)))
        except (OSError, FrqParseError):
            continue
    return records


# PMKピッチマークを読み込む
def parse_pmk(path: str | Path, *, sample_rate: int = 44100, hop_size: int = 256) -> FrqRecord:
    """Read an UTAU-style .pmk pitch-mark file as an F0 record.

    The observed PMK structure stores a count at byte 10 and then int32 pairs
    from byte 14. Each pair is sample position and period in samples. AliaScale
    converts these irregular pitch marks into hop-sized F0 frames so the common
    pitch estimator can use them.
    """
    path = Path(path)
    data = path.read_bytes()
    if len(data) < 14:
        raise FrqParseError(f"Invalid PMK file: {path}")

    mark_count = struct.unpack_from("<i", data, 10)[0]
    if mark_count <= 0 or 14 + mark_count * 8 > len(data):
        raise FrqParseError(f"Invalid PMK mark count: {mark_count}")
    if sample_rate <= 0 or hop_size <= 0:
        raise FrqParseError(f"Invalid PMK sample_rate/hop_size: {sample_rate}/{hop_size}")

    marks: list[tuple[int, float]] = []
    pos = 14
    for _index in range(mark_count):
        sample_pos, period = struct.unpack_from("<ii", data, pos)
        pos += 8
        if sample_pos < 0 or period <= 0:
            continue
        marks.append((sample_pos, sample_rate / period))

    if not marks:
        return FrqRecord(wav_name=_wav_name_from_pmk_path(path), sample_rate=sample_rate, hop_size=hop_size, f0_values=())

    values: list[float] = []
    mark_index = 0
    max_sample = marks[-1][0]
    for frame_sample in range(0, max_sample + hop_size, hop_size):
        while mark_index + 1 < len(marks) and marks[mark_index + 1][0] <= frame_sample:
            mark_index += 1
        values.append(float(marks[mark_index][1]))

    return FrqRecord(
        wav_name=_wav_name_from_pmk_path(path),
        sample_rate=sample_rate,
        hop_size=hop_size,
        f0_values=tuple(values),
    )


# PMKフォルダを読み込む
def parse_pmk_directory(path: str | Path, *, sample_rate: int = 44100, hop_size: int = 256) -> list[FrqRecord]:
    root = Path(path)
    records: list[FrqRecord] = []
    for pmk_path in sorted(root.rglob("*_wav.pmk")):
        try:
            record = parse_pmk(pmk_path, sample_rate=sample_rate, hop_size=hop_size)
            relative = pmk_path.relative_to(root)
            stem = relative.stem
            stem = stem[:-4]
            relative_wav = relative.with_name(stem + ".wav")
            records.append(replace(record, wav_name=str(relative_wav)))
        except (OSError, FrqParseError):
            continue
    return records
