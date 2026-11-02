"""Voice auditions, selected tempo, catch-up limits and preserved translations."""

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydub import AudioSegment
from pydub.generators import Sine

import config
import pipeline_vi
import src.synthesizer as japanese
import src.synthesizer_vi as vietnamese
import src.voice_preview as preview
from src.audio_merger import fit_segments_to_timeline, merge_segments
from src.tts_speed import apply_speed, validate_speed


def tone(path, duration=2000, frequency=440):
    Sine(frequency).to_audio_segment(duration=duration).export(path, format="wav")


@pytest.mark.parametrize("speed", ["", "abc", 0, -1, 0.49, 2.01, float("nan"), float("inf")])
def test_invalid_speed_is_rejected(speed):
    with pytest.raises(ValueError):
        validate_speed(speed)


@pytest.mark.parametrize("speed", [0.75, 1.0, 1.25, 2.0])
def test_real_audio_has_selected_tempo(tmp_path, speed):
    audio = tmp_path / "speech.wav"
    tone(audio, duration=2000)
    duration = apply_speed(str(audio), speed)
    assert duration == pytest.approx(2.0 / speed, abs=0.08)
    assert AudioSegment.from_wav(audio).rms > 0


def test_lucylab_base_speed_is_independent_of_text_and_scene_length(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(vietnamese, "_LUCYLAB_UNAVAILABLE", False)
    monkeypatch.setattr(vietnamese, "_lucylab_api_keys", lambda: ("fake-key",))
    monkeypatch.setattr(vietnamese, "_call_lucylab", lambda method, params:
                        calls.append(params) or {"projectExportId": "sample"})
    monkeypatch.setattr(vietnamese, "_wait_for_audio", lambda _: "sample-url")
    monkeypatch.setattr(vietnamese, "_download_audio", lambda _, path: tone(path))
    results = [vietnamese.synthesize_segment_vi(text, str(tmp_path / f"{index}.wav"),
                target_duration=target, voice_id="voice", speed=1.25)
               for index, (text, target) in enumerate((("Xin chào.", 5.0), ("Câu dài. " * 30, 0.1)))]
    assert len(calls) == 2  # No sentence-dependent re-synthesis or API retry.
    assert all(call["speed"] == 1.0 for call in calls)
    assert all(result["actual_duration"] == pytest.approx(1.6, abs=0.08) for result in results)
    assert all(result["rate_applied"] == "1.25x" for result in results)


def test_azure_fallback_receives_same_selected_speed(monkeypatch, tmp_path):
    received = []
    monkeypatch.setattr(vietnamese, "_lucylab_api_keys", lambda: ())
    monkeypatch.setattr(vietnamese, "_synthesize_with_azure_vi",
                        lambda text, path, voice_id, speed: received.append(speed) or {"provider": "azure"})
    vietnamese.synthesize_segment_vi("Xin chào", str(tmp_path / "speech.wav"),
                                     target_duration=0.1, voice_id="female", speed=0.85)
    assert received == [0.85]


def test_japanese_synthesis_uses_one_natural_request_then_selected_speed(monkeypatch, tmp_path):
    requests = []
    class Synthesizer:
        def __init__(self, speech_config, audio_config):
            self.path = audio_config

        def speak_ssml_async(self, ssml):
            requests.append(ssml)
            tone(self.path)
            return SimpleNamespace(get=lambda: SimpleNamespace(
                reason=japanese.speechsdk.ResultReason.SynthesizingAudioCompleted))
    monkeypatch.setattr(japanese.speechsdk, "SpeechConfig", lambda **_: object())
    monkeypatch.setattr(japanese.speechsdk.audio, "AudioOutputConfig", lambda filename: filename)
    monkeypatch.setattr(japanese.speechsdk, "SpeechSynthesizer", Synthesizer)
    result = japanese.synthesize_segment("日本語 & テスト", str(tmp_path / "jp.wav"),
                                         target_duration=0.01, speed=1.25)
    assert len(requests) == 1
    assert 'rate="+0%"' in requests[0]
    assert "&amp;" in requests[0]
    assert result["actual_duration"] == pytest.approx(1.6, abs=0.08)


def test_catch_up_is_capped_then_audio_overlaps_without_changing_translation(tmp_path):
    source, fitted = tmp_path / "segments", tmp_path / "fitted"
    source.mkdir()
    tone(source / "seg_001.wav", 3000, 440)
    tone(source / "seg_002.wav", 500, 880)
    segments = [{"id": 1, "start": 0.0, "end": 1.0, "duration": 1.0, "text_vi": "Câu dài"},
                {"id": 2, "start": 1.0, "end": 1.5, "duration": 0.5, "text_vi": "Câu sau"}]
    original = copy.deepcopy(segments)
    translated = tmp_path / "transcript_vi.json"
    translated.write_text(json.dumps(segments), encoding="utf-8")
    before = translated.read_bytes()
    adjustments = fit_segments_to_timeline(segments, str(source), str(fitted), max_speedup=1.4)
    assert adjustments[0]["speed"] == 1.4
    assert adjustments[0]["status"] == "STILL_OVERFLOWS"
    assert adjustments[0]["after"] > 1.5
    assert len(AudioSegment.from_wav(source / "seg_001.wav")) == 3000
    output = tmp_path / "mixed.wav"
    merge_segments(segments, str(fitted), str(output), 4.0)
    # Both voices must be audible at the original start of sentence two.
    first = AudioSegment.from_wav(fitted / "seg_001.wav")
    second = AudioSegment.from_wav(fitted / "seg_002.wav")
    expected = AudioSegment.silent(duration=4000).overlay(first, position=0).overlay(second, position=1000)
    mixed = AudioSegment.from_wav(output)
    assert mixed[1100:1300].raw_data == expected[1100:1300].raw_data
    assert segments == original
    assert translated.read_bytes() == before


def test_fixed_speed_setting_disables_extra_compression(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    tone(source / "seg_001.wav", 2000)
    segments = [{"id": 1, "start": 0.0, "end": 0.5}]
    results = fit_segments_to_timeline(segments, str(source), str(tmp_path / "out"), max_speedup=1.0)
    assert results[0]["speed"] == 1.0
    assert results[0]["status"] == "STILL_OVERFLOWS"
    assert len(AudioSegment.from_wav(tmp_path / "out" / "seg_001.wav")) == 2000


def test_final_sentence_is_not_cut_by_overlay_buffer(tmp_path):
    tone(tmp_path / "seg_001.wav", 2500)
    output = tmp_path / "merged.wav"
    merge_segments([{"id": 1, "start": 1.0}], str(tmp_path), str(output), total_duration=2.0)
    assert len(AudioSegment.from_wav(output)) == 3500


def test_tts_cache_tracks_speed_and_changed_translation(monkeypatch):
    segments = [{"id": 1, "text_vi": "Bản dịch cũ"}]
    monkeypatch.setattr(config, "TTS_SPEED", 1.0)
    first = pipeline_vi._build_tts_cache_config("voice", segments)
    monkeypatch.setattr(config, "TTS_SPEED", 1.25)
    second = pipeline_vi._build_tts_cache_config("voice", segments)
    segments[0]["text_vi"] = "Bản dịch mới"
    third = pipeline_vi._build_tts_cache_config("voice", segments)
    assert first != second != third
    assert "Bản dịch mới" not in json.dumps(third)


def test_voice_preview_reuses_cache_but_invalidates_for_voice_speed_text_and_key(monkeypatch, tmp_path):
    calls = []
    def synthesize(text, path, voice_id, speed):
        calls.append((text, voice_id, speed))
        tone(path, 500)
        return {"provider": "lucylab", "voice": voice_id, "actual_duration": 0.5}
    monkeypatch.setattr(vietnamese, "synthesize_segment_vi", synthesize)
    monkeypatch.setattr(config, "VIETNAMESE_API_KEYS", ("private-test-key",))
    first = preview.generate_preview("vi", "male", "Xin chào", 1.0, tmp_path)
    second = preview.generate_preview("vi", "male", "Xin chào", 1.0, tmp_path)
    assert second["cached"] is True and len(calls) == 1
    assert first["path"] == second["path"]
    preview.generate_preview("vi", "female", "Xin chào", 1.0, tmp_path)
    preview.generate_preview("vi", "male", "Xin chào", 1.25, tmp_path)
    preview.generate_preview("vi", "male", "Nội dung khác", 1.0, tmp_path)
    monkeypatch.setattr(config, "VIETNAMESE_API_KEYS", ("new-private-key",))
    preview.generate_preview("vi", "male", "Xin chào", 1.0, tmp_path)
    assert len(calls) == 5
    for manifest in tmp_path.glob("*.json"):
        assert "private" not in manifest.read_text(encoding="utf-8")


def test_invalid_preview_does_not_contact_tts(monkeypatch, tmp_path):
    monkeypatch.setattr(vietnamese, "synthesize_segment_vi", lambda *a, **kw: pytest.fail("Unexpected API call"))
    with pytest.raises(ValueError):
        preview.generate_preview("vi", "male", "x" * 501, 1.0, tmp_path)
    with pytest.raises(ValueError):
        preview.generate_preview("vi", "male", "...", 1.0, tmp_path)
