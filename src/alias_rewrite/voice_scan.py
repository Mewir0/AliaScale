from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from .oto import decode_text
from .ust import get_setting_value, parse_ust_file


@dataclass(frozen=True)
# UST結果を保持する
class UstScanResult:
    path: Path
    voice_dir_value: str | None
    matched: bool


@dataclass(frozen=True)
# UTAU音源を保持する
class UtauVoice:
    display_name: str
    path: Path
    source: str


OTOLIST_RE = re.compile(r"^OtoList(?P<index>\d+)=(?P<value>.*)$")


# パス本文を正規化する
def _normalize_path_text(value: str) -> str:
    return value.replace("/", "\\").rstrip("\\").lower()


# VoiceDirを簡易的に照合する
def voice_dir_matches(value: str | None, voice_dir: str | Path) -> bool:
    if not value:
        return False
    voice_dir = Path(voice_dir)
    normalized_value = _normalize_path_text(value)
    normalized_name = _normalize_path_text(voice_dir.name)
    normalized_full = _normalize_path_text(str(voice_dir))
    return normalized_value.endswith(normalized_name) or normalized_full.endswith(normalized_value)


# パスを処理する
def _resolved_path(path: str | Path) -> Path:
    return Path(str(path).strip().strip('"')).expanduser().resolve(strict=False)


# パスを処理する
def _same_path(left: str | Path, right: str | Path) -> bool:
    return _resolved_path(left) == _resolved_path(right)


# 本文を読み込む
def _read_text_if_exists(path: Path) -> str:
    if not path.is_file():
        return ""
    text, _encoding = decode_text(path.read_bytes())
    return text


# キャラクター名前を処理する
def _character_name(voice_dir: Path) -> str:
    text = _read_text_if_exists(voice_dir / "character.txt")
    for line in text.splitlines():
        if line.startswith("name="):
            return line[len("name=") :].strip()
    return ""


# wavを処理する
def _contains_wav(path: Path) -> bool:
    if not path.is_dir():
        return False
    try:
        return any(item.is_file() for item in path.rglob("*.wav"))
    except OSError:
        return False


# 設定行一覧を処理する
def _setting_lines(utau_exe_path: str | Path | None) -> list[str]:
    if not utau_exe_path:
        return []
    setting_path = Path(utau_exe_path).with_name("setting.ini")
    text = _read_text_if_exists(setting_path)
    return text.splitlines()


# 音源ルートを処理する
def _voice_root(utau_exe_path: str | Path, setting_lines: list[str]) -> Path:
    for line in setting_lines:
        if line.startswith("VoiceRoot="):
            value = line[len("VoiceRoot=") :].strip()
            if value:
                return _resolved_path(value)
    return _resolved_path(Path(utau_exe_path).with_name("voice"))


# 個別登録を処理する
def _registered_voices(setting_lines: list[str]) -> list[tuple[int, str, Path]]:
    voices: list[tuple[int, str, Path]] = []
    for line in setting_lines:
        match = OTOLIST_RE.match(line)
        if not match:
            continue
        value = match.group("value")
        if "," not in value:
            continue
        name, raw_path = value.split(",", 1)
        voices.append((int(match.group("index")), name.strip(), _resolved_path(raw_path)))
    return sorted(voices, key=lambda item: item[0])


# 番号付き名前を処理する
def _numbered_display_name(name: str, counts: dict[str, int]) -> str:
    count = counts.get(name, 0) + 1
    counts[name] = count
    return name if count == 1 else f"{name}({count})"


# UTAU設定から音源解決表を作る
def build_utau_voice_table(utau_exe_path: str | Path | None) -> tuple[Path | None, list[UtauVoice]]:
    if not utau_exe_path or not Path(utau_exe_path).is_file():
        return None, []
    setting_lines = _setting_lines(utau_exe_path)
    voice_root = _voice_root(utau_exe_path, setting_lines)
    result: list[UtauVoice] = []
    counts: dict[str, int] = {}

    # UTAUの規定音源フォルダ配下を先に並べる
    if voice_root.is_dir():
        try:
            voice_root_children = [path for path in voice_root.iterdir() if path.is_dir()]
        except OSError:
            voice_root_children = []
        for child in sorted(voice_root_children, key=lambda path: path.name.casefold()):
            if not _contains_wav(child):
                continue
            base_name = _character_name(child) or child.name
            result.append(UtauVoice(_numbered_display_name(base_name, counts), _resolved_path(child), "voice_root"))

    # 個別登録音源はOtoList番号順で後ろに追加する
    for _index, registered_name, registered_path in _registered_voices(setting_lines):
        if not registered_path.is_dir():
            continue
        base_name = _character_name(registered_path) or registered_name or registered_path.name
        result.append(UtauVoice(_numbered_display_name(base_name, counts), registered_path, "otolist"))

    return voice_root, result


# VoiceDirを厳密に照合する
def strict_voice_dir_matches(value: str | None, voice_dir: str | Path, *, utau_exe_path: str | Path | None) -> bool:
    if not value:
        return False
    voice_dir = _resolved_path(voice_dir)
    text = value.strip().strip('"')
    voice_root, voices = build_utau_voice_table(utau_exe_path)

    # VoiceDir=*名前 はUTAUの表示名テーブルから実パスへ戻して比較する。
    if text.startswith("*"):
        name = text[1:]
        return any(voice.display_name == name and _same_path(voice.path, voice_dir) for voice in voices)

    normalized = text.replace("/", "\\")
    # VoiceDirの規定音源指定はUTAU設定を基準にする
    if normalized.upper().startswith("%VOICE%"):
        if voice_root is None:
            return False
        rest = normalized[len("%VOICE%") :].lstrip("\\/")
        return _same_path(voice_root / rest, voice_dir)

    return _same_path(text, voice_dir)


# USTフォルダから対象USTを探す
def scan_ust_files(
    ust_root: str | Path,
    voice_dir: str | Path,
    *,
    strict_voice_match: bool = False,
    utau_exe_path: str | Path | None = None,
) -> list[UstScanResult]:
    ust_root = Path(ust_root)
    results: list[UstScanResult] = []
    for path in ust_root.rglob("*.ust"):
        document = parse_ust_file(path)
        value = get_setting_value(document, "VoiceDir")
        matched = (
            strict_voice_dir_matches(value, voice_dir, utau_exe_path=utau_exe_path)
            if strict_voice_match
            else voice_dir_matches(value, voice_dir)
        )
        results.append(UstScanResult(path=path, voice_dir_value=value, matched=matched))
    return results
