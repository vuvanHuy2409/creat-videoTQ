"""Kiểm thử cache TTS thay đổi khi voice hoặc API key thay đổi."""

import pipeline_vi


def test_tts_cache_config_changes_for_voice_or_api(monkeypatch):
    monkeypatch.setattr(pipeline_vi.config, "VIETNAMESE_API_KEY", "old-api-key")
    monkeypatch.setattr(pipeline_vi.config, "VIETNAMESE_API_KEYS", ("old-api-key",))
    monkeypatch.setattr(pipeline_vi.config, "VIETNAMESE_VOICEID_MALE", "old-voice")
    first = pipeline_vi._build_tts_cache_config("old-voice")

    monkeypatch.setattr(pipeline_vi.config, "VIETNAMESE_API_KEY", "new-api-key")
    monkeypatch.setattr(pipeline_vi.config, "VIETNAMESE_API_KEYS", ("new-api-key",))
    second = pipeline_vi._build_tts_cache_config("old-voice")
    assert second != first
    assert second["api_key_fingerprint"] != "new-api-key"

    third = pipeline_vi._build_tts_cache_config("new-voice")
    assert third != second
