from __future__ import annotations

from pathlib import Path
import wave


# wavの長さをミリ秒で返す
def wav_duration_ms(path: str | Path | None) -> int:
    if path is None:
        return 0
    path = Path(path)
    if not path.exists():
        return 0
    try:
        with wave.open(str(path), "rb") as wav:
            rate = wav.getframerate()
            if rate <= 0:
                return 0
            return int(wav.getnframes() / rate * 1000)
    except (OSError, wave.Error):
        return 0
