"""Điểm vào CLI cho pipeline lồng tiếng tiếng Nhật.

Ví dụ:
    python pipeline.py                          # đọc VIDEO_URL từ .env
    python pipeline.py --url "https://..."      # ghi đè URL bằng tham số CLI
    python pipeline.py --file video.mp4 --source-lang vi
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime

import config
from src.pipeline_history import track_pipeline, bind_project
from src.voice_registry import pipeline_voice
from src.subtitle_fonts import font_names, get_font, validate_size
from src.subtitle_style import (SubtitleStyle, add_style_arguments, style_overrides_from_args,
                                load_project_subtitles, save_project_subtitles)
from src.utils import setup_logging, ensure_dir
from src.downloader import download_video
from src.audio_extractor import extract_audio
from src.transcriber import transcribe, save_transcript
from src.synthesizer import synthesize_segment
from src.translate_pending import (
    has_complete_translation,
    write_hint as _write_translate_pending_hint,
)
from src.audio_merger import merge_segments, fit_segments_to_timeline
from src.vocal_separator import separate_vocals
from src.video_merger import merge_video
from src.srt_generator import generate_srt
from src.subtitle_timing import generate_tts_subtitles, apply_fitted_timing_to_results
from src.content_generator import generate_content
from src.ai_subtitle_blur import apply_ai_blur
from src.video_quality import has_current_quality_marker

logger = setup_logging("pipeline")

LANG_MAP = {
    "en": "en-US",
    "vi": "vi-VN",
    "zh": "zh-CN",
    "en-US": "en-US",
    "vi-VN": "vi-VN",
    "zh-CN": "zh-CN",
    "zh-HK": "zh-HK",
    "zh-TW": "zh-TW",
}


def _build_timing_guide(report: dict, segments: list[dict], tts_results: list[dict]) -> dict:
    """Tạo báo cáo chênh lệch thời lượng audio gốc và audio tiếng Nhật.

    Người dùng có thể dựa vào báo cáo này để biết segment nào cần chỉnh trong
    CapCut.
    """
    guide = {
        "session_id": report["session_id"],
        "source_url": report["source_url"],
        "summary": {
            "total_segments": report["total_segments"],
            "original_duration": report["total_original_duration"],
            "jp_duration": report["total_tts_duration"],
            "ratio": round(report["total_tts_duration"] / report["total_original_duration"], 2)
                     if report["total_original_duration"] > 0 else 0,
            "segments_need_edit": 0,
            "segments_ok": 0,
        },
        "segments": [],
    }

    need_edit = 0
    for seg, tts in zip(segments, tts_results):
        diff = round(tts["actual_duration"] - seg["duration"], 2)

        # Chấp nhận nếu audio dịch nằm trong khoảng ±30% thời lượng gốc.
        if abs(diff) <= seg["duration"] * 0.3:
            status = "OK"
        elif diff > 0:
            status = "TOO_LONG"
            need_edit += 1
        else:
            status = "TOO_SHORT"
            need_edit += 1

        guide["segments"].append({
            "id": seg["id"],
            "text_original": seg["text"],
            "text_jp": seg.get("text_jp", ""),
            "start": seg["start"],
            "end": seg["end"],
            **{key: tts[key] for key in ("audio_start", "audio_end", "subtitle_start", "subtitle_end") if key in tts},
            "original_duration": seg["duration"],
            "jp_duration": tts["actual_duration"],
            "diff_seconds": diff,
            "speed_adjusted": tts["speed_adjusted"],
            "rate_applied": tts.get("rate_applied", ""),
            "status": status,
            "edit_hint": f"JP {'dài' if diff > 0 else 'ngắn'} hơn {abs(diff):.1f}s"
                         if status != "OK" else "OK",
        })

    guide["summary"]["segments_need_edit"] = need_edit
    guide["summary"]["segments_ok"] = report["total_segments"] - need_edit

    return guide


def parse_args() -> argparse.Namespace:
    """Đọc tham số CLI và nạp URL mặc định từ biến môi trường nếu cần."""
    parser = argparse.ArgumentParser(description="Video Dubbing Pipeline: EN/VI → JP")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--url", help="YouTube/TikTok video URL (default: VIDEO_URL from .env)")
    group.add_argument("--file", help="Local video file path")

    parser.add_argument(
        "--source-lang",
        default=config.DEFAULT_SOURCE_LANG,
        help=f"Source language: en, vi, zh, en-US, vi-VN, zh-CN, zh-HK, zh-TW (default: {config.DEFAULT_SOURCE_LANG})",
    )
    parser.add_argument(
        "--voice",
        default=config.DEFAULT_VOICE_JP or config.TTS_VOICE,
        help=f"TTS voice name (default: {config.TTS_VOICE})",
    )
    parser.add_argument(
        "--skip-video",
        action="store_true",
        help="Skip final video merge (only produce audio + SRT)",
    )
    parser.add_argument(
        "--burn-subtitles",
        action="store_true",
        help="Burn translated subtitles into the output video",
    )
    parser.add_argument(
        "--output-dir",
        default=config.OUTPUT_DIR,
        help=f"Output directory (default: {config.OUTPUT_DIR})",
    )
    parser.add_argument(
        "--subtitle-y",
        type=float,
        default=72.0,
        help="Vertical position of subtitles from the top of the video in percentage (0-100, default: 72)",
    )
    parser.add_argument(
        "--subtitle-size",
        type=validate_size,
        default=None,
        help="Subtitle size in pixels (12-160; default: SUBTITLE_FONT_SIZE)",
    )
    add_style_arguments(parser)
    parser.add_argument(
        "--subtitle-font",
        choices=font_names(),
        default=None,
        help="Bundled subtitle font family (default: SUBTITLE_FONT)",
    )
    parser.add_argument(
        "--bg-mode",
        choices=["demucs", "duck", "none"],
        default="demucs",
        help="How to handle the original audio under the JP narration: "
             "'demucs' (default) runs vocal separation; 'duck' lowers original_audio.wav "
             "by --bg-duck-db (default -12) and overlays JP — fast, no Demucs; "
             "'none' uses a silent base.",
    )
    parser.add_argument(
        "--bg-duck-db",
        type=float,
        default=-12.0,
        help="Gain (dB) applied to original audio in 'duck' mode (default -12 dB ≈ 25%%).",
    )
    parser.add_argument(
        "--no-bg-music",
        action="store_true",
        help="Deprecated alias for --bg-mode=none.",
    )
    parser.add_argument(
        "--resume",
        metavar="WORK_DIR",
        help="Resume a previous run inside the given work directory. "
             "Skips already-completed steps based on cached artifacts.",
    )

    parser.add_argument(
        "--ai-blur",
        action="store_true",
        help="Use AI (YOLO/EasyOCR) to automatically detect and blur Chinese subtitles.",
    )

    args = parser.parse_args()
    if args.no_bg_music:
        args.bg_mode = "none"

    # Nếu không truyền URL/file thì lấy VIDEO_URL từ .env. Khi resume, video
    # được tìm trong work_dir nên không bắt buộc hai tham số này.
    if not args.url and not args.file and not args.resume:
        if config.VIDEO_URL:
            args.url = config.VIDEO_URL
            logger.info(f"Using VIDEO_URL from .env: {args.url}")
        else:
            parser.error("No video specified. Use --url, --file, --resume, or set VIDEO_URL in .env")

    return args


def _resolve_video(work_dir: str, url: str | None, file_path: str | None) -> str:
    """Tìm video nguồn cho thư mục làm việc hiện tại.

    Khi resume, ưu tiên video đã có trong work_dir và bỏ qua các file
    ``dubbed_video*.mp4`` vì đó là output đã kết xuất.
    """
    # File được truyền trực tiếp luôn được ưu tiên, hữu ích khi nguồn nằm ngoài
    # thư mục dự án.
    if file_path:
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Video file not found: {file_path}")
        return file_path

    video_exts = (".mp4", ".mkv", ".webm", ".mov", ".avi")
    output_prefixes = ("dubbed_video",)
    if os.path.isdir(work_dir):
        for f in sorted(os.listdir(work_dir)):
            lower = f.lower()
            if not lower.endswith(video_exts):
                continue
            if any(lower.startswith(prefix) for prefix in output_prefixes):
                continue
            cached = os.path.join(work_dir, f)
            logger.info(f"Reusing existing video: {cached}")
            return cached

    if url:
        return download_video(url, work_dir)

    raise RuntimeError(
        f"No source video found in {work_dir} and no --url/--file given. "
        "Pass --file <path> on resume if the original is outside work_dir."
    )


def _detect_source_lang(text: str, default_lang: str) -> str:
    """Đoán ngôn ngữ từ các dải Unicode, nếu không thì dùng giá trị mặc định."""
    if any('\u4e00' <= c <= '\u9fff' for c in text):
        return "zh"
    if any('\u3040' <= c <= '\u30ff' for c in text):
        return "ja"
    if any('\uac00' <= c <= '\ud7a3' for c in text):
        return "ko"
    return default_lang


@track_pipeline("jp")
def run_pipeline(
    url: str | None,
    file_path: str | None,
    source_lang: str,
    voice: str,
    skip_video: bool,
    output_dir: str,
    resume_dir: str | None = None,
    bg_mode: str = "demucs",
    bg_duck_db: float = -12.0,
    burn_subtitles: bool = False,
    subtitle_y: float = 72.0,
    subtitle_size: int | None = None,
    ai_blur: bool = False,
    subtitle_font: str | None = None,
    subtitle_style: dict | SubtitleStyle | None = None,
) -> dict:
    """Chạy toàn bộ pipeline tiếng Nhật và trả báo cáo kết quả.

    Pipeline có thể tạo mới hoặc resume thư mục cũ; sau ASR sẽ dừng tại điểm
    dịch thủ công cho đến khi transcript tiếng Nhật hợp lệ.
    """
    start_time = time.time()
    if voice.startswith("voice:"):
        voice = pipeline_voice("jp", voice, vars(config))

    lang_code = LANG_MAP.get(source_lang, source_lang)
    logger.info(f"Source language: {lang_code}")

    # Resume dùng lại artifact đã có; lần chạy mới tạo folder theo timestamp.
    if resume_dir:
        if not os.path.isdir(resume_dir):
            raise FileNotFoundError(f"Resume directory not found: {resume_dir}")
        work_dir = os.path.abspath(resume_dir)
        folder_name = os.path.basename(os.path.normpath(work_dir))
        logger.info(f"Resuming work directory: {work_dir}")
    else:
        folder_name = datetime.now().strftime("%Y%m%d%H%M%S")
        work_dir = os.path.abspath(ensure_dir(os.path.join(output_dir, folder_name)))
        logger.info(f"Output folder: {work_dir}")

    profile = load_project_subtitles(work_dir)
    if subtitle_font is not None:
        profile["SUBTITLE_FONT"] = subtitle_font
    if subtitle_size is not None:
        profile["SUBTITLE_FONT_SIZE"] = str(subtitle_size)
    if subtitle_style is not None:
        profile.update(subtitle_style.to_settings() if isinstance(subtitle_style, SubtitleStyle) else subtitle_style)
    profile = save_project_subtitles(work_dir, profile)
    subtitle_font = profile["SUBTITLE_FONT"]
    subtitle_size = int(profile["SUBTITLE_FONT_SIZE"])
    subtitle_style = SubtitleStyle.from_settings(profile)

    bind_project(work_dir, "jp")

    transcript_orig_path = os.path.join(work_dir, "transcript_original.json")
    transcript_jp_path = os.path.join(work_dir, "transcript_jp.json")
    audio_path = os.path.join(work_dir, "original_audio.wav")

    # ── Bước 1: tải video hoặc dùng file cục bộ ─────────────────────────────
    logger.info("=" * 60)
    logger.info("STEP 1: Acquiring video")
    video_path = _resolve_video(work_dir, url, file_path)
    logger.info(f"Video: {video_path}")


    # ── Bước 2: trích audio chuẩn mono PCM ─────────────────────────────────
    logger.info("=" * 60)
    if os.path.exists(audio_path) and os.path.getsize(audio_path) > 0:
        logger.info(f"STEP 2: Reusing existing extracted audio: {audio_path}")
    else:
        logger.info("STEP 2: Extracting audio")
        extract_audio(video_path, audio_path)

    # ── Bước 2.5: chọn nhạc nền cho bản lồng tiếng ─────────────────────────
    # Demucs giữ nhạc/hiệu ứng; duck giảm toàn bộ audio gốc; none dùng nền im lặng.
    background_path: str | None = None
    background_gain_db: float = 0.0
    if bg_mode == "demucs":
        logger.info("=" * 60)
        logger.info("STEP 2.5: Separating vocals from original audio (Demucs)")
        sep = separate_vocals(audio_path, work_dir)
        background_path = sep.get("no_vocals")
        if background_path is None:
            logger.warning(
                "Vocal separation unavailable — dubbed audio will use a silent base"
            )
    elif bg_mode == "duck":
        logger.info("=" * 60)
        logger.info(
            f"STEP 2.5: Ducking original audio by {bg_duck_db:+.1f} dB "
            "(no vocal separation)"
        )
        background_path = audio_path
        background_gain_db = bg_duck_db
    elif bg_mode == "none":
        logger.info("STEP 2.5 skipped: --bg-mode=none, dubbed audio uses silent base")

    # ── Bước 3: ASR và tùy chọn làm mờ phụ đề cứng ──────────────────────────
    logger.info("=" * 60)
    
    if ai_blur:
        logger.info("STEP 3A: Applying AI Subtitle Blur")
        blurred_video_path = os.path.join(work_dir, "video_ai_blurred.mp4")
        if (
            os.path.exists(blurred_video_path)
            and os.path.getsize(blurred_video_path) > 0
            and has_current_quality_marker(blurred_video_path, "ai_blur")
        ):
            logger.info(f"Reusing existing AI blurred video: {blurred_video_path}")
            video_path = blurred_video_path
        else:
            if os.path.exists(blurred_video_path):
                logger.info("Existing AI-blurred video was created with an older quality profile; regenerating it.")
            success = apply_ai_blur(video_path, blurred_video_path)
            if success:
                video_path = blurred_video_path
            else:
                logger.error("AI blur failed, continuing with original video.")
                
    if os.path.exists(transcript_orig_path):
        logger.info(f"STEP 3B: Reusing existing transcript: {transcript_orig_path}")
        with open(transcript_orig_path, encoding="utf-8") as f:
            segments = json.load(f)
    else:
        logger.info("STEP 3B: Transcribing audio (ASR)")
        segments = transcribe(audio_path, lang_code)
        save_transcript(segments, transcript_orig_path)
        generate_srt(segments, os.path.join(work_dir, "transcript_original.srt"), text_field="text")
    logger.info(f"Transcribed {len(segments)} segments")

    # ── Bước 4: điểm dừng dịch tiếng Nhật thủ công ──────────────────────────
    logger.info("=" * 60)
    logger.info("STEP 4: Loading manual Japanese translation")
    
    is_valid_translation = False
    if os.path.exists(transcript_jp_path):
        try:
            with open(transcript_jp_path, encoding="utf-8") as f:
                temp_segs = json.load(f)
            if has_complete_translation(temp_segs, "text_jp", segments):
                is_valid_translation = True
                segments = temp_segs
            else:
                logger.warning(
                    "transcript_jp.json exists but is incomplete; manual translation is still required."
                )
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(f"Cannot load manual translation file: {exc}")

    if is_valid_translation:
        logger.info(f"Reusing existing translation: {transcript_jp_path}")
        # Tạo lại SRT tiếng Nhật để dùng khi burn subtitle.
        pending_hint = os.path.join(work_dir, "TRANSLATE_PENDING.txt")
        if os.path.exists(pending_hint):
            os.remove(pending_hint)
    else:
        hint_path = _write_translate_pending_hint(work_dir, "ja-JP", source_lang)
        logger.warning(
            "Manual translation required — no translation API was called. "
            f"Edit transcript_jp.json from the desktop app or follow {hint_path}, then resume."
        )
        return {
            "status": "translate_pending",
            "session_id": folder_name,
            "work_dir": work_dir,
            "translation_file": transcript_jp_path,
            "translation_hint": hint_path,
        }

    # ── Bước 5: tổng hợp từng segment bằng Azure TTS ────────────────────────
    logger.info("=" * 60)
    logger.info("STEP 5: Synthesizing Japanese audio (TTS)")
    seg_dir = ensure_dir(os.path.join(work_dir, "segments"))
    tts_results = []
    for seg in segments:
        seg_path = os.path.join(seg_dir, f"seg_{seg['id']:03d}.wav")
        result = synthesize_segment(
            text_jp=seg.get("text_jp", seg.get("text", "")),
            output_path=seg_path,
            target_duration=seg["duration"],
            voice=voice,
        )
        tts_results.append(result)
        logger.info(
            f"  Segment {seg['id']}: {result['actual_duration']:.1f}s "
            f"(target: {seg['duration']:.1f}s, adjusted: {result['speed_adjusted']})"
        )

    # ── Bước 6: đặt segment lên timeline và trộn với background ────────────
    logger.info("=" * 60)
    logger.info("STEP 6: Merging audio segments")
    total_duration = max(seg["end"] for seg in segments) + 1.0 if segments else 0
    fit_dir = ensure_dir(os.path.join(work_dir, "segments_fit"))
    max_extra = max(1.0, config.TTS_OVERFLOW_MAX_SPEED / config.TTS_SPEED)
    fit_adjustments = fit_segments_to_timeline(segments, seg_dir, fit_dir, max_speedup=max_extra)
    for adjustment in fit_adjustments:
        adjustment["effective_speed"] = round(config.TTS_SPEED * adjustment["speed"], 3)
    with open(os.path.join(work_dir, "fit_adjustments.json"), "w", encoding="utf-8") as handle:
        json.dump(fit_adjustments, handle, ensure_ascii=False, indent=2)
    subtitle_segments = generate_tts_subtitles(
        segments, fit_dir, os.path.join(work_dir, "transcript_jp.srt"), "text_jp")
    apply_fitted_timing_to_results(tts_results, subtitle_segments, fit_adjustments)
    logger.info("Subtitle timing follows fitted Japanese speech; silent WAV padding is excluded.")
    merged_audio_path = os.path.join(work_dir, "audio_jp_full.wav")
    merge_segments(
        segments, fit_dir, merged_audio_path, total_duration,
        background_path=background_path,
        background_gain_db=0.6,
    )

    # ── Bước 7: ghép audio với video (có thể bỏ qua) ────────────────────────
    dubbed_video_path = None
    if not skip_video:
        logger.info("=" * 60)
        logger.info("STEP 7: Creating dubbed video")
        dubbed_video_path = os.path.join(work_dir, "dubbed_video.mp4")
        srt_file = os.path.join(work_dir, "transcript_jp.srt") if burn_subtitles else None
        merge_video(
            video_path=video_path,
            audio_path=merged_audio_path,
            output_path=dubbed_video_path,
            srt_path=srt_file,
            y_pixel=subtitle_y,
            font_size=subtitle_size,
            font_name=subtitle_font,
            subtitle_style=subtitle_style,
        )

    # ── Bước 8: tạo nội dung YouTube nếu đã cấu hình Gemini ────────────────
    content_result = {"thumbnails": [], "metadata": {}}
    if config.GOOGLE_API_KEY:
        logger.info("=" * 60)
        logger.info("STEP 8: Generating thumbnails & YouTube metadata")
        try:
            content_result = generate_content(
                segments=segments,
                target_lang="ja-JP",
                source_url=url,
                output_dir=work_dir,
                api_key=config.GOOGLE_API_KEY,
                image_model_id=config.IMAGE_MODEL_ID,
                content_model_id=config.CONTENT_MODEL_ID,
            )
            logger.info(f"  Thumbnail prompts: {content_result.get('thumbnail_prompts_file', 'N/A')}")
            logger.info(f"  Metadata: {content_result.get('metadata_file', 'N/A')}")
        except Exception as e:
            logger.error(f"Content generation failed (non-fatal): {e}")
    else:
        logger.info("Skipping thumbnail/metadata generation (GOOGLE_API_KEY not set)")

    # Ghi report để GUI/batch biết trạng thái và các file đầu ra.
    elapsed = time.time() - start_time
    report = {
        "session_id": folder_name,
        "source_url": url,
        "source_language": lang_code,
        "voice": voice,
        "total_segments": len(segments),
        "total_original_duration": round(sum(s["duration"] for s in segments), 3),
        "total_tts_duration": round(sum(r["actual_duration"] for r in tts_results), 3),
        "segments_speed_adjusted": sum(1 for r in tts_results if r["speed_adjusted"]),
        "processing_time_seconds": round(elapsed, 1),
        "output_dir": work_dir,
        "files": {
            "original_audio": audio_path,
            "transcript_original_json": os.path.join(work_dir, "transcript_original.json"),
            "transcript_original_srt": os.path.join(work_dir, "transcript_original.srt"),
            "transcript_jp_json": os.path.join(work_dir, "transcript_jp.json"),
            "transcript_jp_srt": os.path.join(work_dir, "transcript_jp.srt"),
            "subtitle_timing": os.path.join(work_dir, "subtitle_timing.json"),
            "audio_jp_full": merged_audio_path,
            "dubbed_video": dubbed_video_path,
            "thumbnails": content_result.get("thumbnails", []),
            "youtube_metadata": content_result.get("metadata_file"),
        },
    }

    report_path = os.path.join(work_dir, "report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    # Ghi timing guide giúp chỉnh những segment dài/ngắn trong CapCut.
    timing_guide = _build_timing_guide(report, segments, tts_results)
    timing_path = os.path.join(work_dir, "timing_guide.json")
    with open(timing_path, "w", encoding="utf-8") as f:
        json.dump(timing_guide, f, ensure_ascii=False, indent=2)
    logger.info(f"Timing guide: {timing_path}")

    logger.info("=" * 60)
    logger.info("PIPELINE COMPLETE")
    logger.info(f"  Output:    {work_dir}")
    logger.info(f"  Segments:  {report['total_segments']}")
    logger.info(f"  Duration:  {report['total_original_duration']:.1f}s original, "
                f"{report['total_tts_duration']:.1f}s JP audio")
    logger.info(f"  Adjusted:  {report['segments_speed_adjusted']} segments sped up")
    logger.info(f"  Time:      {elapsed:.1f}s")
    logger.info("=" * 60)

    return report


def main():
    """Đọc tham số CLI, chạy pipeline Nhật và trả mã lỗi phù hợp cho shell."""
    args = parse_args()
    try:
        run_pipeline(
            url=args.url,
            file_path=args.file,
            source_lang=args.source_lang,
            voice=args.voice,
            skip_video=args.skip_video,
            output_dir=args.output_dir,
            resume_dir=args.resume,
            bg_mode=args.bg_mode,
            bg_duck_db=args.bg_duck_db,
            burn_subtitles=args.burn_subtitles,
            subtitle_y=args.subtitle_y,
            subtitle_size=args.subtitle_size,
            subtitle_font=args.subtitle_font,
            subtitle_style=style_overrides_from_args(args),
            ai_blur=args.ai_blur,
        )
    except Exception as e:
        logger.error(f"Pipeline failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
