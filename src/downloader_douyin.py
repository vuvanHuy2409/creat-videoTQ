"""Tải video Douyin bằng Chromium và Playwright.

Nguyên nhân: extractor Douyin của yt-dlp cần bộ chữ ký ``a_bogus`` /
``msToken`` / ``x-secsdk-web-signature`` mà yt-dlp không tự tạo được, nên
endpoint chi tiết thường trả body rỗng.

Giải pháp là mở trang video trong trình duyệt thật, bắt các request DASH tới
CDN ``media-video-*``/``media-audio-*``, tải trực tiếp bằng requests rồi mux
hai stream bằng FFmpeg.

Yêu cầu: playwright + Chromium và FFmpeg có trong PATH.
"""
import re
import subprocess
import time
import urllib.parse
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

from src.utils import setup_logging, ensure_dir

logger = setup_logging("downloader_douyin")

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
)
_REFERER = "https://www.douyin.com/"

_DASH_VIDEO_RE = re.compile(r"/media-video-")
_DASH_AUDIO_RE = re.compile(r"/media-audio-")
_CDN_HOST_RE = re.compile(r"\.(zjcdn|douyinvod|douyincdn)\.com|\.bytecdntp\.com")
_VIDEO_MIME_RE = re.compile(r"mime_type=video_mp4")
_VIDEO_ID_RE = re.compile(r"/(?:video|note)/(\d+)|modal_id=(\d+)")


def is_douyin_url(url: str) -> bool:
    """Kiểm tra URL có thuộc miền Douyin hay không."""
    if not url:
        return False
    host = urllib.parse.urlparse(url.strip()).netloc.lower()
    return host == "douyin.com" or host.endswith(".douyin.com")


def _bitrate_of(url: str) -> int:
    """Đọc bitrate ``br`` từ query để chọn stream CDN chất lượng cao hơn."""
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    try:
        return int(qs.get("br", ["0"])[0])
    except (ValueError, TypeError):
        return 0


def _extract_via_playwright(
    url: str,
    wait_seconds: float = 45.0,
    headless: bool | None = None,
) -> dict:
    """Mở trang Douyin và bắt URL CDN trực tiếp của video/audio.

    Douyin có thể phát hiện Chromium headless và không khởi động player. Vì vậy
    mặc định macOS/Windows dùng cửa sổ thật để giảm khả năng bị chặn; Linux
    dùng headless.
    """
    if headless is None:
        import platform
        headless = platform.system() == "Linux"
    launch_args = [
        "--disable-blink-features=AutomationControlled",
        "--disable-features=IsolateOrigins,site-per-process",
    ]
    
    import os
    user_data_dir = os.path.join(os.path.dirname(__file__), "..", "playwright_profile")
    
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=headless,
            viewport={"width": 1920, "height": 1080},
            locale="zh-CN",
            args=launch_args
        )
        # Ẩn cờ webdriver vì Douyin kiểm tra navigator.webdriver.
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        page = context.pages[0] if context.pages else context.new_page()

        captured = {"dash_video": [], "dash_audio": [], "progressive": []}

        def check_url_and_add(u: str):
            if not u:
                return
            if _DASH_VIDEO_RE.search(u) or ("video" in u and ("v26" in u or "v3" in u or "v9" in u or "v11" in u or "amemv" in u)):
                captured["dash_video"].append(u)
            elif _DASH_AUDIO_RE.search(u) or ("audio" in u and ("v26" in u or "v3" in u or "v9" in u or "v11" in u or "amemv" in u)):
                captured["dash_audio"].append(u)
            elif _VIDEO_MIME_RE.search(u) or (".mp4" in u and _CDN_HOST_RE.search(u)):
                captured["progressive"].append(u)

        def on_request(req):
            u = req.url
            if _CDN_HOST_RE.search(u) or ".mp4" in u or "v26-" in u or "v3-" in u:
                check_url_and_add(u)

        def on_response(res):
            u = res.url
            if "aweme/detail" in u or "iteminfo" in u:
                try:
                    data = res.json()
                    # Nếu response API có sẵn play_addr thì cũng lưu lại để dự phòng.
                    aweme = data.get("aweme_detail") or data.get("item_list", [{}])[0]
                    video = aweme.get("video", {})
                    play_addr = video.get("play_addr", {}).get("url_list", [])
                    for p_url in play_addr:
                        captured["progressive"].append(p_url)
                except Exception:
                    pass

        page.on("request", on_request)
        page.on("response", on_response)

        # Đổi sang URL video trực tiếp nếu lấy được ID, tránh trang tìm kiếm hoặc
        # popup modal không tự phát đúng video.
        m = _VIDEO_ID_RE.search(url)
        if m:
            vid = m.group(1) or m.group(2)
            if vid:
                url = f"https://www.douyin.com/video/{vid}"

        logger.info(f"Loading Douyin page: {url}")
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=15000)
        except Exception as e:
            logger.warning(f"page.goto encountered warning/timeout: {e}. Checking captured requests...")

        # Cuộn nhẹ để kích hoạt player nếu trang chưa tự tải video.
        try:
            page.evaluate("window.scrollTo(0, 200);")
        except Exception:
            pass

        deadline = time.time() + wait_seconds
        warned = False
        while time.time() < deadline:
            if captured["progressive"] or (captured["dash_video"] and captured["dash_audio"]):
                break
            if not warned and (time.time() > deadline - wait_seconds + 5):
                logger.warning("Trình duyệt đang chờ tải video. Nếu thấy yêu cầu xác minh (Captcha), vui lòng giải quyết trực tiếp trên cửa sổ trình duyệt Chromium đang mở.")
                warned = True
            page.wait_for_timeout(500)

        title = page.title() or ""
        title = re.sub(r"\s*[-–]\s*抖音\s*$", "", title).strip()
        canonical = page.url

        context.close()

    logger.info(
        f"Captured: progressive={len(captured['progressive'])} "
        f"dash_video={len(captured['dash_video'])} dash_audio={len(captured['dash_audio'])}"
    )

    m = _VIDEO_ID_RE.search(canonical)
    video_id = ""
    if m:
        video_id = m.group(1) or m.group(2) or ""

    if captured["progressive"]:
        return {
            "mode": "progressive",
            "canonical_url": canonical,
            "video_id": video_id,
            "title": title,
            "video_url": max(captured["progressive"], key=_bitrate_of),
        }

    if captured["dash_video"] and captured["dash_audio"]:
        return {
            "mode": "dash",
            "canonical_url": canonical,
            "video_id": video_id,
            "title": title,
            "video_url": max(captured["dash_video"], key=_bitrate_of),
            "audio_url": max(captured["dash_audio"], key=_bitrate_of),
        }

    raise RuntimeError(
        f"No usable video stream captured from Douyin page (canonical={canonical})"
    )


def _download_stream(url: str, dest: Path) -> int:
    """Tải stream CDN theo từng khối và trả về tổng số byte."""
    headers = {"User-Agent": _UA, "Referer": _REFERER}
    size = 0
    with requests.get(url, headers=headers, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    size += len(chunk)
    return size


def _ffmpeg_mux(video_path: Path, audio_path: Path, output_path: Path) -> None:
    """Ghép stream video và audio mà không encode lại."""
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-i", str(audio_path),
        "-c", "copy",
        str(output_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg mux failed: {proc.stderr[-500:]}")


def _ffprobe_duration(path: Path) -> float:
    """Đọc thời lượng file bằng ffprobe; lỗi thì trả 0."""
    try:
        out = subprocess.check_output(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=nokey=1:noprint_wrappers=1",
                str(path),
            ],
            text=True,
        )
        return float(out.strip())
    except Exception:
        return 0.0


def download_douyin(
    url: str,
    output_dir: str,
    filename: str | None = None,
) -> dict:
    """Tải video Douyin và trả metadata cùng cấu trúc với ``download_one``.

    Các khóa trả về gồm URL gốc, URL chuẩn hóa, nền tảng, ID, tiêu đề,
    uploader, duration và filepath.
    """
    if not url:
        raise ValueError("URL cannot be empty")
    url = url.strip()
    m = re.search(r"https?://[^\s]+", url)
    if m:
        url = m.group(0)

    # Bước 1: giải shortlink mobile để không bị chuyển sang video kế tiếp trong feed.
    if "v.douyin.com" in url:
        try:
            headers = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"}
            r = requests.get(url, headers=headers, allow_redirects=False, timeout=10)
            if r.status_code in (301, 302) and "Location" in r.headers:
                loc = r.headers["Location"]
                m2 = re.search(r"/(?:video|note|item)/(\d+)", loc)
                if m2:
                    url = f"https://www.douyin.com/video/{m2.group(1)}"
                else:
                    r2 = requests.get(loc, headers=headers, allow_redirects=True, timeout=10)
                    m3 = re.search(r"/(?:video|note|item)/(\d+)", r2.url)
                    if m3:
                        url = f"https://www.douyin.com/video/{m3.group(1)}"
        except Exception as e:
            logger.warning(f"Failed to resolve Douyin shortlink {url}: {e}")

    ensure_dir(output_dir)

    info = _extract_via_playwright(url)
    video_id = info["video_id"]
    if not video_id:
        canonical_lower = info["canonical_url"].lower()
        if "login" in canonical_lower or "discover" in canonical_lower or ("jingxuan" in canonical_lower and "modal_id" not in canonical_lower):
            raise RuntimeError(
                f"Douyin redirected the request to an explore/login page (canonical={info['canonical_url']}). "
                "This usually happens when automated access is blocked by captcha or login walls. "
                "Please download the video file manually and select it via 'Chọn file video' in the GUI."
            )
        video_id = "unknown"

    out_dir = Path(output_dir)
    name = filename or f"Douyin_{video_id}.mp4"
    final_path = out_dir / name

    if info["mode"] == "progressive":
        logger.info(f"Downloading progressive MP4 id={video_id}")
        size = _download_stream(info["video_url"], final_path)
        logger.info(f"Stream downloaded: {size:,}B")
    else:
        tmp_video = out_dir / f"_tmp_{video_id}.video.mp4"
        tmp_audio = out_dir / f"_tmp_{video_id}.audio.m4a"
        try:
            logger.info(f"Downloading DASH video stream id={video_id}")
            v_size = _download_stream(info["video_url"], tmp_video)
            logger.info(f"Downloading DASH audio stream id={video_id}")
            a_size = _download_stream(info["audio_url"], tmp_audio)
            logger.info(f"Streams downloaded: video={v_size:,}B audio={a_size:,}B")
            _ffmpeg_mux(tmp_video, tmp_audio, final_path)
        finally:
            for p in (tmp_video, tmp_audio):
                if p.exists():
                    try:
                        p.unlink()
                    except OSError:
                        pass

    duration = _ffprobe_duration(final_path)
    logger.info(f"Saved: {final_path} ({duration:.1f}s)")

    return {
        "input_url": url,
        "canonical_url": info["canonical_url"],
        "platform": "Douyin",
        "video_id": video_id,
        "title": info["title"],
        "uploader": "",
        "duration": duration,
        "filepath": str(final_path),
    }
