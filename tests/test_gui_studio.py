"""Desktop interaction regressions; no network, video processing or real .env writes."""

import tkinter as tk

import pytest

import app_gui
from studio_ui import Btn


@pytest.fixture
def studio(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("VOICE_TYPE=female\nAZURE_SPEECH_KEY=demo-secret\n", encoding="utf-8")
    monkeypatch.setattr(app_gui, "ENV_PATH", env)
    monkeypatch.setattr(app_gui, "BASE_DIR", tmp_path)
    monkeypatch.setattr(app_gui, "scan_projects", lambda: [])
    try:
        app = app_gui.App()
    except tk.TclError as error:
        pytest.skip(f"Tk desktop unavailable: {error}")
    errors = []
    app.report_callback_exception = lambda *error: errors.append(error)
    settle(app)
    yield app
    app.destroy()
    assert not errors, f"Unexpected Tk callback errors: {errors}"


def settle(app):
    app.after(140, app.quit)
    app.mainloop()


def project(tmp_path, name, status, lang="VI"):
    folder = tmp_path / name
    folder.mkdir()
    return {
        "path": folder, "name": name, "status": status, "lang": lang,
        "lang_text": "Tiếng Việt (VI)" if lang == "VI" else "Tiếng Nhật (JP)",
        "thumbnail": None, "dubbed_video": None,
    }


def test_library_filters_search_and_clear(studio, monkeypatch, tmp_path):
    projects = [project(tmp_path, "Mèo nhỏ", "done"),
                project(tmp_path, "Du lịch", "pending", "JP"),
                project(tmp_path, "Lời kể", "asr_done")]
    monkeypatch.setattr(app_gui, "scan_projects", lambda: projects)
    library = studio._pages["projects"]
    library._refresh()
    library._set_filter("pending")
    assert len(library._grid.winfo_children()) == 1
    library._search.set("không tồn tại")
    assert len(library._grid.winfo_children()) == 1  # Empty-state panel.
    library._clear_filters()
    assert len(library._grid.winfo_children()) == 3
    library._search.set("MÈO")
    assert len(library._grid.winfo_children()) == 1
    library._clear_filters()
    library._set_filter("active")
    assert len(library._grid.winfo_children()) == 1


def test_library_columns_fit_large_and_small_windows(studio, monkeypatch, tmp_path):
    projects = [project(tmp_path, f"video_{i}", "pending") for i in range(6)]
    monkeypatch.setattr(app_gui, "scan_projects", lambda: projects)
    library = studio._pages["projects"]
    library._refresh()
    for geometry, columns in (("1320x860", 3), ("1080x720", 2)):
        studio.geometry(geometry)
        settle(studio)
        assert library._columns == columns
        cards = library._grid.winfo_children()
        widths = [card.winfo_width() for card in cards[:columns]]
        assert max(widths) - min(widths) <= 1
        assert all(card.winfo_x() + card.winfo_width() <= library._grid.winfo_width()
                   for card in cards)


def test_workflow_language_and_exclusive_cleanup(studio):
    studio._show("new")
    wizard = studio._pages["new"]
    assert wizard.voice_var.get() == "female"
    wizard.target_lang_var.set("Tiếng Nhật (JP)")
    wizard._on_target_lang_change(None)
    assert wizard.voice_var.get().startswith("ja-JP-")
    wizard.target_lang_var.set("Tiếng Việt (VI)")
    wizard._on_target_lang_change(None)
    assert wizard.voice_var.get() == "female"
    wizard.ai_blur_var.set(True)
    wizard._on_ai_blur_toggle()
    assert not wizard.ocr_replace_var.get()
    wizard.ocr_replace_var.set(True)
    wizard._on_ocr_replace_toggle()
    assert not wizard.ai_blur_var.get()
    for step in range(5):
        wizard._goto(step)
        studio.update()
        assert wizard._p[step].winfo_ismapped()
        assert sum(panel.winfo_ismapped() for panel in wizard._p) == 1


def test_settings_tabs_secrets_and_autosave_before_navigation(studio):
    studio._show("settings")
    settings = studio._pages["settings"]
    for index, panel in enumerate(settings._group_panels):
        settings._select_group(index)
        studio.update()
        assert panel.winfo_ismapped()
        assert sum(group.winfo_ismapped() for group in settings._group_panels) == 1
    settings._select_group(0)
    def descendants(parent):
        for child in parent.winfo_children():
            yield child
            yield from descendants(child)
    secret = next(widget for widget in descendants(settings)
                  if isinstance(widget, tk.Entry) and widget.get() == "demo-secret")
    assert secret.cget("show")
    toggle = next(widget for widget in secret.master.winfo_children() if isinstance(widget, Btn))
    toggle.invoke()
    assert secret.cget("show") == ""
    toggle.invoke()
    assert secret.cget("show")
    settings._vars["VOICE_TYPE"].set("male")
    studio._show("new")
    assert "VOICE_TYPE=male" in app_gui.ENV_PATH.read_text(encoding="utf-8")
    assert studio._pages["new"].voice_var.get() == "male"


def test_reset_workflow_removes_only_its_scroll_binding(studio):
    library = studio._pages["projects"]
    library_binding = library._sf._wheel_bind
    for _ in range(3):
        studio.reset_new()
        studio.update()
    assert library_binding in studio.bind_all("<MouseWheel>")
    assert studio.bind_all("<MouseWheel>").count("_on_mousewheel") == 2


def test_button_keyboard_and_disabled_state(studio):
    calls = []
    button = Btn(studio, "Test", command=lambda: calls.append(True))
    button.pack()
    studio.update()
    button._keyboard_invoke(None)
    button.config(state="disabled")
    button.invoke()
    assert calls == [True]
    button.config(state="normal")
    button.invoke()
    assert calls == [True, True]
    button.destroy()


def test_voice_audition_apply_updates_default_voice_and_speed(studio):
    studio._show("new")
    studio.open_voice_preview("vi", "female")
    dialog = studio._voice_preview
    assert dialog.voice.get() == "female"
    dialog.voice.set("male")
    dialog.speed.set("1.25")
    dialog._apply()
    env = app_gui.load_env()
    assert env["VOICE_TYPE"] == "male"
    assert env["TTS_SPEED"] == "1.25"
    assert studio._pages["new"].voice_var.get() == "male"
    assert "1.25×" in studio._pages["new"]._speed_hint.cget("text")
    dialog._close()


def test_preview_invalid_sample_makes_no_api_request(studio, monkeypatch):
    import voice_preview_ui
    studio.open_voice_preview("jp", "ja-JP-NanamiNeural")
    dialog = studio._voice_preview
    monkeypatch.setattr(voice_preview_ui.subprocess, "Popen",
                        lambda *a, **kw: pytest.fail("Invalid preview contacted TTS"))
    dialog.text.delete("1.0", "end")
    dialog.text.insert("1.0", "x" * 501)
    dialog._listen()
    assert not dialog._busy
    dialog._close()


def test_invalid_speed_does_not_replace_saved_config_or_leave_settings(studio):
    studio._show("settings")
    settings = studio._pages["settings"]
    before = app_gui.ENV_PATH.read_bytes()
    settings._vars["TTS_SPEED"].set("not a speed")
    assert settings.flush_autosave() is False
    assert app_gui.ENV_PATH.read_bytes() == before
    studio._show("new")
    assert studio._cur == "settings"
    settings._vars["TTS_SPEED"].set("1.1")
    studio._show("new")
    assert studio._cur == "new"
    assert app_gui.load_env()["TTS_SPEED"] == "1.1"


def test_font_picker_persists_choice_and_updates_existing_and_new_workflows(studio):
    studio._show("new")
    studio.open_subtitle_fonts()
    dialog = studio._font_dialog
    dialog.font_var.set("Be Vietnam Pro")
    dialog.size_var.set("64")
    dialog._apply()
    wizard = studio._pages["new"]
    command = wizard._subtitle_cli_args()
    assert command[:4] == ["--subtitle-font", "Be Vietnam Pro", "--subtitle-size", "64"]
    assert command[command.index("--subtitle-bg-color") + 1] == "#0C100C"
    assert app_gui.load_env()["SUBTITLE_FONT"] == "Be Vietnam Pro"
    dialog.destroy()
    studio.reset_new()
    assert studio._pages["new"].sub_font_var.get() == "Be Vietnam Pro"
    assert studio._pages["new"].sub_size_var.get() == "64"
    studio._show("settings")
    settings = studio._pages["settings"]
    settings._select_group(4)
    settle(studio)
    assert settings._font_panel._photo is not None
    assert settings._vars["SUBTITLE_FONT"].get() == "Be Vietnam Pro"
    settings._vars["SUBTITLE_FONT"].set("Anton")
    settings._vars["SUBTITLE_FONT_SIZE"].set("72")
    assert settings.flush_autosave()
    assert studio._pages["new"].sub_font_var.get() == "Anton"
    assert studio._pages["new"].sub_size_var.get() == "72"


def test_invalid_font_size_does_not_overwrite_saved_configuration(studio):
    studio._show("settings")
    settings = studio._pages["settings"]
    before = app_gui.ENV_PATH.read_bytes()
    settings._vars["SUBTITLE_FONT_SIZE"].set("1000")
    assert settings.flush_autosave() is False
    assert app_gui.ENV_PATH.read_bytes() == before
    settings._vars["SUBTITLE_FONT_SIZE"].set("56")
    assert settings.flush_autosave()


def test_font_dialog_preview_resizes_and_keeps_actions_visible(studio):
    studio.open_subtitle_fonts()
    dialog = studio._font_dialog
    for geometry in ("760x680", "650x650"):
        dialog.geometry(geometry)
        dialog.size_var.set("160")
        settle(studio)
        panel = dialog.panel
        assert panel._photo.width() == panel.canvas.winfo_width()
        assert panel._photo.height() == panel.canvas.winfo_height()
        assert panel.size_var.get() == "160"
        viewport = dialog.scroll_canvas.master
        body = viewport.master
        buttons = next(child for child in body.winfo_children()
                       if isinstance(child, tk.Frame) and any(isinstance(w, Btn) for w in child.winfo_children()))
        assert buttons.winfo_ismapped()
        assert buttons.winfo_y() + buttons.winfo_height() <= body.winfo_height()
        assert viewport.winfo_y() + viewport.winfo_height() <= buttons.winfo_y()
        assert dialog.scroll_canvas.bbox("all")[3] >= dialog.scroll_canvas.winfo_height()
    dialog.destroy()
    settle(studio)  # Destroy cancels pending resize/variable callbacks.


def test_project_color_picker_persists_independently_and_reaches_resume_command(studio, tmp_path, monkeypatch):
    from src.subtitle_style import load_project_subtitles
    studio._show("new")
    wizard = studio._pages["new"]
    first = project(tmp_path, "Dự án đỏ", "pending")
    second = project(tmp_path, "Dự án mới", "pending")
    wizard._work_dir = first["path"]
    wizard._open_project_subtitles()
    dialog = wizard._project_font_dialog
    dialog.style_vars["SUBTITLE_BG_COLOR"].set("#FF0000")
    dialog.style_vars["SUBTITLE_OUTLINE_COLOR"].set("#00FF00")
    dialog.style_vars["SUBTITLE_BG_OPACITY"].set("75")
    dialog._apply()
    dialog.destroy()
    assert app_gui.load_env().get("SUBTITLE_BG_COLOR", "#0C100C") == "#0C100C"
    assert load_project_subtitles(first["path"])["SUBTITLE_BG_COLOR"] == "#FF0000"
    app_gui.save_env({"SUBTITLE_BG_COLOR": "#0000FF"})
    wizard._sync_subtitle_from_settings()
    assert wizard.sub_style_vars["SUBTITLE_BG_COLOR"].get() == "#FF0000"
    monkeypatch.setattr(wizard, "_prep_translate", lambda: None)
    wizard.load_project(second)
    assert wizard.sub_style_vars["SUBTITLE_BG_COLOR"].get() == "#0000FF"
    wizard.load_project(first)
    command = wizard._subtitle_cli_args()
    assert command[command.index("--subtitle-bg-color") + 1] == "#FF0000"
    assert command[command.index("--subtitle-outline-color") + 1] == "#00FF00"
    assert command[command.index("--subtitle-bg-opacity") + 1] == "75"


def test_settings_color_autosaves_and_invalid_color_cannot_replace_saved_values(studio):
    studio._show("settings")
    settings = studio._pages["settings"]
    settings._vars["SUBTITLE_BG_COLOR"].set("#FF0000")
    settings._vars["SUBTITLE_OUTLINE_COLOR"].set("#0000FF")
    assert settings.flush_autosave()
    assert app_gui.load_env()["SUBTITLE_BG_COLOR"] == "#FF0000"
    before = app_gui.ENV_PATH.read_bytes()
    settings._vars["SUBTITLE_BG_COLOR"].set("#FF")
    assert settings.flush_autosave() is False
    assert app_gui.ENV_PATH.read_bytes() == before
    settings._vars["SUBTITLE_BG_COLOR"].set("#FF0000")
    assert settings.flush_autosave()


def test_native_color_chooser_updates_preview_and_cancel_keeps_value(studio, monkeypatch):
    from subtitle_font_ui import colorchooser
    studio.open_subtitle_fonts()
    panel = studio._font_dialog.panel
    monkeypatch.setattr(colorchooser, "askcolor", lambda **kw: (None, None))
    panel._choose_color("SUBTITLE_BG_COLOR")
    assert panel.style_vars["SUBTITLE_BG_COLOR"].get() == "#0C100C"
    monkeypatch.setattr(colorchooser, "askcolor", lambda **kw: ((255, 0, 0), "#ff0000"))
    panel._choose_color("SUBTITLE_BG_COLOR")
    settle(studio)
    assert panel.style_vars["SUBTITLE_BG_COLOR"].get() == "#FF0000"
    assert panel._photo is not None
    studio._font_dialog.destroy()


def test_quick_palette_changes_only_selected_caption_component_and_previews(studio):
    from src.subtitle_style import SUBTITLE_COLOR_PRESETS
    studio.open_subtitle_fonts()
    panel = studio._font_dialog.panel
    assert len(panel.palette_buttons) == len(SUBTITLE_COLOR_PRESETS) == 15
    original_outline = panel.style_vars["SUBTITLE_OUTLINE_COLOR"].get()
    original_background = panel.style_vars["SUBTITLE_BG_COLOR"].get()
    panel.palette_buttons["#FFD400"].invoke()
    settle(studio)
    assert panel.style_vars["SUBTITLE_TEXT_COLOR"].get() == "#FFD400"
    assert panel.style_vars["SUBTITLE_OUTLINE_COLOR"].get() == original_outline
    assert panel.style_vars["SUBTITLE_BG_COLOR"].get() == original_background
    assert panel.palette_buttons["#FFD400"].cget("text") == "✓ Vàng"
    assert "Màu chữ" in panel.palette_hint.cget("text")
    first_preview = panel._photo
    panel.color_target_buttons["SUBTITLE_OUTLINE_COLOR"].invoke()
    panel.palette_buttons["#8B5CF6"].invoke()
    settle(studio)
    assert panel.style_vars["SUBTITLE_OUTLINE_COLOR"].get() == "#8B5CF6"
    assert panel.style_vars["SUBTITLE_TEXT_COLOR"].get() == "#FFD400"
    assert panel._photo is not first_preview
    assert "Màu viền chữ" in panel.palette_hint.cget("text")
    panel.style_vars["SUBTITLE_OUTLINE_COLOR"].set("#123456")
    assert "Tùy chỉnh" in panel.palette_hint.cget("text")
    assert all(not button.cget("text").startswith("✓") for button in panel.palette_buttons.values())
    panel.style_vars["SUBTITLE_OUTLINE_COLOR"].set("invalid")
    panel.palette_buttons["#000000"].invoke()
    settle(studio)
    assert panel.style_vars["SUBTITLE_OUTLINE_COLOR"].get() == "#000000"
    studio._font_dialog.destroy()


def test_project_palette_saves_exact_color_and_keeps_defaults_separate(studio, monkeypatch, tmp_path):
    from src.subtitle_style import load_project_subtitles
    first = project(tmp_path, "palette_project", "pending")
    monkeypatch.setattr(app_gui, "scan_projects", lambda: [first])
    studio._show("new")
    wizard = studio._pages["new"]
    wizard.load_project(first)
    wizard._open_project_subtitles()
    dialog = wizard._project_font_dialog
    panel = dialog.panel
    defaults = {key: app_gui.load_env().get(key) for key in panel.style_vars}
    panel.color_target_buttons["SUBTITLE_BG_COLOR"].invoke()
    panel.palette_buttons["#3B82F6"].invoke()
    panel.color_target_buttons["SUBTITLE_TEXT_COLOR"].invoke()
    panel.palette_buttons["#FFF3B0"].invoke()
    dialog._apply()
    saved = load_project_subtitles(first["path"])
    assert saved["SUBTITLE_BG_COLOR"] == "#3B82F6"
    assert saved["SUBTITLE_TEXT_COLOR"] == "#FFF3B0"
    assert {key: app_gui.load_env().get(key) for key in panel.style_vars} == defaults
    args = wizard._subtitle_cli_args()
    assert args[args.index("--subtitle-bg-color") + 1] == "#3B82F6"
    assert args[args.index("--subtitle-text-color") + 1] == "#FFF3B0"
    dialog.destroy()
    wizard._open_project_subtitles()
    panel = wizard._project_font_dialog.panel
    assert panel.palette_buttons["#FFF3B0"].cget("text") == "✓ Vàng nhạt"
    panel.color_target_buttons["SUBTITLE_BG_COLOR"].invoke()
    assert panel.palette_buttons["#3B82F6"].cget("text") == "✓ Xanh dương"
    wizard._project_font_dialog.destroy()


def test_settings_palette_autosaves_to_sqlite_and_fits_small_dialog(studio):
    from src.storage import Store
    studio._show("settings")
    settings = studio._pages["settings"]
    settings._select_group(4)
    panel = settings._font_panel
    panel.palette_buttons["#FF69B4"].invoke()
    assert settings.flush_autosave()
    assert Store().settings()["SUBTITLE_TEXT_COLOR"] == "#FF69B4"
    studio.open_subtitle_fonts()
    dialog = studio._font_dialog
    dialog.geometry("650x650")
    settle(studio)
    panel = dialog.panel
    assert panel.palette_buttons["#FF69B4"].cget("text") == "✓ Hồng"
    for button in panel.palette_buttons.values():
        assert button.winfo_x() >= 0
        assert button.winfo_x() + button.winfo_width() <= button.master.winfo_width()
        assert button._font.measure(button.cget("text")) + 8 <= button.winfo_width()
    dialog.destroy()
    settle(studio)


def test_asr_command_carries_selected_subtitle_font(studio, monkeypatch):
    from types import SimpleNamespace
    studio._show("new")
    wizard = studio._pages["new"]
    calls = []
    monkeypatch.setattr(app_gui.subprocess, "Popen", lambda cmd, **kw:
                        calls.append(cmd) or SimpleNamespace(stdout=[], wait=lambda: None, returncode=1))
    monkeypatch.setattr(app_gui.threading, "Thread", lambda target, **kw: SimpleNamespace(start=target))
    monkeypatch.setattr(wizard, "_start_poll", lambda: None)
    wizard.url_var.set("https://example.com/video")
    wizard.sub_font_var.set("Roboto Condensed")
    wizard.sub_size_var.set("64")
    wizard._start_asr()
    assert calls[0][calls[0].index("--subtitle-font") + 1] == "Roboto Condensed"
    assert calls[0][calls[0].index("--subtitle-size") + 1] == "64"


def test_resume_command_carries_font_without_changing_source_transcript(studio, monkeypatch, tmp_path):
    import json
    from types import SimpleNamespace
    studio._show("new")
    wizard = studio._pages["new"]
    original = [{"id": 1, "start": 0.0, "end": 1.0, "duration": 1.0, "text": "Hello"}]
    source = tmp_path / "transcript_original.json"
    source.write_text(json.dumps(original), encoding="utf-8")
    before = source.read_bytes()
    wizard._work_dir = tmp_path
    wizard._trans_in.insert("1.0", json.dumps([{**original[0], "text_vi": "Xin chào"}]))
    wizard.sub_font_var.set("Montserrat")
    wizard.sub_size_var.set("72")
    calls = []
    monkeypatch.setattr(app_gui.subprocess, "Popen", lambda cmd, **kw:
                        calls.append(cmd) or SimpleNamespace(stdout=[], wait=lambda: None, returncode=1))
    monkeypatch.setattr(app_gui.threading, "Thread", lambda target, **kw: SimpleNamespace(start=target))
    monkeypatch.setattr(wizard, "_start_poll", lambda: None)
    wizard._start_tts()
    assert calls[0][calls[0].index("--subtitle-font") + 1] == "Montserrat"
    assert calls[0][calls[0].index("--subtitle-size") + 1] == "72"
    assert "--resume" in calls[0]
    assert source.read_bytes() == before


def test_add_custom_voice_choose_default_and_preview_after_restart(studio):
    from src.storage import Store
    studio._show("new")
    studio.open_voice_library()
    library = studio._voice_library
    library._new()
    for key, value in {"name": "Giọng thêm mới", "language": "vi", "provider": "azure",
                       "provider_voice_id": "vi-VN-ExampleVoiceNeural", "gender": "female"}.items():
        library.variables[key].set(value)
    library._save()
    settle(studio)
    ref = library.tree.selection()[0]
    assert Store().voice(ref)["provider_voice_id"] == "vi-VN-ExampleVoiceNeural"
    assert ref in studio._pages["new"].cb_voice._choices
    library._default()
    assert studio._pages["new"].voice_var.get() == ref
    library._preview()
    assert studio._voice_preview.voice.get() == ref
    assert ref in studio._voice_preview._voice_combo._choices
    studio._voice_preview._close()
    library.destroy()
    studio.reset_new()
    assert studio._pages["new"].voice_var.get() == ref


def test_partial_translation_draft_survives_page_reset_without_file_changes(studio, tmp_path):
    import json
    from src.storage import Store
    folder = tmp_path / "job"
    folder.mkdir()
    original = [{"id": 1, "start": 0.0, "end": 1.0, "duration": 1.0, "text": "Hello"}]
    source = folder / "transcript_original.json"
    source.write_text(json.dumps(original), encoding="utf-8")
    translated = folder / "transcript_vi.json"
    translated.write_text(json.dumps([{**original[0], "text_vi": "Xin chào"}]), encoding="utf-8")
    marker = folder / "TRANSLATE_PENDING.txt"
    marker.write_text("pending")
    before = {path: path.read_bytes() for path in (source, translated, marker)}
    project = {"path": folder, "lang": "VI"}
    studio._show("new")
    wizard = studio._pages["new"]
    wizard.load_project(project)
    partial = '[{"text_vi":"Đang gõ'
    wizard._trans_in.delete("1.0", "end")
    wizard._trans_in.insert("1.0", partial)
    studio.reset_new()  # Destruction must flush even before the debounce timer.
    restored = studio._pages["new"]
    restored.load_project(project)
    assert restored._trans_in.get("1.0", "end-1c") == partial
    assert "SQLite" in restored._translation_status.cget("text")
    assert all(path.read_bytes() == content for path, content in before.items())
    project_id = Store().projects()[0]["id"]
    assert Store().draft(project_id)["dirty"]
    assert not Store().draft(project_id)["valid"]


def test_sqlite_backup_is_available_from_gui(studio, monkeypatch, tmp_path):
    import sqlite3
    import storage_ui
    from src.storage import Store
    studio.open_database()
    dialog = studio._database_dialog
    destination = tmp_path / "gui-backup.sqlite3"
    monkeypatch.setattr(storage_ui.filedialog, "asksaveasfilename", lambda **kw: str(destination))
    dialog._backup()
    assert destination.is_file()
    with sqlite3.connect(destination) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("SELECT COUNT(*) FROM voices").fetchone()[0] == Store().counts()["voices"]
    dialog.destroy()
