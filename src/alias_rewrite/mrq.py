from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import math
import struct
from uuid import uuid4


@dataclass(frozen=True)
# MRQ記録を保持する
class MrqRecord:
    wav_name: str
    sample_rate: int
    hop_size: int
    f0_values: tuple[float, ...]
    timestamp: Optional[int] = None
    modified: Optional[int] = None


@dataclass(frozen=True)
# MRQ内wav名更新結果を保持する
class MrqRewriteResult:
    path: Path
    rewritten: int


# MRQエラーを表す
class MrqParseError(ValueError):
    pass


# 値を読み込む
def _read_i32(data: bytes, pos: int) -> tuple[int, int]:
    if pos + 4 > len(data):
        raise MrqParseError(f"Unexpected end of file at byte {pos}")
    return struct.unpack_from("<i", data, pos)[0], pos + 4


# MRQ名前を復号する
def _decode_mrq_name(raw: bytes) -> str:
    return raw.decode("utf-16le", errors="replace").rstrip("\x00")


# MRQ名前を符号化する
def _encode_mrq_name(name: str) -> bytes:
    return name.encode("utf-16le")


# MRQを読み込む
def parse_mrq(path: str | Path) -> list[MrqRecord]:
    """Read a Moresampler .mrq file and return wav-level F0 records."""
    path = Path(path)
    data = path.read_bytes()
    if len(data) < 12 or data[:4] != b"mrq ":
        raise MrqParseError(f"Not an mrq file: {path}")

    pos = 4
    version, pos = _read_i32(data, pos)
    entry_count, pos = _read_i32(data, pos)
    if version < 1:
        raise MrqParseError(f"Unsupported mrq version: {version}")
    if entry_count < 0:
        raise MrqParseError(f"Invalid entry count: {entry_count}")

    records: list[MrqRecord] = []
    for entry_index in range(entry_count):
        entry_start = pos
        nfilename, pos = _read_i32(data, pos)
        if nfilename < 0:
            raise MrqParseError(f"Invalid filename length at entry {entry_index}: {nfilename}")

        name_bytes_len = nfilename * 2
        if pos + name_bytes_len > len(data):
            raise MrqParseError(f"Filename exceeds file length at entry {entry_index}")
        wav_name = _decode_mrq_name(data[pos : pos + name_bytes_len])
        pos += name_bytes_len

        data_size, pos = _read_i32(data, pos)
        if data_size < 12:
            raise MrqParseError(f"Invalid data trunk size at entry {entry_index}: {data_size}")
        data_start = pos
        data_end = data_start + data_size
        if data_end > len(data):
            raise MrqParseError(f"Entry {entry_index} exceeds file length")

        nf0, pos = _read_i32(data, pos)
        sample_rate, pos = _read_i32(data, pos)
        hop_size, pos = _read_i32(data, pos)
        if nf0 < 0:
            raise MrqParseError(f"Invalid nf0 at entry {entry_index}: {nf0}")

        f0_bytes_len = nf0 * 4
        if pos + f0_bytes_len > data_end:
            raise MrqParseError(f"F0 array exceeds entry data at entry {entry_index}")
        f0_values = struct.unpack_from(f"<{nf0}f", data, pos) if nf0 else ()
        pos += f0_bytes_len

        timestamp = None
        modified = None
        if data_size >= 20 + nf0 * 4 and pos + 8 <= data_end:
            timestamp, pos = _read_i32(data, pos)
            modified, pos = _read_i32(data, pos)

        pos = data_end
        if wav_name and wav_name[0] != "\x00":
            records.append(
                MrqRecord(
                    wav_name=wav_name,
                    sample_rate=sample_rate,
                    hop_size=hop_size,
                    f0_values=tuple(float(v) for v in f0_values),
                    timestamp=timestamp,
                    modified=modified,
                )
            )

        if pos <= entry_start:
            raise MrqParseError(f"Parser did not advance at entry {entry_index}")

    return records


# MRQ内のwav名だけを書き換える
def rewrite_mrq_wav_names(path: str | Path, wav_name_map: dict[str, str]) -> MrqRewriteResult:
    path = Path(path)
    if not wav_name_map:
        return MrqRewriteResult(path=path, rewritten=0)

    data = path.read_bytes()
    if len(data) < 12 or data[:4] != b"mrq ":
        raise MrqParseError(f"Not an mrq file: {path}")

    pos = 4
    version, pos = _read_i32(data, pos)
    entry_count, pos = _read_i32(data, pos)
    if version < 1:
        raise MrqParseError(f"Unsupported mrq version: {version}")
    if entry_count < 0:
        raise MrqParseError(f"Invalid entry count: {entry_count}")

    output = bytearray(data[:12])
    rewritten = 0
    for entry_index in range(entry_count):
        nfilename, pos = _read_i32(data, pos)
        if nfilename < 0:
            raise MrqParseError(f"Invalid filename length at entry {entry_index}: {nfilename}")

        name_bytes_len = nfilename * 2
        if pos + name_bytes_len > len(data):
            raise MrqParseError(f"Filename exceeds file length at entry {entry_index}")
        old_name = _decode_mrq_name(data[pos : pos + name_bytes_len])
        pos += name_bytes_len

        data_size, data_size_pos = _read_i32(data, pos)
        if data_size < 12:
            raise MrqParseError(f"Invalid data trunk size at entry {entry_index}: {data_size}")
        data_start = data_size_pos
        data_end = data_start + data_size
        if data_end > len(data):
            raise MrqParseError(f"Entry {entry_index} exceeds file length")

        new_name = wav_name_map.get(old_name, old_name)
        if new_name != old_name:
            rewritten += 1
        name_bytes = _encode_mrq_name(new_name)
        output.extend(struct.pack("<i", len(name_bytes) // 2))
        output.extend(name_bytes)
        output.extend(struct.pack("<i", data_size))
        output.extend(data[data_start:data_end])
        pos = data_end

    output.extend(data[pos:])
    if rewritten:
        temp_path = path.with_name(f".aliascale_tmp_{uuid4().hex}_{path.name}")
        temp_path.write_bytes(bytes(output))
        temp_path.replace(path)
    return MrqRewriteResult(path=path, rewritten=rewritten)


# F0値一覧を返す
def valid_f0_values(values) -> list[float]:
    return [float(v) for v in values if isinstance(v, (int, float)) and math.isfinite(v) and v > 0.0]
