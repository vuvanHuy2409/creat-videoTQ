"""Subtitles follow fitted speech, not original scene duration or reading guesses."""

import copy
import json
from pathlib import Path

import pytest
from pydub import AudioSegment
from pydub.generators import Sine

from src.audio_merger import fit_segments_to_timeline
from src.subtitle_timing import generate_tts_subtitles, apply_fitted_timing_to_results
from src.video_merger import _parse_srt, _build_display_chunks, _write_ass_file, _build_drawtext_filter


def save_audio(directory, number, length=1000, leading=0, trailing=0, gain=0):
    directory.mkdir(exist_ok=True)
    sound = Sine(440).to_audio_segment(duration=length).apply_gain(gain)
    audio = AudioSegment.silent(duration=leading) + sound + AudioSegment.silent(duration=trailing)
    audio.export(directory / f"seg_{number:03d}.wav", format="wav")


@pytest.mark.parametrize("field,text", [("text_vi", "Xin chào bạn."), ("text_jp", "こんにちは。")])
def test_caption_uses_speech_edges_not_scene_end_and_preserves_source(tmp_path, field, text):
    fitted = tmp_path / "segments_fit"
    save_audio(fitted, 1, leading=200, trailing=400)
    segments = [{"id": 1, "start": 5.0, "end": 10.0, "duration": 5.0, "text": "Hello", field: text}]
    before = copy.deepcopy(segments)
    path = tmp_path / "translated.srt"
    aligned = generate_tts_subtitles(segments, fitted, path, field)
    entry = _parse_srt(path)[0]
    assert entry["text"] == text
    assert entry["start"] == pytest.approx(5.2, abs=.01)
    assert entry["end"] == pytest.approx(6.2, abs=.01)
    assert aligned[0]["audio_end"] == 6.6
    assert segments == before
    assert len(AudioSegment.from_wav(fitted / "seg_001.wav")) == 1600
    manifest = json.loads((tmp_path / "subtitle_timing.json").read_text())
    assert manifest["basis"] == "fitted_tts_speech"
    assert manifest["segments"][0]["source_end"] == 10.0
    assert manifest["segments"][0]["subtitle_end"] == entry["end"]


def test_catchup_overflow_keeps_caption_until_voice_finishes(tmp_path):
    raw, fitted = tmp_path / "raw", tmp_path / "fitted"
    save_audio(raw, 1, 3000)
    save_audio(raw, 2, 500)
    segments = [{"id": 1, "start": 0, "end": 1, "text_vi": "Câu đầu dài"},
                {"id": 2, "start": 1, "end": 2, "text_vi": "Câu thứ hai"}]
    adjustments = fit_segments_to_timeline(segments, str(raw), str(fitted), max_speedup=1.4)
    for item in adjustments:
        item["effective_speed"] = item["speed"]
    aligned = generate_tts_subtitles(segments, fitted, tmp_path / "translated.srt", "text_vi")
    captions = _build_display_chunks(tmp_path / "translated.srt")
    duration = len(AudioSegment.from_wav(fitted / "seg_001.wav")) / 1000
    assert captions[0]["end"] == pytest.approx(duration, abs=.01)
    assert 2 < captions[0]["end"] < 2.3
    assert captions[1]["start"] == 1
    assert captions[1]["end"] == 1.5
    results = [{"actual_duration": 3.0}, {"actual_duration": .5}]
    apply_fitted_timing_to_results(results, aligned, adjustments)
    assert results[0]["pre_fit_duration"] == 3
    assert results[0]["actual_duration"] == duration
    assert results[0]["subtitle_end"] == captions[0]["end"]
    assert results[0]["rate_applied"] == "1.4x"


def test_silence_is_not_captioned_and_quiet_speech_is_kept(tmp_path):
    fitted = tmp_path / "fitted"
    fitted.mkdir()
    AudioSegment.silent(duration=500).export(fitted / "seg_001.wav", format="wav")
    save_audio(fitted, 2, 500, gain=-40)
    segments = [{"id": 1, "start": 0, "end": 1, "text_vi": "Không có tiếng"},
                {"id": 2, "start": 2.0009, "end": 3, "text_vi": "Giọng nhỏ"}]
    aligned = generate_tts_subtitles(segments, fitted, tmp_path / "translated.srt", "text_vi")
    captions = _parse_srt(tmp_path / "translated.srt")
    assert len(captions) == 1
    assert captions[0]["text"] == "Giọng nhỏ"
    assert captions[0]["start"] == 2.0  # same quantization as audio placement
    assert not aligned[0]["has_speech"]


def test_missing_fitted_audio_cannot_fall_back_to_incorrect_source_timestamps(tmp_path):
    with pytest.raises(FileNotFoundError):
        generate_tts_subtitles([{"id": 1, "start": 0, "end": 5}], tmp_path, tmp_path / "x.srt", "text_vi")
    assert not (tmp_path / "x.srt").exists()


def test_renderer_limits_long_sentences_but_preserves_outer_bounds_and_silent_gaps(tmp_path):
    path = tmp_path / "test.srt"
    text = "Đây là một câu dài có nhiều hơn mười lăm từ. Câu này vẫn dùng chung một đoạn giọng đọc."
    path.write_text(f"1\n00:00:01,000 --> 00:00:01,650\n{text}\n\n"
                    "2\n00:00:05,000 --> 00:00:05,450\nCâu cuối.\n", encoding="utf-8")
    entries = _build_display_chunks(path)
    assert len(entries) == 3
    assert " ".join(item["text"] for item in entries[:-1]) == text
    assert all(len(item["text"].split()) <= 15 for item in entries)
    assert entries[0]["start"] == 1
    assert entries[0]["end"] == entries[1]["start"]
    assert entries[1]["end"] == 1.65
    assert entries[-1] == {"start": 5, "end": 5.45, "text": "Câu cuối."}
    ass_path = tmp_path / "test.ass"
    _write_ass_file(entries, str(ass_path), 1920, 1080, 780)
    ass = ass_path.read_text()
    assert "0:00:01.00," in ass
    assert ",0:00:01.65," in ass
    assert "0:00:05.00,0:00:05.45" in ass
    assert all(item["text"] in ass for item in entries)
    drawtext = _build_drawtext_filter(entries, frame_w=720)
    assert "gte(t,1.000)*lt(t," in drawtext
    assert "1.650)" in drawtext
    assert "gte(t,5.000)*lt(t,5.450)" in drawtext
    assert "\n" in drawtext  # long text wraps spatially, not into guessed timestamps


def test_ass_rounding_carries_into_next_minute(tmp_path):
    path = tmp_path / "test.ass"
    _write_ass_file([{"start": 59.999, "end": 60.129, "text": "Xin chào"}], str(path), 1920, 1080, 780)
    assert "0:01:00.00,0:01:00.13" in path.read_text()


@pytest.mark.parametrize("language", ["vi", "jp"])
def test_both_pipelines_render_subtitles_measured_after_tts(tmp_path, monkeypatch, language):
    import pipeline
    import pipeline_vi
    module = pipeline_vi if language == "vi" else pipeline
    monkeypatch.setattr(module.config, "GOOGLE_API_KEY", "")
    monkeypatch.setattr(module.config, "TTS_SPEED", 1.0)
    monkeypatch.setattr(module.config, "TTS_OVERFLOW_MAX_SPEED", 1.0)
    folder = tmp_path / "project"
    folder.mkdir()
    source = folder / "source.mp4"
    source.write_bytes(b"source")
    AudioSegment.silent(duration=6000).export(folder / "original_audio.wav", format="wav")
    original = [{"id": 1, "start": 1.0, "end": 5.0, "duration": 4.0, "text": "Hello"}]
    translated = [{**original[0], f"text_{language}": "Xin chào" if language == "vi" else "こんにちは"}]
    (folder / "transcript_original.json").write_text(json.dumps(original))
    translation = folder / f"transcript_{language}.json"
    translation.write_text(json.dumps(translated))
    before = translation.read_bytes()

    def synthesize(**kwargs):
        path = Path(kwargs["output_path"])
        save_audio(path.parent, 1, leading=200, trailing=300)
        return {"path": str(path), "actual_duration": 1.5, "speed_adjusted": False, "rate_applied": "1x"}

    def render(**kwargs):
        entry = _parse_srt(kwargs["srt_path"])[0]
        assert entry["start"] == pytest.approx(1.2, abs=.01)
        assert entry["end"] == pytest.approx(2.2, abs=.01)
        Path(kwargs["output_path"]).write_bytes(b"rendered")

    monkeypatch.setattr(module, "synthesize_segment_vi" if language == "vi" else "synthesize_segment", synthesize)
    monkeypatch.setattr(module, "merge_video", render)
    common = dict(url=None, file_path=str(source), source_lang="en-US", skip_video=False,
                  output_dir=str(tmp_path), resume_dir=str(folder), bg_mode="none", burn_subtitles=True,
                  subtitle_y=780)
    if language == "vi":
        report = module.run_pipeline_vi(voice_id="female", **common)
    else:
        report = module.run_pipeline(voice="ja-JP-KeitaNeural", **common)
    assert translation.read_bytes() == before
    assert report["total_tts_duration"] == 1.5
    guide = json.loads((folder / "timing_guide.json").read_text())
    assert guide["segments"][0]["subtitle_end"] == pytest.approx(2.2, abs=.01)
    assert guide["segments"][0]["end"] == 5
