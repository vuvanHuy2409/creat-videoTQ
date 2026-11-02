"""Kiểm thử chuyển sang API key LucyLab dự phòng khi key hiện tại lỗi."""

import src.synthesizer_vi as synthesizer_vi


def test_lucylab_rotates_to_next_configured_key(monkeypatch):
    monkeypatch.setattr(
        synthesizer_vi.config,
        "VIETNAMESE_API_KEYS",
        ("primary-key", "backup-key"),
    )
    monkeypatch.setattr(synthesizer_vi, "_LUCYLAB_KEY_INDEX", 0)

    assert synthesizer_vi._active_lucylab_api_key() == "primary-key"
    assert synthesizer_vi._is_lucylab_key_rotation_error("insufficient_credits")
    assert synthesizer_vi._rotate_lucylab_api_key() is True
    assert synthesizer_vi._active_lucylab_api_key() == "backup-key"
    assert synthesizer_vi._rotate_lucylab_api_key() is False
