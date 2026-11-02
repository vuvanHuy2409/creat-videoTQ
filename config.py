"""Đọc cấu hình dùng chung cho cả giao diện desktop và CLI.

Các giá trị được lấy từ file ``.env`` tại thư mục gốc của dự án. Những khóa
bắt buộc sẽ làm chương trình dừng sớm nếu chưa được cấu hình; các tùy chọn
khác có giá trị mặc định để pipeline vẫn có thể khởi động trong môi trường
phát triển hoặc khi chạy kiểm thử.
"""
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Trang Cài đặt của desktop ghi vào đúng file .env của dự án. Dùng đường dẫn
# tuyệt đối để CLI vẫn đọc đúng cấu hình ngay cả khi được gọi từ thư mục khác.
# override=False giúp biến môi trường đã tồn tại trong process được ưu tiên.
_process_keys = set(os.environ)
_loaded_env_file = load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=False)
from src.storage import SETTING_KEYS, Store
_store = Store()
if _loaded_env_file:
    _store.import_settings({key: value for key, value in os.environ.items() if key in SETTING_KEYS})
_preferences = _store.settings()
for _key, _value in _preferences.items():
    if _key in SETTING_KEYS and _key not in _process_keys:
        os.environ[_key] = _value
_store.seed_voices(os.environ)

def _require_env(key: str) -> str:
    """Lấy một biến môi trường bắt buộc hoặc in hướng dẫn rồi thoát."""
    value = os.environ.get(key)
    if not value:
        print(f"LỖI: Chưa thiết lập biến môi trường bắt buộc '{key}'.", file=sys.stderr)
        print("Hãy sao chép .env.example thành .env và điền các API key.", file=sys.stderr)
        sys.exit(1)
    return value

# Hai thông tin này cần cho Azure ASR và TTS.
AZURE_SPEECH_KEY = _require_env("AZURE_SPEECH_KEY")
AZURE_SPEECH_REGION = _require_env("AZURE_SPEECH_REGION")

# Các thông số tùy chọn; nếu không có trong .env thì dùng giá trị mặc định.
TTS_VOICE = os.getenv("TTS_VOICE", "ja-JP-KeitaNeural")
DEFAULT_VOICE_VI = os.getenv("DEFAULT_VOICE_VI", "")
DEFAULT_VOICE_JP = os.getenv("DEFAULT_VOICE_JP", "")
from src.subtitle_fonts import DEFAULT_FONT, DEFAULT_SIZE, validate_size
SUBTITLE_FONT = os.getenv("SUBTITLE_FONT") or DEFAULT_FONT
SUBTITLE_FONT_SIZE = validate_size(os.getenv("SUBTITLE_FONT_SIZE") or DEFAULT_SIZE)
from src.subtitle_style import SubtitleStyle
_subtitle_colors = SubtitleStyle.from_settings()
SUBTITLE_TEXT_COLOR = _subtitle_colors.text_color
SUBTITLE_OUTLINE_COLOR = _subtitle_colors.outline_color
SUBTITLE_BG_COLOR = _subtitle_colors.bg_color
SUBTITLE_BG_OPACITY = _subtitle_colors.bg_opacity
SUBTITLE_BG_ENABLED = "1" if _subtitle_colors.bg_enabled else "0"
from src.tts_speed import validate_speed
TTS_SPEED = validate_speed(os.getenv("TTS_SPEED", "1.0"))
TTS_OVERFLOW_MAX_SPEED = validate_speed(os.getenv("TTS_OVERFLOW_MAX_SPEED", "1.4"))
# Legacy options remain importable for older callers; current pipelines use
# TTS_SPEED as the base and TTS_OVERFLOW_MAX_SPEED as the catch-up ceiling.
TTS_MAX_SPEED_RATIO = float(os.getenv("TTS_MAX_SPEED_RATIO", "1.3"))
DEFAULT_SOURCE_LANG = os.getenv("DEFAULT_SOURCE_LANG", "en-US")
AUDIO_SAMPLE_RATE = int(os.getenv("AUDIO_SAMPLE_RATE", "16000"))
OUTPUT_DIR = os.getenv("OUTPUT_DIR", "./output")
# Cấu hình TTS tiếng Việt (LucyLab) và danh sách khóa dự phòng.
VIETNAMESE_API_KEY = os.getenv("VIETNAMESE_API_KEY", "").strip()


def _collect_vietnamese_api_keys() -> tuple[str, ...]:
    """Thu thập khóa LucyLab chính và các khóa dự phòng theo thứ tự."""
    values = []
    for index in range(1, 11):
        name = "VIETNAMESE_API_KEY" if index == 1 else f"VIETNAMESE_API_KEY_{index}"
        value = os.getenv(name, "").strip()
        if value and value not in values:
            values.append(value)
    return tuple(values)


VIETNAMESE_API_KEYS = _collect_vietnamese_api_keys()
VIETNAMESE_VOICEID_MALE = os.getenv("VIETNAMESE_VOICEID_MALE", "")
VIETNAMESE_VOICEID_FEMALE = os.getenv("VIETNAMESE_VOICEID_FEMALE", "")
LUCYLAB_API_URL = os.getenv("LUCYLAB_API_URL", "https://api.lucylab.io/json-rpc")
VIETNAMESE_TTS_MAX_SPEED = float(os.getenv("VIETNAMESE_TTS_MAX_SPEED", "2.0"))
AZURE_VI_TTS_VOICE = os.getenv("AZURE_VI_TTS_VOICE", "").strip()
# Hệ số làm chậm audio tiếng Việt (0.82 chậm hơn 18%, 1.0 là giữ nguyên).
AUDIO_SLOW_FACTOR = float(os.getenv("AUDIO_SLOW_FACTOR", "0.82"))
VIETNAMESE_OUTPUT_DIR = os.getenv("VIETNAMESE_OUTPUT_DIR", "")
VOICE_TYPE = os.getenv("VOICE_TYPE", os.getenv("Voice_type", "")).strip().lower()

VIETNAMESE_VIDEO_URL = os.getenv("VIETNAMESE_VIDEO_URL", os.getenv("Vietnamese_video_url", ""))

VIDEO_URL = os.getenv("VIDEO_URL", "")

# Google Gemini dùng cho thumbnail và nội dung YouTube.
GOOGLE_API_KEY = os.getenv("google_api_key", os.getenv("GOOGLE_API_KEY", ""))
def _first_non_empty_env(*names: str, default: str) -> str:
    """Lấy giá trị cấu hình đầu tiên không rỗng trong danh sách biến môi trường."""
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return default


# Có thể cấu hình bằng tên biến chữ thường (tương thích cấu hình cũ) hoặc
# chữ hoa (cách GUI lưu vào .env). Gemini 2.0 Flash đã bị ngừng cung cấp.
# Giá trị rỗng trong .env không được phép che mất giá trị mặc định.
IMAGE_MODEL_ID = _first_non_empty_env(
    "image_model_id", "IMAGE_MODEL_ID", default="gemini-3.1-flash-image"
)
CONTENT_MODEL_ID = _first_non_empty_env(
    "content_model_id", "CONTENT_MODEL_ID", default="gemini-3.6-flash"
)
