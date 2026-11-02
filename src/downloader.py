"""Tải video thông thường qua yt-dlp và chuyển Douyin sang downloader riêng."""
import os
from urllib.parse import urlparse, parse_qs

import yt_dlp
from src.utils import setup_logging, ensure_dir

logger = setup_logging("downloader")


def normalize_url(url: str) -> str:
    """Chuẩn hóa URL Douyin dạng modal về URL video mà yt-dlp hiểu được.

    Douyin có thể đặt video ID trong query ``modal_id`` thay vì đường dẫn
    ``/video/<id>``; chuyển đổi này giúp các bước sau nhận đúng video.
    """
    if not url:
        return url
    url = url.strip()
    
    # Bóc tách URL thực sự nếu người dùng dán cả đoạn văn bản (Share text từ Douyin)
    import re
    m = re.search(r"https?://[^\s]+", url)
    if m:
        url = m.group(0)

    parsed = urlparse(url)
    host = parsed.netloc.lower()

    if "douyin.com" in host:
        qs = parse_qs(parsed.query)
        modal_id = qs.get("modal_id", [None])[0]
        if modal_id and modal_id.isdigit():
            return f"https://www.douyin.com/video/{modal_id}"

    return url


def download_video(url: str, output_dir: str) -> str:
    """Tải một video và trả về đường dẫn file đã lưu trên đĩa."""
    if not url:
        raise ValueError("URL cannot be empty")

    ensure_dir(output_dir)

    # Extractor Douyin của yt-dlp cần chữ ký a_bogus nên dễ thất bại. Các URL
    # Douyin, kể cả short link v.douyin.com, được chuyển sang Playwright.
    from src.downloader_douyin import is_douyin_url, download_douyin
    if is_douyin_url(url):
        logger.info(f"Routing to Playwright Douyin extractor: {url}")
        info = download_douyin(url, output_dir)
        return info["filepath"]

    canonical = normalize_url(url)
    if canonical != url:
        logger.info(f"Normalized URL: {url} -> {canonical}")

    ydl_opts = {
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "outtmpl": os.path.join(output_dir, "%(id)s.%(ext)s"),
        "merge_output_format": "mp4",
        "quiet": False,
        "no_warnings": False,
    }

    logger.info(f"Downloading video from: {canonical}")

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(canonical, download=True)
        video_id = info.get("id", "video")
        ext = info.get("ext", "mp4")
        filepath = os.path.join(output_dir, f"{video_id}.{ext}")

        if not os.path.exists(filepath):
            for f in os.listdir(output_dir):
                if f.startswith(video_id):
                    filepath = os.path.join(output_dir, f)
                    break

    if not os.path.exists(filepath):
        raise RuntimeError(f"Download failed: file not found at {filepath}")

    logger.info(f"Downloaded: {filepath}")
    return filepath


def get_video_id(url: str) -> str:
    """Lấy ID video từ metadata của yt-dlp mà không tải file."""
    with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
        info = ydl.extract_info(url, download=False)
        return info.get("id", "video")
