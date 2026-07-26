"""Douyin downloader using a headless Chromium (Playwright).

Background: as of 2025+ yt-dlp's Douyin/TikTok extractor is broken because the
detail endpoint (`/aweme/v1/web/aweme/detail/`) requires an `a_bogus` /
`msToken` / `x-secsdk-web-signature` signature triplet that yt-dlp cannot
generate — every request returns an empty body and the extractor surfaces
"Fresh cookies (not necessarily logged in) are needed".

Workaround: load the video page in a real browser (no login needed), intercept
the DASH segment requests it issues to `*.zjcdn.com/.../media-video-*/` and
`media-audio-*/`, then download those direct CDN URLs with `requests` and mux
them with ffmpeg.

Requires: playwright + chromium (`pip install playwright && playwright install
chromium`), ffmpeg on PATH.
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
    if not url:
        return False
    host = urllib.parse.urlparse(url.strip()).netloc.lower()
    return host == "douyin.com" or host.endswith(".douyin.com")


def _bitrate_of(url: str) -> int:
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
    """Open the Douyin page and capture direct CDN URLs for video + audio.

    Douyin detects vanilla headless Chromium and refuses to start the player.
    We default to headful (headless=False) on macOS and Windows to easily bypass
    anti-bot detection, and headless on Linux.
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
            user_agent=_UA,
            viewport={"width": 1920, "height": 1080},
            locale="zh-CN",
            args=launch_args
        )
        # Hide webdriver flag — Douyin checks navigator.webdriver
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
                    # Try extracting video URLs directly from API responses if present
                    aweme = data.get("aweme_detail") or data.get("item_list", [{}])[0]
                    video = aweme.get("video", {})
                    play_addr = video.get("play_addr", {}).get("url_list", [])
                    for p_url in play_addr:
                        captured["progressive"].append(p_url)
                except Exception:
                    pass

        page.on("request", on_request)
        page.on("response", on_response)

        # Normalize URL to a direct video link if a video ID is present
        # This prevents issues with search pages or modal popups not auto-playing
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

        # Attempt to trigger video playback by clicking or scrolling if video didn't start automatically
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
    """Download a Douyin video and return metadata matching download_one() shape.

    Returned dict keys:
        input_url, canonical_url, platform, video_id, title, uploader,
        duration, filepath
    """
    if not url:
        raise ValueError("URL cannot be empty")
    url = url.strip()
    m = re.search(r"https?://[^\s]+", url)
    if m:
        url = m.group(0)

    # 1. Resolve mobile shortlinks to avoid jumping to the next video in the feed
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
