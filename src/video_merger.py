"""Ghép audio lồng tiếng vào video và tùy chọn burn phụ đề bằng FFmpeg."""

import os
import re
import shutil
import subprocess
from src.utils import setup_logging
from src.video_quality import build_lossless_video_args
from src.subtitle_fonts import FONT_DIR, get_font, validate_size, needs_cjk_fallback
from src.subtitle_style import SubtitleStyle
from src.subtitle_chunks import split_caption_entries

logger = setup_logging("video_merger")

def _find_ffmpeg_bin() -> str:
    """Tìm FFmpeg có đủ filter phụ đề, ưu tiên bản ``ffmpeg-full``.

    Homebrew cài ``ffmpeg-full`` ở dạng keg-only nên binary thường không nằm
    trực tiếp trong ``PATH``. Dùng ``brew --prefix`` để tìm động, tránh phụ
    thuộc vào đường dẫn người dùng hoặc một máy cụ thể.
    """
    configured = os.environ.get("FFMPEG_BIN")
    if configured:
        return configured

    # Cho phép dùng binary ``ffmpeg-full`` do người dùng đặt trong PATH.
    found = shutil.which("ffmpeg-full")
    if found:
        return found

    # Với Homebrew, ffmpeg-full là keg-only nên cần lấy prefix của formula.
    brew = shutil.which("brew")
    if brew:
        try:
            result = subprocess.run(
                [brew, "--prefix", "ffmpeg-full"],
                capture_output=True,
                text=True,
                check=True,
            )
            candidate = os.path.join(result.stdout.strip(), "bin", "ffmpeg")
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
        except (OSError, subprocess.SubprocessError):
            pass

    # Chỉ dùng bản FFmpeg thường sau khi đã thử mọi vị trí của ffmpeg-full.
    found = shutil.which("ffmpeg")
    if found:
        return found

    # Để thông báo lỗi bên dưới vẫn được sinh ra rõ ràng nếu máy chưa cài FFmpeg.
    return "ffmpeg"


FFMPEG_BIN = _find_ffmpeg_bin()

# ── Cấu hình kiểu phụ đề ───────────────────────────────────────────────────────
WORDS_PER_CHUNK = 12      # số từ mục tiêu trong một chunk trên màn hình
FONT_SIZE       = 56      # px — đủ lớn cho video dọc trên điện thoại
# Màu libass có dạng &HAABBGGRR (alpha, blue, green, red).
ASS_TEXT_COLOR  = "&H00FFFFFF"   # trắng, alpha 00 là không trong suốt
ASS_BACK_COLOR  = "&H00000000"   # nền/viền đen
# drawtext: màu chữ và màu hộp nền.
DT_FONT_COLOR   = "white"
DT_ZONE_FONT_COLOR = "black"  # chữ đen trên hộp che màu trắng
DT_BOX_COLOR    = "black"
DT_BOX_BORDER   = 0
# Vị trí Y mặc định: 28% phía dưới màn hình (Y = 72% tính từ đỉnh).
Y_EXPR          = "(h*0.72)"
# ──────────────────────────────────────────────────────────────────────────────


def _has_filter(name: str) -> bool:
    """Kiểm tra bản FFmpeg hiện tại có hỗ trợ filter được yêu cầu không."""
    try:
        out = subprocess.run([FFMPEG_BIN, "-filters"], capture_output=True, text=True)
        return name in out.stdout
    except Exception:
        return False


# ── Phân tích SRT ──────────────────────────────────────────────────────────────

def _parse_srt(srt_path: str) -> list[dict]:
    """Đọc SRT thành ``{start, end, text}``, trong đó thời gian tính bằng giây."""
    entries = []
    with open(srt_path, encoding="utf-8") as f:
        content = f.read()

    blocks  = re.split(r"\n\n+", content.strip())
    time_re = re.compile(
        r"(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2}),(\d{3})"
    )
    for block in blocks:
        lines = block.strip().splitlines()
        if len(lines) < 2:
            continue
        for i, line in enumerate(lines):
            m = time_re.match(line.strip())
            if m:
                h1,m1,s1,ms1, h2,m2,s2,ms2 = (int(x) for x in m.groups())
                start = h1*3600 + m1*60 + s1 + ms1/1000.0
                end   = h2*3600 + m2*60 + s2 + ms2/1000.0
                text  = " ".join(lines[i+1:]).strip()
                text  = re.sub(r"<[^>]+>", "", text)
                import unicodedata
                text  = unicodedata.normalize('NFC', text)
                if text:
                    entries.append({"start": start, "end": end, "text": text})
                break
    return entries


def _split_into_chunks(entries: list[dict], words_per_chunk: int = WORDS_PER_CHUNK) -> list[dict]:
    """Legacy entry point; short phrases retain each original speech interval."""
    return split_caption_entries(entries, max_words=words_per_chunk)


# ── Nhánh libass (filter phụ đề) ──────────────────────────────────────────────

def _write_chunked_srt(entries: list[dict], out_path: str) -> None:
    """Ghi SRT mới từ các chunk và thêm khoảng đệm quanh text."""
    def fmt(sec: float) -> str:
        h  = int(sec // 3600)
        m  = int((sec % 3600) // 60)
        s  = int(sec % 60)
        ms = int(round((sec - int(sec)) * 1000))
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    lines = []
    for idx, e in enumerate(entries, 1):
        lines.append(str(idx))
        lines.append(f"{fmt(e['start'])} --> {fmt(e['end'])}")
        # Khoảng trắng giúp hộp nền trắng rộng hơn, che được chữ cũ hoàn toàn.
        lines.append(f"   {e['text']}   ")
        lines.append("")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def _escape_subtitles_path(path: str) -> str:
    """Escape đường dẫn để đưa an toàn vào chuỗi filter của FFmpeg."""
    return path.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def _write_ass_file(entries: list[dict], out_path: str, frame_w: int, frame_h: int, y_pixel: float,
                    font_size: int = 56, font_name: str | None = None,
                    subtitle_style: SubtitleStyle | None = None) -> None:
    """Ghi các entry thành file ASS và căn tâm chữ theo tọa độ Y đã chọn.

    ASS dùng centisecond thay vì millisecond. Alignment 2 là căn giữa phía
    dưới, vì vậy ``MarginV`` được tính ngược từ tâm vùng phụ đề.
    """
    selected = get_font(font_name)
    font_size = validate_size(font_size)
    colors = subtitle_style or SubtitleStyle.from_settings()
    def ass_color(color, opacity=100):
        alpha = round(255 * (1 - opacity / 100))
        return f"&H{alpha:02X}{color[5:7]}{color[3:5]}{color[1:3]}"
    text_color = ass_color(colors.text_color)
    outline_color = ass_color(colors.outline_color)
    box_color = ass_color(colors.bg_color, colors.bg_opacity)
    if y_pixel >= frame_h or y_pixel <= 0:
        y_pixel = int(frame_h * 0.85)
    margin_v = int(frame_h - (y_pixel + font_size // 2))
    if margin_v < 4:
        margin_v = 4

    def fmt(sec: float) -> str:
        # Round total centiseconds before splitting to carry across seconds/minutes.
        whole, cs = divmod(round(sec * 100), 100)
        h, remaining = divmod(whole, 3600)
        m, s = divmod(remaining, 60)
        return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"

    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {frame_w}",
        f"PlayResY: {frame_h}",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        # Dùng viền đậm để chữ lớn vẫn đọc được trên nền sáng, nhiều chi tiết.
        f"Style: Default,{selected.family},{font_size},{text_color},&H000000FF,{outline_color},&HFF000000,{selected.ass_bold},0,0,0,100,100,0,0,1,4.5,0,2,10,10,{margin_v},1",
        f"Style: Japanese,{get_font('Noto Sans JP').family},{font_size},{text_color},&H000000FF,{outline_color},&HFF000000,-1,0,0,0,100,100,0,0,1,4.5,0,2,10,10,{margin_v},1",
        # Separate box and glyph layers allow independent background/outline colours.
        f"Style: DefaultBox,{selected.family},{font_size},&HFF000000,&HFF000000,{box_color},&HFF000000,{selected.ass_bold},0,0,0,100,100,0,0,3,10,0,2,10,10,{margin_v},1",
        f"Style: JapaneseBox,{get_font('Noto Sans JP').family},{font_size},&HFF000000,&HFF000000,{box_color},&HFF000000,-1,0,0,0,100,100,0,0,3,10,0,2,10,10,{margin_v},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"
    ]

    for e in entries:
        text = e["text"].strip()
        if not text:
            continue
        start_str = fmt(e["start"])
        end_str = fmt(e["end"])
        style = "Japanese" if needs_cjk_fallback(text) else "Default"
        if colors.bg_enabled and colors.bg_opacity:
            lines.append(f"Dialogue: 0,{start_str},{end_str},{style}Box,,0,0,0,,{text}")
        lines.append(f"Dialogue: 1,{start_str},{end_str},{style},,0,0,0,,{text}")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# ── Nhánh drawtext (freetype) ──────────────────────────────────────────────────

def _escape_drawtext(text: str) -> str:
    """Escape các ký tự đặc biệt của filter drawtext."""
    text = text.replace("\\", "\\\\")
    text = text.replace("'",  "\u2019")   # curly apostrophe (safe)
    text = text.replace(":",  "\\:")
    text = text.replace("%",  "\\%")
    return text


def _find_bold_font(font_name: str | None = None) -> str:
    """Resolve the exact bundled font instead of guessing a system face."""
    return str(get_font(font_name).path)


def _build_drawtext_filter(chunks: list[dict], frame_h: int = 1080, y_pixel: float = 780.0,
                           font_size: int = 56, font_name: str | None = None,
                           subtitle_style: SubtitleStyle | None = None, frame_w: int = 1920) -> str:
    """Tạo chuỗi drawtext cho các chunk với chữ trắng và viền đen."""
    if y_pixel >= frame_h or y_pixel <= 0:
        y_pixel = int(frame_h * 0.85)
    # y_pixel là tâm dọc; drawtext neo ở đỉnh chữ nên dịch lên nửa chiều cao font.
    y_top = max(0, int(y_pixel) - font_size // 2)
    font_size = validate_size(font_size)
    selected = get_font(font_name)
    colors = subtitle_style or SubtitleStyle.from_settings()
    filters   = []

    for chunk in chunks:
        font_path = str(get_font("Noto Sans JP").path if needs_cjk_fallback(chunk['text']) else selected.path)
        # Wrap the whole sentence spatially, without inventing word timestamps.
        from PIL import ImageFont
        font = ImageFont.truetype(font_path, font_size)
        lines, line = [], ""
        max_width = max(font_size, frame_w * .9)
        for word in chunk['text'].split():
            candidate = f"{line} {word}".strip()
            if font.getlength(candidate) <= max_width:
                line = candidate
                continue
            if line:
                lines.append(line)
                line = ""
            for char in word:
                if line and font.getlength(line + char) > max_width:
                    lines.append(line)
                    line = ""
                line += char
        if line:
            lines.append(line)
        text = _escape_drawtext("\n".join(lines))
        start = chunk["start"]
        end   = chunk["end"]
        # Đặt đỉnh chữ lên trên tâm một nửa cỡ font để chữ nằm giữa tại y_pixel.
        y_expr = str(y_top)

        parts = [
            f"text='{text}'",
            f"fontcolor=0x{colors.text_color[1:]}",
            f"fontsize={font_size}",
            "borderw=4.5",
            f"bordercolor=0x{colors.outline_color[1:]}",
            f"box={int(colors.bg_enabled and colors.bg_opacity > 0)}",
            f"boxcolor=0x{colors.bg_color[1:]}@{colors.bg_opacity / 100:.2f}",
            "boxborderw=10",
            "x=(w-text_w)/2",          # căn giữa theo chiều ngang
            f"y={y_expr}",             # vùng 1/3 phía dưới màn hình
            f"enable='gte(t,{start:.3f})*lt(t,{end:.3f})'",
        ]
        if font_path:
            escaped_font_path = _escape_subtitles_path(font_path)
            parts.insert(0, f"fontfile='{escaped_font_path}'")
        filters.append("drawtext=" + ":".join(parts))

    return ",".join(filters)


def _build_display_chunks(srt_path: str) -> list[dict]:
    """Cap captions at 15 words, preserving already-timed short SRT phrases."""
    return split_caption_entries(_parse_srt(srt_path))


def _get_video_dimensions(video_path: str) -> tuple[int, int]:
    """Đọc chiều rộng/cao của video, đồng thời kiểm tra video có mở được không."""
    import cv2

    cap = cv2.VideoCapture(video_path)
    try:
        if not cap.isOpened():
            raise RuntimeError(f"Cannot open video: {video_path}")
        frame_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        frame_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    finally:
        cap.release()

    if frame_w <= 0 or frame_h <= 0:
        raise RuntimeError(f"Cannot read video dimensions: {video_path}")
    return frame_w, frame_h


# ── API công khai ─────────────────────────────────────────────────────────────

def merge_video(video_path: str, audio_path: str, output_path: str,
                srt_path: str | None = None, y_pixel: float = 780.0,
                font_size: int = 56, _video_filter: str | None = None,
                font_name: str | None = None, subtitle_style: SubtitleStyle | None = None) -> str:
    """Ghép audio vào video, stream-copy nếu không cần thay đổi hình ảnh."""
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video not found: {video_path}")
    if not os.path.exists(audio_path):
        raise FileNotFoundError(f"Audio not found: {audio_path}")

    logger.info(f"Using FFmpeg: {FFMPEG_BIN}")

    cmd = [FFMPEG_BIN, "-i", video_path, "-i", audio_path]

    video_reencoded = False

    if _video_filter:
        cmd += ["-vf", _video_filter, *build_lossless_video_args(video_path)]
        video_reencoded = True
    elif srt_path and os.path.exists(srt_path):
        logger.info(f"Burning subtitles from {srt_path} onto video at Y={y_pixel}px, size={font_size}px")

        chunks = _build_display_chunks(srt_path)

        # SRT rỗng/không hợp lệ không làm đổi pixel, nên vẫn giữ nguyên stream
        # video nén ban đầu.
        if not chunks:
            logger.info("No subtitle entries found; copying the original video stream")

        if chunks:
            # ── Phương án 1: filter phụ đề/libass (chất lượng tốt nhất) ───────
            if _has_filter("subtitles"):
                frame_w, frame_h = _get_video_dimensions(video_path)

                # Tạo file ASS tạm từ các chunk đã chia.
                chunked_ass = srt_path.replace(".srt", "_chunked.ass")
                _write_ass_file(chunks, chunked_ass, frame_w, frame_h, y_pixel, font_size, font_name, subtitle_style)
                escaped = _escape_subtitles_path(chunked_ass)
                escaped_fontsdir = _escape_subtitles_path(str(FONT_DIR))
                subtitle_vf = f"subtitles=filename='{escaped}':fontsdir='{escaped_fontsdir}'"
                cmd += ["-vf", subtitle_vf, *build_lossless_video_args(video_path)]
                video_reencoded = True
                logger.info("  Using subtitles filter (libass) with lossless video encode")

            # ── Phương án 2: drawtext (không có libass, cần freetype) ──────────
            elif _has_filter("drawtext"):
                frame_w, frame_h = _get_video_dimensions(video_path)

                subtitle_vf = _build_drawtext_filter(chunks, frame_h, y_pixel, font_size, font_name, subtitle_style, frame_w)
                cmd += ["-vf", subtitle_vf, *build_lossless_video_args(video_path)]
                video_reencoded = True
                logger.info(f"  Using drawtext filter ({len(chunks)} chunks) with lossless video encode")

            # ── Phương án 3: không có filter chữ ───────────────────────────────
            else:
                raise RuntimeError(
                    "FFmpeg không hỗ trợ filter 'subtitles' hoặc 'drawtext'. "
                    "Hãy cài bản FFmpeg có libass/freetype và bảo đảm ffmpeg nằm trong PATH."
                )

    if not video_reencoded:
        # Không có filter hình ảnh thì copy stream thật sự; bitstream và chất
        # lượng video gốc không bị encode lại.
        cmd += ["-c:v", "copy"]

    cmd += [
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-map_metadata", "0",
        "-map_chapters", "0",
        "-fps_mode", "passthrough",
        "-movflags", "+faststart",
        "-y", output_path,
    ]

    logger.info(f"Merging video + audio → {output_path}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg merge failed: {result.stderr}")

    logger.info(f"Video merged: {output_path}")
    return output_path


# ── Chế độ thay phụ đề bằng OCR ───────────────────────────────────────────────

def merge_video_with_ocr_replace(
    video_path: str,
    audio_path: str,
    output_path: str,
    srt_path: str,
    y_pixel: float = 780.0,
    subtitle_size: int = 56,
    subtitle_font: str | None = None,
    subtitle_style: SubtitleStyle | None = None,
) -> str:
    """Dùng OCR + xử lý ảnh cục bộ để xoá phụ đề gốc rồi burn SRT tiếng Việt.

    Bản cũ dùng ``drawbox=color=white`` nên vừa không xử lý đúng nền ảnh vừa
    khiến phụ đề gốc vẫn còn khi OCR không trả confidence đủ cao. Nhánh này
    dùng cùng bộ xử lý ảnh với pipeline chính và chỉ gọi merge để đặt chữ sau
    khi video đã được làm sạch.
    """
    if not os.path.exists(srt_path):
        raise FileNotFoundError(f"Subtitle file not found: {srt_path}")
    from src.ocr_image_replace import OCR_IMAGE_REPLACE_MODE, apply_ocr_image_replace
    from src.video_quality import has_current_quality_marker

    cleaned_path = f"{output_path}.ocr_cleaned.mp4"
    if (
        os.path.exists(cleaned_path)
        and os.path.getsize(cleaned_path) > 0
        and has_current_quality_marker(cleaned_path, OCR_IMAGE_REPLACE_MODE)
    ):
        logger.info(f"Reusing OCR-cleaned intermediate video: {cleaned_path}")
    else:
        if not apply_ocr_image_replace(video_path, cleaned_path):
            raise RuntimeError(
                "OCR không thể xoá chữ Trung trong vùng đã cấu hình; "
                "không tiếp tục render để tránh giữ phụ đề gốc."
            )

    # Bộ xử lý ảnh ghi lại tâm dòng phụ đề theo pixel. Đọc lại sau khi clean để
    # không dùng tọa độ cũ từ một lần chạy AI blur trước đó.
    render_y = y_pixel
    y_path = os.path.join(os.path.dirname(output_path), "subtitle_y_pos.txt")
    try:
        with open(y_path, encoding="utf-8") as handle:
            render_y = float(handle.read().strip())
    except (OSError, ValueError):
        pass

    return merge_video(
        video_path=cleaned_path,
        audio_path=audio_path,
        output_path=output_path,
        srt_path=srt_path,
        y_pixel=render_y,
        font_size=subtitle_size,
        font_name=subtitle_font,
        subtitle_style=subtitle_style,
    )
