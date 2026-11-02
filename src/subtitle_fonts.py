"""Bundled, licensed subtitle fonts shared by GUI, CLI and FFmpeg renderers."""

import os
from dataclasses import dataclass
from pathlib import Path

FONT_DIR = Path(__file__).resolve().parents[1] / "assets" / "fonts" / "ttf"
DEFAULT_FONT = "Phudu"
DEFAULT_SIZE = 56
MIN_SIZE = 12
MAX_SIZE = 160


@dataclass(frozen=True)
class SubtitleFont:
    name: str
    style: str
    description: str

    @property
    def path(self) -> Path:
        return FONT_DIR / f"{self.name.replace(' ', '')}-{self.style}.ttf"

    @property
    def family(self) -> str:
        return f"AutoDub {self.name}"

    @property
    def ass_bold(self) -> int:
        # Anton already has heavy strokes at weight 400;
        # asking libass for fake bold would make the preview and output differ.
        return 0 if self.style == "Regular" else -1


FONTS = (
    SubtitleFont("Phudu", "Black", "Chữ đậm cá tính · phụ đề nổi bật"),
    SubtitleFont("Be Vietnam Pro", "ExtraBold", "Dấu tiếng Việt rõ · kể chuyện, review"),
    SubtitleFont("Montserrat", "ExtraBold", "Hiện đại, chắc nét · vlog, thương hiệu"),
    SubtitleFont("Inter", "Bold", "Gọn và dễ đọc · chia sẻ kiến thức"),
    SubtitleFont("Roboto", "Bold", "Quen thuộc, rõ ràng · nội dung hằng ngày"),
    SubtitleFont("Roboto Condensed", "Bold", "Chữ hẹp · câu dài trên video dọc"),
    SubtitleFont("Oswald", "Bold", "Cao, đậm, gọn · hook mở đầu"),
    SubtitleFont("Barlow Condensed", "Bold", "Năng động · thể thao, chuyển động"),
    SubtitleFont("Anton", "Regular", "Khối chữ mạnh · tiêu đề ngắn"),
    SubtitleFont("Archivo", "Black", "Nét dày nổi bật · nhấn thông điệp"),
    SubtitleFont("Noto Sans", "Bold", "Trung tính, dễ đọc · phụ đề nhiều nội dung"),
    SubtitleFont("Noto Sans JP", "Bold", "Hỗ trợ tiếng Nhật · phụ đề Việt / Nhật"),
)


def font_names() -> list[str]:
    return [font.name for font in FONTS]


def get_font(name: str | None = None) -> SubtitleFont:
    name = name or os.getenv("SUBTITLE_FONT") or DEFAULT_FONT
    for font in FONTS:
        if font.name == name:
            if not font.path.is_file():
                raise FileNotFoundError(f"Thiếu phông {name}: {font.path}. Khôi phục thư mục assets/fonts.")
            return font
    raise ValueError(f"Phông phụ đề không hợp lệ: {name}. Hãy chọn một phông trong danh sách.")


def validate_size(value) -> int:
    try:
        size = int(str(value).strip())
    except (TypeError, ValueError):
        raise ValueError(f"Cỡ phụ đề phải là số nguyên từ {MIN_SIZE} đến {MAX_SIZE} px.") from None
    if not MIN_SIZE <= size <= MAX_SIZE:
        raise ValueError(f"Cỡ phụ đề phải nằm trong khoảng {MIN_SIZE}–{MAX_SIZE} px.")
    return size


def needs_cjk_fallback(text: str) -> bool:
    return any("\u3040" <= char <= "\u30ff" or "\u3400" <= char <= "\u9fff"
               or "\uff66" <= char <= "\uff9f" for char in text)
