"""Bộ phát hiện vùng phụ đề dựa trên OCR.

OCR chỉ chạy trong dải 20%-40% tính từ đáy frame. Cách này tập trung vào vùng
phụ đề và tránh nhận diện chữ không liên quan ở nơi khác.
"""
import os
import cv2
import numpy as np
from src.utils import setup_logging
from src.ocr_zone import (
    OCR_ZONE_BOTTOM,
    OCR_ZONE_LABEL,
    OCR_ZONE_TOP,
    crop_ocr_zone,
    get_ocr_zone_bounds,
)
from src.ocr_image_replace import detect_chinese_boxes

logger = setup_logging("subtitle_detector")

# ── Cấu hình ──────────────────────────────────────────────────────────────────
SAMPLE_COUNT   = 12     # số frame lấy mẫu
# Giữ alias để tương thích với code bên ngoài đã import hằng số cũ.
SCAN_ZONE_TOP    = OCR_ZONE_TOP
SCAN_ZONE_BOTTOM = OCR_ZONE_BOTTOM
PADDING_H      = 6      # extra vertical padding around detected text (px)
PADDING_W      = 20     # extra horizontal padding
MIN_CONFIDENCE = 0.4    # minimum OCR confidence to count as subtitle text
# ──────────────────────────────────────────────────────────────────────────────


def _get_frame_count(video_path: str) -> int:
    """Lấy số frame của video, tối thiểu trả về 1 để tránh chia cho 0."""
    cap = cv2.VideoCapture(video_path)
    n   = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()
    return max(n, 1)


def _sample_frames(video_path: str, count: int) -> list[np.ndarray]:
    """Trích ``count`` frame phân bố đều trong toàn bộ video."""
    cap    = cv2.VideoCapture(video_path)
    total  = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    frames = []
    indices = [int(total * i / count) for i in range(count)]
    for idx in indices:
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ret, frame = cap.read()
        if ret:
            frames.append(frame)
    cap.release()
    return frames


def _crop_scan_zone(frame: np.ndarray) -> tuple[np.ndarray, int]:
    """Trả về vùng crop và offset Y của dải OCR cố định."""
    return crop_ocr_zone(frame)


def detect_subtitle_zone(video_path: str) -> dict | None:
    """
    Phát hiện bounding box phụ đề tổng quát theo tọa độ frame gốc.
    Hàm này chủ yếu dùng làm phương án dự phòng.
    """
    try:
        import easyocr
    except ImportError:
        return None

    frames = _sample_frames(video_path, SAMPLE_COUNT)
    if not frames:
        return None

    frame_h, frame_w = frames[0].shape[:2]
    logger.info(f"Scanning OCR zone: {OCR_ZONE_LABEL}")

    import ssl
    try:
        _orig_ctx = ssl._create_default_https_context
        ssl._create_default_https_context = ssl._create_unverified_context
        reader = easyocr.Reader(["ch_sim", "en"], gpu=False, verbose=False)
    finally:
        ssl._create_default_https_context = _orig_ctx

    all_boxes: list[tuple[int, int, int, int]] = []

    for fi, frame in enumerate(frames):
        # Dùng chung detector với nhánh xử lý ảnh: có dải đệm ở mốc 80% và chấp
        # nhận chữ Trung confidence thấp khi EasyOCR vẫn đọc được ký tự.
        all_boxes.extend(detect_chinese_boxes(frame, reader))

    if not all_boxes:
        return None

    zone_top, zone_bottom = get_ocr_zone_bounds(frame_h)
    x1 = max(0,          min(b[0] for b in all_boxes) - PADDING_W)
    y1 = max(zone_top,   min(b[1] for b in all_boxes) - PADDING_H)
    x2 = min(frame_w,    max(b[2] for b in all_boxes) + PADDING_W)
    y2 = min(zone_bottom, max(b[3] for b in all_boxes) + PADDING_H)

    return {
        "x":       x1,
        "y":       y1,
        "width":   x2 - x1,
        "height":  y2 - y1,
        "frame_w": frame_w,
        "frame_h": frame_h,
    }


def detect_subtitle_positions_for_chunks(video_path: str, chunks: list[dict]) -> list[dict]:
    """
    Với mỗi chunk, đọc frame ở giữa, chạy OCR để tìm vị trí Y và chiều cao
    phụ đề Trung Quốc, sau đó gắn ``zone`` vào chunk hoặc đặt ``None``.
    """
    try:
        import easyocr
    except ImportError:
        logger.error("easyocr is not installed. Skipping precise subtitle position detection.")
        for chunk in chunks:
            chunk["zone"] = None
        return chunks

    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS)
    if fps <= 0:
        fps = 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    frame_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    frame_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))

    # Khởi tạo EasyOCR; tạm nới SSL vì model có thể cần tải file trọng số.
    import ssl
    try:
        _orig_ctx = ssl._create_default_https_context
        ssl._create_default_https_context = ssl._create_unverified_context
        reader = easyocr.Reader(["ch_sim", "en"], gpu=False, verbose=False)
    finally:
        ssl._create_default_https_context = _orig_ctx

    logger.info(
        f"Scanning {len(chunks)} chunks for precise vertical subtitle coordinates "
        f"in OCR zone: {OCR_ZONE_LABEL}..."
    )

    for idx, chunk in enumerate(chunks):
        t_mid = (chunk["start"] + chunk["end"]) / 2.0
        frame_idx = int(t_mid * fps)
        if frame_idx >= total_frames:
            frame_idx = total_frames - 1

        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ret, frame = cap.read()
        if not ret:
            chunk["zone"] = None
            continue

        candidates = []
        for x1, y1, x2, y2 in detect_chinese_boxes(frame, reader):
            # Ưu tiên box nằm gần tâm màn hình vì phụ đề thường được căn giữa.
            text_center = (x1 + x2) / 2.0
            screen_center = frame_w / 2.0
            center_dist = abs(text_center - screen_center)

            candidates.append({
                "x1": x1, "y1": y1,
                "x2": x2, "y2": y2,
                "height": y2 - y1,
                "width": x2 - x1,
                "center_dist": center_dist,
                "text": "Chinese OCR",
            })

        if candidates:
            # Bỏ box quá nhỏ/nhiễu rồi ưu tiên box gần tâm và cao hơn.
            candidates = [c for c in candidates if c["height"] > 10]
            if candidates:
                # Sắp xếp chính theo khoảng cách tới tâm ngang của video.
                candidates.sort(key=lambda c: (c["center_dist"], -c["height"]))
                best = candidates[0]
                
                # Thêm padding ngang/dọc để che trọn nét chữ.
                x1_pad = max(0, best["x1"] - PADDING_W)
                zone_top, zone_bottom = get_ocr_zone_bounds(frame_h)
                y1_pad = max(zone_top, best["y1"] - PADDING_H)
                x2_pad = min(frame_w, best["x2"] + PADDING_W)
                y2_pad = min(zone_bottom, best["y2"] + PADDING_H)

                chunk["zone"] = {
                    "x": x1_pad,
                    "y": y1_pad,
                    "width": x2_pad - x1_pad,
                    "height": y2_pad - y1_pad,
                    "frame_w": frame_w,
                    "frame_h": frame_h,
                }
                logger.info(
                    f"  Chunk {idx+1} ({chunk['start']:.1f}s-{chunk['end']:.1f}s): "
                    f"detected Chinese subtitle at y={chunk['zone']['y']}, h={chunk['zone']['height']} (text: '{best['text']}')"
                )
            else:
                chunk["zone"] = None
        else:
            chunk["zone"] = None

    cap.release()
    return chunks
