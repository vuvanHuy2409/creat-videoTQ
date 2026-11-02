"""Các hàm dùng chung để giữ chất lượng video khi phải encode lại.

Pipeline có hai nhánh video:

* Không đổi hình ảnh thì có thể copy nguyên stream video.
* Khi blur hoặc burn subtitle thì FFmpeg phải encode frame mới.

Đặt chính sách encode ở một module giúp các bước không âm thầm chọn CRF,
pixel format hoặc metadata màu thấp hơn.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Any


_FFPROBE_BIN = os.environ.get("FFPROBE_BIN", "ffprobe")
_QUALITY_MARKER_SUFFIX = ".video_quality.json"
_DEFAULT_CRF = "0"       # x264 lossless; override with VIDEO_CRF if desired
_DEFAULT_PRESET = "medium"
_VALID_PRESETS = {
    "ultrafast", "superfast", "veryfast", "faster", "fast", "medium",
    "slow", "slower", "veryslow", "placebo",
}
_H264_PROFILES = {
    "Constrained Baseline": "baseline",
    "Baseline": "baseline",
    "Main": "main",
    "High": "high",
    "High 10": "high10",
    "High 4:2:2": "high422",
    "High 4:4:4 Predictive": "high444",
}

# Các pixel format thường được libx264 trên máy phát triển hỗ trợ. Alias yuvj*
# sẽ được chuẩn hóa vì đây là tên đã lỗi thời.
_X264_PIXEL_FORMATS = {
    "yuv420p", "yuv422p", "yuv444p",
    "yuv420p10le", "yuv422p10le", "yuv444p10le",
    "nv12", "nv16", "nv21", "nv20le", "gray", "gray10le",
}

_COLOUR_OPTIONS = (
    ("color_range", "-color_range"),
    ("color_space", "-colorspace"),
    ("color_transfer", "-color_trc"),
    ("color_primaries", "-color_primaries"),
)


def probe_video_stream(video_path: str) -> dict[str, Any]:
    """Đọc metadata stream video đầu tiên, hoặc trả dict rỗng nếu lỗi."""
    command = [
        _FFPROBE_BIN,
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries",
        "stream=codec_name,profile,level,width,height,pix_fmt,"
        "sample_aspect_ratio,r_frame_rate,avg_frame_rate,field_order,"
        "color_range,color_space,color_transfer,color_primaries",
        "-of", "json",
        video_path,
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=True,
        )
        payload = json.loads(result.stdout or "{}")
        streams = payload.get("streams") or []
        return streams[0] if streams else {}
    except (OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError):
        return {}


def _encoding_settings() -> dict[str, str]:
    """Đọc CRF/preset hợp lệ từ môi trường để đưa vào lệnh FFmpeg dạng list."""
    crf = os.environ.get("VIDEO_CRF", _DEFAULT_CRF).strip()
    # x264 nhận CRF nguyên hoặc thập phân trong khoảng 0..51.
    if not re.fullmatch(r"(?:\d+(?:\.\d+)?)", crf):
        crf = _DEFAULT_CRF
    else:
        try:
            numeric_crf = float(crf)
            if not 0 <= numeric_crf <= 51:
                crf = _DEFAULT_CRF
            elif numeric_crf == 0:
                crf = _DEFAULT_CRF
        except ValueError:
            crf = _DEFAULT_CRF

    preset = os.environ.get("VIDEO_PRESET", _DEFAULT_PRESET).strip().lower()
    if preset not in _VALID_PRESETS:
        preset = _DEFAULT_PRESET

    return {"crf": crf, "preset": preset}


def _normalise_pixel_format(pixel_format: str | None) -> str | None:
    """Chuẩn hóa alias pixel format và chỉ giữ giá trị x264 hỗ trợ."""
    if not pixel_format:
        return None
    if pixel_format.startswith("yuvj"):
        pixel_format = "yuv" + pixel_format[4:]
    return pixel_format if pixel_format in _X264_PIXEL_FORMATS else None


def build_lossless_video_args(video_path: str) -> list[str]:
    """Tạo tham số encoder cho thao tác làm thay đổi frame.

    ``VIDEO_CRF=0`` mặc định là x264 lossless. Người dùng có thể chọn file nhỏ
    hơn bằng ``VIDEO_CRF=16``. Pixel format và tín hiệu màu nguồn được giữ lại
    khi libx264 hỗ trợ.
    """
    stream = probe_video_stream(video_path)
    settings = _encoding_settings()

    args = [
        "-c:v", "libx264",
        "-preset", settings["preset"],
        "-crf", settings["crf"],
    ]

    pixel_format = _normalise_pixel_format(stream.get("pix_fmt"))
    # Frame thô từ OpenCV là BGR 8-bit; yuv420p là fallback tương thích nhất
    # khi probe thất bại hoặc format nguồn không được x264 hỗ trợ.
    args += ["-pix_fmt", pixel_format or "yuv420p"]

    # Với CRF thông thường, giữ profile/level H.264 của nguồn. Chế độ lossless
    # cần profile riêng nên không ép profile khi CRF=0.
    if stream.get("codec_name") == "h264" and settings["crf"] != "0":
        profile = _H264_PROFILES.get(stream.get("profile"))
        if profile:
            args += ["-profile:v", profile]
        try:
            level = int(stream.get("level"))
        except (TypeError, ValueError):
            level = 0
        if level > 0:
            args += ["-level:v", f"{level / 10:.1f}"]

    for stream_key, ffmpeg_option in _COLOUR_OPTIONS:
        value = stream.get(stream_key)
        if value and value not in {"unknown", "unspecified"}:
            args += [ffmpeg_option, str(value)]

    return args


def quality_marker_path(video_path: str) -> str:
    """Trả đường dẫn sidecar dùng để xác thực video đã biến đổi được cache."""
    return f"{video_path}{_QUALITY_MARKER_SUFFIX}"


def write_quality_marker(output_path: str, source_path: str, mode: str) -> None:
    """Ghi lại chính sách encode đã dùng cho video biến đổi."""
    settings = _encoding_settings()
    payload = {
        "schema": 1,
        "mode": mode,
        "encoder": "libx264",
        "crf": settings["crf"],
        "preset": settings["preset"],
        "source": os.path.abspath(source_path),
    }
    with open(quality_marker_path(output_path), "w", encoding="utf-8") as marker:
        json.dump(payload, marker, ensure_ascii=False, indent=2)


def has_current_quality_marker(video_path: str, mode: str) -> bool:
    """Kiểm tra video cache có dùng đúng thiết lập chất lượng hiện tại không."""
    marker_path = quality_marker_path(video_path)
    try:
        with open(marker_path, encoding="utf-8") as marker:
            payload = json.load(marker)
    except (OSError, ValueError, json.JSONDecodeError):
        return False

    settings = _encoding_settings()
    return (
        payload.get("schema") == 1
        and payload.get("mode") == mode
        and payload.get("encoder") == "libx264"
        and payload.get("crf") == settings["crf"]
        and payload.get("preset") == settings["preset"]
    )
