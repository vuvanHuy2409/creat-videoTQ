"""Tiện ích cho điểm dừng dịch thủ công trong pipeline.

Pipeline cố ý dừng trước bước dịch để không gọi API dịch có tính phí. Người
dùng chỉnh JSON sinh ra trong desktop hoặc trình soạn thảo rồi chạy tiếp khi
mọi phân đoạn đã sẵn sàng.
"""
import os


def has_complete_translation(
    translated_segments: object,
    field: str,
    source_segments: list[dict] | None = None,
) -> bool:
    """Kiểm tra transcript dịch đã an toàn để gửi sang TTS hay chưa.

    Mọi segment phải có text đích không rỗng. Nếu có transcript gốc, số lượng,
    ID và các trường timeline cũng phải giữ nguyên để người dùng không vô tình
    làm lệch thời điểm phát.
    """
    if not isinstance(translated_segments, list) or not translated_segments:
        return False
    if source_segments is not None:
        if not isinstance(source_segments, list):
            return False
        if len(translated_segments) != len(source_segments):
            return False
        if not all(isinstance(source, dict) for source in source_segments):
            return False

    for index, segment in enumerate(translated_segments):
        if not isinstance(segment, dict):
            return False
        value = segment.get(field)
        if not isinstance(value, str) or not value.strip():
            return False
        if source_segments is not None:
            source = source_segments[index]
            if segment.get("id") != source.get("id"):
                return False
            # Các trường này quyết định timeline TTS nên không được sửa khi
            # người dùng chỉ thực hiện thao tác dịch.
            for key in ("text", "start", "end", "duration"):
                if segment.get(key) != source.get(key):
                    return False
    return True


def build_translation_template(segments: list[dict], field: str) -> list[dict]:
    """Sao chép segment gốc và thêm trường dịch đích đang để trống."""
    template = []
    for segment in segments:
        item = dict(segment)
        item[field] = ""
        template.append(item)
    return template


def write_hint(work_dir: str, target_lang: str, source_lang: str) -> str:
    """Tạo ``TRANSLATE_PENDING.txt`` hướng dẫn dịch rồi trả về đường dẫn."""
    is_vi = target_lang == "vi-VN"
    target_name = "Vietnamese" if is_vi else "Japanese"
    out_field = "text_vi" if is_vi else "text_jp"
    out_file = "transcript_vi.json" if is_vi else "transcript_jp.json"
    resume_script = "pipeline_vi.py" if is_vi else "pipeline.py"
    pace = ("Vietnamese: ~12 chars/sec normal, ~15 chars/sec max at 1.3x"
            if is_vi
            else "Japanese: ~7-8 chars/sec normal, ~10 chars/sec max at 1.3x")

    if is_vi:
        style_rules = """- Bạn / mình / các bạn (never mày/tao, never ông/bà).
- Casual YouTube-creator tone, direct, skip filler.
- Chinese-origin character names → pinyin (e.g. 方太谢 → Fang Tai-xie).
- Korean drama names → Korean Romanization the VN audience knows
  (e.g. 朱志勋 → Joo Ji-hoon, 河智苑 → Ha Ji-won).
- Brand names stay original (Mercedes, Samsung). Sino-Vietnamese only when
  everyday ("kiểm sát viên" OK, but "tự tử" not "tự sát" in casual tone).
- Drop Chinese discourse particles (啊/呢/嘛/吧).
- Bleeped/censored segments (text contains only "**" or punctuation): use a
  short Vietnamese exclamation like "Hả." or "Á." — DO NOT output "..." or
  empty string (the TTS service rejects pure-punctuation text)."""
    else:
        style_rules = """- Casual plain form (だ/である体), NOT polite (です/ます体).
- タメ口, short and punchy, drop unnecessary particles.
- For Chinese sources: use Japanese readings/meanings of shared 漢字, not
  Chinese. Watch false friends (手紙 JP=letter / ZH=toilet paper).
- Bleeped segments (text contains only "**"): use a short exclamation
  like "あー" or "うっ" — DO NOT output "..." or empty string."""

    hint_path = os.path.join(work_dir, "TRANSLATE_PENDING.txt")
    with open(hint_path, "w", encoding="utf-8") as f:
        f.write(
            f"""TRANSLATION STEP PENDING — MANUAL MODE
========================================

Ngôn ngữ nguồn : {source_lang}
Ngôn ngữ đích   : {target_lang} ({target_name})
Thư mục dự án   : {work_dir}

Hệ thống đã dừng trước bước dịch để không gọi API dịch tự động.

1. Mở transcript_original.json trong thư mục này.
2. Sao chép thành {out_file}.
3. Giữ nguyên id, text, start, end, duration và thứ tự các phân đoạn.
4. Điền bản dịch thủ công vào trường "{out_field}" của từng phân đoạn.
   Bản dịch nên ngắn gọn để vừa thời lượng TTS của phân đoạn.
5. Lưu file UTF-8 rồi chạy tiếp:

    python {resume_script} --resume "{work_dir}" --file <original_video.mp4>

Pipeline chỉ tiếp tục khi mọi phân đoạn đều có trường "{out_field}" không rỗng.

Gợi ý phong cách:
{style_rules}

Tốc độ nói tham khảo: {pace}.
"""
        )
    return hint_path
