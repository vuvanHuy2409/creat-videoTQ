"""Custom SQLite voices must reach the real provider adapters, not just UI lists."""

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydub.generators import Sine

import config
import src.synthesizer_vi as vietnamese
import src.synthesizer as japanese
import src.voice_preview as preview
from src.storage import Store


def sound(path):
    Sine(440).to_audio_segment(duration=400).export(path, format="wav")


def test_custom_lucylab_voice_id_is_sent_to_provider(monkeypatch, tmp_path):
    ref = Store().save_voice("My Lucy", "vi", "lucylab", "custom-lucy-id", "male")
    calls = []
    monkeypatch.setattr(vietnamese, "_LUCYLAB_UNAVAILABLE", False)
    monkeypatch.setattr(vietnamese, "_lucylab_api_keys", lambda: ("fake",))
    monkeypatch.setattr(vietnamese, "_call_lucylab", lambda method, params: calls.append(params) or {"projectExportId": "job"})
    monkeypatch.setattr(vietnamese, "_wait_for_audio", lambda _: "fake-url")
    monkeypatch.setattr(vietnamese, "_download_audio", lambda _, path: sound(path))
    result = vietnamese.synthesize_segment_vi("Xin chào", str(tmp_path / "test.wav"), voice_id=ref)
    assert calls[0]["userVoiceId"] == "custom-lucy-id"
    assert result["voice"] == "custom-lucy-id"


def test_custom_azure_vi_bypasses_lucylab_and_global_fallback(monkeypatch, tmp_path):
    ref = Store().save_voice("My Azure", "vi", "azure", "vi-VN-CustomVoiceNeural", "male")
    import azure.cognitiveservices.speech as sdk
    configs = []
    class SpeechConfig:
        def __init__(self, **kw):
            configs.append(self)
        def set_property(self, *a):
            pass
        def set_speech_synthesis_output_format(self, *a):
            pass
    class Synthesizer:
        def __init__(self, speech_config, audio_config):
            self.path = audio_config
        def speak_text_async(self, text):
            sound(self.path)
            return SimpleNamespace(get=lambda: SimpleNamespace(reason=sdk.ResultReason.SynthesizingAudioCompleted))
    monkeypatch.setattr(sdk, "SpeechConfig", SpeechConfig)
    monkeypatch.setattr(sdk, "SpeechSynthesizer", Synthesizer)
    monkeypatch.setattr(sdk.audio, "AudioOutputConfig", lambda filename: filename)
    monkeypatch.setattr(config, "AZURE_VI_TTS_VOICE", "vi-VN-HoaiMyNeural")
    monkeypatch.setattr(vietnamese, "_call_lucylab", lambda *a, **kw: pytest.fail("Explicit Azure voice contacted LucyLab"))
    result = vietnamese.synthesize_segment_vi("Xin chào", str(tmp_path / "azure.wav"), voice_id=ref)
    assert configs[0].speech_synthesis_voice_name == "vi-VN-CustomVoiceNeural"
    assert result["provider"] == "azure"


def test_custom_japanese_voice_reference_reaches_ssml(monkeypatch, tmp_path):
    ref = Store().save_voice("My JP", "jp", "azure", "ja-JP-CustomVoiceNeural")
    calls = []
    class Synthesizer:
        def __init__(self, speech_config, audio_config):
            self.path = audio_config
        def speak_ssml_async(self, ssml):
            calls.append(ssml)
            sound(self.path)
            return SimpleNamespace(get=lambda: SimpleNamespace(reason=japanese.speechsdk.ResultReason.SynthesizingAudioCompleted))
    monkeypatch.setattr(japanese.speechsdk, "SpeechConfig", lambda **kw: object())
    monkeypatch.setattr(japanese.speechsdk, "SpeechSynthesizer", Synthesizer)
    monkeypatch.setattr(japanese.speechsdk.audio, "AudioOutputConfig", lambda filename: filename)
    result = japanese.synthesize_segment("日本語", str(tmp_path / "jp.wav"), voice=ref)
    assert 'voice name="ja-JP-CustomVoiceNeural"' in calls[0]
    assert ref not in calls[0]
    assert result["voice"] == "ja-JP-CustomVoiceNeural"


def test_preview_sqlite_cache_and_editing_custom_voice(monkeypatch, tmp_path):
    store = Store()
    ref = store.save_voice("Custom", "vi", "lucylab", "first-id")
    calls = []
    def synthesize(text, path, voice_id, speed):
        calls.append(voice_id)
        sound(path)
        return {"provider": "lucylab", "voice": voice_id, "actual_duration": 0.4}
    monkeypatch.setattr(vietnamese, "synthesize_segment_vi", synthesize)
    first = preview.generate_preview("vi", ref, "Hello", 1.0, tmp_path)
    second = preview.generate_preview("vi", ref, "Hello", 1.0, tmp_path)
    assert calls == ["first-id"] and second["cached"]
    assert store.preview(first["cache_key"])["use_count"] == 2
    store.save_voice("Custom edited", "vi", "lucylab", "second-id", reference=ref)
    third = preview.generate_preview("vi", ref, "Hello", 1.0, tmp_path)
    assert third["cache_key"] != first["cache_key"]
    assert calls == ["first-id", "second-id"]
    assert store.counts()["voice_previews"] == 2


def test_sqlite_preferences_reach_fresh_cli_but_explicit_environment_wins():
    Store().save_settings({"TTS_SPEED": "1.33", "SUBTITLE_FONT": "Anton"})
    env = dict(os.environ)
    env.pop("TTS_SPEED", None)
    env.pop("SUBTITLE_FONT", None)
    command = [sys.executable, "-c", "import config,json; print(json.dumps([config.TTS_SPEED,config.SUBTITLE_FONT]))"]
    first = subprocess.run(command, env=env, cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, check=True)
    assert json.loads(first.stdout) == [1.33, "Anton"]
    env["TTS_SPEED"] = "1.1"
    second = subprocess.run(command, env=env, cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, check=True)
    assert json.loads(second.stdout) == [1.1, "Anton"]
