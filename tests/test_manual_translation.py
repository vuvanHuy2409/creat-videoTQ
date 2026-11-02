"""Kiểm thử mẫu dịch thủ công và tính bất biến của timeline nguồn."""

from src.translate_pending import build_translation_template, has_complete_translation


def test_translation_template_is_incomplete_until_every_segment_is_filled():
    source = [
        {"id": 1, "text": "你好", "start": 0.0, "end": 1.0, "duration": 1.0},
        {"id": 2, "text": "谢谢", "start": 1.0, "end": 2.0, "duration": 1.0},
    ]
    template = build_translation_template(source, "text_vi")

    assert not has_complete_translation(template, "text_vi", source)

    template[0]["text_vi"] = "Xin chào"
    template[1]["text_vi"] = "Cảm ơn"
    assert has_complete_translation(template, "text_vi", source)


def test_translation_validation_rejects_changed_timeline():
    source = [
        {"id": 1, "text": "Hello", "start": 0.0, "end": 2.0, "duration": 2.0},
    ]
    translated = build_translation_template(source, "text_jp")
    translated[0]["text_jp"] = "こんにちは"
    translated[0]["start"] = 0.5

    assert not has_complete_translation(translated, "text_jp", source)
