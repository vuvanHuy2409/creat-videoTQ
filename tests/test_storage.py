"""SQLite durability, legacy import, voice references and translation recovery."""

import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from src.storage import Store, SCHEMA_VERSION
from src.preferences import load_preferences
from src.voice_registry import default_reference, resolve, pipeline_voice
from src.legacy_import import import_legacy
from src.pipeline_history import track_pipeline, bind_project


def legacy_project(root):
    folder = root / "output" / "VN" / "20261022_vi"
    folder.mkdir(parents=True)
    original = [{"id": 1, "start": 0.0, "end": 1.0, "duration": 1.0, "text": "Hello"}]
    translated = [{**original[0], "text_vi": "Xin chào"}]
    (folder / "transcript_original.json").write_text(json.dumps(original), encoding="utf-8")
    (folder / "transcript_vi.json").write_text(json.dumps(translated), encoding="utf-8")
    (folder / "TRANSLATE_PENDING.txt").write_text("manual translation", encoding="utf-8")
    (folder / "source.mp4").write_bytes(b"media stays on disk")
    return folder, original, translated


def test_schema_wal_foreign_keys_and_future_version(tmp_path):
    store = Store()
    with store.connect() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("INSERT INTO run_events(run_id,level,message,created_at) VALUES ('missing','INFO','test','now')")
    future = tmp_path / "future.sqlite3"
    with sqlite3.connect(future) as connection:
        connection.execute("PRAGMA user_version=999")
    with pytest.raises(RuntimeError, match="mới hơn"):
        Store(future)


def test_import_preferences_excludes_secrets_and_does_not_replace_sqlite_choice(tmp_path):
    env = tmp_path / ".env"
    env.write_text("TTS_SPEED=1.1\nVOICE_TYPE=female\nAZURE_SPEECH_KEY=private-secret\nGOOGLE_API_KEY=another-secret\n")
    first = load_preferences(env)
    assert first["AZURE_SPEECH_KEY"] == "private-secret"
    assert Store().settings()["TTS_SPEED"] == "1.1"
    assert "AZURE_SPEECH_KEY" not in Store().settings()
    assert "GOOGLE_API_KEY" not in Store().settings()
    Store().save_settings({"TTS_SPEED": "1.25", "AZURE_SPEECH_KEY": "do-not-store"})
    assert load_preferences(env)["TTS_SPEED"] == "1.25"
    assert "do-not-store" not in Store().path.read_bytes().decode("utf-8", errors="ignore")


def test_voice_create_update_duplicate_validation_and_archive():
    store = Store()
    store.seed_voices({"VIETNAMESE_VOICEID_MALE": "male-id", "VIETNAMESE_VOICEID_FEMALE": "female-id"})
    ref = store.save_voice("Giọng kể của tôi", "vi", "lucylab", "custom-id", "female", "Review")
    store.save_settings({"DEFAULT_VOICE_VI": ref})
    assert default_reference("vi", store.settings()) == ref
    assert resolve("vi", ref, {})["provider_voice_id"] == "custom-id"
    assert pipeline_voice("vi", ref, {}) == "custom-id"
    store.save_voice("Giọng mới", "vi", "lucylab", "updated-id", reference=ref)
    assert pipeline_voice("vi", ref, {}) == "updated-id"
    with pytest.raises(ValueError, match="đã có"):
        store.save_voice("Tên khác", "vi", "lucylab", "updated-id")
    with pytest.raises(ValueError):
        store.save_voice("Sai ngôn ngữ", "jp", "lucylab", "another-id")
    with pytest.raises(ValueError):
        store.save_voice("Sai Azure", "vi", "azure", "ja-JP-KeitaNeural")
    store.archive_voice(ref)
    assert ref not in [voice["id"] for voice in store.voices("vi")]
    assert store.settings().get("DEFAULT_VOICE_VI") != ref
    store.import_settings({"DEFAULT_VOICE_VI": ref})
    assert store.settings().get("DEFAULT_VOICE_VI") != ref
    with pytest.raises(ValueError):
        resolve("vi", ref, {})
    store.save_voice("Khôi phục", "vi", "lucylab", "updated-id", reference=ref)
    assert store.voice(ref)["enabled"]


def test_parameterized_voice_name_cannot_execute_sql():
    store = Store()
    name = "Robert'); DROP TABLE voices;--"
    ref = store.save_voice(name, "vi", "lucylab", "safe-provider-id")
    assert store.voice(ref)["name"] == name
    assert store.counts()["voices"] == 1


def test_import_is_idempotent_preserves_files_and_manual_draft(tmp_path):
    folder, original, translated = legacy_project(tmp_path)
    before = {file.name: file.read_bytes() for file in folder.iterdir()}
    result = import_legacy(tmp_path, environment={"TTS_SPEED": "1.0"})
    assert result["projects"] == 1
    store = Store()
    project_id = store.projects()[0]["id"]
    assert store.draft(project_id)["valid"]
    assert store.counts()["transcript_segments"] == 1
    assert store.counts()["artifacts"] == 3
    counts = store.counts()
    import_legacy(tmp_path, environment={})
    assert store.counts() == counts
    partial = '[{"id": 1, "text_vi": "Bản đang gõ'
    assert store.save_draft(project_id, partial, "vi") is False
    changed = [{**translated[0], "text_vi": "Sửa bên ngoài"}]
    (folder / "transcript_vi.json").write_text(json.dumps(changed))
    store.sync_project(folder, "vi")
    assert store.draft(project_id)["content"] == partial
    assert any("Sửa bên ngoài" in row["content"] or "\\u1eeda" in row["content"] for row in store.revisions(project_id))
    assert (folder / "transcript_original.json").read_bytes() == before["transcript_original.json"]
    assert (folder / "source.mp4").read_bytes() == before["source.mp4"]
    assert (folder / "TRANSLATE_PENDING.txt").read_bytes() == before["TRANSLATE_PENDING.txt"]
    store.save_draft(project_id, "", "vi")
    store.sync_project(folder, "vi")
    assert store.draft(project_id)["content"] == ""


def test_valid_draft_stores_translations_but_changed_timeline_is_incomplete(tmp_path):
    folder, original, translated = legacy_project(tmp_path)
    store = Store()
    project_id = store.sync_project(folder, "vi")
    assert store.save_draft(project_id, json.dumps(translated), "vi")
    with store.connect() as connection:
        assert connection.execute("SELECT translated_text FROM transcript_segments WHERE project_id=?", (project_id,)).fetchone()[0] == "Xin chào"
    translated[0]["start"] = 99
    assert not store.save_draft(project_id, json.dumps(translated), "vi")
    assert json.loads((folder / "transcript_original.json").read_text()) == original


def test_report_history_import_is_idempotent_and_keeps_actual_runs(tmp_path):
    folder, _, _ = legacy_project(tmp_path)
    report = folder / "report.json"
    report.write_text(json.dumps({"voice_id": "male", "source_language": "en"}))
    store = Store()
    project_id = store.sync_project(folder, "vi")
    store.import_report(project_id, report, "vi")
    store.import_report(project_id, report, "vi")
    runs = store.recent_runs()
    assert len(runs) == 1
    assert runs[0]["id"].startswith("import:")
    assert runs[0]["status"] == "done"
    assert json.loads(runs[0]["settings_json"])["SELECTED_VOICE"] == "male"
    assert store.counts()["run_events"] == 1
    actual = store.begin_run("vi", {"TTS_SPEED": "1.1"})
    store.bind_run(actual, project_id)
    store.finish_run(actual, "done")
    report.write_text(json.dumps({"voice_id": "female", "source_language": "en"}))
    store.import_report(project_id, report, "vi")
    assert len(store.recent_runs()) == 2


def test_source_change_removes_obsolete_segments_and_revalidates_draft(tmp_path):
    folder, original, translated = legacy_project(tmp_path)
    store = Store()
    project_id = store.sync_project(folder, "vi")
    assert store.draft(project_id)["valid"]
    changed_source = [{**original[0], "id": 2, "text": "Goodbye"}]
    (folder / "transcript_original.json").write_text(json.dumps(changed_source))
    store.sync_project(folder, "vi")
    with store.connect() as connection:
        rows = connection.execute("SELECT segment_id,source_text FROM transcript_segments WHERE project_id=?", (project_id,)).fetchall()
    assert [tuple(row) for row in rows] == [("2", "Goodbye")]
    draft = store.draft(project_id)
    assert not draft["valid"]
    assert json.loads(draft["content"]) == translated
    corrected = [{**changed_source[0], "text_vi": "Tạm biệt"}]
    assert store.save_draft(project_id, json.dumps(corrected), "vi")
    store.sync_project(folder, "vi")
    assert store.draft(project_id)["dirty"]
    assert store.draft(project_id)["valid"]


def test_backup_copies_committed_wal_and_refuses_existing_files(tmp_path):
    store = Store()
    store.save_settings({"TTS_SPEED": "1.4"})
    store.save_voice("Voice", "vi", "lucylab", "id")
    destination = tmp_path / "backup.sqlite3"
    store.backup(destination)
    with sqlite3.connect(destination) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("SELECT value FROM app_settings WHERE key='TTS_SPEED'").fetchone()[0] == "1.4"
        assert connection.execute("SELECT COUNT(*) FROM voices").fetchone()[0] == 1
    before = destination.read_bytes()
    with pytest.raises(ValueError):
        store.backup(destination)
    assert destination.read_bytes() == before
    with pytest.raises(ValueError):
        store.backup(store.path)


def test_concurrent_ui_and_worker_connections():
    Store().seed_voices({})
    def write(index):
        store = Store()
        store.save_voice(f"Voice {index}", "vi", "lucylab", f"id-{index}")
        store.save_settings({"TTS_SPEED": str(1 + index / 100)})
        return len(store.voices())
    with ThreadPoolExecutor(max_workers=6) as executor:
        results = list(executor.map(write, range(18)))
    assert all(result >= 8 for result in results)
    assert Store().counts()["voices"] == 26


def test_run_history_tracks_stages_settings_and_errors_without_credentials(monkeypatch, tmp_path):
    import logging
    monkeypatch.setenv("SOME_API_KEY", "very-private-key")
    folder, _, _ = legacy_project(tmp_path)
    @track_pipeline("vi")
    def succeed(resume_dir=None, voice_id="my-voice", burn_subtitles=True):
        bind_project(folder, "vi")
        logger = logging.getLogger("test.pipeline.history")
        logger.setLevel(logging.INFO)
        logger.info("STEP 5: TTS")
        return {"output_dir": str(folder)}
    succeed()
    @track_pipeline("vi")
    def fail(resume_dir=None):
        raise RuntimeError("bad very-private-key")
    with pytest.raises(RuntimeError):
        fail(resume_dir=str(folder))
    store = Store()
    runs = store.recent_runs()
    assert {run["status"] for run in runs} == {"done", "failed"}
    failed = next(run for run in runs if run["status"] == "failed")
    assert "very-private-key" not in failed["error"]
    complete = next(run for run in runs if run["status"] == "done")
    assert json.loads(complete["settings_json"])["SELECTED_VOICE"] == "my-voice"
    assert "AZURE_SPEECH_KEY" not in json.loads(complete["settings_json"])
    with store.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM run_events WHERE run_id=?", (complete["id"],)).fetchone()[0] == 1


def test_artifact_metadata_is_valid_json_with_secret_fields_removed(monkeypatch, tmp_path):
    monkeypatch.setenv("SOME_API_KEY", "a-real-secret")
    store = Store()
    project = store.project(tmp_path, "vi")
    file = tmp_path / "tts_config.json"
    file.write_text("{}")
    store.artifact(project, file, "json", {"api_key": "a-real-secret", "details": "x" * 6000,
                   "nested": {"api_key_fingerprint": "hash", "token": "secret"}})
    with store.connect() as connection:
        metadata = json.loads(connection.execute("SELECT metadata_json FROM artifacts").fetchone()[0])
    assert "api_key" not in metadata
    assert metadata["nested"] == {"api_key_fingerprint": "hash"}
    assert isinstance(metadata["details"], str)
