"""Đồng bộ và ghép các đoạn TTS vào một track audio hoàn chỉnh."""
import os
import shutil
import subprocess

from pydub import AudioSegment

from src.utils import setup_logging, ensure_dir

logger = setup_logging("audio_merger")


def fit_segments_to_timeline(
    segments: list[dict],
    src_dir: str,
    dst_dir: str,
    max_speedup: float = 1.4,
    tolerance_s: float = 0.1,
) -> list[dict]:
    """Tăng tốc câu dài trong giới hạn, còn dư cho chồng theo timestamp gốc.

    Vì bộ ghép đặt audio theo timestamp gốc, phần đuôi của segment quá dài có
    thể chồng lên câu kế tiếp. Chỉ những segment bị tràn mới được tăng tốc bằng
    filter ``atempo``; tốc độ bị giới hạn để giọng vẫn dễ nghe.

    Segment cuối dùng end-start làm khoảng trống; không dời câu hay đổi phụ đề.

    Tham số:
        segments: danh sách segment theo thứ tự, có id/start/end/duration.
        src_dir: thư mục chứa các file seg_NNN.wav.
        dst_dir: thư mục ghi các file sau khi đã khớp timeline.
        max_speedup: tỷ lệ atempo tối đa, mặc định 1.4 (+40%).
        tolerance_s: phần tràn nhỏ hơn ngưỡng này được bỏ qua.

    Trả về danh sách điều chỉnh với trạng thái ``OK``, ``FIT``,
    ``STILL_OVERFLOWS`` hoặc ``FFMPEG_ERROR``.
    """
    from src.video_merger import FFMPEG_BIN
    from src.tts_speed import tempo_filter
    ensure_dir(dst_dir)
    adjustments: list[dict] = []

    for i, seg in enumerate(segments):
        src = os.path.join(src_dir, f"seg_{seg['id']:03d}.wav")
        dst = os.path.join(dst_dir, f"seg_{seg['id']:03d}.wav")

        if not os.path.exists(src):
            logger.warning(f"Segment file missing: {src}")
            continue

        actual_s = len(AudioSegment.from_wav(src)) / 1000.0

        available_s = max(0.0, float(seg["end"]) - float(seg["start"]))
        if i + 1 < len(segments):
            available_s = min(available_s, max(0.0, segments[i + 1]["start"] - seg["start"]))

        if actual_s <= available_s + tolerance_s or available_s <= 0 or max_speedup <= 1.0:
            shutil.copyfile(src, dst)
            adjustments.append({
                "id": seg["id"],
                "available": round(available_s, 2) if available_s != float("inf") else None,
                "before": round(actual_s, 2),
                "after": round(actual_s, 2),
                "speed": 1.0,
                "status": "OK" if actual_s <= available_s + tolerance_s else "STILL_OVERFLOWS",
            })
            continue

        # Tỷ lệ cần thiết để audio mới vừa trong khoảng trống, sau đó áp dụng
        # giới hạn tốc độ tối đa để tránh giọng đọc bị méo hoặc quá gấp.
        target_ratio = actual_s / available_s
        speed = min(target_ratio, max_speedup)

        result = subprocess.run(
            [FFMPEG_BIN, "-y", "-i", src, "-filter:a", tempo_filter(speed), dst],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            logger.error(
                f"atempo failed on seg {seg['id']} (speed={speed:.2f}): "
                f"{result.stderr[:200]}"
            )
            shutil.copyfile(src, dst)
            adjustments.append({
                "id": seg["id"],
                "available": round(available_s, 2),
                "before": round(actual_s, 2),
                "after": round(actual_s, 2),
                "speed": 1.0,
                "status": "FFMPEG_ERROR",
            })
            continue

        new_dur_s = len(AudioSegment.from_wav(dst)) / 1000.0
        status = "FIT" if new_dur_s <= available_s + tolerance_s else "STILL_OVERFLOWS"

        msg = (
            f"Seg {seg['id']}: {actual_s:.2f}s > {available_s:.2f}s gap, "
            f"sped {speed:.2f}x -> {new_dur_s:.2f}s [{status}]"
        )
        if status == "STILL_OVERFLOWS":
            logger.warning(msg)
        else:
            logger.info(msg)

        adjustments.append({
            "id": seg["id"],
            "available": round(available_s, 2),
            "before": round(actual_s, 2),
            "after": round(new_dur_s, 2),
            "speed": round(speed, 3),
            "status": status,
        })

    fit_count = sum(1 for a in adjustments if a["status"] == "FIT")
    overflow_count = sum(1 for a in adjustments if a["status"] == "STILL_OVERFLOWS")
    logger.info(
        f"Timeline fit: {len(adjustments)} segments | "
        f"{fit_count} compressed to fit | "
        f"{overflow_count} still overflow (above {max_speedup}x cap)"
    )
    return adjustments


def merge_segments(
    segments: list[dict],
    segment_dir: str,
    output_path: str,
    total_duration: float,
    background_path: str | None = None,
    background_gain_db: float = 0.0,
) -> str:
    """Trộn audio TTS từng segment lên nhạc nền hoặc nền im lặng.

    ``background_path`` có thể là nhạc đã tách bằng Demucs hoặc audio gốc.
    Với audio gốc, dùng ``background_gain_db`` âm để tiếng nói cũ nằm nhỏ phía
    dưới lời thoại mới.
    """
    total_ms = int(total_duration * 1000)
    if background_path:
        merged = _load_background(background_path, total_ms, background_gain_db)
    else:
        merged = AudioSegment.silent(duration=total_ms)

    # Overlay theo position thay vì nối tiếp, nhờ đó khoảng lặng giữa các câu
    # vẫn giữ nguyên vị trí của transcript gốc.
    for seg in segments:
        seg_file = os.path.join(segment_dir, f"seg_{seg['id']:03d}.wav")
        if not os.path.exists(seg_file):
            logger.warning(f"Segment file not found: {seg_file}, skipping")
            continue

        segment_audio = AudioSegment.from_wav(seg_file)
        start_ms = int(seg["start"] * 1000)
        # Overlay permits simultaneous voices, but pydub would otherwise cut
        # any tail outside the base track. Keep the complete last sentence.
        required_ms = start_ms + len(segment_audio)
        if required_ms > len(merged):
            merged += AudioSegment.silent(duration=required_ms - len(merged),
                                          frame_rate=merged.frame_rate)

        merged = merged.overlay(segment_audio, position=start_ms)
        logger.debug(f"Placed segment {seg['id']} at {seg['start']:.1f}s")

    merged.export(output_path, format="wav")
    if background_path:
        gain_note = f" (gain {background_gain_db:+.1f} dB)" if background_gain_db else ""
        bg_label = f"with BGM{gain_note}"
    else:
        bg_label = "silent base"
    logger.info(
        f"Audio merged ({bg_label}): {output_path} "
        f"({len(segments)} segments, {total_duration:.1f}s)"
    )
    return output_path


def _load_background(
    background_path: str, total_ms: int, gain_db: float = 0.0
) -> AudioSegment:
    """Đọc nhạc nền, chỉnh gain và đệm/cắt về đúng ``total_ms``.

    Nếu file lỗi hoặc không tồn tại, trả về nền im lặng để lỗi tách giọng không
    làm dừng toàn bộ quá trình ghép video.
    """
    if not os.path.exists(background_path):
        logger.warning(f"Background not found: {background_path}; using silent base")
        return AudioSegment.silent(duration=total_ms)

    try:
        bg = AudioSegment.from_wav(background_path)
    except Exception as exc:
        logger.warning(f"Failed to load background {background_path}: {exc}; using silent base")
        return AudioSegment.silent(duration=total_ms)

    if gain_db != 0.0:
        bg = bg.apply_gain(gain_db)

    if len(bg) < total_ms:
        bg = bg + AudioSegment.silent(duration=total_ms - len(bg))
    elif len(bg) > total_ms:
        bg = bg[:total_ms]
    return bg
