from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import random
import tempfile
import wave

from .wav_utils import wav_duration_ms


try:  # pragma: no cover - platform dependent
    import winsound
except ImportError:  # pragma: no cover
    winsound = None


@dataclass
# 音声応答を保持する
class AudioResponse:
    ok: bool
    message: str = ""
    path: str = ""
    playing: bool = False
    duration_ms: int = 0


# 音声再生を管理する
class AudioPlayer:
    # 初期状態を設定する
    def __init__(self) -> None:
        self.current_path: Path | None = None
        self._temp_path: Path | None = None

    # 値を再生する
    def play(self, path: str | Path) -> AudioResponse:
        path = Path(path)
        if winsound is None:
            return AudioResponse(False, "audio playback is only implemented through winsound on Windows", str(path))
        if not path.exists():
            return AudioResponse(False, "audio file was not found", str(path))
        self.stop()
        winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC)
        self.current_path = path
        return AudioResponse(True, path=str(path), playing=True, duration_ms=wav_duration_ms(path))

    # 範囲を再生する
    def play_range(self, path: str | Path, start_ms: int = 0, end_ms: int = 0) -> AudioResponse:
        path = Path(path)
        if start_ms <= 0 and end_ms <= 0:
            return self.play(path)
        if winsound is None:
            return AudioResponse(False, "audio playback is only implemented through winsound on Windows", str(path))
        if not path.exists():
            return AudioResponse(False, "audio file was not found", str(path))
        try:
            segment_path, duration_ms = _extract_wav_segment(path, start_ms, end_ms)
        except (OSError, wave.Error, ValueError) as exc:
            return AudioResponse(False, str(exc), str(path))
        self.stop()
        winsound.PlaySound(str(segment_path), winsound.SND_FILENAME | winsound.SND_ASYNC)
        self.current_path = segment_path
        self._temp_path = segment_path
        return AudioResponse(True, path=str(path), playing=True, duration_ms=duration_ms)

    # ランダムを再生する
    def play_random(self, voice_dir: str | Path) -> AudioResponse:
        voice_dir = Path(voice_dir)
        wav_files = [path for path in voice_dir.rglob("*.wav") if path.is_file()]
        if not wav_files:
            return AudioResponse(False, "no wav files were found", str(voice_dir))
        return self.play(random.choice(wav_files))

    # 値を停止する
    def stop(self) -> AudioResponse:
        if winsound is not None:
            winsound.PlaySound(None, winsound.SND_PURGE)
        path = "" if self.current_path is None else str(self.current_path)
        self.current_path = None
        if self._temp_path is not None:
            try:
                self._temp_path.unlink(missing_ok=True)
            except OSError:
                pass
            self._temp_path = None
        return AudioResponse(True, path=path, playing=False)


# wavを取り出す
def _extract_wav_segment(path: Path, start_ms: int, end_ms: int) -> tuple[Path, int]:
    with wave.open(str(path), "rb") as src:
        rate = src.getframerate()
        if rate <= 0:
            raise ValueError("invalid wav sample rate")
        total_frames = src.getnframes()
        start_frame = max(0, int(rate * max(0, start_ms) / 1000))
        end_frame = int(rate * end_ms / 1000) if end_ms > 0 else total_frames
        end_frame = max(start_frame + 1, min(total_frames, end_frame))
        src.setpos(start_frame)
        frames = src.readframes(end_frame - start_frame)
        with tempfile.NamedTemporaryFile(prefix="aliascale_segment_", suffix=".wav", delete=False) as fp:
            temp_path = Path(fp.name)
        with wave.open(str(temp_path), "wb") as dst:
            dst.setnchannels(src.getnchannels())
            dst.setsampwidth(src.getsampwidth())
            dst.setframerate(rate)
            dst.writeframes(frames)
    return temp_path, int((end_frame - start_frame) / rate * 1000)
