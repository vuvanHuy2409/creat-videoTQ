"""Generate cached voice samples with exactly the same TTS path as dubbing.

GUI calls this module in a subprocess so changed credentials and voices are
loaded fresh without changing global config in the desktop process.
"""

import hashlib
import json
import os
import tempfile
from pathlib import Path

import config
from src.tts_speed import validate_speed
from src.storage import Store
from src.voice_registry import resolve, pipeline_voice

SAMPLES = {
    "vi": "Xin chào! Đây là giọng đọc thử cho video của bạn. Hãy lắng nghe nhịp đọc và chọn giọng phù hợp với câu chuyện.",
    "jp": "こんにちは。これは動画のナレーション用のサンプルです。声の雰囲気と読み上げの速さを確認してください。",
}


def generate_preview(language, voice, text, speed, cache_dir):
    if language not in SAMPLES:
        raise ValueError("Chỉ hỗ trợ nghe thử tiếng Việt hoặc tiếng Nhật.")
    speed = validate_speed(speed)
    text = text.strip()
    if not text or len(text) > 500 or not any(character.isalnum() for character in text):
        raise ValueError("Nhập câu đọc thử từ 1 đến 500 ký tự, có chữ hoặc số.")
    selected = resolve(language, voice, vars(config))
    store = Store()
    # Only the digest reaches disk; API keys never appear in a cache manifest.
    credentials = [config.AZURE_SPEECH_KEY, *config.VIETNAMESE_API_KEYS]
    identity = {
        "version": 2, "language": language, "voice": voice, "text": text, "speed": speed,
        "selected_provider": selected["provider"], "selected_voice_id": selected["provider_voice_id"],
        "credentials": hashlib.sha256(json.dumps(credentials).encode()).hexdigest(),
        "azure_region": config.AZURE_SPEECH_REGION,
        "azure_vi_voice": config.AZURE_VI_TTS_VOICE,
        "male": config.VIETNAMESE_VOICEID_MALE, "female": config.VIETNAMESE_VOICEID_FEMALE,
        "endpoint": config.LUCYLAB_API_URL,
    }
    key = hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    directory = Path(cache_dir)
    directory.mkdir(parents=True, exist_ok=True)
    audio = directory / f"{key}.wav"
    manifest = directory / f"{key}.json"
    from src.synthesizer_vi import is_usable_tts_audio
    cached = store.preview(key)
    if cached and is_usable_tts_audio(cached["path"]):
        metadata = {"cache_key": key, "provider": cached["provider"], "voice": cached["actual_voice"],
                    "speed": cached["speed"], "duration": cached["duration"], "requested_provider": selected["provider"]}
        store.save_preview(key, voice, text, metadata, cached["path"])
        return {**metadata, "path": cached["path"], "cached": True}
    if manifest.exists() and is_usable_tts_audio(str(audio)):
        try:
            metadata = json.loads(manifest.read_text(encoding="utf-8"))
            if metadata.get("cache_key") == key:
                metadata["requested_provider"] = selected["provider"]
                store.save_preview(key, voice, text, metadata, audio)
                return {**metadata, "path": str(audio), "cached": True}
        except (OSError, ValueError):
            pass
    with tempfile.TemporaryDirectory(prefix=".sample-", dir=directory) as temp:
        pending = str(Path(temp) / "sample.wav")
        if language == "vi":
            from src.synthesizer_vi import synthesize_segment_vi
            voice_id = pipeline_voice("vi", voice, vars(config))
            result = synthesize_segment_vi(text, pending, voice_id=voice_id, speed=speed)
        else:
            from src.synthesizer import synthesize_segment
            result = synthesize_segment(text, pending, voice=selected["provider_voice_id"], speed=speed)
        if not is_usable_tts_audio(pending):
            raise RuntimeError("Dịch vụ không trả về mẫu giọng đọc hợp lệ.")
        metadata = {
            "cache_key": key, "provider": result["provider"], "voice": result["voice"],
            "requested_provider": selected["provider"],
            "speed": speed, "duration": result["actual_duration"], "cached": False,
        }
        os.replace(pending, audio)
        manifest.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
        store.save_preview(key, voice, text, metadata, audio)
    return {**metadata, "path": str(audio)}


if __name__ == "__main__":
    import sys
    try:
        request = json.load(sys.stdin)
        result = generate_preview(**request)
        print(json.dumps({"preview_result": result}, ensure_ascii=False), flush=True)
    except Exception as error:
        # The UI shows a bounded message; avoid echoing service payloads/API keys.
        print(json.dumps({"preview_error": "Không tạo được mẫu giọng. Kiểm tra kết nối, khóa API và giọng đã cài đặt.",
                          "error_type": type(error).__name__}, ensure_ascii=False), flush=True)
        sys.exit(1)
