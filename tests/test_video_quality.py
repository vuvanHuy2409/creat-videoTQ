"""Kiểm thử chính sách encode và marker chất lượng video."""

import json
from types import SimpleNamespace

from src import video_quality


def _mock_probe(monkeypatch, stream):
    monkeypatch.setattr(
        video_quality.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            stdout=json.dumps({"streams": [stream]}),
        ),
    )


def test_lossless_args_keep_source_format_and_colour(monkeypatch):
    monkeypatch.delenv("VIDEO_CRF", raising=False)
    monkeypatch.delenv("VIDEO_PRESET", raising=False)
    _mock_probe(
        monkeypatch,
        {
            "codec_name": "h264",
            "profile": "High",
            "level": 50,
            "pix_fmt": "yuv420p",
            "color_range": "tv",
            "color_space": "bt709",
            "color_transfer": "bt709",
            "color_primaries": "bt709",
        },
    )

    args = video_quality.build_lossless_video_args("input.mp4")

    assert args[:8] == [
        "-c:v", "libx264", "-preset", "medium", "-crf", "0", "-pix_fmt", "yuv420p",
    ]
    # CRF 0 không được ép profile High: x264 tự chọn profile lossless phù hợp.
    assert "-profile:v" not in args
    assert ["-color_range", "tv"] == args[args.index("-color_range"):args.index("-color_range") + 2]
    assert ["-colorspace", "bt709"] == args[args.index("-colorspace"):args.index("-colorspace") + 2]


def test_normal_crf_keeps_h264_profile_and_level(monkeypatch):
    monkeypatch.setenv("VIDEO_CRF", "18")
    monkeypatch.setenv("VIDEO_PRESET", "slow")
    _mock_probe(
        monkeypatch,
        {"codec_name": "h264", "profile": "High", "level": 50, "pix_fmt": "yuv420p"},
    )

    args = video_quality.build_lossless_video_args("input.mp4")

    assert ["-preset", "slow"] == args[args.index("-preset"):args.index("-preset") + 2]
    assert ["-crf", "18"] == args[args.index("-crf"):args.index("-crf") + 2]
    assert ["-profile:v", "high"] == args[args.index("-profile:v"):args.index("-profile:v") + 2]
    assert ["-level:v", "5.0"] == args[args.index("-level:v"):args.index("-level:v") + 2]


def test_quality_marker_matches_current_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_CRF", "0")
    monkeypatch.setenv("VIDEO_PRESET", "medium")
    output = str(tmp_path / "blurred.mp4")

    video_quality.write_quality_marker(output, "source.mp4", "ai_blur")

    assert video_quality.has_current_quality_marker(output, "ai_blur")
    assert not video_quality.has_current_quality_marker(output, "subtitles")

    monkeypatch.setenv("VIDEO_CRF", "18")
    assert not video_quality.has_current_quality_marker(output, "ai_blur")
