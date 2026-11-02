"""Chạy riêng bước tạo nội dung YouTube cho các video đã hoàn tất."""
import json
import os
import sys

from dotenv import load_dotenv
load_dotenv()

from src.content_generator import generate_content, generate_youtube_metadata, extract_script_text
from src.utils import setup_logging

logger = setup_logging("content_gen_batch")

OUTPUT_BASE = os.getenv("OUTPUT_DIR", "./output")
VN_DIR = os.path.join(OUTPUT_BASE, "VN")


def _first_non_empty_env(*names: str, default: str) -> str:
    """Lấy cấu hình model đầu tiên có giá trị, bỏ qua biến rỗng."""
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return default

def main():
    """Sinh lại thumbnail/metadata còn thiếu cho các video trạng thái success."""
    api_key = os.getenv("google_api_key", "")
    image_model = _first_non_empty_env(
        "image_model_id", "IMAGE_MODEL_ID", default="gemini-3.1-flash-image"
    )
    content_model = _first_non_empty_env(
        "content_model_id", "CONTENT_MODEL_ID", default="gemini-3.6-flash"
    )

    if not api_key:
        logger.error("google_api_key not set in .env")
        sys.exit(1)

    videos = json.load(open("list_video.json", encoding="utf-8"))
    success_videos = [v for v in videos if v.get("status") == "success"]

    logger.info(f"Running content generation for {len(success_videos)} videos")

    for i, v in enumerate(success_videos):
        vid = v["id"]
        url = v["video_url"]
        folder = os.path.join(VN_DIR, v["output_folder"])
        transcript_path = os.path.join(folder, "transcript_vi.json")

        logger.info(f"[{i+1}/{len(success_videos)}] ID={vid} | {url}")

        if not os.path.exists(transcript_path):
            logger.error(f"  Transcript not found: {transcript_path}")
            continue

        has_thumbnails = os.path.exists(os.path.join(folder, "thumbnail_1.png"))
        has_metadata = os.path.exists(os.path.join(folder, "youtube_metadata.json"))

        # Bỏ qua nếu thumbnail và metadata hợp lệ đã có sẵn.
        if has_thumbnails and has_metadata:
            # Kiểm tra metadata có phải giá trị fallback hay không.
            try:
                meta = json.load(open(os.path.join(folder, "youtube_metadata.json"), encoding="utf-8"))
                if meta.get("title") != "Video":
                    logger.info(f"  Already complete, skipping")
                    continue
                logger.info(f"  Has fallback metadata, re-running metadata only")
            except (json.JSONDecodeError, OSError):
                pass

        if has_thumbnails:
            logger.info(f"  Thumbnails exist, running metadata only")

        segments = json.load(open(transcript_path, encoding="utf-8"))

        try:
            if has_thumbnails:
                # Khi đã có thumbnail, chỉ cần gọi lại model văn bản.
                script_orig_path = os.path.join(folder, "script_original.txt")
                script_vi_path = os.path.join(folder, "script_vi.txt")

                if os.path.exists(script_orig_path):
                    script_original = open(script_orig_path, encoding="utf-8").read()
                else:
                    script_original = extract_script_text(segments, "text", script_orig_path)

                if os.path.exists(script_vi_path):
                    script_translated = open(script_vi_path, encoding="utf-8").read()
                else:
                    script_translated = extract_script_text(segments, "text_vi", script_vi_path)

                metadata = generate_youtube_metadata(
                    script_original=script_original,
                    script_translated=script_translated,
                    target_lang="vi-VN",
                    source_url=url,
                    api_key=api_key,
                    model_id=content_model,
                )
                # Lưu JSON và bản TXT tiện copy lên YouTube.
                meta_path = os.path.join(folder, "youtube_metadata.json")
                with open(meta_path, "w", encoding="utf-8") as f:
                    json.dump(metadata, f, ensure_ascii=False, indent=2)

                txt_path = os.path.join(folder, "youtube_post.txt")
                with open(txt_path, "w", encoding="utf-8") as f:
                    f.write(f"TITLE:\n{metadata.get('title', '')}\n\n")
                    f.write(f"DESCRIPTION:\n{metadata.get('description', '')}\n\n")
                    f.write(f"HASHTAGS:\n{' '.join(metadata.get('hashtags', []))}\n")

                logger.info(f"  Metadata regenerated: title='{metadata.get('title', '')[:50]}'")
            else:
                result = generate_content(
                    segments=segments,
                    target_lang="vi-VN",
                    source_url=url,
                    output_dir=folder,
                    api_key=api_key,
                    image_model_id=image_model,
                    content_model_id=content_model,
                )
                logger.info(f"  Thumbnails: {len(result['thumbnails'])}, Metadata: OK")
        except Exception as e:
            logger.error(f"  Failed: {e}")

    logger.info("Content generation batch complete")

if __name__ == "__main__":
    main()
