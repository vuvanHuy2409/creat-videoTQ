"""Tổng hợp TTS tiếng Việt bằng LucyLab, có Azure làm phương án dự phòng.

Quy trình LucyLab gồm: gọi ``ttsLongText`` lấy ``projectExportId``, thăm dò
``getExportStatus`` đến khi hoàn tất, sau đó tải audio và đổi sang WAV.

Nếu LucyLab hết quota hoặc không truy cập được thì dùng giọng neural tiếng Việt
của Azure. Lỗi TTS không bị biến thành audio im lặng đối với text có nội dung,
tránh tạo video không có giọng dịch nhưng tưởng là thành công.
"""
import os
import re
import tempfile
import textwrap
import time
from pathlib import Path
import requests
from pydub import AudioSegment
import config
from src.utils import setup_logging
from src.tts_speed import apply_speed, validate_speed

logger = setup_logging("synthesizer_vi")

POLL_INTERVAL = 2  # seconds between status checks
POLL_TIMEOUT = int(os.getenv("LUCYLAB_POLL_TIMEOUT", "300"))  # max seconds to wait for TTS completion
AZURE_VI_TTS_MAX_ATTEMPTS = max(1, int(os.getenv("AZURE_VI_TTS_MAX_ATTEMPTS", "3")))
AZURE_VI_TTS_RETRY_DELAY = max(0.0, float(os.getenv("AZURE_VI_TTS_RETRY_DELAY", "2")))
AZURE_VI_TTS_FRAME_TIMEOUT_MS = max(1000, int(os.getenv("AZURE_VI_TTS_FRAME_TIMEOUT_MS", "10000")))
AZURE_VI_TTS_RTF_TIMEOUT_THRESHOLD = max(1.0, float(os.getenv("AZURE_VI_TTS_RTF_TIMEOUT_THRESHOLD", "10")))
AZURE_VI_TTS_CHUNK_CHARS = max(1, int(os.getenv("AZURE_VI_TTS_CHUNK_CHARS", "240")))
_LUCYLAB_UNAVAILABLE = False
_LUCYLAB_KEY_INDEX = 0


def _lucylab_api_keys() -> tuple[str, ...]:
    """Trả các khóa LucyLab theo thứ tự khóa chính rồi đến khóa dự phòng."""
    configured = getattr(config, "VIETNAMESE_API_KEYS", ()) or ()
    if configured:
        return tuple(configured)
    primary = getattr(config, "VIETNAMESE_API_KEY", "") or ""
    return (primary.strip(),) if primary.strip() else ()


def _active_lucylab_api_key() -> str:
    """Lấy khóa LucyLab đang được chọn, hoặc chuỗi rỗng nếu đã hết khóa."""
    keys = _lucylab_api_keys()
    if _LUCYLAB_KEY_INDEX >= len(keys):
        return ""
    return keys[_LUCYLAB_KEY_INDEX]


def _is_lucylab_key_rotation_error(error: Exception | str) -> bool:
    """Nhận diện lỗi quota/xác thực/rate-limit nên chuyển sang key kế tiếp."""
    text = str(error).lower()
    markers = (
        "insufficient_credits",
        "insufficient credits",
        "out of credits",
        "credit limit",
        "quota_exceeded",
        "quota exceeded",
        "resource_exhausted",
        "rate limit",
        "too many requests",
        "429 client error",
        "401 client error",
        "unauthorized",
        "invalid api key",
        "invalid_api_key",
        "hết credit",
        "hết lượt",
    )
    return any(marker in text for marker in markers)


def _rotate_lucylab_api_key() -> bool:
    """Chuyển sang key đã cấu hình tiếp theo và báo còn key hay không."""
    global _LUCYLAB_KEY_INDEX
    keys = _lucylab_api_keys()
    next_index = _LUCYLAB_KEY_INDEX + 1
    if next_index >= len(keys):
        return False
    previous_index = _LUCYLAB_KEY_INDEX
    _LUCYLAB_KEY_INDEX = next_index
    logger.warning(
        "LucyLab key #%d is unavailable or out of quota; switching to fallback key #%d.",
        previous_index + 1,
        next_index + 1,
    )
    return True


def _request_with_retry(func, *args, max_retries=5, delay=2, **kwargs):
    """Bọc request HTTP bằng retry và thời gian chờ tăng dần."""
    for attempt in range(1, max_retries + 1):
        try:
            response = func(*args, **kwargs)
            response.raise_for_status()
            return response
        except Exception as e:
            # Lỗi quota/xác thực/rate-limit sẽ do logic đổi key xử lý; không
            # retry vô ích cùng một key đã hết hạn hoặc hết quota.
            if _is_lucylab_key_rotation_error(e):
                raise
            if attempt == max_retries:
                raise e
            logger.warning(f"LucyLab API request failed (attempt {attempt}/{max_retries}): {e}. Retrying in {delay}s...")
            time.sleep(delay)
            delay *= 2


def _call_lucylab(method: str, input_data: dict) -> dict:
    """Gọi LucyLab JSON-RPC bằng key hiện tại và trả phần result."""
    api_key = _active_lucylab_api_key()
    if not api_key:
        raise RuntimeError("No VIETNAMESE_API_KEY or fallback LucyLab API key is configured")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    json_data = {
        "method": method,
        "input": input_data,
    }
    
    response = _request_with_retry(
        requests.post,
        config.LUCYLAB_API_URL,
        headers=headers,
        json=json_data,
        timeout=30
    )
    data = response.json()

    if "error" in data:
        raise RuntimeError(f"LucyLab API error: {data['error']}")

    return data.get("result", {})


def _wait_for_audio(export_id: str) -> str:
    """Thăm dò trạng thái export đến khi hoàn tất và trả URL audio."""
    start = time.time()

    while time.time() - start < POLL_TIMEOUT:
        result = _call_lucylab("getExportStatus", {"projectExportId": export_id})
        state = result.get("state", "")

        if state == "completed":
            url = result.get("url", "")
            if not url:
                raise RuntimeError("TTS completed but no audio URL returned")
            return url

        if state == "failed":
            raise RuntimeError(f"TTS job failed: {result}")

        time.sleep(POLL_INTERVAL)

    raise TimeoutError(f"TTS polling timed out after {POLL_TIMEOUT}s for export {export_id}")


def _download_audio(url: str, output_path: str) -> str:
    """Tải file audio từ URL với cơ chế retry."""
    response = _request_with_retry(
        requests.get,
        url,
        timeout=60
    )

    with open(output_path, "wb") as f:
        f.write(response.content)

    return output_path


def is_usable_tts_audio(path: str) -> bool:
    """Kiểm tra WAV cache có tín hiệu âm thanh thực hay chỉ là file im lặng."""
    try:
        audio = AudioSegment.from_wav(path)
        return len(audio) > 0 and audio.rms > 0
    except Exception:
        return False


def _resolve_azure_vi_voice(voice_id: str | None) -> str:
    """Chọn giọng Azure tiếng Việt dự phòng theo cấu hình/giới tính LucyLab."""
    if voice_id and voice_id.startswith("azure:"):
        return voice_id[6:]
    configured = os.getenv("AZURE_VI_TTS_VOICE", "").strip() or getattr(config, "AZURE_VI_TTS_VOICE", "")
    if configured:
        return configured
    if voice_id and voice_id == config.VIETNAMESE_VOICEID_MALE:
        return "vi-VN-NamMinhNeural"
    from src.storage import Store
    for profile in Store().voices("vi"):
        if profile["provider_voice_id"] == voice_id and profile["gender"] == "male":
            return "vi-VN-NamMinhNeural"
    return "vi-VN-HoaiMyNeural"


def _split_azure_tts_text(text: str, max_chars: int) -> list[str]:
    """Giới hạn độ dài request, ưu tiên giữ nguyên câu và từ tiếng Việt."""
    if max_chars < 1:
        raise ValueError("Azure TTS chunk size must be positive")
    chunks = []
    pending = ""
    for sentence in re.split(r"(?<=[.!?;。！？；])\s*", text.strip()):
        for piece in textwrap.wrap(sentence, width=max_chars, break_on_hyphens=False):
            combined = f"{pending} {piece}".strip()
            if pending and len(combined) > max_chars:
                chunks.append(pending)
                pending = piece
            else:
                pending = combined
    if pending:
        chunks.append(pending)
    return chunks


def _is_azure_tts_retryable(error: Exception | str) -> bool:
    """Chỉ thử lại lỗi tạm thời; lỗi xác thực/cấu hình phải được báo ngay."""
    message = str(error).lower()
    if any(marker in message for marker in (
        "authentication", "unauthorized", "forbidden", "invalid key", "401", "403",
        "badrequest", "bad request",
    )):
        return False
    return any(marker in message for marker in (
        "timeout", "timed out", "connectionfailure", "connection failure",
        "connection closed", "connection was closed", "connection reset",
        "serviceunavailable", "service unavailable", "temporarily unavailable",
        "toomanyrequests", "too many requests", "429", "serviceerror", "service error",
    ))


def _synthesize_with_azure_vi(
    text_vi: str,
    output_path: str,
    voice_id: str | None,
    speed: float | None = None,
) -> dict:
    """Tạo từng đoạn ngắn, retry lỗi tạm thời và chỉ lưu WAV hoàn chỉnh."""
    speed = validate_speed(config.TTS_SPEED if speed is None else speed)
    if voice_id and voice_id.startswith("voice:"):
        from src.voice_registry import pipeline_voice
        voice_id = pipeline_voice("vi", voice_id, vars(config))
    if not config.AZURE_SPEECH_KEY or not config.AZURE_SPEECH_REGION:
        raise RuntimeError(
            "Azure Vietnamese TTS fallback is unavailable: "
            "AZURE_SPEECH_KEY/AZURE_SPEECH_REGION is not configured."
        )

    import azure.cognitiveservices.speech as speechsdk

    voice_name = _resolve_azure_vi_voice(voice_id)
    speech_config = speechsdk.SpeechConfig(
        subscription=config.AZURE_SPEECH_KEY,
        region=config.AZURE_SPEECH_REGION,
    )
    speech_config.speech_synthesis_voice_name = voice_name
    speech_config.set_speech_synthesis_output_format(
        speechsdk.SpeechSynthesisOutputFormat.Riff16Khz16BitMonoPcm
    )
    speech_config.set_property(
        speechsdk.PropertyId.SpeechSynthesis_FrameTimeoutInterval,
        str(AZURE_VI_TTS_FRAME_TIMEOUT_MS),
    )
    speech_config.set_property(
        speechsdk.PropertyId.SpeechSynthesis_RtfTimeoutThreshold,
        str(AZURE_VI_TTS_RTF_TIMEOUT_THRESHOLD),
    )

    chunks = _split_azure_tts_text(text_vi, AZURE_VI_TTS_CHUNK_CHARS)
    if not chunks:
        raise ValueError("Azure TTS requires non-empty text")
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    combined = AudioSegment.empty()
    # File lỗi/đang ghi chỉ nằm trong thư mục tạm. Không thay cache cũ bằng
    # audio một phần nếu Azure timeout giữa chừng hoặc ở một chunk sau.
    with tempfile.TemporaryDirectory(prefix=".azure-tts-", dir=output.parent) as temp_dir:
        chunk_path = Path(temp_dir) / "chunk.wav"
        for index, chunk in enumerate(chunks, 1):
            for attempt in range(1, AZURE_VI_TTS_MAX_ATTEMPTS + 1):
                chunk_path.unlink(missing_ok=True)
                try:
                    audio_config = speechsdk.audio.AudioOutputConfig(filename=str(chunk_path))
                    synthesizer = speechsdk.SpeechSynthesizer(
                        speech_config=speech_config,
                        audio_config=audio_config,
                    )
                    result = synthesizer.speak_text_async(chunk).get()
                    if result.reason != speechsdk.ResultReason.SynthesizingAudioCompleted:
                        details = getattr(result, "cancellation_details", None)
                        error_code = getattr(details, "error_code", "")
                        error_details = getattr(details, "error_details", "")
                        raise RuntimeError(f"Azure Vietnamese TTS failed: {error_code} — {error_details}")
                    if not is_usable_tts_audio(str(chunk_path)):
                        raise RuntimeError("Azure Vietnamese TTS returned a silent or invalid audio file")
                    combined += AudioSegment.from_wav(chunk_path)
                    break
                except Exception as error:
                    if attempt == AZURE_VI_TTS_MAX_ATTEMPTS or not _is_azure_tts_retryable(error):
                        raise
                    delay = min(AZURE_VI_TTS_RETRY_DELAY * 2 ** (attempt - 1), 30.0)
                    logger.warning(
                        "Azure TTS chunk %d/%d failed (attempt %d/%d): %s. Retrying in %.1fs.",
                        index, len(chunks), attempt, AZURE_VI_TTS_MAX_ATTEMPTS, error, delay,
                    )
                    time.sleep(delay)
        completed_path = Path(temp_dir) / "completed.wav"
        combined.export(str(completed_path), format="wav")
        if not is_usable_tts_audio(str(completed_path)):
            raise RuntimeError("Azure Vietnamese TTS returned a silent audio file")
        actual_duration = apply_speed(str(completed_path), speed)
        os.replace(completed_path, output)

    logger.info(
        f"Azure Vietnamese TTS completed: voice={voice_name}, "
        f"duration={actual_duration:.2f}s"
    )
    return {
        "path": output_path,
        "actual_duration": round(actual_duration, 3),
        "speed_adjusted": speed != 1.0,
        "rate_applied": f"{speed:g}x",
        "provider": "azure",
        "voice": voice_name,
    }


def synthesize_segment_vi(
    text_vi: str,
    output_path: str,
    target_duration: float | None = None,
    voice_id: str | None = None,
    speed: float | None = None,
) -> dict:
    """Tổng hợp text tiếng Việt thành WAV bằng LucyLab hoặc Azure fallback.

    Args:
        text_vi: nội dung tiếng Việt cần đọc.
        output_path: đường dẫn WAV đầu ra.
        target_duration: chỉ dùng để tạo khoảng lặng cho text không có lời nói.
        speed: tốc độ cố định; mặc định lấy TTS_SPEED của người dùng.
        voice_id: mã giọng LucyLab.

    Returns:
        dict gồm path, actual_duration, speed_adjusted và rate_applied.
    """
    speed = validate_speed(config.TTS_SPEED if speed is None else speed)
    if voice_id and voice_id.startswith("voice:"):
        from src.voice_registry import pipeline_voice
        voice_id = pipeline_voice("vi", voice_id, vars(config))
    if not voice_id:
        raise ValueError("voice_id is required. Use --voice male/female or select a saved voice.")
    import re
    # Text chỉ gồm dấu câu/khoảng trắng không có nội dung để đọc. Tạo im lặng
    # chủ động nhằm tránh tốn quota và tránh API từ chối request.
    if not re.search(r"\w", text_vi):
        logger.info(f"Text has no speech content: '{text_vi}'. Generating silent audio.")
        dur = target_duration if target_duration and target_duration > 0 else 0.5
        audio = AudioSegment.silent(duration=int(dur * 1000))
        audio.export(output_path, format="wav")
        return {
            "path": output_path,
            "actual_duration": round(dur, 3),
            "speed_adjusted": False,
            "rate_applied": "1.0x",
        }

    global _LUCYLAB_UNAVAILABLE
    if voice_id.startswith("azure:"):
        return _synthesize_with_azure_vi(text_vi, output_path, voice_id, speed=speed)
    api_keys = _lucylab_api_keys()
    if not api_keys or _LUCYLAB_UNAVAILABLE:
        if not api_keys:
            logger.warning("VIETNAMESE_API_KEY* is not set; using Azure Vietnamese TTS.")
        else:
            logger.info("LucyLab is unavailable for this run; using Azure Vietnamese TTS.")
        return _synthesize_with_azure_vi(text_vi, output_path, voice_id, speed=speed)

    try:
        # Bước 2: gọi API tạo job TTS.
        logger.info(f"TTS request: {len(text_vi)} chars, speed={speed}, target={target_duration:.1f}s"
                    if target_duration else f"TTS request: {len(text_vi)} chars, speed={speed}")

        result = _call_lucylab("ttsLongText", {
            "text": text_vi,
            "userVoiceId": voice_id,
            "speed": 1.0,
        })

        export_id = result.get("projectExportId")
        if not export_id:
            raise RuntimeError(f"No projectExportId in response: {result}")

        logger.info(f"TTS job created: {export_id} (chars={result.get('characterCount', '?')}, "
                    f"blocks={result.get('blockCount', '?')})")

        # Bước 3: chờ job hoàn tất, tải file tạm rồi đổi sang WAV.
        audio_url = _wait_for_audio(export_id)
        logger.info(f"TTS completed, downloading audio...")

        # Tải vào file tạm vì API có thể trả MP3 hoặc WAV.
        temp_path = output_path + ".tmp"
        _download_audio(audio_url, temp_path)

        # Chuẩn hóa về WAV để các bước pydub/FFmpeg phía sau dùng cùng định dạng.
        audio = AudioSegment.from_file(temp_path)
        audio.export(output_path, format="wav")
        os.remove(temp_path)

        if not is_usable_tts_audio(output_path):
            raise RuntimeError("LucyLab returned a silent audio file")

        # Providers always synthesize at their natural tempo. Apply the same
        # user-selected multiplier once for LucyLab and Azure fallback.
        actual_duration = apply_speed(output_path, speed)
        speed_adjusted = speed != 1.0
    except Exception as e:
        if _is_lucylab_key_rotation_error(e) and _rotate_lucylab_api_key():
            # Thử lại toàn bộ segment bằng key kế tiếp; cách này cũng xử lý lỗi
            # phát hiện trong lúc đang poll job export.
            return synthesize_segment_vi(
                text_vi,
                output_path,
                target_duration=target_duration,
                voice_id=voice_id,
                speed=speed,
            )
        if _is_lucylab_key_rotation_error(e):
            _LUCYLAB_UNAVAILABLE = True
        logger.warning(
            f"LucyLab TTS failed for segment '{text_vi}' due to error: {e}. "
            "Trying Azure Vietnamese TTS fallback."
        )
        try:
            return _synthesize_with_azure_vi(text_vi, output_path, voice_id, speed=speed)
        except Exception as fallback_error:
            raise RuntimeError(
                f"Vietnamese TTS failed in both LucyLab and Azure. "
                f"LucyLab: {e}; Azure: {fallback_error}"
            ) from fallback_error

    return {
        "path": output_path,
        "actual_duration": round(actual_duration, 3),
        "speed_adjusted": speed_adjusted,
        "rate_applied": f"{speed:g}x",
        "provider": "lucylab",
        "voice": voice_id,
    }
