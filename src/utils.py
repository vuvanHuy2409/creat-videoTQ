"""Các hàm tiện ích nhỏ được dùng lại trong nhiều module của pipeline."""
import os
import logging


def setup_logging(name: str, level: int = logging.INFO) -> logging.Logger:
    """Tạo logger có định dạng thống nhất và không gắn handler trùng lặp."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        logger.setLevel(level)
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("[%(asctime)s] %(name)s - %(levelname)s - %(message)s")
        )
        logger.addHandler(handler)
    return logger


def ensure_dir(path: str) -> str:
    """Tạo thư mục nếu chưa tồn tại và trả lại chính đường dẫn đó."""
    os.makedirs(path, exist_ok=True)
    return path


def format_timestamp(seconds: float) -> str:
    """Đổi số giây sang định dạng timestamp chuẩn của file SRT."""
    # Tách lần lượt giờ, phút, giây và mili-giây để SRT đọc được chính xác.
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"
