import os
import re
import subprocess
from src.utils import setup_logging

logger = setup_logging("video_merger")

# Prefer ffmpeg-full (has libass / drawtext) if installed, fall back to system ffmpeg
_FFMPEG_FULL = "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg"
FFMPEG_BIN = _FFMPEG_FULL if os.path.exists(_FFMPEG_FULL) else "ffmpeg"

# ── Subtitle style config ──────────────────────────────────────────────────────
WORDS_PER_CHUNK = 12      # target words per on-screen chunk (8-15 range)
FONT_SIZE       = 42      # px (increased font size for fatter look)
# libass colour format: &HAABBGGRR  (AA=alpha 00=opaque, BB=blue, GG=green, RR=red)
ASS_TEXT_COLOR  = "&H00FFFFFF"   # opaque white
ASS_BACK_COLOR  = "&H00000000"   # transparent/black outline backing
# drawtext: fontcolor / boxcolor
DT_FONT_COLOR   = "white"
DT_BOX_COLOR    = "black"
DT_BOX_BORDER   = 0
# vertical position default: bottom 28% of screen (y = 72% from top)
Y_EXPR          = "(h*0.72)"
# ──────────────────────────────────────────────────────────────────────────────


def _has_filter(name: str) -> bool:
    """Check whether the active ffmpeg binary supports a given filter."""
    try:
        out = subprocess.run([FFMPEG_BIN, "-filters"], capture_output=True, text=True)
        return name in out.stdout
    except Exception:
        return False


# ── SRT parsing ───────────────────────────────────────────────────────────────

def _parse_srt(srt_path: str) -> list[dict]:
    """Parse SRT → list of {start, end, text} (times in seconds)."""
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
    """
    Split each entry into chunks:
    - Split at periods (.) so that sentences end a subtitle chunk.
    - If a sentence is longer than 15 words, split it into sub-chunks of 8-15 words (target 10 words).
    - Distribute time proportionally.
    """
    import re
    result = []
    for entry in entries:
        # Split by periods, keeping the periods attached to the sentence
        sentences = re.split(r'(?<=\.)\s*', entry["text"].strip())
        sentences = [s.strip() for s in sentences if s.strip()]

        sub_chunks = []
        for sentence in sentences:
            words = sentence.split()
            if len(words) <= 15:
                sub_chunks.append(sentence)
            else:
                # If longer than 15 words, split into smaller chunks of 10 words
                for i in range(0, len(words), 10):
                    chunk_words = words[i:i + 10]
                    sub_chunks.append(" ".join(chunk_words))

        if not sub_chunks:
            continue

        if len(sub_chunks) == 1:
            result.append({
                "start": entry["start"],
                "end": entry["end"],
                "text": sub_chunks[0]
            })
            continue

        # Distribute time proportionally based on character length
        total_chars = sum(len(c) for c in sub_chunks)
        if total_chars == 0:
            continue

        total_dur = entry["end"] - entry["start"]
        start = entry["start"]
        for chunk_text in sub_chunks:
            char_pct = len(chunk_text) / total_chars
            chunk_dur = total_dur * char_pct
            result.append({
                "start": round(start, 3),
                "end": round(start + chunk_dur, 3),
                "text": chunk_text
            })
            start += chunk_dur

    logger.info(f"  Subtitle chunks: {len(entries)} SRT entries → {len(result)} display chunks")
    return result


# ── libass (subtitles filter) path ────────────────────────────────────────────

def _write_chunked_srt(entries: list[dict], out_path: str) -> None:
    """Write a new SRT file from a list of {start,end,text} dicts with padded spaces to mask old subtitles."""
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
        # Pad with 3 spaces on each side to make the white background box wider (masks old text completely)
        lines.append(f"   {e['text']}   ")
        lines.append("")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def _escape_subtitles_path(path: str) -> str:
    return path.replace("\\", "/").replace(":", "\\:")


def _write_ass_file(entries: list[dict], out_path: str, frame_w: int, frame_h: int, y_pixel: float, font_size: int = 40) -> None:
    """Write subtitle entries into a v4.00+ ASS file.
    y_pixel = vertical centre of the subtitle zone (pixels from top of frame).
    Alignment 2 = bottom-centre; MarginV = distance from bottom of frame to bottom of text.
    To centre text at y_pixel: margin_v = frame_h - (y_pixel + font_size//2)
    """
    if y_pixel >= frame_h or y_pixel <= 0:
        y_pixel = int(frame_h * 0.85)
    margin_v = int(frame_h - (y_pixel + font_size // 2))
    if margin_v < 4:
        margin_v = 4

    def fmt(sec: float) -> str:
        h = int(sec // 3600)
        m = int((sec % 3600) // 60)
        s = int(sec % 60)
        ms = int(round((sec - int(sec)) * 100)) # ASS uses centiseconds
        if ms >= 100:
            ms = 99
        return f"{h:d}:{m:02d}:{s:02d}.{ms:02d}"

    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {frame_w}",
        f"PlayResY: {frame_h}",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        # Default style is outline-based (BorderStyle=1) with 3.5px outline and 1px shadow, using white text color and black outline/shadow
        f"Style: Default,Phudu,{font_size},{ASS_TEXT_COLOR},&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,3.5,1,2,10,10,{margin_v},1",
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
        lines.append(f"Dialogue: 0,{start_str},{end_str},Default,,0,0,0,,{text}")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# ── drawtext (freetype) path ──────────────────────────────────────────────────

def _escape_drawtext(text: str) -> str:
    text = text.replace("\\", "\\\\")
    text = text.replace("'",  "\u2019")   # curly apostrophe (safe)
    text = text.replace(":",  "\\:")
    text = text.replace("%",  "\\%")
    return text


def _find_bold_font() -> str | None:
    """Return the custom font path specified by the user, preferring Black and Bold weights."""
    paths = [
        "/Users/mac/Desktop/Auto-Translade-video/Phudu-Black.ttf",
        "/Users/mac/Desktop/Auto-Translade-video/Phudu-Bold.ttf",
        "/Users/mac/Desktop/Auto-Translade-video/Phudu-SemiBold.ttf",
    ]
    for p in paths:
        if os.path.exists(p):
            return p
    return None


def _build_drawtext_filter(chunks: list[dict], frame_h: int = 1080, y_pixel: float = 780.0, font_size: int = 40) -> str:
    """Build a chained drawtext filter for all subtitle chunks with white text and black borders."""
    if y_pixel >= frame_h or y_pixel <= 0:
        y_pixel = int(frame_h * 0.85)
    # y_pixel = vertical centre; drawtext y anchors at top of text, so shift up by half font height
    y_top = max(0, int(y_pixel) - font_size // 2)
    font_path = _find_bold_font()
    filters   = []

    for chunk in chunks:
        # No extra spaces needed since there's no background box
        text  = _escape_drawtext(chunk['text'])
        start = chunk["start"]
        end   = chunk["end"]
        y_expr = str(y_top)   # top of text = y_center - half_font → text centered at y_pixel

        parts = [
            f"text='{text}'",
            f"fontcolor=white",
            f"fontsize={font_size}",
            "borderw=3.5",
            "bordercolor=black",
            "x=(w-text_w)/2",          # centered horizontally
            f"y={y_expr}",             # bottom 1/3 of screen
            f"enable='between(t,{start:.3f},{end:.3f})'",
        ]
        if font_path:
            parts.insert(0, f"fontfile={font_path}")
        filters.append("drawtext=" + ":".join(parts))

    return ",".join(filters)


# ── Public API ────────────────────────────────────────────────────────────────

def merge_video(video_path: str, audio_path: str, output_path: str,
                srt_path: str | None = None, y_pixel: float = 780.0,
                font_size: int = 40) -> str:
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video not found: {video_path}")
    if not os.path.exists(audio_path):
        raise FileNotFoundError(f"Audio not found: {audio_path}")

    logger.info(f"Using FFmpeg: {FFMPEG_BIN}")

    cmd = [FFMPEG_BIN, "-i", video_path, "-i", audio_path]

    if srt_path and os.path.exists(srt_path):
        logger.info(f"Burning subtitles from {srt_path} onto video at Y={y_pixel}px, size={font_size}px")

        raw_entries = _parse_srt(srt_path)
        chunks      = _split_into_chunks(raw_entries)

        # Extend chunk end times to touch the next chunk's start time (capped at 2 seconds)
        for i in range(len(chunks) - 1):
            spoken_dur = chunks[i]["end"] - chunks[i]["start"]
            gap_dur = chunks[i + 1]["start"] - chunks[i]["start"]
            chunks[i]["end"] = chunks[i]["start"] + max(spoken_dur, min(gap_dur, 2.0))
        if chunks:
            spoken_dur = chunks[-1]["end"] - chunks[-1]["start"]
            # Keep the last chunk visible for at least 2.0 seconds
            chunks[-1]["end"] = chunks[-1]["start"] + max(spoken_dur, 2.0)

        # ── Option 1: libass subtitles filter (best quality) ──────────────────
        if _has_filter("subtitles"):
            import cv2
            cap = cv2.VideoCapture(video_path)
            frame_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            frame_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            cap.release()

            # Write a new ASS file with the split chunks
            chunked_ass = srt_path.replace(".srt", "_chunked.ass")
            _write_ass_file(chunks, chunked_ass, frame_w, frame_h, y_pixel, font_size)
            escaped = _escape_subtitles_path(chunked_ass)
            project_dir = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
            escaped_fontsdir = project_dir.replace("\\", "/").replace(":", "\\:")
            subtitle_vf = f"subtitles=filename='{escaped}':fontsdir='{escaped_fontsdir}'"
            vf = subtitle_vf
            cmd += [
                "-vf", vf,
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
            ]
            logger.info("  Using subtitles filter (libass) with chunked ASS")

        # ── Option 2: drawtext filter (no libass, needs freetype) ─────────────
        elif _has_filter("drawtext"):
            import cv2
            cap = cv2.VideoCapture(video_path)
            frame_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            cap.release()

            subtitle_vf = _build_drawtext_filter(chunks, frame_h, y_pixel, font_size)
            vf = subtitle_vf
            cmd += [
                "-vf", vf,
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "22"
            ]
            logger.info(f"  Using drawtext filter ({len(chunks)} chunks)")

        # ── Option 3: no text filter available ───────────────────────────────
        else:
            logger.warning("No drawtext/subtitles filter available - skipping all overlays")
            cmd += ["-c:v", "copy"]
    else:
        cmd += ["-c:v", "copy"]

    cmd += ["-map", "0:v", "-map", "1:a", "-y", output_path]

    logger.info(f"Merging video + audio → {output_path}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg merge failed: {result.stderr}")

    logger.info(f"Video merged: {output_path}")
    return output_path


# ── OCR-replace mode ──────────────────────────────────────────────────────────

def _build_ocr_replace_filter(chunks: list[dict], frame_w: int, frame_h: int) -> str:
    """
    FFmpeg vf chain:
      For each chunk:
        - If a precise subtitle zone is detected on this chunk:
          1. drawbox  → white rectangle covering the Y range of the subtitle,
                         active ONLY during the chunk duration.
          2. drawtext → Vietnamese text centered horizontally and vertically inside that zone.
        - If no subtitle is detected:
          1. drawtext → Vietnamese text with box=1 at fallback bottom position.
    """
    font_path    = _find_bold_font()
    filters      = []

    for chunk in chunks:
        text  = _escape_drawtext(chunk["text"])
        start = chunk["start"]
        end   = chunk["end"]
        enable_str = f"enable='between(t,{start:.3f},{end:.3f})'"

        zone = chunk.get("zone")
        if zone:
            y = zone["y"]
            h = zone["height"]

            # 1. Drawbox covering full width at this precise Y height
            cover_parts = [
                "x=0",
                f"y={y}",
                f"w={frame_w}",
                f"h={h}",
                "color=white",
                "t=fill",
                enable_str
            ]
            filters.append("drawbox=" + ":".join(cover_parts))

            # 2. Drawtext centered in the cover box
            text_parts = [
                f"text='{text}'",
                f"fontcolor={DT_FONT_COLOR}",
                f"fontsize={FONT_SIZE}",
                "x=(w-text_w)/2",
                f"y={y} + ({h}-text_h)/2",  # vertical centering in the box
                enable_str
            ]
            if font_path:
                text_parts.insert(0, f"fontfile={font_path}")
            filters.append("drawtext=" + ":".join(text_parts))
        else:
            # Fallback when no subtitle is detected: drawtext with a background box at bottom 1/3
            text_parts = [
                f"text='{text}'",
                f"fontcolor={DT_FONT_COLOR}",
                f"fontsize={FONT_SIZE}",
                "box=1",
                f"boxcolor={DT_BOX_COLOR}",
                f"boxborderw={DT_BOX_BORDER}",
                "x=(w-text_w)/2",
                f"y={Y_EXPR}",
                enable_str
            ]
            if font_path:
                text_parts.insert(0, f"fontfile={font_path}")
            filters.append("drawtext=" + ":".join(text_parts))

    return ",".join(filters)


def merge_video_with_ocr_replace(
    video_path: str,
    audio_path: str,
    output_path: str,
    srt_path: str,
    y_pixel: float = 780.0,
    subtitle_size: int = 40,
) -> str:
    """OCR replace mode has been deprecated and replaced by manual Y-pixel placement replacement."""
    logger.info(f"OCR Replace mode active: using manual Y-pixel replace at {y_pixel}px, size={subtitle_size}px")
    return merge_video(
        video_path=video_path,
        audio_path=audio_path,
        output_path=output_path,
        srt_path=srt_path,
        y_pixel=y_pixel,
        font_size=subtitle_size,
    )

