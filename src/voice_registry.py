"""Resolve stable SQLite voice references for GUI, preview and both pipelines."""

from src.storage import Store


def options(language, environment):
    store = Store()
    store.seed_voices(environment)
    voices = store.voices(language)
    references = [voice["id"] for voice in voices]
    labels = {voice["id"]: voice["name"] for voice in voices}
    configured = environment.get("TTS_VOICE", "") if language == "jp" else ""
    if configured.startswith("ja-JP-") and configured not in references:
        references.append(configured)
        labels[configured] = configured + " (cấu hình)"
    return references, labels


def default_reference(language, environment):
    key = "DEFAULT_VOICE_VI" if language == "vi" else "DEFAULT_VOICE_JP"
    selected = environment.get(key)
    if selected:
        voice = Store().voice(selected)
        if voice and voice["enabled"] and voice["language"] == language:
            return selected
    if language == "vi":
        return environment.get("VOICE_TYPE") if environment.get("VOICE_TYPE") in ("male", "female") else "male"
    return environment.get("TTS_VOICE") or "ja-JP-KeitaNeural"


def resolve(language, reference, environment):
    if language not in ("vi", "jp"):
        raise ValueError("Chỉ hỗ trợ giọng Việt hoặc Nhật.")
    if language == "vi" and reference in ("male", "female"):
        return {"reference": reference, "provider": "lucylab", "language": language,
                "provider_voice_id": environment.get("VIETNAMESE_VOICEID_" + reference.upper()) or "",
                "gender": reference, "name": "Giọng nam" if reference == "male" else "Giọng nữ"}
    record = Store().voice(reference)
    if record:
        if not record["enabled"] or record["language"] != language:
            raise ValueError("Giọng đã bị ẩn hoặc không khớp ngôn ngữ đang chọn.")
        return {**record, "reference": reference}
    if language == "vi" and reference.startswith("azure:vi-VN-"):
        return {"reference": reference, "provider": "azure", "provider_voice_id": reference[6:],
                "language": "vi", "gender": "other", "name": reference[6:]}
    if language == "jp" and reference.startswith("ja-JP-"):
        return {"reference": reference, "provider": "azure", "provider_voice_id": reference,
                "language": "jp", "gender": "other", "name": reference}
    raise ValueError("Không tìm thấy giọng đọc đã lưu. Hãy chọn lại trong thư viện giọng.")


def pipeline_voice(language, reference, environment):
    record = resolve(language, reference, environment)
    voice_id = record["provider_voice_id"]
    if not voice_id:
        if reference in ("male", "female"):
            return ""  # Legacy CLI parses without contacting a TTS service.
        raise ValueError("Giọng LucyLab chưa có Voice ID. Hãy điền trong Cài đặt hoặc chọn giọng Azure.")
    return "azure:" + voice_id if language == "vi" and record["provider"] == "azure" else voice_id
