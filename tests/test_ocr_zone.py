"""Kiểm thử dải ảnh được chia sẻ giữa OCR blur và bộ phát hiện phụ đề."""

import numpy as np

from src.ocr_zone import (
    OCR_ZONE_BOTTOM,
    OCR_ZONE_TOP,
    crop_ocr_zone,
    crop_ocr_scan_zone,
    get_ocr_scan_bounds,
    get_ocr_zone_bounds,
)
from src.subtitle_detector import _crop_scan_zone


def test_ocr_zone_is_20_to_40_percent_up_from_bottom():
    assert (OCR_ZONE_TOP, OCR_ZONE_BOTTOM) == (0.60, 0.80)
    assert get_ocr_zone_bounds(1000) == (600, 800)

    frame = np.zeros((1000, 12, 3), dtype=np.uint8)
    crop, offset = crop_ocr_zone(frame)
    assert offset == 600
    assert crop.shape == (200, 12, 3)

    detector_crop, detector_offset = _crop_scan_zone(frame)
    assert detector_offset == offset
    assert detector_crop.shape == crop.shape


def test_ocr_scan_guard_extends_below_main_zone_without_changing_main_bounds():
    frame = np.zeros((1000, 12, 3), dtype=np.uint8)
    assert get_ocr_scan_bounds(1000) == (600, 860)

    scan_crop, offset = crop_ocr_scan_zone(frame)
    assert offset == 600
    assert scan_crop.shape == (260, 12, 3)
