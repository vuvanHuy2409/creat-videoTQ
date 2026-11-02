"""User-selected base tempo and pitch-preserving audio adjustment."""

import math
import os
import subprocess
import tempfile
from pathlib import Path

from pydub import AudioSegment

MIN_SPEED = 0.5
MAX_SPEED = 2.0


def validate_speed(value) -> float:
    try:
        speed = float(value)
    except (TypeError, ValueError):
        raise ValueError("Tốc độ đọc phải là một số từ 0.5 đến 2.0.") from None
    if not math.isfinite(speed) or not MIN_SPEED <= speed <= MAX_SPEED:
        raise ValueError("Tốc độ đọc phải nằm trong khoảng 0.5× đến 2.0×.")
    return speed


def tempo_filter(ratio: float) -> str:
    """Chain safe atempo steps, including a >2x relative catch-up adjustment."""
    if not math.isfinite(ratio) or ratio <= 0:
        raise ValueError("Hệ số tốc độ phải lớn hơn 0.")
    factors = []
    while ratio > 2:
        factors.append("atempo=2")
        ratio /= 2
    while ratio < 0.5:
        factors.append("atempo=0.5")
        ratio /= 0.5
    return ",".join([*factors, f"atempo={ratio:.8f}"])


def apply_speed(path: str, speed: float) -> float:
    """Change tempo once, preserve pitch, and atomically publish a valid WAV."""
    speed = validate_speed(speed)
    if speed != 1.0:
        from src.video_merger import FFMPEG_BIN

        output = Path(path)
        with tempfile.TemporaryDirectory(prefix=".tts-speed-", dir=output.parent) as temp:
            adjusted = Path(temp) / "adjusted.wav"
            result = subprocess.run(
                [FFMPEG_BIN, "-v", "error", "-y", "-i", str(output),
                 "-filter:a", tempo_filter(speed), "-c:a", "pcm_s16le", str(adjusted)],
                capture_output=True, text=True, timeout=180,
            )
            if result.returncode:
                raise RuntimeError(f"Không thể áp dụng tốc độ đọc {speed:g}×: {result.stderr[-600:]}")
            audio = AudioSegment.from_wav(adjusted)
            if not len(audio):
                raise RuntimeError("Audio sau khi đổi tốc độ bị rỗng.")
            os.replace(adjusted, output)
    return len(AudioSegment.from_wav(path)) / 1000.0
