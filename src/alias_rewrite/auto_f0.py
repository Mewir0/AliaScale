from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import math
import struct
import wave


@dataclass(frozen=True)
# F0記録を保持する
class AutoF0Record:
    wav_name: str
    sample_rate: int
    hop_size: int
    f0_values: tuple[float, ...]


# PCMを復号する
def _decode_pcm(raw: bytes, sample_width: int) -> list[float]:
    if sample_width == 1:
        return [(value - 128) / 128.0 for value in raw]
    if sample_width == 2:
        count = len(raw) // 2
        return [value / 32768.0 for value in struct.unpack_from(f"<{count}h", raw)]
    if sample_width == 3:
        values = []
        for index in range(0, len(raw) - 2, 3):
            chunk = raw[index : index + 3]
            sign = b"\xff" if chunk[2] & 0x80 else b"\x00"
            values.append(int.from_bytes(chunk + sign, "little", signed=True) / 8388608.0)
        return values
    if sample_width == 4:
        count = len(raw) // 4
        return [value / 2147483648.0 for value in struct.unpack_from(f"<{count}i", raw)]
    return []


# 値を変換する
def _to_mono(samples: list[float], channels: int) -> list[float]:
    if channels <= 1:
        return samples
    mono = []
    for index in range(0, len(samples) - channels + 1, channels):
        mono.append(sum(samples[index : index + channels]) / channels)
    return mono


# F0を処理する
def _estimate_frame_f0(
    frame: list[float],
    sample_rate: int,
    *,
    min_frequency: float,
    max_frequency: float,
    threshold: float,
) -> float:
    if not frame:
        return 0.0
    mean = sum(frame) / len(frame)
    centered = [value - mean for value in frame]
    energy = sum(value * value for value in centered)
    if energy <= 1e-8:
        return 0.0

    min_lag = max(1, int(sample_rate / max_frequency))
    max_lag = min(len(centered) - 2, int(sample_rate / min_frequency))
    if max_lag <= min_lag:
        return 0.0

    best_lag = 0
    best_score = 0.0
    for lag in range(min_lag, max_lag + 1):
        score = 0.0
        limit = len(centered) - lag
        for index in range(limit):
            score += centered[index] * centered[index + lag]
        normalized = score / energy
        if normalized > best_score:
            best_score = normalized
            best_lag = lag

    if best_lag <= 0 or best_score < threshold:
        return 0.0
    return sample_rate / best_lag


# F0wavを処理する
def estimate_f0_for_wav(
    wav_path: str | Path,
    *,
    wav_name: str | None = None,
    hop_size: int = 256,
    window_size: int = 2048,
    min_frequency: float = 50.0,
    max_frequency: float = 1000.0,
    threshold: float = 0.28,
) -> AutoF0Record:
    path = Path(wav_path)
    with wave.open(str(path), "rb") as wav:
        sample_rate = wav.getframerate()
        channels = wav.getnchannels()
        sample_width = wav.getsampwidth()
        raw = wav.readframes(wav.getnframes())

    samples = _to_mono(_decode_pcm(raw, sample_width), channels)
    if not samples or sample_rate <= 0:
        return AutoF0Record(wav_name=wav_name or path.name, sample_rate=max(sample_rate, 1), hop_size=hop_size, f0_values=())

    downsample = max(1, int(sample_rate // 11025))
    effective_rate = sample_rate
    effective_hop = hop_size
    effective_window = window_size
    if downsample > 1:
        samples = samples[::downsample]
        effective_rate = sample_rate // downsample
        effective_hop = max(1, hop_size // downsample)
        effective_window = max(512, window_size // downsample)

    values: list[float] = []
    half_window = effective_window // 2
    for center in range(0, len(samples), effective_hop):
        start = max(0, center - half_window)
        end = min(len(samples), start + effective_window)
        frame = samples[start:end]
        if len(frame) < effective_window // 2:
            values.append(0.0)
            continue
        rms = math.sqrt(sum(value * value for value in frame) / len(frame))
        if rms < 0.003:
            values.append(0.0)
            continue
        values.append(
            _estimate_frame_f0(
                frame,
                effective_rate,
                min_frequency=min_frequency,
                max_frequency=max_frequency,
                threshold=threshold,
            )
        )

    return AutoF0Record(wav_name=wav_name or path.name, sample_rate=effective_rate, hop_size=effective_hop, f0_values=tuple(values))


# F0を処理する
def estimate_f0_for_wavs(voice_dir: str | Path, wav_names: set[str]) -> list[AutoF0Record]:
    root = Path(voice_dir)
    records: list[AutoF0Record] = []
    for wav_name in sorted(wav_names):
        wav_path = root / wav_name
        if not wav_path.exists():
            continue
        try:
            records.append(estimate_f0_for_wav(wav_path, wav_name=wav_name))
        except (OSError, wave.Error):
            continue
    return records
