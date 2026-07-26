import time
import re
from playwright.sync_api import sync_playwright

url = "https://www.douyin.com/video/7602582885844929811"

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False, args=[
        "--disable-blink-features=AutomationControlled",
        "--disable-features=IsolateOrigins,site-per-process",
    ])
    context = browser.new_context(
        user_agent=_UA,
        viewport={"width": 1920, "height": 1080},
        locale="zh-CN",
    )
    context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
    )
    page = context.new_page()

    def on_request(req):
        if "media" in req.url or "video" in req.url or "audio" in req.url or "mp4" in req.url:
            print(f"[REQ] {req.url[:100]}...")

    page.on("request", on_request)

    print(f"Loading {url}")
    page.goto(url, wait_until="domcontentloaded", timeout=15000)
    time.sleep(5)
    print("Done waiting. Checking if there was any captcha...")
    print(page.title())
    page.screenshot(path="debug_douyin.jpg")
    browser.close()
