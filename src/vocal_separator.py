"""Tách giọng nói khỏi nhạc nền cho pipeline lồng tiếng.

Tách ``original_audio.wav`` thành ``vocals.wav`` (giọng người nói gốc) và
``no_vocals.wav`` (nhạc, hiệu ứng, âm thanh môi trường). Pipeline sẽ trộn
``no_vocals.wav`` với TTS để video lồng tiếng vẫn giữ được soundtrack gốc.

Demucs được gọi qua Python API thay vì CLI ``demucs.separate`` để tránh đường
ghi ``torchaudio.save`` phụ thuộc ``torchcodec`` và ABI FFmpeg cụ thể. Audio
được ghi bằng ``soundfile``.
"""
import os
import subprocess

import config
from src.utils import setup_logging

logger = setup_logging("vocal_separator")

DEFAULT_MODEL = "htdemucs"


def separate_vocals(
    input_wav: str,
    output_dir: str,
    model: str = DEFAULT_MODEL,
) -> dict[str, str | None]:
    """Chạy Demucs tách hai nhóm stem từ ``input_wav``.

    Ghi hai file stem và mã hóa lại về sample rate/mono giống audio gốc để
    overlay chính xác với các segment TTS.

    Trả về đường dẫn khi thành công hoặc cả hai giá trị ``None`` khi thất bại.
    Nếu hai file đầu ra đã có thì dùng lại để hỗ trợ resume.
    """
    vocals_out = os.path.join(output_dir, "vocals.wav")
    no_vocals_out = os.path.join(output_dir, "no_vocals.wav")

    if (os.path.exists(no_vocals_out) and os.path.getsize(no_vocals_out) > 0
            and os.path.exists(vocals_out) and os.path.getsize(vocals_out) > 0):
        logger.info(f"Reusing existing separation: {no_vocals_out}")
        return {"vocals": vocals_out, "no_vocals": no_vocals_out}

    if not os.path.exists(input_wav):
        logger.error(f"Input audio not found: {input_wav}")
        return {"vocals": None, "no_vocals": None}

    raw_vocals = os.path.join(output_dir, "_vocals_raw.wav")
    raw_no_vocals = os.path.join(output_dir, "_no_vocals_raw.wav")

    try:
        _run_demucs(input_wav, raw_vocals, raw_no_vocals, model)
    except Exception as exc:
        logger.warning(f"Demucs separation failed: {exc}; falling back to silent base.")
        for path in (raw_vocals, raw_no_vocals):
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
        return {"vocals": None, "no_vocals": None}

    sample_rate = str(config.AUDIO_SAMPLE_RATE)
    try:
        _normalize(raw_vocals, vocals_out, sample_rate)
        _normalize(raw_no_vocals, no_vocals_out, sample_rate)
    except RuntimeError as exc:
        logger.warning(f"Post-processing Demucs output failed: {exc}")
        for path in (vocals_out, no_vocals_out):
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
        return {"vocals": None, "no_vocals": None}
    finally:
        for path in (raw_vocals, raw_no_vocals):
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass

    logger.info(f"Vocal separation complete: {no_vocals_out}")
    return {"vocals": vocals_out, "no_vocals": no_vocals_out}


def _run_demucs(input_wav: str, vocals_out: str, no_vocals_out: str, model_name: str) -> None:
    """Chạy Demucs qua Python API và ghi stem bằng soundfile."""
    import numpy as np
    import soundfile as sf
    import torch
    from demucs.apply import apply_model
    from demucs.audio import convert_audio
    from demucs.pretrained import get_model

    logger.info(f"Loading Demucs model: {model_name}")
    model = get_model(model_name)
    model.eval()

    audio, src_sr = sf.read(input_wav, always_2d=True)
    wav = torch.from_numpy(audio.T).float()

    wav = convert_audio(wav, src_sr, model.samplerate, model.audio_channels)
    ref = wav.mean(0)
    wav = (wav - ref.mean()) / (ref.std() + 1e-8)

    logger.info(f"Running Demucs ({model_name}) on {input_wav}")
    with torch.no_grad():
        sources = apply_model(model, wav[None], split=True, overlap=0.25, progress=False)[0]
    sources = sources * (ref.std() + 1e-8) + ref.mean()

    stem_index = {name: i for i, name in enumerate(model.sources)}
    if "vocals" not in stem_index:
        raise RuntimeError(f"Model {model_name} has no 'vocals' stem; got {model.sources}")

    vocals = sources[stem_index["vocals"]]
    others = [sources[i] for name, i in stem_index.items() if name != "vocals"]
    no_vocals = torch.stack(others).sum(dim=0)

    _save_wav(vocals_out, vocals.cpu().numpy(), model.samplerate)
    _save_wav(no_vocals_out, no_vocals.cpu().numpy(), model.samplerate)


def _save_wav(path: str, arr, sample_rate: int) -> None:
    """Ghi tensor float dạng (kênh, mẫu) thành WAV PCM 16-bit."""
    import numpy as np
    import soundfile as sf

    data = np.clip(arr, -1.0, 1.0).T  # (samples, channels) — soundfile convention
    sf.write(path, data, sample_rate, subtype="PCM_16")


def _normalize(src: str, dst: str, sample_rate: str) -> None:
    """Mã hóa stem về PCM mono với sample rate của pipeline."""
    cmd = [
        "ffmpeg", "-y", "-i", src,
        "-ac", "1",
        "-ar", sample_rate,
        "-acodec", "pcm_s16le",
        dst,
    ]
    result = subprocess.run(
        cmd, capture_output=True, encoding="utf-8", errors="replace"
    )
    if result.returncode != 0 or not os.path.exists(dst) or os.path.getsize(dst) == 0:
        tail = (result.stderr or "")[-300:].strip()
        raise RuntimeError(f"ffmpeg normalize failed for {src}: {tail}")
