"""Thiết lập dải quét OCR dùng chung cho toàn bộ pipeline.

OCR phụ đề chỉ chạy ở dải 20%-40% tính từ đáy video, tương ứng Y=60%-80%
tính từ đỉnh frame. Điều này tránh quét chữ ở nơi khác và vùng UI/watermark
sát cạnh dưới.
"""

from __future__ import annotations

from typing import Tuple


# Tỷ lệ được tính từ mép dưới, đúng cách hiển thị cho người dùng.
OCR_ZONE_FROM_BOTTOM_START = 0.20
OCR_ZONE_FROM_BOTTOM_END = 0.40

# OpenCV đo tọa độ từ mép trên nên cần đổi lại hai mốc tương ứng.
OCR_ZONE_TOP = 1.0 - OCR_ZONE_FROM_BOTTOM_END       # 60% from the top
OCR_ZONE_BOTTOM = 1.0 - OCR_ZONE_FROM_BOTTOM_START  # 80% from the top
OCR_ZONE_LABEL = "20%-40% tính từ đáy (Y=60%-80% từ đỉnh)"

# Chữ phụ đề có thể nằm sát đường biên 80% và bị cắt mất phần chân chữ do
# anti-aliasing hoặc do video đã được crop. Dải đệm này chỉ dùng để hoàn tất
# box đã chạm biên; vùng OCR chính vẫn là 20%-40% tính từ đáy.
OCR_EDGE_GUARD_RATIO = 0.06


def get_ocr_zone_bounds(frame_height: int) -> Tuple[int, int]:
    """Trả về biên cắt OCR ``(y_top, y_bottom)`` theo chiều cao frame."""
    if frame_height <= 0:
        return 0, 0

    y_top = max(0, min(frame_height, int(round(frame_height * OCR_ZONE_TOP))))
    y_bottom = max(y_top, min(frame_height, int(round(frame_height * OCR_ZONE_BOTTOM))))
    return y_top, y_bottom


def get_ocr_scan_bounds(frame_height: int) -> Tuple[int, int]:
    """Trả biên OCR kèm dải đệm nhỏ ở mép dưới vùng chính.

    Vùng chính vẫn được xác định bởi ``get_ocr_zone_bounds``. Dải đệm giúp
    EasyOCR không nhận một dòng chữ bị cắt ngang đúng mốc 80%; các box hợp lệ
    vẫn phải giao với vùng chính trước khi được dùng để xoá chữ.
    """
    y_top, y_bottom = get_ocr_zone_bounds(frame_height)
    if frame_height <= 0:
        return y_top, y_bottom
    guarded_bottom = int(round(frame_height * (OCR_ZONE_BOTTOM + OCR_EDGE_GUARD_RATIO)))
    return y_top, min(frame_height, max(y_bottom, guarded_bottom))


def crop_ocr_zone(frame):
    """Cắt dải OCR dùng chung và trả thêm offset Y so với frame gốc."""
    y_top, y_bottom = get_ocr_zone_bounds(frame.shape[0])
    return frame[y_top:y_bottom, :], y_top


def crop_ocr_scan_zone(frame):
    """Cắt vùng OCR có dải đệm biên để tránh làm cụt dòng phụ đề."""
    y_top, y_bottom = get_ocr_scan_bounds(frame.shape[0])
    return frame[y_top:y_bottom, :], y_top
