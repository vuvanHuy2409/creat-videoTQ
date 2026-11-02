"""Điểm vào CLI cho pipeline lồng tiếng tiếng Việt.

Ví dụ:
    python pipeline_vi.py                          # đọc URL từ .env
    python pipeline_vi.py --url "https://..."      # ghi đè URL bằng CLI
    python pipeline_vi.py --file video.mp4 --source-lang en
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
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
from src.synthesizer_vi import synthesize_segment_vi, is_usable_tts_audio
from src.audio_merger import merge_segments, fit_segments_to_timeline
from src.vocal_separator import separate_vocals
from src.video_merger import merge_video
from src.translate_pending import (
    has_complete_translation,
    write_hint as _write_translate_pending_hint,
)
from src.srt_generator import generate_srt
from src.subtitle_timing import generate_tts_subtitles, apply_fitted_timing_to_results
from src.content_generator import generate_content
from src.ai_subtitle_blur import AI_BLUR_MODE, apply_ai_blur
from src.ocr_image_replace import OCR_IMAGE_REPLACE_MODE, apply_ocr_image_replace
from src.video_quality import has_current_quality_marker

logger = setup_logging("pipeline_vi")

LANG_MAP = {
    "en": "en-US",
    "ja": "ja-JP",
    "zh": "zh-CN",
    "en-US": "en-US",
    "ja-JP": "ja-JP",
    "zh-CN": "zh-CN",
    "zh-HK": "zh-HK",
    "zh-TW": "zh-TW",
}


def _secret_fingerprint(value: str | None) -> str:
    """Tạo dấu vân tay ngắn, không thể đảo ngược, để invalid cache TTS."""
    if not value:
        return ""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _build_tts_cache_config(voice_id: str | None, segments: list[dict] | None = None) -> dict:
    """Mô tả các thiết lập quyết định nội dung audio TTS đã sinh."""
    api_keys = tuple(getattr(config, "VIETNAMESE_API_KEYS", ()) or ())
    if not api_keys and config.VIETNAMESE_API_KEY:
        api_keys = (config.VIETNAMESE_API_KEY,)
    key_fingerprints = [_secret_fingerprint(value) for value in api_keys]
    has_lucylab = bool(api_keys) and not (voice_id or "").startswith("azure:")
    return {
        "provider": "lucylab" if has_lucylab else "azure",
        "voice_id": voice_id or "",
        "lucylab_api_url": config.LUCYLAB_API_URL if has_lucylab else "",
        # Giữ field cũ để tương thích nhưng theo dõi mọi key dự phòng; khi key
        # thay đổi, audio TTS cũ sẽ không được dùng lại.
        "api_key_fingerprint": key_fingerprints[0] if key_fingerprints else "",
        "api_key_fingerprints": key_fingerprints,
        "api_key_count": len(api_keys),
        "azure_vi_tts_voice": getattr(config, "AZURE_VI_TTS_VOICE", ""),
        "azure_region": config.AZURE_SPEECH_REGION,
        "tempo_version": 2,
        "tts_speed": config.TTS_SPEED,
        "azure_key_fingerprint": _secret_fingerprint(config.AZURE_SPEECH_KEY),
        "text_fingerprint": _secret_fingerprint(json.dumps(
            [(segment["id"], segment.get("text_vi", segment.get("text", ""))) for segment in (segments or [])],
            ensure_ascii=False,
        )),
    }


def _load_tts_cache_config(path: str) -> dict | None:
    """Đọc manifest cache TTS, lỗi hoặc JSON sai thì trả về ``None``."""
    try:
        with open(path, encoding="utf-8") as f:
            value = json.load(f)
        return value if isinstance(value, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _build_timing_guide(report: dict, segments: list[dict], tts_results: list[dict]) -> dict:
    """Tạo timing guide JSON cho audio tiếng Việt."""
    guide = {
        "session_id": report["session_id"],
        "source_url": report["source_url"],
        "target_language": "vi-VN",
        "summary": {
            "total_segments": report["total_segments"],
            "original_duration": report["total_original_duration"],
            "vi_duration": report["total_tts_duration"],
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
            "text_vi": seg.get("text_vi", ""),
            "start": seg["start"],
            "end": seg["end"],
            **{key: tts[key] for key in ("audio_start", "audio_end", "subtitle_start", "subtitle_end") if key in tts},
            "original_duration": seg["duration"],
            "vi_duration": tts["actual_duration"],
            "diff_seconds": diff,
            "speed_adjusted": tts["speed_adjusted"],
            "rate_applied": tts.get("rate_applied", ""),
            "status": status,
            "edit_hint": f"VI {'dài' if diff > 0 else 'ngắn'} hơn {abs(diff):.1f}s"
                         if status != "OK" else "OK",
        })

    guide["summary"]["segments_need_edit"] = need_edit
    guide["summary"]["segments_ok"] = report["total_segments"] - need_edit

    return guide


def _get_default_vi_output_dir() -> str:
    """Lấy thư mục output Việt từ cấu hình hoặc dùng ``OUTPUT_DIR/VN``."""
    if config.VIETNAMESE_OUTPUT_DIR:
        return config.VIETNAMESE_OUTPUT_DIR
    return os.path.join(config.OUTPUT_DIR, "VN")


def _ask_voice_gender() -> str:
    """Hỏi người dùng chọn giọng nam/nữ và trả về voice ID tương ứng."""
    print("\n" + "=" * 40)
    print("Chọn giọng đọc / Choose voice:")
    print("  1. Nam (Male)")
    print("  2. Nữ (Female)")
    print("=" * 40)

    while True:
        choice = input("Nhập 1 hoặc 2 (Enter 1 or 2): ").strip()
        if choice == "1":
            voice_id = config.VIETNAMESE_VOICEID_MALE
            logger.info(f"Selected: Male voice ({voice_id})")
            return voice_id
        elif choice == "2":
            voice_id = config.VIETNAMESE_VOICEID_FEMALE
            logger.info(f"Selected: Female voice ({voice_id})")
            return voice_id
        else:
            print("Vui lòng nhập 1 hoặc 2.")


def parse_args() -> argparse.Namespace:
    """Đọc tùy chọn CLI, nguồn video và voice ID cho pipeline tiếng Việt."""
    parser = argparse.ArgumentParser(description="Vietnamese Video Dubbing Pipeline: EN/JA → VI")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--url", help="YouTube/TikTok video URL (default: VIDEO_URL from .env)")
    group.add_argument("--file", help="Local video file path")

    parser.add_argument(
        "--source-lang",
        default=config.DEFAULT_SOURCE_LANG,
        help=f"Source language: en, ja, zh, en-US, ja-JP, zh-CN, zh-HK, zh-TW (default: {config.DEFAULT_SOURCE_LANG})",
    )
    parser.add_argument(
        "--voice",
        default=None,
        help="male/female, an Azure reference, or a saved SQLite voice reference",
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
        "--ocr-replace",
        action="store_true",
        help="Use OCR to detect original Chinese subtitle zone and replace with Vietnamese text",
    )
    parser.add_argument(
        "--output-dir",
        default=_get_default_vi_output_dir(),
        help="Output directory (default: VIETNAMESE_OUTPUT_DIR or OUTPUT_DIR/VN)",
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
        "--resume",
        metavar="WORK_DIR",
        help="Resume an existing work directory. Steps whose outputs already exist are skipped.",
    )

    parser.add_argument(
        "--ai-blur",
        action="store_true",
        help="Use EasyOCR to automatically detect and blur Chinese subtitles.",
    )
    parser.add_argument(
        "--bg-mode",
        choices=["demucs", "duck", "none"],
        default="demucs",
        help="How to handle the original audio under the VI narration: "
             "'demucs' (default) runs vocal separation so only music/SFX remain — "
             "highest quality, ~7 min CPU per video. "
             "'duck' lowers the entire original_audio.wav by --bg-duck-db (default -12) "
             "and overlays VI on top — fast (no Demucs), original speech audible faintly. "
             "'none' merges VI on a silent base — legacy behavior, no original audio.",
    )
    parser.add_argument(
        "--bg-duck-db",
        type=float,
        default=-12.0,
        help="Gain (dB) applied to original audio in 'duck' mode. -12 dB ≈ 25%% volume "
             "(default), -6 dB ≈ 50%%, -20 dB ≈ 10%%. Ignored unless --bg-mode=duck.",
    )
    parser.add_argument(
        "--no-bg-music",
        action="store_true",
        help="Deprecated alias for --bg-mode=none. Kept for backwards compatibility.",
    )

    args = parser.parse_args()
    if args.no_bg_music:
        args.bg_mode = "none"

    if not args.url and not args.file and not args.resume:
        if config.VIETNAMESE_VIDEO_URL:
            args.url = config.VIETNAMESE_VIDEO_URL
            logger.info(f"Using VIETNAMESE_VIDEO_URL from .env: {args.url}")
        elif config.VIDEO_URL:
            args.url = config.VIDEO_URL
            logger.info(f"Using VIDEO_URL from .env: {args.url}")
        else:
            parser.error("No video specified. Use --url, --file, --resume, or set VIETNAMESE_VIDEO_URL in .env")

    reference = args.voice or config.DEFAULT_VOICE_VI or config.VOICE_TYPE
    if reference:
        args.voice_id = pipeline_voice("vi", reference, vars(config))
    else:
        args.voice_id = _ask_voice_gender()

    return args


def _resolve_video(work_dir: str, url: str | None, file_path: str | None) -> str:
    """Tìm video nguồn, ưu tiên file chỉ định hoặc artifact đã có khi resume.

    Các file có tên ``dubbed_video*.mp4`` bị bỏ qua vì đó là kết quả render.
    ``--file`` được ưu tiên để hỗ trợ nguồn nằm ngoài thư mục dự án.
    """
    if file_path:
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Video file not found: {file_path}")
        return file_path

    video_exts = (".mp4", ".mkv", ".webm", ".mov", ".avi")
    output_prefixes = ("dubbed_video",)
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


def _subtitle_y_pixel(video_path: str, value: float) -> float:
    """Đổi ``--subtitle-y`` dạng phần trăm sang pixel, giữ tương thích GUI.

    CLI hiển thị tùy chọn này là phần trăm (mặc định 72), còn giao diện cũ đã
    truyền pixel như 780. Giá trị <=100 được hiểu là phần trăm; giá trị lớn
    hơn 100 được giữ nguyên như pixel.
    """
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = 72.0
    if 0 < numeric <= 100:
        try:
            import cv2

            cap = cv2.VideoCapture(video_path)
            frame_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            cap.release()
            if frame_h > 0:
                return frame_h * numeric / 100.0
        except Exception:
            pass
    return numeric


def _detect_source_lang(text: str, default_lang: str) -> str:
    """Đoán tiếng Trung/Nhật/Hàn bằng Unicode, nếu không dùng mặc định."""
    if any('\u4e00' <= c <= '\u9fff' for c in text):
        return "zh"
    if any('\u3040' <= c <= '\u30ff' for c in text):
        return "ja"
    if any('\uac00' <= c <= '\ud7a3' for c in text):
        return "ko"
    return default_lang


@track_pipeline("vi")
def run_pipeline_vi(
    url: str | None,
    file_path: str | None,
    source_lang: str,
    voice_id: str,
    skip_video: bool,
    output_dir: str,
    resume_dir: str | None = None,
    bg_mode: str = "demucs",
    bg_duck_db: float = -12.0,
    burn_subtitles: bool = False,
    ocr_replace: bool = False,
    subtitle_y: float = 72.0,
    subtitle_size: int | None = None,
    ai_blur: bool = False,
    subtitle_font: str | None = None,
    subtitle_style: dict | SubtitleStyle | None = None,
) -> dict:
    """Chạy pipeline tải, ASR, dịch thủ công, TTS và render sang tiếng Việt."""
    start_time = time.time()
    if voice_id.startswith("voice:"):
        voice_id = pipeline_voice("vi", voice_id, vars(config))

    lang_code = LANG_MAP.get(source_lang, source_lang)
    logger.info(f"Source language: {lang_code} → Vietnamese")

    # Resume dùng lại thư mục cũ; lần chạy mới tạo thư mục timestamp hậu tố _vi.
    if resume_dir:
        if not os.path.isdir(resume_dir):
            raise FileNotFoundError(f"Resume directory not found: {resume_dir}")
        work_dir = os.path.abspath(resume_dir)
        folder_name = os.path.basename(os.path.normpath(work_dir))
        logger.info(f"Resuming work directory: {work_dir}")
    else:
        folder_name = datetime.now().strftime("%Y%m%d%H%M%S") + "_vi"
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

    bind_project(work_dir, "vi")

    transcript_orig_path = os.path.join(work_dir, "transcript_original.json")
    transcript_vi_path = os.path.join(work_dir, "transcript_vi.json")
    audio_path = os.path.join(work_dir, "original_audio.wav")

    # ── Bước 1: tải video hoặc dùng file cục bộ ─────────────────────────────
    logger.info("=" * 60)
    logger.info("STEP 1: Acquiring video")
    video_path = _resolve_video(work_dir, url, file_path)
    logger.info(f"Video: {video_path}")


    # ── Bước 2: trích audio đầu vào ─────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("STEP 2: Extracting audio")
    if not (os.path.exists(audio_path) and os.path.getsize(audio_path) > 0):
        extract_audio(video_path, audio_path)
    else:
        logger.info(f"Reusing existing audio: {audio_path}")

    # ── Bước 2.5: chọn background theo demucs/duck/none ────────────────────
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

    # ── Bước 3: nhận dạng lời nói và cache transcript ──────────────────────
    logger.info("=" * 60)
    logger.info("STEP 3: Transcribing audio (ASR)")
    if os.path.exists(transcript_orig_path):
        logger.info(f"Reusing existing transcript: {transcript_orig_path}")
        with open(transcript_orig_path, encoding="utf-8") as f:
            segments = json.load(f)
        logger.info(f"Loaded {len(segments)} segments from cache")
    else:
        if not (os.path.exists(audio_path) and os.path.getsize(audio_path) > 0):
            extract_audio(video_path, audio_path)
        segments = transcribe(audio_path, lang_code)
        save_transcript(segments, transcript_orig_path)
        generate_srt(segments, os.path.join(work_dir, "transcript_original.srt"), text_field="text")
        logger.info(f"Transcribed {len(segments)} segments")

    # ── Bước 4: điểm dừng dịch tiếng Việt thủ công ─────────────────────────
    logger.info("=" * 60)
    logger.info("STEP 4: Loading manual Vietnamese translation")
    is_valid_translation = False
    if os.path.exists(transcript_vi_path):
        try:
            with open(transcript_vi_path, encoding="utf-8") as f:
                cached_segments = json.load(f)
            if has_complete_translation(cached_segments, "text_vi", segments):
                segments = cached_segments
                is_valid_translation = True
            else:
                logger.warning(
                    "transcript_vi.json exists but is incomplete; manual translation is still required."
                )
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(f"Cannot load manual translation file: {exc}")

    if is_valid_translation:
        logger.info(f"Reusing existing translation: {transcript_vi_path}")
        pending_hint = os.path.join(work_dir, "TRANSLATE_PENDING.txt")
        if os.path.exists(pending_hint):
            os.remove(pending_hint)
    else:
        hint_path = _write_translate_pending_hint(work_dir, "vi-VN", source_lang)
        logger.warning(
            "Manual translation required — no translation API was called. "
            f"Edit transcript_vi.json from the desktop app or follow {hint_path}, then resume."
        )
        return {
            "status": "translate_pending",
            "session_id": folder_name,
            "work_dir": work_dir,
            "translation_file": transcript_vi_path,
            "translation_hint": hint_path,
        }

    # ── Bước 4B: xoá phụ đề cứng sau dịch, trước khi TTS ───────────────────
    logger.info("=" * 60)
    # OCR replace là chế độ chính: OCR tìm box chữ Trung, OpenCV xử lý ảnh
    # cục bộ đúng vùng đó rồi bước 7 đặt phụ đề Việt lên đúng vị trí. Chạy
    # trước TTS để log lỗi rõ ràng và để artifact có thể dùng lại khi resume.
    if ocr_replace:
        logger.info("STEP 4B: Removing Chinese subtitles with OCR + local image cleanup")
        cleaned_video_path = os.path.join(work_dir, "video_ocr_cleaned.mp4")
        if (
            os.path.exists(cleaned_video_path)
            and os.path.getsize(cleaned_video_path) > 0
            and has_current_quality_marker(cleaned_video_path, OCR_IMAGE_REPLACE_MODE)
        ):
            logger.info(f"Reusing OCR-cleaned video: {cleaned_video_path}")
            video_path = cleaned_video_path
        else:
            if os.path.exists(cleaned_video_path):
                logger.info("Existing OCR-cleaned video is from an older algorithm; regenerating it.")
            success = apply_ocr_image_replace(video_path, cleaned_video_path)
            if not success:
                raise RuntimeError(
                    "OCR không tìm thấy chữ Trung trong vùng 20%-40% tính từ đáy "
                    "hoặc không thể phục hồi ảnh. Không tạo video để tránh giữ lại phụ đề gốc."
                )
            video_path = cleaned_video_path
            logger.info("OCR image subtitle replacement completed successfully.")
    elif ai_blur:
        logger.info("STEP 4B: Applying AI Subtitle Blur")
        blurred_video_path = os.path.join(work_dir, "video_ai_blurred.mp4")
        if (
            os.path.exists(blurred_video_path)
            and os.path.getsize(blurred_video_path) > 0
            and has_current_quality_marker(blurred_video_path, AI_BLUR_MODE)
        ):
            logger.info(f"Reusing existing AI blurred video: {blurred_video_path}")
            video_path = blurred_video_path
        else:
            if os.path.exists(blurred_video_path):
                logger.info("Existing AI-blurred video was created with an older quality profile; regenerating it.")
            success = apply_ai_blur(video_path, blurred_video_path, ocr_every_n_frames=2)
            if success:
                video_path = blurred_video_path
                logger.info("AI subtitle blur completed successfully.")
            else:
                logger.error("AI blur failed, continuing with original video.")

    # ── Bước 5: TTS từng segment, có cache và Azure fallback ───────────────
    logger.info("=" * 60)
    logger.info("STEP 5: Synthesizing Vietnamese audio (LucyLab TTS)")
    seg_dir = ensure_dir(os.path.join(work_dir, "segments"))
    tts_config_path = os.path.join(work_dir, "tts_config.json")
    current_tts_config = _build_tts_cache_config(voice_id, segments)
    previous_tts_config = _load_tts_cache_config(tts_config_path)
    tts_cache_matches = previous_tts_config == current_tts_config
    if tts_cache_matches:
        logger.info(
            "TTS cache matches current provider/API/voice settings; reusable audio will be kept."
        )
    else:
        logger.info(
            "TTS provider/API/voice settings changed (or no cache manifest exists); "
            "regenerating every spoken segment."
        )
    logger.info(
        f"Using TTS provider={'LucyLab' if current_tts_config['provider'] == 'lucylab' else 'Azure fallback'}, "
        f"voice_id={voice_id}"
    )
    tts_results = []
    from pydub import AudioSegment as _ASeg

    for seg in segments:
        seg_path = os.path.join(seg_dir, f"seg_{seg['id']:03d}.wav")
        text_to_speak = seg.get("text_vi", seg.get("text", "")).strip()
        


        has_spoken_text = bool(re.search(r"\w", text_to_speak))
        cached_is_usable = is_usable_tts_audio(seg_path)
        if tts_cache_matches and os.path.exists(seg_path) and os.path.getsize(seg_path) > 0 and (cached_is_usable or not has_spoken_text):
            cached = _ASeg.from_wav(seg_path)
            result = {
                "path": seg_path,
                "actual_duration": round(len(cached) / 1000.0, 3),
                "speed_adjusted": False,
                "rate_applied": "cached",
            }
            logger.info(
                f"  Segment {seg['id']}: cached ({result['actual_duration']:.1f}s, "
                f"target {seg['duration']:.1f}s)"
            )
        else:
            if tts_cache_matches and os.path.exists(seg_path):
                logger.warning(
                    f"  Segment {seg['id']}: cached audio is silent or invalid; regenerating TTS."
                )
            elif os.path.exists(seg_path):
                logger.info(
                    f"  Segment {seg['id']}: regenerating because TTS settings changed."
                )
            result = synthesize_segment_vi(
                text_vi=text_to_speak,
                output_path=seg_path,
                target_duration=seg["duration"],
                voice_id=voice_id,
            )
            logger.info(
                f"  Segment {seg['id']}: {result['actual_duration']:.1f}s "
                f"(target: {seg['duration']:.1f}s, speed: {result['rate_applied']})"
            )
        tts_results.append(result)

    # Chỉ ghi manifest sau khi mọi segment thành công; nếu giữa chừng lỗi thì
    # lần resume sau phải thử lại thay vì tin cache sinh dở.
    with open(tts_config_path, "w", encoding="utf-8") as f:
        json.dump(current_tts_config, f, ensure_ascii=False, indent=2)
    logger.info(f"TTS cache manifest saved: {tts_config_path}")

    # ── Bước 6: tăng tốc có giới hạn, rồi ghép đúng timestamp gốc ────────
    logger.info("=" * 60)
    total_duration = max(seg["end"] for seg in segments) + 1.0 if segments else 0
    max_extra = max(1.0, config.TTS_OVERFLOW_MAX_SPEED / config.TTS_SPEED)
    logger.info("STEP 6: base speed %.2fx; catch-up ceiling %.2fx; remaining overflow may overlap",
                config.TTS_SPEED, max(config.TTS_SPEED, config.TTS_OVERFLOW_MAX_SPEED))
    fit_dir = ensure_dir(os.path.join(work_dir, "segments_fit"))
    fit_adjustments = fit_segments_to_timeline(segments, seg_dir, fit_dir, max_speedup=max_extra)
    for adjustment in fit_adjustments:
        adjustment["effective_speed"] = round(config.TTS_SPEED * adjustment["speed"], 3)

    fit_log_path = os.path.join(work_dir, "fit_adjustments.json")
    with open(fit_log_path, "w", encoding="utf-8") as f:
        json.dump(fit_adjustments, f, ensure_ascii=False, indent=2)

    subtitle_segments = generate_tts_subtitles(
        segments, fit_dir, os.path.join(work_dir, "transcript_vi.srt"), "text_vi")
    apply_fitted_timing_to_results(tts_results, subtitle_segments, fit_adjustments)
    logger.info("Subtitle timing follows fitted Vietnamese speech; silent WAV padding is excluded.")

    logger.info("STEP 6c: Merging audio segments")
    merged_audio_path = os.path.join(work_dir, "audio_vi_full.wav")
    merge_segments(
        segments, fit_dir, merged_audio_path, total_duration,
        background_path=background_path,
        background_gain_db=0.5,
    )

    # ── Bước 7: ghép audio, video và tùy chọn phụ đề ───────────────────────
    if skip_video:
        logger.info("Skipping video merge (--skip-video)")
        dubbed_video_path = None
    else:
        logger.info("=" * 60)
        logger.info("STEP 7: Merging audio, video, and subtitle")
        
        # Nếu blur đã phân tích vùng chữ, ưu tiên tọa độ Y tự động cho subtitle.
        y_pos_file = os.path.join(work_dir, "subtitle_y_pos.txt")
        if os.path.exists(y_pos_file):
            try:
                with open(y_pos_file, "r") as f:
                    auto_y = float(f.read().strip())
                    subtitle_y = auto_y
                    logger.info(f"Using auto-detected subtitle Y position: {subtitle_y}")
            except Exception as e:
                logger.warning(f"Failed to read subtitle_y_pos.txt: {e}")

        dubbed_video_path = os.path.join(work_dir, "dubbed_video.mp4")
        srt_vi = os.path.join(work_dir, "transcript_vi.srt")
        subtitle_y_px = _subtitle_y_pixel(video_path, subtitle_y)
        if ocr_replace:
            # Ảnh đã được làm sạch ở bước 4B; ở đây chỉ burn SRT Việt, không
            # vẽ thêm hộp trắng che toàn khung.
            merge_video(
                video_path=video_path,
                audio_path=merged_audio_path,
                output_path=dubbed_video_path,
                srt_path=srt_vi,
                y_pixel=subtitle_y_px,
                font_size=subtitle_size,
                font_name=subtitle_font,
                subtitle_style=subtitle_style,
            )
        elif burn_subtitles:
            merge_video(
                video_path=video_path,
                audio_path=merged_audio_path,
                output_path=dubbed_video_path,
                srt_path=srt_vi,
                y_pixel=subtitle_y_px,
                font_size=subtitle_size,
                font_name=subtitle_font,
                subtitle_style=subtitle_style,
            )
        else:
            merge_video(
                video_path=video_path,
                audio_path=merged_audio_path,
                output_path=dubbed_video_path,
                y_pixel=subtitle_y_px,
                font_size=subtitle_size,
                font_name=subtitle_font,
                subtitle_style=subtitle_style,
            )

    # ── Bước 8: tạo prompt thumbnail và metadata nếu có Gemini key ─────────
    content_result = {"thumbnails": [], "metadata": {}}
    if config.GOOGLE_API_KEY:
        logger.info("=" * 60)
        logger.info("STEP 8: Generating thumbnails & YouTube metadata")
        try:
            content_result = generate_content(
                segments=segments,
                target_lang="vi-VN",
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

    # Ghi report tổng hợp để GUI/batch theo dõi tiến độ và artifact.
    elapsed = time.time() - start_time
    report = {
        "session_id": folder_name,
        "source_url": url,
        "source_language": lang_code,
        "target_language": "vi-VN",
        "voice_id": voice_id,
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
            "transcript_vi_json": os.path.join(work_dir, "transcript_vi.json"),
            "transcript_vi_srt": os.path.join(work_dir, "transcript_vi.srt"),
            "subtitle_timing": os.path.join(work_dir, "subtitle_timing.json"),
            "audio_vi_full": merged_audio_path,
            "dubbed_video": dubbed_video_path,
            "thumbnails": content_result.get("thumbnails", []),
            "youtube_metadata": content_result.get("metadata_file"),
        },
    }

    report_path = os.path.join(work_dir, "report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    # Ghi hướng dẫn chênh lệch thời lượng để chỉnh thủ công trong CapCut.
    timing_guide = _build_timing_guide(report, segments, tts_results)
    timing_path = os.path.join(work_dir, "timing_guide.json")
    with open(timing_path, "w", encoding="utf-8") as f:
        json.dump(timing_guide, f, ensure_ascii=False, indent=2)
    logger.info(f"Timing guide: {timing_path}")

    logger.info("=" * 60)
    logger.info("PIPELINE COMPLETE (Vietnamese)")
    logger.info(f"  Output:    {work_dir}")
    logger.info(f"  Segments:  {report['total_segments']}")
    logger.info(f"  Duration:  {report['total_original_duration']:.1f}s original, "
                f"{report['total_tts_duration']:.1f}s VI audio")
    logger.info(f"  Adjusted:  {report['segments_speed_adjusted']} segments speed-adjusted")
    logger.info(f"  Time:      {elapsed:.1f}s")
    logger.info("=" * 60)

    return report


def main():
    """Đọc tham số CLI và chạy pipeline lồng tiếng tiếng Việt."""
    args = parse_args()
    try:
        run_pipeline_vi(
            url=args.url,
            file_path=args.file,
            source_lang=args.source_lang,
            voice_id=args.voice_id,
            skip_video=args.skip_video,
            output_dir=args.output_dir,
            resume_dir=args.resume,
            bg_mode=args.bg_mode,
            bg_duck_db=args.bg_duck_db,
            burn_subtitles=args.burn_subtitles,
            ocr_replace=args.ocr_replace,
            subtitle_y=args.subtitle_y,
            subtitle_size=args.subtitle_size,
            subtitle_font=args.subtitle_font,
            subtitle_style=style_overrides_from_args(args),
            ai_blur=args.ai_blur,
        )
    except Exception as e:
        logger.error(f"Pipeline failed: {e}", exc_info=True)
        os._exit(1)
        
    os._exit(0)


if __name__ == "__main__":
    main()
