"""Caption timing measured from the same fitted WAVs used by the audio mixer."""

import json
import math
from pathlib import Path

from pydub import AudioSegment
from pydub.silence import detect_nonsilent

from src.srt_generator import generate_srt
from src.subtitle_chunks import split_caption_interval, caption_word_budgets, MAX_CAPTION_WORDS


def align_subtitles_to_tts(segments, segment_dir, text_field):
    """Keep placement and text; replace display bounds with audible TTS bounds.

    Alignment is per sentence/segment, not a guess at individual word timings.
    Quiet padding at the edges is excluded; pauses within a sentence stay in
    that sentence's caption. Source dictionaries and audio files are untouched.
    """
    aligned = []
    for segment in segments:
        start = float(segment["start"])
        if not math.isfinite(start) or start < 0:
            raise ValueError("Thời điểm bắt đầu lời đọc phải là số không âm.")
        # Match the integer-millisecond placement used by merge_segments.
        position_ms = int(start * 1000)
        path = Path(segment_dir) / f"seg_{segment['id']:03d}.wav"
        audio = AudioSegment.from_wav(path)
        audible = []
        if audio.rms and len(audio):
            audible = detect_nonsilent(audio, min_silence_len=60,
                                      silence_thresh=max(-55.0, audio.max_dBFS - 45.0),
                                      seek_step=5)
        first_ms, last_ms = (audible[0][0], audible[-1][1]) if audible else (0, 0)
        caption = dict(segment)
        caption.update({
            "source_start": segment["start"], "source_end": segment["end"],
            "audio_start": position_ms / 1000,
            "audio_end": (position_ms + len(audio)) / 1000,
            "audio_duration": len(audio) / 1000,
            "start": (position_ms + first_ms) / 1000,
            "end": (position_ms + last_ms) / 1000,
            "duration": (last_ms - first_ms) / 1000,
            "has_speech": bool(audible),
            "speech_spans": [[(position_ms + first) / 1000, (position_ms + last) / 1000]
                             for first, last in audible],
            "text": segment.get(text_field, segment.get("text", "")),
        })
        aligned.append(caption)
    return aligned


def generate_tts_subtitles(segments, segment_dir, output_path, text_field):
    """Write final SRT plus an auditable timeline; fail if fitted audio is missing."""
    aligned = align_subtitles_to_tts(segments, segment_dir, text_field)
    visible = []
    spoken = [item for item in aligned if item["has_speech"] and item["text"].strip()]
    budgets = caption_word_budgets(spoken)
    for item, budget in zip(spoken, budgets):
        item["word_budget"] = budget
    for item in aligned:
        item["chunks"] = (split_caption_interval(item, item["speech_spans"], item["word_budget"])
                          if item["has_speech"] and item["text"].strip() else [])
        visible.extend(item["chunks"])
    generate_srt(visible, output_path, text_field="text")
    manifest = Path(output_path).with_name("subtitle_timing.json")
    manifest.write_text(json.dumps({
        "version": 2, "basis": "fitted_tts_speech", "alignment": "phrase",
        "max_words": MAX_CAPTION_WORDS,
        "phrase_timing": "speech_pauses_with_word_weighted_fallback",
        "segments": [{
            "id": item["id"], "source_start": item["source_start"], "source_end": item["source_end"],
            "audio_start": item["audio_start"], "audio_end": item["audio_end"],
            "audio_duration": item["audio_duration"], "has_speech": item["has_speech"],
            "subtitle_start": item["start"] if item["has_speech"] else None,
            "subtitle_end": item["end"] if item["has_speech"] else None,
            "text": item["text"],
            "chunks": [{key: chunk[key] for key in ("start", "end", "text")} for chunk in item["chunks"]],
        } for item in aligned],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return aligned


def apply_fitted_timing_to_results(results, aligned, adjustments):
    """Reports use final duration, including catch-up tempo, rather than raw TTS."""
    rates = {item["id"]: item for item in adjustments}
    for result, caption in zip(results, aligned):
        result["pre_fit_duration"] = result["actual_duration"]
        result["actual_duration"] = caption["audio_duration"]
        for key in ("audio_start", "audio_end"):
            result[key] = caption[key]
        result["subtitle_start"] = caption["start"] if caption["has_speech"] else None
        result["subtitle_end"] = caption["end"] if caption["has_speech"] else None
        rate = rates.get(caption["id"], {})
        result["speed_adjusted"] = result.get("speed_adjusted", False) or rate.get("speed", 1) != 1
        if rate.get("effective_speed"):
            result["rate_applied"] = f"{rate['effective_speed']:g}x"
