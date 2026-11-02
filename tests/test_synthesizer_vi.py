"""Kiểm thử nhận diện audio TTS hợp lệ và ánh xạ voice Azure tiếng Việt."""

from pydub import AudioSegment
from pydub.generators import Sine

import config
from src.synthesizer_vi import (
    _resolve_azure_vi_voice,
    is_usable_tts_audio,
)


def test_silent_cached_tts_is_not_reused(tmp_path):
    path = tmp_path / "silent.wav"
    AudioSegment.silent(duration=500).export(path, format="wav")

    assert not is_usable_tts_audio(str(path))


def test_non_silent_tts_is_usable(tmp_path):
    path = tmp_path / "speech.wav"
    Sine(440).to_audio_segment(duration=500).export(path, format="wav")

    assert is_usable_tts_audio(str(path))


def test_azure_voice_follows_selected_lucylab_gender(monkeypatch):
    monkeypatch.delenv("AZURE_VI_TTS_VOICE", raising=False)
    monkeypatch.setattr(config, "AZURE_VI_TTS_VOICE", "")
    monkeypatch.setattr(config, "VIETNAMESE_VOICEID_MALE", "test-male-voice")
    monkeypatch.setattr(config, "VIETNAMESE_VOICEID_FEMALE", "test-female-voice")

    assert _resolve_azure_vi_voice(config.VIETNAMESE_VOICEID_MALE) == "vi-VN-NamMinhNeural"
    assert _resolve_azure_vi_voice(config.VIETNAMESE_VOICEID_FEMALE) == "vi-VN-HoaiMyNeural"


def test_azure_voice_can_be_overridden(monkeypatch):
    monkeypatch.setenv("AZURE_VI_TTS_VOICE", "vi-VN-NamMinhNeural")

    assert _resolve_azure_vi_voice(config.VIETNAMESE_VOICEID_FEMALE) == "vi-VN-NamMinhNeural"
