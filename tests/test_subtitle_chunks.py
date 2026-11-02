"""Readable 10–15 word phrases remain inside actual TTS speech bounds."""

import copy
import json

import pytest
from pydub import AudioSegment
from pydub.generators import Sine

from src.subtitle_chunks import split_caption_text, split_caption_interval, split_caption_entries
from src.subtitle_timing import generate_tts_subtitles
from src.video_merger import _build_display_chunks, _parse_srt


@pytest.mark.parametrize("count", [1, 10, 15, 16, 20, 25, 30, 31, 44, 100])
def test_word_cap_keeps_all_words_in_order_and_balances_tail(count):
    text = " ".join(f"từ{index}" for index in range(count))
    chunks = split_caption_text(text)
    assert " ".join(chunks) == text
    assert all(1 <= len(chunk.split()) <= 15 for chunk in chunks)
    if count > 15:
        assert all(len(chunk.split()) >= 5 for chunk in chunks)


def test_punctuation_is_preferred_to_a_cut_in_middle_of_clause():
    text = "Ngoan xinh, mặt nghiêm mà vẫn cưng dễ thương. Hai bé cùng đàn thì bất ngờ luôn!"
    chunks = split_caption_text(text)
    assert chunks == ["Ngoan xinh, mặt nghiêm mà vẫn cưng dễ thương.", "Hai bé cùng đàn thì bất ngờ luôn!"]


def test_japanese_without_spaces_is_split_without_losing_characters():
    text = "日本語の字幕を短く読みやすい単位で順番に表示します。" * 3
    chunks = split_caption_text(text)
    assert "".join(chunks) == text
    assert all(len(chunk) <= 15 for chunk in chunks)


def test_pause_is_used_as_boundary_and_is_not_filled_with_caption():
    text = " ".join(f"từ{index}" for index in range(24))
    entry = {"start": 10., "end": 16., "text": text}
    before = copy.deepcopy(entry)
    chunks = split_caption_interval(entry, [[10., 12.8], [13.2, 16.]])
    assert len(chunks) == 2
    assert chunks[0]["start"] == 10.
    assert chunks[0]["end"] == 12.8
    assert chunks[1]["start"] == 13.2
    assert chunks[1]["end"] == 16.
    assert entry == before


def test_without_word_times_fallback_is_bounded_positive_and_keeps_overlap():
    entry = {"start": 1., "end": 3., "text": " ".join(["chữ"] * 31)}
    chunks = split_caption_interval(entry)
    assert len(chunks) == 3
    assert chunks[0]["start"] == 1.
    assert chunks[-1]["end"] == 3.
    assert all(item["start"] < item["end"] for item in chunks)
    assert all(left["end"] == right["start"] for left, right in zip(chunks, chunks[1:]))
    following = split_caption_interval({"start": 2., "end": 2.5, "text": "Câu kế tiếp"})
    assert chunks[-1]["end"] > following[0]["start"]


def test_srt_manifest_and_renderers_receive_same_short_phrases(tmp_path):
    audio_dir = tmp_path / "segments_fit"
    audio_dir.mkdir()
    voice = Sine(440).to_audio_segment(duration=1000)
    audio = AudioSegment.silent(duration=200) + voice + AudioSegment.silent(duration=300) + voice + AudioSegment.silent(duration=300)
    audio.export(audio_dir / "seg_001.wav", format="wav")
    text = " ".join(f"từ{index}" for index in range(24))
    segments = [{"id": 1, "text": "original", "text_vi": text, "start": 5., "end": 12., "duration": 7.}]
    before = copy.deepcopy(segments)
    path = tmp_path / "transcript_vi.srt"
    aligned = generate_tts_subtitles(segments, audio_dir, path, "text_vi")
    entries = _parse_srt(path)
    assert len(entries) == 2
    assert " ".join(entry["text"] for entry in entries) == text
    assert all(len(entry["text"].split()) <= 15 for entry in entries)
    assert _build_display_chunks(path) == entries
    assert entries[0]["start"] == pytest.approx(5.2, abs=.01)
    assert entries[0]["end"] == pytest.approx(6.2, abs=.01)
    assert entries[1]["start"] == pytest.approx(6.5, abs=.01)
    assert entries[1]["end"] == pytest.approx(7.5, abs=.01)
    assert segments == before
    assert len(aligned) == 1  # report retains the original segment count
    manifest = json.loads((tmp_path / "subtitle_timing.json").read_text())
    assert manifest["alignment"] == "phrase"
    assert manifest["max_words"] == 15
    assert manifest["segments"][0]["chunks"] == entries


def test_overlapping_voices_share_total_screen_budget_without_losing_text():
    entries = [{"start": 0., "end": 4., "text": " ".join(f"a{i}" for i in range(24))},
               {"start": 1., "end": 3., "text": " ".join(f"b{i}" for i in range(20))},
               {"start": 8., "end": 10., "text": " ".join(f"c{i}" for i in range(15))}]
    chunks = split_caption_entries(entries)
    for prefix, original in zip("abc", entries):
        words = [word for chunk in chunks for word in chunk["text"].split() if word.startswith(prefix)]
        assert words == original["text"].split()
    events = sorted(event for chunk in chunks for event in (
        (chunk["start"], 1, len(chunk["text"].split())),
        (chunk["end"], 0, -len(chunk["text"].split()))))
    current = 0
    for _, _, delta in events:
        current += delta
        assert current <= 15
    assert next(chunk for chunk in chunks if chunk["start"] == 8.)["text"] == entries[2]["text"]
