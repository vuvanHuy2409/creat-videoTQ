"""Azure timeout recovery must not publish partial audio or retry bad keys."""

from pathlib import Path
from types import SimpleNamespace

import azure.cognitiveservices.speech as speechsdk
import pytest
from pydub import AudioSegment
from pydub.generators import Sine

import src.synthesizer_vi as tts


@pytest.fixture
def fake_azure(monkeypatch):
    def install(outcomes, chunk_chars=240):
        remaining = iter(outcomes)
        calls, sleeps, configs = [], [], []
        tone = Sine(440).to_audio_segment(duration=100).set_frame_rate(16000)

        class SpeechConfig:
            def __init__(self, **kwargs):
                self.properties = {}
                configs.append(self)

            def set_property(self, key, value):
                self.properties[key] = value

            def set_speech_synthesis_output_format(self, value):
                self.output_format = value

        class Synthesizer:
            def __init__(self, speech_config, audio_config):
                self.path = Path(audio_config)

            def speak_text_async(self, text):
                def get():
                    calls.append(text)
                    outcome = next(remaining)
                    if outcome == "ok":
                        tone.export(str(self.path), format="wav")
                        return SimpleNamespace(reason=speechsdk.ResultReason.SynthesizingAudioCompleted)
                    self.path.write_bytes(b"incomplete WAV")
                    return SimpleNamespace(
                        reason=speechsdk.ResultReason.Canceled,
                        cancellation_details=SimpleNamespace(error_code="Error", error_details=outcome),
                    )
                return SimpleNamespace(get=get)

        monkeypatch.setattr(speechsdk, "SpeechConfig", SpeechConfig)
        monkeypatch.setattr(speechsdk, "SpeechSynthesizer", Synthesizer)
        monkeypatch.setattr(speechsdk.audio, "AudioOutputConfig", lambda filename: filename)
        monkeypatch.setattr(tts.config, "AZURE_SPEECH_KEY", "test-key")
        monkeypatch.setattr(tts.config, "AZURE_SPEECH_REGION", "test-region")
        monkeypatch.setattr(tts.config, "TTS_SPEED", 1.0)
        monkeypatch.setattr(tts, "AZURE_VI_TTS_MAX_ATTEMPTS", 3)
        monkeypatch.setattr(tts, "AZURE_VI_TTS_RETRY_DELAY", 2)
        monkeypatch.setattr(tts, "AZURE_VI_TTS_FRAME_TIMEOUT_MS", 10000)
        monkeypatch.setattr(tts, "AZURE_VI_TTS_RTF_TIMEOUT_THRESHOLD", 10)
        monkeypatch.setattr(tts, "AZURE_VI_TTS_CHUNK_CHARS", chunk_chars)
        monkeypatch.setattr(tts.time, "sleep", sleeps.append)
        return SimpleNamespace(calls=calls, sleeps=sleeps, configs=configs)
    return install


def test_azure_retries_timeout_then_saves_complete_audio(fake_azure, tmp_path):
    state = fake_azure(["Timeout while synthesizing", "ConnectionFailure", "ok"])
    output = tmp_path / "speech.wav"

    result = tts._synthesize_with_azure_vi("Xin chào bạn.", str(output), "male")

    assert len(state.calls) == 3
    assert state.sleeps == [2, 4]
    assert result["actual_duration"] == pytest.approx(0.1)
    assert tts.is_usable_tts_audio(str(output))
    assert state.configs[0].properties[speechsdk.PropertyId.SpeechSynthesis_FrameTimeoutInterval] == "10000"
    assert state.configs[0].properties[speechsdk.PropertyId.SpeechSynthesis_RtfTimeoutThreshold] == "10"
    assert not list(tmp_path.glob(".azure-tts-*"))


@pytest.mark.parametrize("error,attempts", [
    ("Timeout while synthesizing", 3),
    ("Unauthorized (401)", 1),
])
def test_azure_failure_is_bounded_and_preserves_existing_file(fake_azure, tmp_path, error, attempts):
    state = fake_azure([error] * 3)
    output = tmp_path / "speech.wav"
    output.write_bytes(b"existing cache")

    with pytest.raises(RuntimeError, match="Azure Vietnamese TTS failed"):
        tts._synthesize_with_azure_vi("Xin chào bạn.", str(output), "male")

    assert len(state.calls) == attempts
    assert output.read_bytes() == b"existing cache"
    assert not list(tmp_path.glob(".azure-tts-*"))


def test_azure_joins_chunks_and_retries_only_failed_chunk(fake_azure, tmp_path):
    text = "Xin chào bạn. Chúc một ngày vui."
    state = fake_azure(["ok", "Timeout while synthesizing", "ok"], chunk_chars=18)
    output = tmp_path / "speech.wav"

    result = tts._synthesize_with_azure_vi(text, str(output), "male")

    assert state.calls == ["Xin chào bạn.", "Chúc một ngày vui.", "Chúc một ngày vui."]
    assert len(AudioSegment.from_wav(output)) == 200
    assert result["actual_duration"] == pytest.approx(0.2)


def test_later_chunk_failure_does_not_publish_earlier_audio(fake_azure, tmp_path):
    fake_azure(["ok", "Timeout", "Timeout", "Timeout"], chunk_chars=18)
    output = tmp_path / "speech.wav"

    with pytest.raises(RuntimeError):
        tts._synthesize_with_azure_vi("Xin chào bạn. Chúc một ngày vui.", str(output), "male")

    assert not output.exists()
    assert not list(tmp_path.glob(".azure-tts-*"))


def test_azure_chunking_preserves_words_and_limits_request_size():
    text = "Xin chào bạn. " + "Đây là tiếng Việt rất tự nhiên " * 20
    chunks = tts._split_azure_tts_text(text, 60)
    assert len(chunks) > 1
    assert all(0 < len(chunk) <= 60 for chunk in chunks)
    assert " ".join(chunks).split() == text.split()


def test_azure_chunking_bounds_single_long_token():
    assert tts._split_azure_tts_text("a" * 75, 30) == ["a" * 30, "a" * 30, "a" * 15]


def test_azure_does_not_classify_authentication_timeout_as_retryable():
    assert not tts._is_azure_tts_retryable("AuthenticationFailure: connection timeout (401)")
