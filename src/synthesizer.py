"""Tổng hợp giọng đọc tiếng Nhật từng segment bằng Azure Speech TTS."""
import os
import azure.cognitiveservices.speech as speechsdk
from pydub import AudioSegment
from xml.sax.saxutils import escape as xml_escape
import config
from src.utils import setup_logging
from src.tts_speed import apply_speed, validate_speed

logger = setup_logging("synthesizer")


def _build_ssml(text: str, voice: str, rate: str = "+0%", reduce_pauses: bool = True) -> str:
    """Tạo SSML, escape nội dung và tùy chọn rút ngắn khoảng ngắt câu."""
    safe_text = xml_escape(text)

    if reduce_pauses:
        # Giảm khoảng im lặng giữa câu/dấu phẩy để audio dễ vừa vào timeline
        # gốc, nhưng vẫn giữ nội dung trong thẻ prosody để chỉnh tốc độ.
        inner = (
            f'<prosody rate="{rate}">'
            f'<mstts:silence type="Sentenceboundary" value="100ms"/>'
            f'<mstts:silence type="Comma-Semicolon" value="50ms"/>'
            f'{safe_text}'
            f'</prosody>'
        )
    else:
        inner = f'<prosody rate="{rate}">{safe_text}</prosody>'

    return (
        f'<speak version="1.0" '
        f'xmlns="http://www.w3.org/2001/10/synthesis" '
        f'xmlns:mstts="http://www.w3.org/2001/mstts" '
        f'xml:lang="ja-JP">'
        f'<voice name="{voice}">'
        f'{inner}'
        f'</voice>'
        f'</speak>'
    )


def synthesize_segment(
    text_jp: str,
    output_path: str,
    target_duration: float | None = None,
    voice: str | None = None,
    speed: float | None = None,
) -> dict:
    """Sinh giọng Nhật ở tốc độ người dùng chọn, không suy ra từ bản dịch."""
    voice = voice or config.TTS_VOICE
    if voice.startswith("voice:"):
        from src.voice_registry import pipeline_voice
        voice = pipeline_voice("jp", voice, vars(config))
    speed = validate_speed(config.TTS_SPEED if speed is None else speed)

    speech_config = speechsdk.SpeechConfig(
        subscription=config.AZURE_SPEECH_KEY,
        region=config.AZURE_SPEECH_REGION,
    )
    audio_config = speechsdk.audio.AudioOutputConfig(filename=output_path)

    synthesizer = speechsdk.SpeechSynthesizer(
        speech_config=speech_config,
        audio_config=audio_config,
    )

    ssml = _build_ssml(text_jp, voice, rate="+0%", reduce_pauses=False)
    result = synthesizer.speak_ssml_async(ssml).get()

    if result.reason == speechsdk.ResultReason.Canceled:
        details = result.cancellation_details
        raise RuntimeError(f"TTS failed: {details.reason} — {details.error_details}")

    actual_duration = apply_speed(output_path, speed)
    return {
        "path": output_path,
        "actual_duration": round(actual_duration, 3),
        "speed_adjusted": speed != 1.0,
        "rate_applied": f"{speed:g}x",
        "provider": "azure",
        "voice": voice,
    }
