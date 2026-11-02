"""Chạy batch lồng tiếng tiếng Việt: đọc URL từ Excel và cập nhật trạng thái.

Usage:
    python batch_run_vi.py                                  # Dùng output/video_link.xlsx mặc định
    python batch_run_vi.py --excel path/to/links.xlsx       # Chọn file Excel khác
    python batch_run_vi.py --voice male                     # Dùng voice nam, không hỏi lại
    python batch_run_vi.py --voice female                   # Dùng voice nữ, không hỏi lại
"""
import argparse
import os
import sys

from openpyxl import load_workbook

import config
from src.utils import setup_logging
from pipeline_vi import run_pipeline_vi, _ask_voice_gender, _get_default_vi_output_dir

logger = setup_logging("batch_run_vi")

DEFAULT_EXCEL = "output/video_link.xlsx"


def main():
    """Chạy tuần tự các video tiếng Việt trong Excel và lưu trạng thái."""
    parser = argparse.ArgumentParser(description="Batch Vietnamese Video Dubbing: process URLs from Excel")
    parser.add_argument(
        "--excel",
        default=DEFAULT_EXCEL,
        help=f"Path to Excel file with video links (default: {DEFAULT_EXCEL})",
    )
    parser.add_argument(
        "--source-lang",
        default=config.DEFAULT_SOURCE_LANG,
        help=f"Source language for all videos (default: {config.DEFAULT_SOURCE_LANG})",
    )
    parser.add_argument(
        "--voice",
        choices=["male", "female"],
        default=None,
        help="Voice gender: male or female (if not set, will ask interactively)",
    )
    parser.add_argument(
        "--skip-video",
        action="store_true",
        help="Skip final video merge",
    )
    parser.add_argument(
        "--output-dir",
        default=_get_default_vi_output_dir(),
        help="Output directory (default: VIETNAMESE_OUTPUT_DIR or OUTPUT_DIR/VN)",
    )
    args = parser.parse_args()

    # Chọn voice ID theo cờ CLI, cấu hình .env hoặc hỏi tương tác.
    if args.voice == "male":
        voice_id = config.VIETNAMESE_VOICEID_MALE
    elif args.voice == "female":
        voice_id = config.VIETNAMESE_VOICEID_FEMALE
    elif config.VOICE_TYPE == "male":
        voice_id = config.VIETNAMESE_VOICEID_MALE
        logger.info("Using VOICE_TYPE=male from .env")
    elif config.VOICE_TYPE == "female":
        voice_id = config.VIETNAMESE_VOICEID_FEMALE
        logger.info("Using VOICE_TYPE=female from .env")
    else:
        voice_id = _ask_voice_gender()

    # Mở workbook và dùng sheet hiện tại làm danh sách đầu vào.
    if not os.path.exists(args.excel):
        logger.error(f"Excel file not found: {args.excel}")
        sys.exit(1)

    wb = load_workbook(args.excel)
    ws = wb.active

    # Chỉ xử lý dòng có URL nhưng chưa có trạng thái.
    pending_rows = []
    for row_idx in range(2, ws.max_row + 1):
        url = ws.cell(row=row_idx, column=1).value
        status = ws.cell(row=row_idx, column=2).value
        if url and not status:
            pending_rows.append(row_idx)

    if not pending_rows:
        logger.info("No pending videos. All URLs already have a status.")
        return

    logger.info(f"Found {len(pending_rows)} pending video(s) to process (Vietnamese)")
    logger.info("=" * 60)

    success_count = 0
    fail_count = 0
    pending_count = 0

    for i, row_idx in enumerate(pending_rows):
        url = ws.cell(row=row_idx, column=1).value
        logger.info(f"[{i + 1}/{len(pending_rows)}] Processing: {url}")

        try:
            report = run_pipeline_vi(
                url=url,
                file_path=None,
                source_lang=args.source_lang,
                voice_id=voice_id,
                skip_video=args.skip_video,
                output_dir=args.output_dir,
            )

            folder_name = report["session_id"]

            if report.get("status") == "translate_pending":
                ws.cell(row=row_idx, column=2, value="TRANSLATION_PENDING")
                ws.cell(row=row_idx, column=3, value=report["work_dir"])
                pending_count += 1
                logger.info(
                    f"[{i + 1}/{len(pending_rows)}] TRANSLATION_PENDING → {report['work_dir']}"
                )
                wb.save(args.excel)
                continue

            ws.cell(row=row_idx, column=2, value="SUCCESS")
            ws.cell(row=row_idx, column=3, value=folder_name)

            success_count += 1
            logger.info(f"[{i + 1}/{len(pending_rows)}] SUCCESS → {folder_name}")

        except Exception as e:
            error_msg = str(e)[:100]

            ws.cell(row=row_idx, column=2, value=f"FAILED: {error_msg}")
            ws.cell(row=row_idx, column=3, value="")

            fail_count += 1
            logger.error(f"[{i + 1}/{len(pending_rows)}] FAILED: {error_msg}")

        # Lưu sau từng video để giữ tiến độ an toàn khi có sự cố.
        wb.save(args.excel)

    # Tổng kết kết quả của cả batch.
    logger.info("=" * 60)
    logger.info("BATCH COMPLETE (Vietnamese)")
    logger.info(f"  Total:   {len(pending_rows)}")
    logger.info(f"  Success: {success_count}")
    logger.info(f"  Translation pending: {pending_count}")
    logger.info(f"  Failed:  {fail_count}")
    logger.info(f"  Excel:   {args.excel}")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
