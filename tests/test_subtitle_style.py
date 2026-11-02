"""Style validation, project isolation and actual encoded subtitle colours."""

import importlib
import subprocess

import pytest
from PIL import Image

from src.subtitle_style import (SubtitleStyle, load_project_subtitles, save_project_subtitles,
                                style_overrides_from_args)
from src import video_merger as vm


@pytest.mark.parametrize("kwargs", [{"bg_color": "red"}, {"outline_color": "#123"},
    {"text_color": "#FF0000:box=0"}, {"bg_opacity": "50.5"}, {"bg_opacity": 101},
    {"bg_opacity": -1}, {"bg_enabled": "0"}])
def test_invalid_styles_never_reach_a_video_filter(kwargs):
    with pytest.raises(ValueError):
        SubtitleStyle(**kwargs)


def test_project_style_survives_default_changes_and_does_not_leak_secrets(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    style = SubtitleStyle("#ffff00", "#0000ff", "#ff0000", 50)
    saved = save_project_subtitles(first, {"SUBTITLE_FONT": "Anton", "SUBTITLE_FONT_SIZE": "72",
        **style.to_settings(), "AZURE_SPEECH_KEY": "not-to-be-saved"})
    assert load_project_subtitles(first, SubtitleStyle(bg_color="#00FF00").to_settings()) == saved
    assert load_project_subtitles(second)["SUBTITLE_BG_COLOR"] == "#0C100C"
    assert "not-to-be-saved" not in (first / "subtitle_style.json").read_text()
    assert not list(first.glob(".subtitle-*.tmp"))


@pytest.mark.parametrize("module_name", ["pipeline", "pipeline_vi"])
def test_both_pipeline_clis_accept_partial_color_overrides(monkeypatch, module_name):
    module = importlib.import_module(module_name)
    voice = "azure:vi-VN-HoaiMyNeural" if module_name == "pipeline_vi" else "ja-JP-KeitaNeural"
    monkeypatch.setattr("sys.argv", [module_name, "--file", "input.mp4", "--voice", voice, "--subtitle-bg-color", "#ff0000",
        "--subtitle-bg-opacity", "75", "--subtitle-outline-color", "#00ff00"])
    args = module.parse_args()
    assert style_overrides_from_args(args) == {"SUBTITLE_BG_COLOR": "#FF0000",
        "SUBTITLE_BG_OPACITY": 75, "SUBTITLE_OUTLINE_COLOR": "#00FF00"}
    assert args.subtitle_font is None  # A resume keeps the saved project face/size.
    assert args.subtitle_size is None


@pytest.mark.parametrize("module_name", ["pipeline", "pipeline_vi"])
def test_resume_keeps_saved_project_style_when_only_one_colour_is_overridden(monkeypatch, tmp_path, module_name):
    module = importlib.import_module(module_name)
    save_project_subtitles(tmp_path, {"SUBTITLE_FONT": "Anton", "SUBTITLE_FONT_SIZE": "72",
        **SubtitleStyle(bg_color="#FF0000", bg_opacity=75).to_settings()})
    monkeypatch.setenv("SUBTITLE_BG_COLOR", "#0000FF")
    def stop_before_media(*args):
        raise RuntimeError("stop before media processing")
    monkeypatch.setattr(module, "_resolve_video", stop_before_media)
    run = module.run_pipeline_vi if module_name == "pipeline_vi" else module.run_pipeline
    voice_arg = {"voice_id": "male"} if module_name == "pipeline_vi" else {"voice": "ja-JP-KeitaNeural"}
    with pytest.raises(RuntimeError, match="stop before media processing"):
        run.__wrapped__(url=None, file_path="unused.mp4", source_lang="zh", skip_video=False,
                        output_dir=str(tmp_path), resume_dir=str(tmp_path),
                        subtitle_style={"SUBTITLE_OUTLINE_COLOR": "#00FF00"}, **voice_arg)
    saved = load_project_subtitles(tmp_path)
    assert saved["SUBTITLE_BG_COLOR"] == "#FF0000"
    assert saved["SUBTITLE_BG_OPACITY"] == "75"
    assert saved["SUBTITLE_OUTLINE_COLOR"] == "#00FF00"
    assert saved["SUBTITLE_FONT"] == "Anton"
    assert saved["SUBTITLE_FONT_SIZE"] == "72"


@pytest.mark.parametrize("enabled,opacity", [(False, 84), (True, 0)])
def test_background_can_be_disabled_in_both_renderers(tmp_path, enabled, opacity):
    style = SubtitleStyle(bg_enabled=enabled, bg_opacity=opacity)
    entries = [{"text": "Phụ đề", "start": 0, "end": 1}]
    path = tmp_path / "captions.ass"
    vm._write_ass_file(entries, str(path), 720, 480, 300, subtitle_style=style)
    assert "DefaultBox,," not in path.read_text()
    drawtext = vm._build_drawtext_filter(entries, 480, 300, subtitle_style=style)
    assert ":box=0:" in drawtext


def _ffmpeg(*args):
    result = subprocess.run([vm.FFMPEG_BIN, "-hide_banner", "-loglevel", "error", "-y", *args],
                             capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.fixture
def tiny_media(tmp_path):
    try:
        supported = {name: vm._has_filter(name) for name in ("subtitles", "drawtext")}
    except OSError:
        pytest.skip("FFmpeg unavailable")
    if not all(supported.values()):
        pytest.skip("Needs FFmpeg with both libass and drawtext")
    video, audio, srt = (tmp_path / name for name in ("input.mp4", "audio.wav", "caption.srt"))
    _ffmpeg("-f", "lavfi", "-i", "color=c=0x707070:s=720x480:r=2:d=1",
            "-c:v", "libx264", "-crf", "0", str(video))
    _ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=1", str(audio))
    srt.write_text("1\n00:00:00,000 --> 00:00:00,800\nPHỤ ĐỀ MÀU\n", encoding="utf-8")
    return video, audio, srt


@pytest.mark.parametrize("renderer", ["subtitles", "drawtext"])
@pytest.mark.parametrize("opacity", [0, 50, 100])
def test_encoded_video_contains_selected_text_outline_and_background_colors(
        monkeypatch, tmp_path, tiny_media, renderer, opacity):
    video, audio, srt = tiny_media
    monkeypatch.setattr(vm, "_has_filter", lambda name: name == renderer)
    monkeypatch.setenv("VIDEO_CRF", "0")
    output, frame = tmp_path / "output.mp4", tmp_path / "frame.png"
    vm.merge_video(str(video), str(audio), str(output), str(srt), y_pixel=300, font_size=56,
                   font_name="Be Vietnam Pro", subtitle_style=SubtitleStyle("#FFFF00", "#0000FF", "#FF0000", opacity))
    _ffmpeg("-i", str(output), "-frames:v", "1", str(frame))
    _ffmpeg("-i", str(output), "-f", "null", "-")  # Also decode the resulting audio/video.
    colors = Image.open(frame).convert("RGB").getcolors(720 * 480)
    assert sum(n for n, (r, g, b) in colors if r > 210 and g > 210 and b < 40) > 100  # Yellow text.
    assert sum(n for n, (r, g, b) in colors if b > 210 and r < 40 and g < 40) > 100  # Blue outline.
    background = sum(n for n, (r, g, b) in colors if r > g + 40 and r > b + 40)
    assert background > 1000 if opacity else background < 100
