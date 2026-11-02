"""Tải video TikTok/Douyin/YouTube để xử lý bằng pipeline sau đó.

Hỗ trợ URL Douyin không chuẩn như:
    https://www.douyin.com/jingxuan?modal_id=7623494985249262886
    https://www.douyin.com/discover?modal_id=<id>

Các URL này được đổi thành ``https://www.douyin.com/video/<id>`` trước khi
xử lý; những website khác vẫn được chuyển thẳng cho yt-dlp.

Usage:
    python download_video.py URL                            # single URL
    python download_video.py URL1 URL2 URL3                 # multiple URLs
    python download_video.py --file urls.txt                # one URL per line
    python download_video.py URL --output-dir downloads/    # custom folder
    python download_video.py URL --cookies-from-browser chrome
                                                            # video yêu cầu đăng nhập

Sau khi tải, script ghi ``manifest_<timestamp>.json`` gồm URL, nền tảng, đường
dẫn file, tiêu đề và thời lượng của từng video.
"""
import argparse
import json
import os
import sys
from datetime import datetime

import yt_dlp

from src.downloader import normalize_url
from src.downloader_douyin import is_douyin_url, download_douyin
from src.utils import setup_logging, ensure_dir

logger = setup_logging("download_video")

DEFAULT_OUTPUT_DIR = "downloads"


def _build_ydl_opts(
    output_dir: str,
    cookies_from_browser: str | None,
    cookies_file: str | None,
) -> dict:
    """Tạo option yt-dlp, gồm cookie trình duyệt hoặc cookie file nếu có."""
    opts = {
        # Ghép extractor + ID để file TikTok/Douyin/YouTube không trùng tên.
        "outtmpl": os.path.join(output_dir, "%(extractor_key)s_%(id)s.%(ext)s"),
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "merge_output_format": "mp4",
        "quiet": False,
        "no_warnings": False,
        "noprogress": False,
    }
    if cookies_from_browser:
        opts["cookiesfrombrowser"] = (cookies_from_browser,)
    if cookies_file:
        opts["cookiefile"] = cookies_file
    return opts


def _resolve_filepath(info: dict, output_dir: str) -> str:
    """Tìm file thực tế vì yt-dlp có thể đổi tên sau khi merge stream."""
    extractor = info.get("extractor_key", info.get("extractor", "video"))
    video_id = info.get("id", "video")
    ext = info.get("ext", "mp4")

    expected = os.path.join(output_dir, f"{extractor}_{video_id}.{ext}")
    if os.path.exists(expected):
        return expected

    prefix = f"{extractor}_{video_id}"
    for f in sorted(os.listdir(output_dir)):
        if f.startswith(prefix):
            return os.path.join(output_dir, f)

    raise RuntimeError(f"Downloaded but file not found (prefix={prefix})")


def download_one(
    url: str,
    output_dir: str,
    cookies_from_browser: str | None = None,
    cookies_file: str | None = None,
) -> dict:
    """Tải một URL và trả metadata cùng đường dẫn file.

    URL Douyin được chuyển sang extractor Playwright; các site khác dùng yt-dlp.
    """
    if is_douyin_url(url):
        logger.info(f"Routing to Playwright Douyin extractor: {url}")
        return download_douyin(url, output_dir)

    canonical = normalize_url(url)
    if canonical != url:
        logger.info(f"Normalized: {url} -> {canonical}")

    ydl_opts = _build_ydl_opts(output_dir, cookies_from_browser, cookies_file)

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(canonical, download=True)

    filepath = _resolve_filepath(info, output_dir)

    return {
        "input_url": url,
        "canonical_url": canonical,
        "platform": info.get("extractor_key", info.get("extractor", "")),
        "video_id": info.get("id", ""),
        "title": info.get("title", ""),
        "uploader": info.get("uploader", ""),
        "duration": info.get("duration", 0),
        "filepath": filepath,
    }


def _read_urls_from_file(path: str) -> list[str]:
    """Đọc URL không rỗng, bỏ qua dòng bắt đầu bằng ``#``."""
    with open(path, "r", encoding="utf-8") as f:
        return [
            line.strip() for line in f
            if line.strip() and not line.startswith("#")
        ]


def main():
    """Điều phối tải nhiều URL và ghi manifest thành công/thất bại."""
    parser = argparse.ArgumentParser(
        description="Download TikTok/Douyin/YouTube videos via yt-dlp.",
    )
    parser.add_argument("urls", nargs="*", help="Video URLs (one or more)")
    parser.add_argument(
        "--file",
        help="Text file with URLs, one per line (# starts a comment)",
    )
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help=f"Output directory (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--manifest",
        help="Manifest JSON path (default: <output-dir>/manifest_<timestamp>.json)",
    )
    parser.add_argument(
        "--cookies-from-browser",
        help="Reuse cookies from a browser profile (chrome/firefox/edge/...). "
             "The browser must be CLOSED on Windows or the cookie DB is locked.",
    )
    parser.add_argument(
        "--cookies",
        help="Path to a Netscape-format cookies.txt file. Use this when the "
             "browser DB is locked: export cookies via the 'Get cookies.txt LOCALLY' "
             "extension while logged into douyin.com.",
    )
    args = parser.parse_args()

    urls = list(args.urls)
    if args.file:
        urls.extend(_read_urls_from_file(args.file))

    # Loại URL trùng nhưng giữ nguyên thứ tự người dùng nhập.
    seen = set()
    urls = [u for u in urls if not (u in seen or seen.add(u))]

    if not urls:
        parser.error("Need at least 1 URL (positional arg or --file)")

    ensure_dir(args.output_dir)

    print(f"Total: {len(urls)} URL(s) -> {args.output_dir}")
    print("=" * 60)

    results: list[dict] = []
    success = 0
    failed = 0

    for i, url in enumerate(urls, 1):
        print(f"\n[{i}/{len(urls)}] {url}")
        try:
            entry = download_one(
                url,
                args.output_dir,
                args.cookies_from_browser,
                args.cookies,
            )
            entry["status"] = "success"
            results.append(entry)
            success += 1
            print(f"  OK -> {entry['filepath']}  ({entry['duration']}s)")
        except Exception as e:
            results.append({
                "input_url": url,
                "status": "failed",
                "error": str(e)[:300],
            })
            failed += 1
            logger.error(f"Failed: {e}")
            print(f"  FAILED: {str(e)[:200]}", file=sys.stderr)

    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    manifest_path = args.manifest or os.path.join(
        args.output_dir, f"manifest_{timestamp}.json"
    )
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print()
    print("=" * 60)
    print(f"Success: {success}  |  Failed: {failed}")
    print(f"Manifest: {manifest_path}")

    if failed and not success:
        sys.exit(1)


if __name__ == "__main__":
    main()
