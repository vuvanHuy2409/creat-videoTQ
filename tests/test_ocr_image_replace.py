"""Kiểm thử detector OCR và chọn đúng nhóm subtitle để xử lý ảnh."""

import numpy as np
import pytest
import cv2
import shutil

from src.ocr_image_replace import (
    _readtext,
    _sanitize_ocr_regions,
    _select_subtitle_boxes,
    _subtitle_y_from_boxes,
    detect_chinese_boxes,
)


class _FakeReader:
    """Reader tối giản để test không tải model EasyOCR."""

    def __init__(self):
        self.calls = 0

    def detect(self, image, **kwargs):
        self.calls += 1
        return [[[120, 380, 120, 180]]], [[]]

    def recognize(self, image, horizontal, free, **kwargs):
        return [
            (
                [[120, 120], [380, 120], [380, 180], [120, 180]],
                "中文字幕",
                0.0,
            )
        ]


def test_detector_accepts_chinese_text_even_when_confidence_is_zero():
    frame = np.zeros((1000, 500, 3), dtype=np.uint8)
    reader = _FakeReader()

    boxes = detect_chinese_boxes(frame, reader)

    assert boxes
    assert reader.calls == 1
    x1, y1, x2, y2 = boxes[0]
    # Box nằm trong vùng chính 60%-80% từ đỉnh, dù có dải guard ở phía dưới.
    assert x1 < x2
    assert 600 <= y1 < 800
    assert y2 > y1


def test_subtitle_group_ignores_higher_watermark_for_cleanup_and_y():
    boxes = [
        (300, 1380, 500, 1435),  # watermark
        (100, 1490, 900, 1540),  # dòng trên
        (120, 1545, 880, 1600),  # dòng dưới
    ]

    selected = _select_subtitle_boxes(boxes, 1920)

    assert selected == boxes[1:]
    assert _subtitle_y_from_boxes(boxes, 1920) == 1543.75


def test_recognition_filters_regions_whose_clipped_crops_are_empty():
    class Reader:
        def detect(self, image, **kwargs):
            return [[[110, 130, 10, 40], [-30, -4, 10, 40], [10, 35, 90, 110],
                     [0, 30, 20, 10], [2, 2, 2, 15], [float("nan"), 30, 0, 10],
                     [-4, 28, -2, 18]]], [[]]

        def recognize(self, image, horizontal, free, **kwargs):
            assert horizontal == [[0, 28, 0, 18]]
            assert not free
            for x1, x2, y1, y2 in horizontal:
                assert image[y1:y2, x1:x2].size > 0
            return [([[0, 0], [28, 0], [28, 18], [0, 18]], "中文", 0.9)]

    assert _readtext(Reader(), np.zeros((60, 100, 3), dtype=np.uint8))[0][1] == "中文"


def test_rotated_regions_reject_zero_area_and_clip_to_image():
    valid = [[-3, 3], [20, 1], [22, 15], [-1, 18]]
    invalid = [
        [[110, 1], [130, 1], [130, 20], [110, 20]],
        [[1, 1], [4, 4], [8, 8], [12, 12]],
        [[1, 1], [1, 1], [20, 20], [1, 20]],
        [[float("inf"), 1], [10, 1], [10, 20], [1, 20]],
    ]
    horizontal, free = _sanitize_ocr_regions([], invalid + [valid], 100, 60)
    assert horizontal == []
    assert len(free) == 1
    assert min(point[0] for point in free[0]) == 0
    from easyocr.utils import get_image_list
    crops, _ = get_image_list(horizontal, free, np.zeros((60, 100), dtype=np.uint8))
    assert len(crops) == 1 and crops[0][1].size > 0


def test_empty_detection_does_not_recognize_whole_image():
    class Reader:
        def detect(self, image, **kwargs):
            return [[[110, 130, 10, 40]]], [[]]

        def recognize(self, *args, **kwargs):
            pytest.fail("No valid crop should reach recognition")

    assert _readtext(Reader(), np.zeros((60, 100, 3), dtype=np.uint8)) == []


def test_recognition_errors_are_not_silently_discarded():
    class Reader(_FakeReader):
        def recognize(self, *args, **kwargs):
            raise RuntimeError("model unavailable")

    with pytest.raises(RuntimeError, match="model unavailable"):
        _readtext(Reader(), np.zeros((240, 400, 3), dtype=np.uint8))


@pytest.mark.parametrize("failure", [None, "recognition", "short_decode"])
def test_video_is_published_only_after_complete_processing(tmp_path, monkeypatch, failure):
    from src import ocr_image_replace as ocr
    from src.video_quality import has_current_quality_marker

    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg required for encoder integration test")
    source = tmp_path / "source.mp4"
    writer = cv2.VideoWriter(str(source), cv2.VideoWriter_fourcc(*"mp4v"), 10, (96, 64))
    assert writer.isOpened()
    for number in range(6):
        writer.write(np.full((64, 96, 3), number * 20, dtype=np.uint8))
    writer.release()
    output = tmp_path / "cleaned.mp4"
    output.write_bytes(b"previous output")
    monkeypatch.setattr(ocr, "_load_reader", lambda: object())
    calls = []

    def detect(frame, reader):
        calls.append(True)
        if failure == "recognition" and len(calls) == 2:
            raise RuntimeError("recognition failed mid-video")
        return [(10, 40, 75, 53)]

    monkeypatch.setattr(ocr, "detect_chinese_boxes", detect)
    if failure == "short_decode":
        real_capture = cv2.VideoCapture

        class ShortCapture:
            def __init__(self, path):
                self.capture = real_capture(path)
                self.frames = 0

            def __getattr__(self, name):
                return getattr(self.capture, name)

            def read(self):
                self.frames += 1
                return (False, None) if self.frames > 3 else self.capture.read()

        monkeypatch.setattr(ocr.cv2, "VideoCapture", ShortCapture)
    if failure == "recognition":
        with pytest.raises(RuntimeError, match="mid-video"):
            ocr.apply_ocr_image_replace(str(source), str(output), 1)
    else:
        assert ocr.apply_ocr_image_replace(str(source), str(output), 1) is (failure is None)
    if failure:
        assert output.read_bytes() == b"previous output"
        assert not has_current_quality_marker(str(output), ocr.OCR_IMAGE_REPLACE_MODE)
    else:
        assert output.stat().st_size > 100
        assert has_current_quality_marker(str(output), ocr.OCR_IMAGE_REPLACE_MODE)
        assert len(calls) == 6
    assert not list(tmp_path.glob(".ocr-*.mp4"))
