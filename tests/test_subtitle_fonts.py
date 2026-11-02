"""Ensure the chosen font reaches both subtitle renderers and works offline."""

import hashlib
import json
from pathlib import Path

import pytest
from PIL import Image, ImageChops, ImageFont

from src.subtitle_fonts import FONTS, FONT_DIR, get_font, validate_size
from src.video_merger import _write_ass_file, _build_drawtext_filter
from subtitle_font_ui import PREVIEW_BG, SAMPLE_TEXT, render_preview


@pytest.mark.parametrize("spec", FONTS, ids=lambda spec: spec.name)
def test_every_font_is_bundled_attributed_and_uses_expected_face(spec):
    manifest = json.loads((FONT_DIR.parent / "sources.json").read_text(encoding="utf-8"))
    entry = next(item for item in manifest["fonts"] if item["name"] == spec.name)
    assert hashlib.sha256(spec.path.read_bytes()).hexdigest() == entry["sha256"]
    assert ImageFont.truetype(str(spec.path), 32).getname()[0] == spec.family
    license_path = FONT_DIR.parent / "licenses" / f"{spec.name.replace(' ', '')}-OFL.txt"
    assert "SIL OPEN FONT LICENSE" in license_path.read_text(encoding="utf-8").upper()


@pytest.mark.parametrize("size", ["", "abc", 0, 11, 161, "56.5"])
def test_invalid_subtitle_size_cannot_reach_render(size):
    with pytest.raises(ValueError):
        validate_size(size)


def test_unknown_or_missing_font_is_not_silently_replaced(monkeypatch, tmp_path):
    with pytest.raises(ValueError):
        get_font("Some missing system font")
    monkeypatch.setattr("src.subtitle_fonts.FONT_DIR", tmp_path)
    with pytest.raises(FileNotFoundError):
        get_font("Montserrat")


def test_both_renderers_use_selected_face_and_japanese_fallback(tmp_path):
    entries = [{"start": 0.0, "end": 1.0, "text": "Phụ đề đẹp và rõ"},
               {"start": 1.0, "end": 2.0, "text": "日本語の字幕"}]
    ass_path = tmp_path / "selected.ass"
    _write_ass_file(entries, str(ass_path), 720, 1280, 900, 64, font_name="Montserrat")
    ass = ass_path.read_text(encoding="utf-8")
    assert "Style: Default,AutoDub Montserrat,64," in ass
    assert "Style: Default,Phudu" not in ass
    assert "Japanese,,0,0,0,,日本語の字幕" in ass
    drawtext = _build_drawtext_filter(entries, 1280, 900, 64, font_name="Montserrat")
    assert str(get_font("Montserrat").path) in drawtext
    assert str(get_font("Noto Sans JP").path) in drawtext
    assert drawtext.count("fontsize=64") == 2


def test_distinct_fonts_produce_distinct_preview_pixels():
    text = "Phụ đề tiếng Việt rõ nét"
    images = [render_preview(name, 56, text) for name in ("Anton", "Inter", "Montserrat")]
    assert len({image.tobytes() for image in images}) == 3
    assert render_preview("Anton", 56, "日本語の字幕").tobytes() == render_preview("Noto Sans JP", 56, "日本語の字幕").tobytes()


@pytest.mark.parametrize("name", ["Anton", "Be Vietnam Pro", "Phudu", "Noto Sans JP"])
@pytest.mark.parametrize("size", [12, 56, 160])
@pytest.mark.parametrize("dimensions", [(1948, 370), (320, 185)])
def test_preview_keeps_complete_caption_inside_wide_and_narrow_panels(name, size, dimensions):
    image = render_preview(name, size, SAMPLE_TEXT, *dimensions)
    bounds = ImageChops.difference(image, Image.new("RGB", dimensions, PREVIEW_BG)).getbbox()
    assert bounds is not None
    left, top, right, bottom = bounds
    assert 0 < left < right < image.width
    assert 0 < top < bottom < image.height


def test_preview_retains_lines_after_four_and_text_after_200_characters():
    prefix = "\n".join(["Phụ đề tiếng Việt rõ nét, đầy cảm xúc và có dấu."] * 6)
    assert len(prefix) > 200
    first = render_preview("Anton", 56, prefix + "\nKết thúc A")
    second = render_preview("Anton", 56, prefix + "\nKết thúc B")
    assert first.tobytes() != second.tobytes()


def test_preview_wraps_unspaced_cjk_and_long_words():
    from PIL import ImageDraw
    from subtitle_font_ui import _wrap_caption
    font = ImageFont.truetype(str(get_font("Noto Sans JP").path), 56)
    draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    for text in ("日本語の字幕を最後まで正しく表示します" * 3, "A" * 100):
        lines = _wrap_caption(draw, text, font, 400)
        assert len(lines) > 1
        assert "".join(lines) == text
        assert all(draw.textlength(line, font=font) <= 400 for line in lines)


def test_preview_has_a_separate_dark_background_behind_the_letters():
    image = render_preview("Be Vietnam Pro", 56, "Phụ đề có nền", 720, 240)
    assert image.getpixel((0, 0)) == (48, 60, 48)
    # The translucent black box blends with the scene to a third, darker color.
    colors = {color for _, color in image.getcolors(image.width * image.height)}
    assert (18, 23, 18) in colors
    assert (255, 255, 255) in colors
    empty = render_preview("Anton", 56, "   ", 320, 185)
    assert ImageChops.difference(empty, Image.new("RGB", empty.size, PREVIEW_BG)).getbbox() is None


def test_preview_normalizes_vietnamese_combining_accents():
    import unicodedata
    text = "Phông chữ phụ đề rõ nét"
    assert render_preview("Anton", 56, text).tobytes() == render_preview(
        "Anton", 56, unicodedata.normalize("NFD", text)).tobytes()


@pytest.mark.parametrize("module_name,voice", [("pipeline", "ja-JP-KeitaNeural"), ("pipeline_vi", "male")])
def test_cli_keeps_font_names_with_spaces(monkeypatch, module_name, voice):
    import importlib
    module = importlib.import_module(module_name)
    monkeypatch.setattr("sys.argv", [module_name, "--file", "video.mp4", "--voice", voice,
                                    "--subtitle-font", "Be Vietnam Pro", "--subtitle-size", "64"])
    args = module.parse_args()
    assert args.subtitle_font == "Be Vietnam Pro"
    assert args.subtitle_size == 64
