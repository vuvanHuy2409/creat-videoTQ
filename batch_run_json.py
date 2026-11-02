"""Chạy batch lồng tiếng tiếng Việt tuần tự từ danh sách ``list_video.json``.

Usage:
    python batch_run_json.py                                # Dùng list_video.json mặc định
    python batch_run_json.py --json path/to/list.json       # Chọn file JSON khác
    python batch_run_json.py --skip-video                   # Bỏ qua bước ghép video cuối
"""
import argparse
import json
import os
import sys
import tempfile

import config
from src.utils import setup_logging
from pipeline_vi import run_pipeline_vi, _get_default_vi_output_dir

logger = setup_logging("batch_json")

DEFAULT_JSON = "list_video.json"


def _load_list(path: str) -> list[dict]:
    """Đọc danh sách video từ JSON UTF-8."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_list(data: list[dict], path: str):
    """Lưu an toàn: ghi file tạm rồi replace file chính một lần."""
    dir_name = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(suffix=".json", dir=dir_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def main():
    """Xử lý tuần tự video có trạng thái ``waiting`` trong file JSON."""
    parser = argparse.ArgumentParser(description="Batch Vietnamese Dubbing from JSON list")
    parser.add_argument(
        "--json",
        default=DEFAULT_JSON,
        help=f"Path to JSON video list (default: {DEFAULT_JSON})",
    )
    parser.add_argument(
        "--source-lang",
        default=config.DEFAULT_SOURCE_LANG,
        help=f"Source language (default: {config.DEFAULT_SOURCE_LANG})",
    )
    parser.add_argument(
        "--skip-video",
        action="store_true",
        help="Skip final video merge",
    )
    parser.add_argument(
        "--output-dir",
        default=_get_default_vi_output_dir(),
        help="Output directory (default: ANKO Project/VN)",
    )
    args = parser.parse_args()

    if not os.path.exists(args.json):
        logger.error(f"JSON file not found: {args.json}")
        sys.exit(1)

    videos = _load_list(args.json)

    # Chỉ đưa những video đang chờ vào hàng đợi xử lý.
    pending = [v for v in videos if v.get("status") == "waiting"]
    if not pending:
        logger.info("No pending videos (all status != 'waiting').")
        return

    logger.info(f"Found {len(pending)} pending video(s) out of {len(videos)} total")
    logger.info("=" * 60)

    success_count = 0
    fail_count = 0
    pending_count = 0

    for i, video in enumerate(pending):
        vid = video["id"]
        url = video["video_url"]
        voice_type = video.get("voice_type", "male")

        # Chuyển voice_type trong JSON thành voice ID LucyLab tương ứng.
        if voice_type == "female":
            voice_id = config.VIETNAMESE_VOICEID_FEMALE
        else:
            voice_id = config.VIETNAMESE_VOICEID_MALE

        logger.info(f"[{i + 1}/{len(pending)}] ID={vid} | {url} | voice={voice_type}")

        # Ghi trạng thái sớm để lần chạy khác không nhầm là video chưa xử lý.
        video["status"] = "processing"
        _save_list(videos, args.json)

        try:
            report = run_pipeline_vi(
                url=url,
                file_path=None,
                source_lang=args.source_lang,
                voice_id=voice_id,
                skip_video=args.skip_video,
                output_dir=args.output_dir,
            )

            if report.get("status") == "translate_pending":
                video["status"] = "translation_pending"
                video["output_folder"] = report["work_dir"]
                video["translation_file"] = report.get("translation_file")
                pending_count += 1
                logger.info(
                    f"[{i + 1}/{len(pending)}] TRANSLATION_PENDING → {report['work_dir']}"
                )
                _save_list(videos, args.json)
                continue

            video["status"] = "success"
            video["output_folder"] = report["session_id"]
            video["segments"] = report["total_segments"]
            video["duration_original"] = report["total_original_duration"]
            video["duration_vi"] = report["total_tts_duration"]
            video["processing_time"] = report["processing_time_seconds"]

            success_count += 1
            logger.info(f"[{i + 1}/{len(pending)}] SUCCESS → {report['session_id']}")

        except Exception as e:
            error_msg = str(e)[:200]
            video["status"] = "failed"
            video["error"] = error_msg

            fail_count += 1
            logger.error(f"[{i + 1}/{len(pending)}] FAILED: {error_msg}")

        # Lưu ngay sau từng video để bảo toàn tiến độ batch.
        _save_list(videos, args.json)

    logger.info("=" * 60)
    logger.info("BATCH COMPLETE (Vietnamese from JSON)")
    logger.info(f"  Total:   {len(pending)}")
    logger.info(f"  Success: {success_count}")
    logger.info(f"  Translation pending: {pending_count}")
    logger.info(f"  Failed:  {fail_count}")
    logger.info(f"  JSON:    {args.json}")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
