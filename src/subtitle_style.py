"""Validated subtitle colours shared by preview, projects and video renderers."""

import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from src.subtitle_fonts import DEFAULT_FONT, DEFAULT_SIZE, get_font, validate_size

STYLE_DEFAULTS = {
    "SUBTITLE_TEXT_COLOR": "#FFFFFF",
    "SUBTITLE_OUTLINE_COLOR": "#000000",
    "SUBTITLE_BG_COLOR": "#0C100C",
    "SUBTITLE_BG_OPACITY": "84",
    "SUBTITLE_BG_ENABLED": "1",
}
SUBTITLE_DEFAULTS = {"SUBTITLE_FONT": DEFAULT_FONT, "SUBTITLE_FONT_SIZE": str(DEFAULT_SIZE),
                     **STYLE_DEFAULTS}
PROJECT_FILE = "subtitle_style.json"

# Named swatches shared by the project picker and default settings panel.
SUBTITLE_COLOR_PRESETS = (
    ("Trắng", "#FFFFFF"),
    ("Đen", "#000000"),
    ("Xám", "#9CA3AF"),
    ("Vàng", "#FFD400"),
    ("Vàng nhạt", "#FFF3B0"),
    ("Cam", "#FF8A00"),
    ("Đỏ", "#FF3B30"),
    ("Hồng", "#FF69B4"),
    ("Hồng tím", "#E040FB"),
    ("Tím", "#8B5CF6"),
    ("Xanh dương", "#3B82F6"),
    ("Xanh da trời", "#38BDF8"),
    ("Xanh ngọc", "#2DD4BF"),
    ("Xanh lá", "#22C55E"),
    ("Xanh chanh", "#B6FF00"),
)


def validate_color(value):
    if not isinstance(value, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", value.strip()):
        raise ValueError("Màu phụ đề phải có dạng #RRGGBB, ví dụ #FFFFFF.")
    return value.strip().upper()


def validate_opacity(value):
    try:
        opacity = int(str(value).strip())
    except (ValueError, TypeError):
        raise ValueError("Độ đậm nền phải là số nguyên từ 0 đến 100%.") from None
    if not 0 <= opacity <= 100:
        raise ValueError("Độ đậm nền phải nằm trong khoảng 0–100%.")
    return opacity


@dataclass(frozen=True)
class SubtitleStyle:
    text_color: str = "#FFFFFF"
    outline_color: str = "#000000"
    bg_color: str = "#0C100C"
    bg_opacity: int = 84
    bg_enabled: bool = True

    def __post_init__(self):
        for field in ("text_color", "outline_color", "bg_color"):
            object.__setattr__(self, field, validate_color(getattr(self, field)))
        object.__setattr__(self, "bg_opacity", validate_opacity(self.bg_opacity))
        if not isinstance(self.bg_enabled, bool):
            raise ValueError("Trạng thái nền phụ đề phải là bật hoặc tắt.")

    @classmethod
    def from_settings(cls, values=None):
        values = os.environ if values is None else values
        data = {key: values.get(key, default) for key, default in STYLE_DEFAULTS.items()}
        enabled = str(data["SUBTITLE_BG_ENABLED"]).lower()
        if enabled not in ("0", "1", "true", "false"):
            raise ValueError("Trạng thái nền phụ đề phải là 1 (bật) hoặc 0 (tắt).")
        return cls(data["SUBTITLE_TEXT_COLOR"], data["SUBTITLE_OUTLINE_COLOR"],
                   data["SUBTITLE_BG_COLOR"], data["SUBTITLE_BG_OPACITY"], enabled in ("1", "true"))

    def to_settings(self):
        return dict(zip(STYLE_DEFAULTS, (self.text_color, self.outline_color, self.bg_color,
                                        str(self.bg_opacity), "1" if self.bg_enabled else "0")))

    def cli_args(self):
        result = []
        for key, value in self.to_settings().items():
            result.extend(["--" + key.lower().replace("_", "-"), value])
        return result


def validate_subtitle_settings(values):
    return {"SUBTITLE_FONT": get_font(values.get("SUBTITLE_FONT") or DEFAULT_FONT).name,
            "SUBTITLE_FONT_SIZE": str(validate_size(values.get("SUBTITLE_FONT_SIZE") or DEFAULT_SIZE)),
            **SubtitleStyle.from_settings(values).to_settings()}


def load_project_subtitles(work_dir, defaults=None):
    values = {**SUBTITLE_DEFAULTS, **(defaults if defaults is not None else os.environ)}
    path = Path(work_dir) / PROJECT_FILE
    if path.exists():
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(saved, dict):
                raise ValueError("Cấu hình phải là một đối tượng JSON.")
            values.update({key: saved[key] for key in SUBTITLE_DEFAULTS if key in saved})
        except (ValueError, OSError) as error:
            raise ValueError(f"Không đọc được cấu hình phụ đề của dự án: {error}") from error
    return validate_subtitle_settings(values)


def save_project_subtitles(work_dir, values):
    """Save only subtitle options, atomically; never persist credentials."""
    data = validate_subtitle_settings(values)
    path = Path(work_dir) / PROJECT_FILE
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix=".subtitle-", suffix=".tmp", delete=False) as handle:
        temporary = Path(handle.name)
        json.dump({"version": 1, **data}, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return data


def add_style_arguments(parser):
    for key in STYLE_DEFAULTS:
        kwargs = {"default": None}
        if key.endswith("COLOR"):
            kwargs["type"] = validate_color
        elif key.endswith("OPACITY"):
            kwargs["type"] = validate_opacity
        else:
            kwargs["choices"] = ("0", "1")
        parser.add_argument("--" + key.lower().replace("_", "-"), **kwargs)


def style_overrides_from_args(args):
    return {key: getattr(args, key.lower()) for key in STYLE_DEFAULTS
            if getattr(args, key.lower(), None) is not None}
