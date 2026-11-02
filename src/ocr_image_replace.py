"""OCR + xử lý ảnh cục bộ để thay phụ đề cứng.

EasyOCR chỉ quét vùng phụ đề ở 20%-40% tính từ đáy. Sau khi có bounding box
chứa ký tự Trung, OpenCV làm sạch đúng vùng ảnh đó bằng blur có feather mềm;
không phủ một dải trắng lên toàn khung và không ảnh hưởng tới phần còn lại của
video. Phụ đề tiếng Việt được burn ở bước merge video sau đó.
"""

from __future__ import annotations

import os
import re
import shutil
import statistics
import subprocess
import tempfile
import warnings
from typing import Any

import cv2
import numpy as np

from src.ocr_zone import (
    OCR_ZONE_LABEL,
    crop_ocr_scan_zone,
    get_ocr_zone_bounds,
)
from src.utils import setup_logging
from src.video_quality import build_lossless_video_args, write_quality_marker


logger = setup_logging("ocr_image_replace")

# Đổi version khi thay thuật toán để pipeline không dùng lại artifact cũ.
OCR_IMAGE_REPLACE_MODE = "ocr_image_replace_v4"
OCR_EVERY_N_FRAMES = 2
OCR_EDGE_PADDING_X = 10
OCR_EDGE_PADDING_Y = 8
# Blur cục bộ ổn định hơn inpaint cả hình chữ nhật trên nền có nhiều đường
# chéo/vật thể. Feather giúp mép vùng xử lý tan vào ảnh, không tạo dải ngang.
OCR_REPAIR_BLUR_SIGMA = 16.0
OCR_REPAIR_FEATHER_SIGMA = 16.0
MAX_MISSED_OCR_SCANS = 3


def _find_ffmpeg() -> str:
    configured = os.environ.get("FFMPEG_BIN")
    if configured:
        return configured
    return shutil.which("ffmpeg") or "ffmpeg"


def _load_reader() -> Any:
    """Khởi tạo EasyOCR, ưu tiên MPS/CUDA nếu PyTorch hỗ trợ."""
    import easyocr
    import torch

    warnings.filterwarnings("ignore", category=UserWarning, message=".*pin_memory.*")
    use_gpu = bool(torch.backends.mps.is_available() or torch.cuda.is_available())
    logger.info(f"Loading EasyOCR for image replacement (GPU={use_gpu})...")
    reader = easyocr.Reader(["ch_sim", "en"], gpu=use_gpu, verbose=False)
    logger.info("EasyOCR ready for image replacement.")
    return reader


def _enhance_crop(crop: np.ndarray) -> np.ndarray:
    """Tăng tương phản nhẹ nhưng vẫn giữ ảnh màu cho EasyOCR."""
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)
    return cv2.cvtColor(enhanced, cv2.COLOR_GRAY2BGR)


def _sanitize_ocr_regions(horizontal: list, free: list, width: int, height: int) -> tuple[list, list]:
    """Clip detector coordinates before EasyOCR slices/warps recognition crops."""
    safe_horizontal, safe_free = [], []
    for box in horizontal:
        try:
            coords = np.asarray(box, dtype=float)
            if coords.shape != (4,) or not np.isfinite(coords).all():
                continue
            x1, x2, y1, y2 = coords
            x1, x2 = max(0, int(np.floor(x1))), min(width, int(np.ceil(x2)))
            y1, y2 = max(0, int(np.floor(y1))), min(height, int(np.ceil(y2)))
            if x2 - x1 >= 2 and y2 - y1 >= 2:
                safe_horizontal.append([x1, x2, y1, y2])
        except (TypeError, ValueError, OverflowError):
            continue
    for box in free:
        try:
            points = np.asarray(box, dtype=np.float32)
            if points.shape != (4, 2) or not np.isfinite(points).all():
                continue
            points[:, 0] = np.clip(points[:, 0], 0, width - 1)
            points[:, 1] = np.clip(points[:, 1], 0, height - 1)
            edges = np.linalg.norm(points - np.roll(points, 1, axis=0), axis=1)
            if min(edges) >= 2 and abs(cv2.contourArea(points)) >= 4 and cv2.isContourConvex(points):
                safe_free.append(points.tolist())
        except (TypeError, ValueError, OverflowError):
            continue
    return safe_horizontal, safe_free


def _readtext(reader: Any, image: np.ndarray) -> list:
    """Split detection/recognition so malformed detector boxes cannot reach resize."""
    if image is None or image.size == 0 or min(image.shape[:2]) < 2:
        return []
    horizontal, free = reader.detect(
        image,
        min_size=8,
        text_threshold=0.25,
        low_text=0.20,
        link_threshold=0.20,
        canvas_size=1920,
        mag_ratio=1.5,
    )
    horizontal, free = horizontal[0], free[0]
    valid_horizontal, valid_free = _sanitize_ocr_regions(horizontal, free, image.shape[1], image.shape[0])
    skipped = len(horizontal) + len(free) - len(valid_horizontal) - len(valid_free)
    if skipped:
        logger.warning("Ignored %d empty or degenerate OCR region(s)", skipped)
    if not valid_horizontal and not valid_free:
        return []
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    return reader.recognize(gray, valid_horizontal, valid_free, detail=1, reformat=False)


def _merge_boxes(boxes: list[tuple[int, int, int, int]]) -> list[tuple[int, int, int, int]]:
    """Gộp box trùng do EasyOCR đọc ảnh gốc và ảnh tăng tương phản."""
    if not boxes:
        return []

    result: list[tuple[int, int, int, int]] = []
    for box in sorted(boxes, key=lambda item: (item[1], item[0])):
        x1, y1, x2, y2 = box
        merged = False
        for index, current in enumerate(result):
            cx1, cy1, cx2, cy2 = current
            overlap_x = min(x2, cx2) - max(x1, cx1)
            overlap_y = min(y2, cy2) - max(y1, cy1)
            close_y = abs(((y1 + y2) / 2) - ((cy1 + cy2) / 2)) <= max(12, (y2 - y1) * 0.45)
            if overlap_x > 0 and overlap_y > 0 or close_y and overlap_x > -20:
                result[index] = (min(x1, cx1), min(y1, cy1), max(x2, cx2), max(y2, cy2))
                merged = True
                break
        if not merged:
            result.append(box)
    return result


def detect_chinese_boxes(frame: np.ndarray, reader: Any) -> list[tuple[int, int, int, int]]:
    """Trả box chữ Trung theo tọa độ frame gốc.

    ``reader.readtext`` đôi khi trả confidence 0.0 cho chữ trắng có viền đen,
    nhưng phần text vẫn có ký tự Trung. Vì vậy ký tự Trung là điều kiện chính;
    không loại box chỉ vì confidence thấp.
    """
    if frame is None or frame.size == 0:
        return []

    fh, fw = frame.shape[:2]
    zone_top, zone_bottom = get_ocr_zone_bounds(fh)
    crop, y_offset = crop_ocr_scan_zone(frame)
    if crop.size == 0:
        return []

    crop_h, crop_w = crop.shape[:2]
    scale = max(1.0, 960.0 / max(1, crop_w))
    if scale > 1.0:
        ocr_image = cv2.resize(
            crop,
            (int(round(crop_w * scale)), int(round(crop_h * scale))),
            interpolation=cv2.INTER_CUBIC,
        )
    else:
        ocr_image = crop

    # Ảnh gốc bắt được chữ trong đa số trường hợp. Chỉ thử ảnh tăng tương
    # phản khi lượt đầu không có ký tự Trung để không tăng chi phí OCR thường
    # xuyên.
    attempts = [ocr_image]
    results = _readtext(reader, ocr_image)
    if not any(re.search(r"[\u4e00-\u9fff]", str(item[1])) for item in results if len(item) >= 2):
        attempts.append(_enhance_crop(ocr_image))
        results = results + _readtext(reader, attempts[-1])

    boxes: list[tuple[int, int, int, int]] = []
    for item in results:
        if len(item) < 2:
            continue
        bbox, text = item[0], str(item[1] or "")
        if not re.search(r"[\u4e00-\u9fff]", text):
            continue
        try:
            xs = [float(point[0]) for point in bbox]
            ys = [float(point[1]) for point in bbox]
        except (TypeError, ValueError, IndexError):
            continue
        if not xs or not ys:
            continue

        x1 = max(0, int(round(min(xs) / scale)) - OCR_EDGE_PADDING_X)
        y1 = y_offset + max(0, int(round(min(ys) / scale))) - OCR_EDGE_PADDING_Y
        x2 = min(fw, int(round(max(xs) / scale)) + OCR_EDGE_PADDING_X)
        y2 = y_offset + min(crop_h, int(round(max(ys) / scale))) + OCR_EDGE_PADDING_Y

        # Box phải giao với vùng chính. Có thể vươn xuống dải đệm để xử lý
        # trường hợp dòng chữ chạm mốc 80% và bị cắt nếu chỉ dùng crop cứng.
        y1 = max(0, y1)
        y2 = min(fh, y2)
        if y2 <= zone_top or y1 >= zone_bottom:
            continue
        y1 = max(zone_top, y1)
        if x2 - x1 < 12 or y2 - y1 < 8:
            continue
        boxes.append((x1, y1, x2, y2))

    return _merge_boxes(boxes)


def _select_subtitle_boxes(
    boxes: list[tuple[int, int, int, int]],
    frame_h: int,
) -> list[tuple[int, int, int, int]]:
    """Chọn nhóm box thấp nhất, bỏ chữ nhỏ/watermark nằm cao hơn.

    Một frame có thể chứa watermark tiếng Trung ở phía trên và phụ đề tiếng
    Trung ở phía dưới. Hai dòng của cùng một phụ đề thường cách nhau ít hơn
    watermark, vì vậy lấy nhóm gần tâm thấp nhất để xử lý ảnh và đặt subtitle.
    """
    if not boxes:
        return []

    lowest_center = max((y1 + y2) / 2.0 for _, y1, _, y2 in boxes)
    tolerance = max(72.0, frame_h * 0.045)
    selected = [
        box
        for box in boxes
        if lowest_center - ((box[1] + box[3]) / 2.0) <= tolerance
    ]
    return selected or [max(boxes, key=lambda box: (box[1] + box[3]) / 2.0)]


def _blur_boxes(frame: np.ndarray, boxes: list[tuple[int, int, int, int]]) -> np.ndarray:
    """Làm sạch cục bộ nhóm box OCR bằng blur và feather mềm.

    Inpaint một hình chữ nhật lớn thường kéo màu/đường biên từ trên xuống dưới
    và tạo thành dải ngang rõ rệt. Blur trong đúng box OCR vẫn xoá được nét
    chữ hard-subtitle; feather ở mép làm phần xử lý hoà vào nền tự nhiên hơn.
    """
    if not boxes:
        return frame

    fh, fw = frame.shape[:2]
    selected = _select_subtitle_boxes(boxes, fh)
    if not selected:
        return frame

    x1 = max(0, min(fw - 1, min(box[0] for box in selected)))
    y1 = max(0, min(fh - 1, min(box[1] for box in selected)))
    x2 = min(fw, max(box[2] for box in selected))
    y2 = min(fh, max(box[3] for box in selected))
    if x2 <= x1 or y2 <= y1:
        return frame

    box_height = max(1, y2 - y1)
    blur_sigma = max(10.0, min(OCR_REPAIR_BLUR_SIGMA, box_height * 0.18))
    feather_sigma = max(8.0, min(OCR_REPAIR_FEATHER_SIGMA, blur_sigma))
    margin = int(round(feather_sigma * 2.5))

    rx1 = max(0, x1 - margin)
    ry1 = max(0, y1 - margin)
    rx2 = min(fw, x2 + margin)
    ry2 = min(fh, y2 + margin)
    roi = frame[ry1:ry2, rx1:rx2]
    if roi.size == 0:
        return frame

    blurred = cv2.GaussianBlur(roi, (0, 0), blur_sigma)
    mask = np.zeros(roi.shape[:2], dtype=np.float32)
    mx1, my1 = x1 - rx1, y1 - ry1
    mx2, my2 = x2 - rx1, y2 - ry1
    mask[max(0, my1):min(mask.shape[0], my2), max(0, mx1):min(mask.shape[1], mx2)] = 1.0
    mask = cv2.GaussianBlur(mask, (0, 0), feather_sigma)
    mask = np.clip(mask, 0.0, 1.0)[..., np.newaxis]

    cleaned = (
        blurred.astype(np.float32) * mask
        + roi.astype(np.float32) * (1.0 - mask)
    ).clip(0, 255).astype(np.uint8)
    output = frame.copy()
    output[ry1:ry2, rx1:rx2] = cleaned
    return output


def _subtitle_y_from_boxes(boxes: list[tuple[int, int, int, int]], frame_h: int) -> float | None:
    """Chọn tâm dòng phụ đề thấp nhất, bỏ watermark nằm cao hơn."""
    if not boxes:
        return None
    centers = [((y1 + y2) / 2.0, y1, y2) for _, y1, _, y2 in boxes]
    lowest_center = max(item[0] for item in centers)
    # Nếu phụ đề có hai dòng, giữ các dòng gần nhau; watermark cách xa sẽ bị
    # bỏ khỏi phép tính vị trí render.
    tolerance = max(72.0, frame_h * 0.045)
    selected = [item[0] for item in centers if lowest_center - item[0] <= tolerance]
    return float(statistics.median(selected)) if selected else float(lowest_center)


def _write_subtitle_y(output_path: str, values: list[float]) -> None:
    if not values:
        return
    y_path = os.path.join(os.path.dirname(output_path), "subtitle_y_pos.txt")
    with open(y_path, "w", encoding="utf-8") as handle:
        handle.write(str(int(round(statistics.median(values)))))
    logger.info(f"OCR subtitle Y position saved: {statistics.median(values):.1f}px → {y_path}")


def apply_ocr_image_replace(
    video_path: str,
    output_path: str,
    ocr_every_n_frames: int = OCR_EVERY_N_FRAMES,
) -> bool:
    """Xoá chữ Trung bằng OCR + xử lý ảnh cục bộ, giữ nguyên audio."""
    logger.info(f"OCR image replacement: {video_path} → {output_path}")
    logger.info(f"OCR scan zone: {OCR_ZONE_LABEL} (with edge guard)")

    try:
        reader = _load_reader()
    except Exception as exc:
        logger.error(f"EasyOCR load failed: {exc}")
        return False

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        logger.error(f"Cannot open video: {video_path}")
        return False

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frame_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    frame_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if frame_w <= 0 or frame_h <= 0:
        cap.release()
        logger.error("Cannot read source video dimensions")
        return False

    try:
        descriptor, temporary_output = tempfile.mkstemp(
            prefix=".ocr-", suffix=".mp4", dir=os.path.dirname(os.path.abspath(output_path)))
        os.close(descriptor)
    except OSError:
        cap.release()
        raise
    ffmpeg_cmd = [
        _find_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-vcodec", "rawvideo",
        "-s", f"{frame_w}x{frame_h}", "-pix_fmt", "bgr24",
        "-r", str(fps), "-i", "-",
        "-i", video_path,
        "-map", "0:v:0", "-map", "1:a:0?",
        *build_lossless_video_args(video_path),
        "-map_metadata", "1", "-map_chapters", "1",
        "-fps_mode", "passthrough", "-movflags", "+faststart",
        "-c:a", "copy", temporary_output,
    ]

    try:
        encoder = subprocess.Popen(
            ffmpeg_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
    except OSError as exc:
        cap.release()
        os.remove(temporary_output)
        logger.error(f"Cannot start FFmpeg: {exc}")
        return False

    scan_step = max(1, int(ocr_every_n_frames))
    frame_number = 0
    cached_boxes: list[tuple[int, int, int, int]] = []
    missed_scans = MAX_MISSED_OCR_SCANS
    subtitle_y_values: list[float] = []
    detected_frames = 0
    detected_boxes = 0
    ok = True

    logger.info(f"Processing {total_frames or '?'} frames with OCR every {scan_step} frame(s)...")
    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            frame_number += 1

            if frame_number == 1 or frame_number % scan_step == 0:
                boxes = detect_chinese_boxes(frame, reader)
                if boxes:
                    cached_boxes = boxes
                    missed_scans = 0
                    detected_frames += 1
                    detected_boxes += len(boxes)
                    subtitle_y = _subtitle_y_from_boxes(boxes, frame_h)
                    if subtitle_y is not None:
                        subtitle_y_values.append(subtitle_y)
                else:
                    missed_scans += 1
                    if missed_scans > MAX_MISSED_OCR_SCANS:
                        cached_boxes = []

            annotated = _blur_boxes(frame, cached_boxes)
            try:
                encoder.stdin.write(annotated.tobytes())
            except (BrokenPipeError, OSError) as exc:
                logger.error(f"FFmpeg frame write failed: {exc}")
                ok = False
                break

            if frame_number % 100 == 0:
                logger.info(
                    f"  {frame_number}/{total_frames or '?'} frames | OCR boxes={len(cached_boxes)}"
                )
    except BaseException:
        ok = False
        raise
    finally:
        cap.release()
        try:
            encoder.stdin.close()
        except (BrokenPipeError, OSError):
            ok = False
        stderr = encoder.stderr.read().decode("utf-8", errors="replace") if encoder.stderr else ""
        return_code = encoder.wait()
        if not ok or return_code != 0:
            try:
                os.remove(temporary_output)
            except OSError:
                pass

    if total_frames > 0 and frame_number != total_frames:
        logger.error("OCR decoded only %d/%d frames; refusing incomplete video", frame_number, total_frames)
        ok = False
    if return_code != 0 or not ok:
        logger.error(
            f"OCR image replacement encode failed (FFmpeg exit code={return_code}): {stderr[-2000:]}"
        )
        try:
            os.remove(temporary_output)
        except OSError:
            pass
        return False

    if detected_frames <= 0:
        logger.warning("No Chinese subtitle box was detected in the configured OCR zone")
        try:
            os.remove(temporary_output)
        except OSError:
            pass
        return False

    os.replace(temporary_output, output_path)
    try:
        write_quality_marker(output_path, video_path, OCR_IMAGE_REPLACE_MODE)
        _write_subtitle_y(output_path, subtitle_y_values)
    except OSError as exc:
        logger.warning(f"Could not write OCR replacement metadata: {exc}")

    logger.info(
        f"OCR image replacement completed: {detected_frames} OCR frames, "
        f"{detected_boxes} detected boxes → {output_path}"
    )
    return True
