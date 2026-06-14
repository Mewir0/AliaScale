from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .keys import resolve_call_key
from .oto import OtoEntry, decode_text
from .voice_scan import strict_voice_dir_matches, voice_dir_matches


@dataclass(frozen=True)
# Store one UST lyric occurrence
class UstLyricOccurrence:
    lyric: str
    note_id: str = ""


@dataclass(frozen=True)
# Store unresolved UST lyric details
class UnresolvedLyric:
    ust_path: Path
    lyric: str
    note_id: str = ""


@dataclass(frozen=True)
# Store per UST usage for one oto line
class UstLineUsage:
    ust_path: Path
    count: int = 0


@dataclass(frozen=True)
# Store lightweight UST information
class UstUsageDocument:
    path: Path
    voice_dir_value: str | None
    lyrics: tuple[UstLyricOccurrence, ...]
    mtime_ns: int
    size: int


@dataclass(frozen=True)
# Store UST usage aggregation result
class UstUsageSummary:
    usage_by_line: dict[int, int]
    usage_by_line_ust: dict[int, tuple[UstLineUsage, ...]]
    matched_paths: tuple[Path, ...]
    selected_paths: tuple[Path, ...]
    unresolved_lyrics: tuple[UnresolvedLyric, ...]
    total_lyrics: int = 0
    resolved_lyrics: int = 0


_UST_USAGE_CACHE: dict[tuple[str, int, int], UstUsageDocument] = {}


# Normalize paths for selection comparison
def _resolved(path: str | Path) -> Path:
    return Path(str(path).strip().strip('"')).expanduser().resolve(strict=False)


# Read only VoiceDir and Lyric lines from UST
def read_ust_usage_document(path: str | Path) -> UstUsageDocument:
    path = Path(path)
    stat = path.stat()
    cache_key = (str(path.resolve(strict=False)), stat.st_mtime_ns, stat.st_size)
    cached = _UST_USAGE_CACHE.get(cache_key)
    if cached is not None:
        return cached

    text, _encoding = decode_text(path.read_bytes())
    voice_dir_value: str | None = None
    lyrics: list[UstLyricOccurrence] = []
    current_note_id = ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[#") and stripped.endswith("]"):
            current_note_id = stripped
            continue
        if voice_dir_value is None and line.startswith("VoiceDir="):
            voice_dir_value = line[len("VoiceDir=") :]
            continue
        if line.startswith("Lyric="):
            lyrics.append(UstLyricOccurrence(line[len("Lyric=") :], current_note_id))

    document = UstUsageDocument(
        path=path,
        voice_dir_value=voice_dir_value,
        lyrics=tuple(lyrics),
        mtime_ns=stat.st_mtime_ns,
        size=stat.st_size,
    )
    _UST_USAGE_CACHE[cache_key] = document
    return document


# Check whether UST belongs to the target voice
def _matches_voice(
    document: UstUsageDocument,
    voice_dir: str | Path,
    *,
    strict_voice_match: bool,
    utau_exe_path: str | Path | None,
) -> bool:
    if strict_voice_match:
        return strict_voice_dir_matches(document.voice_dir_value, voice_dir, utau_exe_path=utau_exe_path)
    return voice_dir_matches(document.voice_dir_value, voice_dir)


# Scan UST files with lightweight cached parsing
def scan_ust_usage_documents(
    ust_root: str | Path,
    voice_dir: str | Path,
    *,
    strict_voice_match: bool = False,
    utau_exe_path: str | Path | None = None,
) -> tuple[UstUsageDocument, ...]:
    ust_root = Path(ust_root)
    documents: list[UstUsageDocument] = []
    if not ust_root.is_dir():
        return ()
    for path in ust_root.rglob("*.ust"):
        try:
            document = read_ust_usage_document(path)
        except OSError:
            continue
        if _matches_voice(
            document,
            voice_dir,
            strict_voice_match=strict_voice_match,
            utau_exe_path=utau_exe_path,
        ):
            documents.append(document)
    return tuple(documents)


# Aggregate UST lyric usage by oto line
def collect_ust_usage(
    ust_root: str | Path,
    voice_dir: str | Path,
    entries: list[OtoEntry],
    *,
    selected_ust_paths: tuple[str | Path, ...] | None = None,
    excluded_ust_paths: tuple[str | Path, ...] = (),
    strict_voice_match: bool = False,
    utau_exe_path: str | Path | None = None,
) -> UstUsageSummary:
    documents = scan_ust_usage_documents(
        ust_root,
        voice_dir,
        strict_voice_match=strict_voice_match,
        utau_exe_path=utau_exe_path,
    )
    selected_set = None
    if selected_ust_paths is not None:
        selected_set = {_resolved(path) for path in selected_ust_paths}
    excluded_set = {_resolved(path) for path in excluded_ust_paths}

    usage_by_line: dict[int, int] = {}
    usage_by_line_ust: dict[int, dict[Path, int]] = {}
    unresolved_by_key: dict[tuple[Path, str, str], int] = {}
    selected_paths: list[Path] = []
    total_lyrics = 0
    resolved_lyrics = 0

    for document in documents:
        resolved_path = _resolved(document.path)
        if resolved_path in excluded_set:
            continue
        if selected_set is not None and resolved_path not in selected_set:
            continue
        selected_paths.append(document.path)
        for occurrence in document.lyrics:
            total_lyrics += 1
            entry = resolve_call_key(occurrence.lyric, entries)
            if entry is None:
                if occurrence.lyric == "R":
                    continue
                key = (document.path, occurrence.lyric, occurrence.note_id)
                unresolved_by_key[key] = unresolved_by_key.get(key, 0) + 1
                continue
            resolved_lyrics += 1
            usage_by_line[entry.line_number] = usage_by_line.get(entry.line_number, 0) + 1
            per_ust = usage_by_line_ust.setdefault(entry.line_number, {})
            per_ust[document.path] = per_ust.get(document.path, 0) + 1

    unresolved = tuple(
        UnresolvedLyric(path, lyric, note_id)
        for (path, lyric, note_id), count in sorted(
            unresolved_by_key.items(),
            key=lambda item: (str(item[0][0]), item[0][2], item[0][1]),
        )
    )
    usage_detail = {
        line_number: tuple(
            UstLineUsage(path, count)
            for path, count in sorted(per_ust.items(), key=lambda item: str(item[0]))
        )
        for line_number, per_ust in usage_by_line_ust.items()
    }
    return UstUsageSummary(
        usage_by_line=usage_by_line,
        usage_by_line_ust=usage_detail,
        matched_paths=tuple(document.path for document in documents),
        selected_paths=tuple(selected_paths),
        unresolved_lyrics=unresolved,
        total_lyrics=total_lyrics,
        resolved_lyrics=resolved_lyrics,
    )
