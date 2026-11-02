"""Local SQLite repository. Media stays on disk; credentials stay in .env."""

import hashlib
import json
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = 1
SETTING_KEYS = frozenset({
    "TTS_VOICE", "VOICE_TYPE", "DEFAULT_VOICE_VI", "DEFAULT_VOICE_JP",
    "TTS_SPEED", "TTS_OVERFLOW_MAX_SPEED", "SUBTITLE_FONT", "SUBTITLE_FONT_SIZE",
    "SUBTITLE_TEXT_COLOR", "SUBTITLE_OUTLINE_COLOR", "SUBTITLE_BG_COLOR",
    "SUBTITLE_BG_OPACITY", "SUBTITLE_BG_ENABLED",
    "VIETNAMESE_VOICEID_MALE", "VIETNAMESE_VOICEID_FEMALE", "AZURE_VI_TTS_VOICE",
    "AZURE_SPEECH_REGION", "LUCYLAB_API_URL", "DEFAULT_SOURCE_LANG", "AUDIO_SAMPLE_RATE",
    "OUTPUT_DIR", "VIETNAMESE_OUTPUT_DIR", "IMAGE_MODEL_ID", "CONTENT_MODEL_ID",
    "VIDEO_CRF", "VIDEO_PRESET", "AUDIO_SLOW_FACTOR", "TTS_MAX_SPEED_RATIO",
    "VIETNAMESE_TTS_MAX_SPEED",
})

SCHEMA = """
CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS voices (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, language TEXT NOT NULL CHECK(language IN ('vi','jp')),
    provider TEXT NOT NULL CHECK(provider IN ('lucylab','azure')), provider_voice_id TEXT,
    gender TEXT NOT NULL DEFAULT 'other', notes TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1, builtin INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    UNIQUE(provider,language,provider_voice_id), UNIQUE(language,name COLLATE NOCASE));
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
    language TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'running',
    source_digest TEXT, translation_digest TEXT, deleted INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS transcript_segments (
    project_id INTEGER NOT NULL REFERENCES projects(id), segment_id TEXT NOT NULL,
    start REAL NOT NULL, end REAL NOT NULL, duration REAL NOT NULL,
    source_text TEXT NOT NULL, translated_text TEXT NOT NULL DEFAULT '', source_json TEXT NOT NULL,
    updated_at TEXT NOT NULL, PRIMARY KEY(project_id,segment_id));
CREATE TABLE IF NOT EXISTS translation_drafts (
    project_id INTEGER PRIMARY KEY REFERENCES projects(id), content TEXT NOT NULL,
    digest TEXT NOT NULL, dirty INTEGER NOT NULL, valid INTEGER NOT NULL,
    updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS translation_revisions (
    id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id),
    content TEXT NOT NULL, digest TEXT NOT NULL, origin TEXT NOT NULL,
    valid INTEGER NOT NULL, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS revisions_project ON translation_revisions(project_id,id DESC);
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY, project_id INTEGER REFERENCES projects(id), language TEXT NOT NULL,
    status TEXT NOT NULL, pid INTEGER NOT NULL, settings_json TEXT NOT NULL,
    error TEXT, started_at TEXT NOT NULL, finished_at TEXT);
CREATE TABLE IF NOT EXISTS run_events (
    id INTEGER PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
    level TEXT NOT NULL, message TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS events_run ON run_events(run_id,id);
CREATE TABLE IF NOT EXISTS artifacts (
    id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id),
    path TEXT NOT NULL UNIQUE, kind TEXT NOT NULL, size_bytes INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}', updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS voice_previews (
    cache_key TEXT PRIMARY KEY, voice_reference TEXT, provider TEXT NOT NULL,
    actual_voice TEXT NOT NULL, sample_text TEXT NOT NULL, speed REAL NOT NULL,
    path TEXT NOT NULL, duration REAL NOT NULL, use_count INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL, last_used_at TEXT NOT NULL);
"""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def digest(value):
    return hashlib.sha256(value.encode("utf-8") if isinstance(value, str) else value).hexdigest()


def redact(message):
    text = str(message)
    for key, value in os.environ.items():
        if re.search(r"KEY|TOKEN|PASSWORD|SECRET", key, re.I) and len(value) >= 6:
            text = text.replace(value, "[redacted]")
    text = re.sub(r"(?i)(api[_-]?key|token|password|secret)=([^\s&]+)", r"\1=[redacted]", text)
    return text[:4000]


def safe_metadata(value):
    if isinstance(value, dict):
        return {key: safe_metadata(item) for key, item in value.items()
                if not re.search(r"key|token|password|secret", key, re.I) or "fingerprint" in key}
    if isinstance(value, list):
        return [safe_metadata(item) for item in value]
    return redact(value) if isinstance(value, str) else value


class Store:
    def __init__(self, path=None):
        self.path = Path(path or os.getenv("STUDIO_DB_PATH") or BASE_DIR / "data" / "studio.sqlite3").expanduser()
        if not self.path.is_absolute():
            self.path = BASE_DIR / self.path
        self.path = self.path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        created = not self.path.exists()
        with self.connect() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RuntimeError("SQLite đang dùng phiên bản mới hơn ứng dụng. Hãy cập nhật ứng dụng.")
            if version == 0:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.executescript("BEGIN IMMEDIATE;\n" + SCHEMA +
                                         f"\nPRAGMA user_version={SCHEMA_VERSION}; COMMIT;")
        if created:
            self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=15000")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def settings(self):
        with self.connect() as connection:
            return {row["key"]: row["value"] for row in connection.execute("SELECT key,value FROM app_settings")}

    def import_settings(self, values):
        with self.connect() as connection:
            connection.executemany("INSERT OR IGNORE INTO app_settings VALUES (?,?,?)",
                                   [(key, str(value), now()) for key, value in values.items() if key in SETTING_KEYS])

    def save_settings(self, values):
        with self.connect() as connection:
            connection.executemany("INSERT INTO app_settings VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
                                   [(key, str(value), now()) for key, value in values.items() if key in SETTING_KEYS])

    def seed_voices(self, environment):
        builtins = [
            ("male", "Giọng nam LucyLab", "vi", "lucylab", environment.get("VIETNAMESE_VOICEID_MALE") or None, "male"),
            ("female", "Giọng nữ LucyLab", "vi", "lucylab", environment.get("VIETNAMESE_VOICEID_FEMALE") or None, "female"),
            ("azure:vi-VN-NamMinhNeural", "Nam Minh · Azure", "vi", "azure", "vi-VN-NamMinhNeural", "male"),
            ("azure:vi-VN-HoaiMyNeural", "Hoài My · Azure", "vi", "azure", "vi-VN-HoaiMyNeural", "female"),
        ]
        builtins += [(name, label + " · Azure", "jp", "azure", name, gender) for name, label, gender in (
            ("ja-JP-KeitaNeural", "Keita", "male"), ("ja-JP-NanamiNeural", "Nanami", "female"),
            ("ja-JP-NaokiNeural", "Naoki", "male"), ("ja-JP-MayuNeural", "Mayu", "female"))]
        with self.connect() as connection:
            for ref, name, language, provider, provider_id, gender in builtins:
                if provider_id and connection.execute("SELECT 1 FROM voices WHERE provider=? AND language=? AND provider_voice_id=? AND id<>?",
                    (provider, language, provider_id, ref)).fetchone():
                    provider_id = None
                connection.execute("""INSERT INTO voices
                    (id,name,language,provider,provider_voice_id,gender,builtin,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,1,?,?) ON CONFLICT(id) DO UPDATE SET
                    provider_voice_id=excluded.provider_voice_id,updated_at=excluded.updated_at
                    WHERE voices.provider_voice_id IS NOT excluded.provider_voice_id""",
                    (ref, name, language, provider, provider_id, gender, now(), now()))

    def voices(self, language=None, include_disabled=False):
        sql, args = "SELECT * FROM voices WHERE 1=1", []
        if language:
            sql += " AND language=?"
            args.append(language)
        if not include_disabled:
            sql += " AND enabled=1"
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(sql + " ORDER BY builtin DESC,name COLLATE NOCASE", args)]

    def voice(self, reference):
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM voices WHERE id=?", (reference,)).fetchone()
            return dict(row) if row else None

    def save_voice(self, name, language, provider, provider_voice_id, gender="other", notes="", reference=None):
        name, provider_voice_id = name.strip(), provider_voice_id.strip()
        if not name or len(name) > 80 or not provider_voice_id or len(provider_voice_id) > 160:
            raise ValueError("Nhập tên giọng (1–80 ký tự) và Voice ID (1–160 ký tự).")
        if language not in ("vi", "jp") or provider not in ("lucylab", "azure"):
            raise ValueError("Ngôn ngữ hoặc dịch vụ giọng đọc không hợp lệ.")
        if provider == "lucylab" and language != "vi":
            raise ValueError("LucyLab hiện chỉ được hỗ trợ cho tiếng Việt.")
        if provider == "azure":
            prefix = "vi-VN-" if language == "vi" else "ja-JP-"
            if not provider_voice_id.startswith(prefix) or not re.fullmatch(r"[A-Za-z0-9-]+", provider_voice_id):
                raise ValueError(f"Voice ID Azure phải bắt đầu bằng {prefix} và không chứa khoảng trắng.")
        if gender not in ("male", "female", "other"):
            raise ValueError("Giới tính giọng đọc không hợp lệ.")
        ref = reference or "voice:" + uuid.uuid4().hex
        try:
            with self.connect() as connection:
                existing = connection.execute("SELECT builtin FROM voices WHERE id=?", (ref,)).fetchone()
                if existing and existing["builtin"]:
                    raise ValueError("Giọng có sẵn được cập nhật qua cài đặt dịch vụ. Hãy thêm một giọng mới.")
                connection.execute("""INSERT INTO voices
                    (id,name,language,provider,provider_voice_id,gender,notes,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,
                    language=excluded.language,provider=excluded.provider,provider_voice_id=excluded.provider_voice_id,
                    gender=excluded.gender,notes=excluded.notes,enabled=1,updated_at=excluded.updated_at""",
                    (ref, name, language, provider, provider_voice_id, gender, notes[:1000], now(), now()))
        except sqlite3.IntegrityError:
            raise ValueError("Tên hoặc Voice ID này đã có trong thư viện giọng đọc.") from None
        return ref

    def archive_voice(self, reference):
        voice = self.voice(reference)
        if not voice or voice["builtin"]:
            raise ValueError("Chỉ có thể ẩn giọng do bạn thêm.")
        with self.connect() as connection:
            connection.execute("UPDATE voices SET enabled=0,updated_at=? WHERE id=?", (now(), reference))
            connection.execute("UPDATE app_settings SET value='',updated_at=? WHERE key IN ('DEFAULT_VOICE_VI','DEFAULT_VOICE_JP') AND value=?", (now(), reference))

    def project(self, path, language="vi", status=None):
        path = Path(path).resolve()
        with self.connect() as connection:
            connection.execute("""INSERT INTO projects(path,name,language,status,created_at,updated_at)
                VALUES (?,?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET name=excluded.name,
                language=excluded.language,deleted=0,updated_at=excluded.updated_at""",
                (str(path), path.name, language, status or "running", now(), now()))
            row = connection.execute("SELECT * FROM projects WHERE path=?", (str(path),)).fetchone()
            if status:
                connection.execute("UPDATE projects SET status=? WHERE id=?", (status, row["id"]))
            return row["id"]

    def mark_project_deleted(self, path):
        with self.connect() as connection:
            connection.execute("UPDATE projects SET deleted=1,updated_at=? WHERE path=?", (now(), str(Path(path).resolve())))

    def projects(self):
        with self.connect() as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM projects WHERE deleted=0 ORDER BY updated_at DESC")]

    def active_project_paths(self):
        with self.connect() as connection:
            return {row[0] for row in connection.execute("SELECT p.path FROM projects p JOIN runs r ON r.project_id=p.id WHERE r.status='running'")}

    def recover_interrupted_runs(self):
        with self.connect() as connection:
            running = list(connection.execute("SELECT id,pid FROM runs WHERE status='running'"))
        for run in running:
            try:
                os.kill(run["pid"], 0)
            except ProcessLookupError:
                self.finish_run(run["id"], "interrupted", "Tiến trình đã kết thúc trước khi ghi trạng thái hoàn tất.")
            except PermissionError:
                pass

    def sync_project(self, path, language="vi", status=None, include_artifacts=False):
        path = Path(path).resolve()
        project_id = self.project(path, language, status)
        source_path = path / "transcript_original.json"
        source = None
        source_changed = False
        if source_path.exists():
            try:
                content = source_path.read_text(encoding="utf-8")
                source = json.loads(content)
                if not isinstance(source, list) or not all(isinstance(item, dict) and "id" in item for item in source):
                    source = None
                if source is not None:
                    with self.connect() as connection:
                        row = connection.execute("SELECT source_digest FROM projects WHERE id=?", (project_id,)).fetchone()
                        if row[0] != digest(content):
                            current_ids = {str(item["id"]) for item in source}
                            previous_ids = {item[0] for item in connection.execute(
                                "SELECT segment_id FROM transcript_segments WHERE project_id=?", (project_id,))}
                            connection.executemany("DELETE FROM transcript_segments WHERE project_id=? AND segment_id=?",
                                                   [(project_id, item) for item in previous_ids - current_ids])
                            for item in source:
                                connection.execute("""INSERT INTO transcript_segments
                                    (project_id,segment_id,start,end,duration,source_text,source_json,updated_at)
                                    VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(project_id,segment_id) DO UPDATE SET
                                    start=excluded.start,end=excluded.end,duration=excluded.duration,
                                    source_text=excluded.source_text,source_json=excluded.source_json,updated_at=excluded.updated_at""",
                                    (project_id, str(item["id"]), item.get("start", 0), item.get("end", 0),
                                     item.get("duration", 0), item.get("text", ""), json.dumps(item, ensure_ascii=False), now()))
                            connection.execute("UPDATE projects SET source_digest=? WHERE id=?", (digest(content), project_id))
                            source_changed = True
            except (OSError, ValueError, TypeError, sqlite3.IntegrityError):
                source = None
        translated = path / f"transcript_{language}.json"
        if translated.exists():
            try:
                raw = translated.read_text(encoding="utf-8")
                with self.connect() as connection:
                    old = connection.execute("SELECT translation_digest FROM projects WHERE id=?", (project_id,)).fetchone()[0]
                if digest(raw) != old:
                    draft = self.draft(project_id)
                    self.save_draft(project_id, raw, language, origin="file-import", dirty=False,
                                    replace_draft=not draft or not draft["dirty"])
                    with self.connect() as connection:
                        connection.execute("UPDATE projects SET translation_digest=? WHERE id=?", (digest(raw), project_id))
            except (OSError, ValueError, TypeError):
                pass
        if source_changed:
            draft = self.draft(project_id)
            if draft:
                self.save_draft(project_id, draft["content"], language, origin="source-sync", dirty=bool(draft["dirty"]))
        if include_artifacts:
            files = list(path.iterdir()) if path.exists() else []
            segments_dir = path / "segments"
            if segments_dir.is_dir():
                files += list(segments_dir.glob("*.wav"))
            for file in files:
                if file.is_file() and file.suffix.lower() in (".mp4", ".mkv", ".mov", ".wav", ".srt", ".ass", ".jpg", ".png", ".json"):
                    metadata = {}
                    if file.name in ("tts_config.json", "fit_adjustments.json", "report.json"):
                        try:
                            metadata = json.loads(file.read_text(encoding="utf-8"))
                        except (OSError, ValueError):
                            pass
                    self.artifact(project_id, file, file.suffix.lower().lstrip("."), metadata)
        return project_id

    def draft(self, project_id):
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM translation_drafts WHERE project_id=?", (project_id,)).fetchone()
            return dict(row) if row else None

    def save_draft(self, project_id, content, language, origin="editor", dirty=True, replace_draft=True):
        field = "text_vi" if language == "vi" else "text_jp"
        parsed, valid = None, False
        try:
            parsed = json.loads(content)
            with self.connect() as connection:
                source = [json.loads(row[0]) for row in connection.execute("SELECT source_json FROM transcript_segments WHERE project_id=? ORDER BY start,segment_id", (project_id,))]
            from src.translate_pending import has_complete_translation
            valid = bool(source) and has_complete_translation(parsed, field, source)
        except (ValueError, TypeError):
            pass
        fingerprint = digest(content)
        with self.connect() as connection:
            last = connection.execute("SELECT digest FROM translation_revisions WHERE project_id=? ORDER BY id DESC LIMIT 1", (project_id,)).fetchone()
            if not last or last[0] != fingerprint:
                connection.execute("INSERT INTO translation_revisions(project_id,content,digest,origin,valid,created_at) VALUES (?,?,?,?,?,?)",
                                   (project_id, content, fingerprint, origin, int(valid), now()))
            if replace_draft:
                connection.execute("""INSERT INTO translation_drafts VALUES (?,?,?,?,?,?) ON CONFLICT(project_id)
                    DO UPDATE SET content=excluded.content,digest=excluded.digest,dirty=excluded.dirty,
                    valid=excluded.valid,updated_at=excluded.updated_at""",
                    (project_id, content, fingerprint, int(dirty), int(valid), now()))
                if valid:
                    for item in parsed:
                        connection.execute("UPDATE transcript_segments SET translated_text=?,updated_at=? WHERE project_id=? AND segment_id=?",
                                           (item[field], now(), project_id, str(item["id"])))
        return valid

    def revisions(self, project_id, limit=100):
        with self.connect() as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM translation_revisions WHERE project_id=? ORDER BY id DESC LIMIT ?", (project_id, limit))]

    def artifact(self, project_id, path, kind, metadata=None):
        path = Path(path).resolve()
        try:
            stat = path.stat()
        except OSError:
            return
        # Store structured manifests, not media bytes or credential payloads.
        metadata_json = json.dumps(safe_metadata(metadata or {}), ensure_ascii=False)
        with self.connect() as connection:
            connection.execute("""INSERT INTO artifacts(project_id,path,kind,size_bytes,mtime_ns,metadata_json,updated_at)
                VALUES (?,?,?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET size_bytes=excluded.size_bytes,
                mtime_ns=excluded.mtime_ns,metadata_json=excluded.metadata_json,updated_at=excluded.updated_at""",
                (project_id, str(path), kind, stat.st_size, stat.st_mtime_ns, metadata_json, now()))

    def preview(self, cache_key):
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM voice_previews WHERE cache_key=?", (cache_key,)).fetchone()
            return dict(row) if row else None

    def save_preview(self, cache_key, reference, text, metadata, path):
        with self.connect() as connection:
            connection.execute("""INSERT INTO voice_previews
                (cache_key,voice_reference,provider,actual_voice,sample_text,speed,path,duration,created_at,last_used_at)
                VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(cache_key) DO UPDATE SET
                use_count=voice_previews.use_count+1,last_used_at=excluded.last_used_at,path=excluded.path""",
                (cache_key, reference, metadata["provider"], metadata["voice"], text, metadata["speed"],
                 str(Path(path).resolve()), metadata["duration"], now(), now()))

    def import_previews(self, directory):
        count = 0
        for manifest in Path(directory).glob("*.json"):
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
                key = data["cache_key"]
                audio = manifest.with_suffix(".wav")
                if audio.is_file() and not self.preview(key):
                    self.save_preview(key, None, "", data, audio)
                    count += 1
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return count

    def import_report(self, project_id, report_path, language):
        report_path = Path(report_path)
        try:
            content = report_path.read_text(encoding="utf-8")
            report = json.loads(content)
            if not isinstance(report, dict):
                return
            reference = "import:" + digest(str(report_path.resolve()) + content)
            timestamp = datetime.fromtimestamp(report_path.stat().st_mtime, timezone.utc).isoformat(timespec="milliseconds")
            settings = {"SELECTED_VOICE": report.get("voice_id") or report.get("voice"),
                        "SOURCE_LANGUAGE": report.get("source_language")}
            with self.connect() as connection:
                if connection.execute("SELECT 1 FROM runs WHERE project_id=? AND status='done' AND id NOT LIKE 'import:%'", (project_id,)).fetchone():
                    return
                existed = connection.execute("SELECT 1 FROM runs WHERE id=?", (reference,)).fetchone()
                if not existed:
                    connection.execute("""INSERT INTO runs(id,project_id,language,status,pid,settings_json,started_at,finished_at)
                        VALUES (?,?,?,'done',0,?,?,?)""", (reference, project_id, language, json.dumps(settings), timestamp, timestamp))
                    connection.execute("INSERT INTO run_events(run_id,level,message,created_at) VALUES (?,'INFO',?,?)",
                        (reference, "Nhập lịch sử hoàn tất từ report.json có sẵn; không chạy lại pipeline.", now()))
        except (OSError, ValueError, TypeError):
            return

    def begin_run(self, language, settings):
        ref = uuid.uuid4().hex
        with self.connect() as connection:
            connection.execute("INSERT INTO runs(id,language,status,pid,settings_json,started_at) VALUES (?,?,'running',?,?,?)",
                               (ref, language, os.getpid(), json.dumps({key: str(value) for key, value in settings.items()
                                if key in SETTING_KEYS or key in {"SELECTED_VOICE", "BURN_SUBTITLES", "OCR_REPLACE", "BG_MODE", "SOURCE_LANGUAGE"}}), now()))
        return ref

    def bind_run(self, run_id, project_id):
        with self.connect() as connection:
            connection.execute("UPDATE runs SET project_id=? WHERE id=?", (project_id, run_id))

    def finish_run(self, run_id, status, error=None):
        with self.connect() as connection:
            connection.execute("UPDATE runs SET status=?,error=?,finished_at=? WHERE id=?",
                               (status, redact(error) if error else None, now(), run_id))
            connection.execute("UPDATE projects SET status=?,updated_at=? WHERE id=(SELECT project_id FROM runs WHERE id=?)",
                               (status, now(), run_id))

    def event(self, run_id, level, message):
        with self.connect() as connection:
            connection.execute("INSERT INTO run_events(run_id,level,message,created_at) VALUES (?,?,?,?)",
                               (run_id, level, redact(message), now()))

    def recent_runs(self, limit=20):
        with self.connect() as connection:
            return [dict(row) for row in connection.execute("SELECT r.*,p.name AS project_name FROM runs r LEFT JOIN projects p ON p.id=r.project_id ORDER BY started_at DESC LIMIT ?", (limit,))]

    def counts(self):
        tables = ("voices", "app_settings", "projects", "transcript_segments", "translation_drafts",
                  "translation_revisions", "runs", "run_events", "artifacts", "voice_previews")
        with self.connect() as connection:
            return {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in tables}

    def backup(self, destination):
        destination = Path(destination).expanduser().resolve()
        if destination == self.path or destination.exists():
            raise ValueError("Chọn một tệp sao lưu mới, khác tệp SQLite đang sử dụng.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        try:
            with self.connect() as source:
                target = sqlite3.connect(destination)
                try:
                    source.backup(target)
                    if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise RuntimeError("Bản sao lưu SQLite không qua kiểm tra toàn vẹn.")
                finally:
                    target.close()
            destination.chmod(0o600)
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        return destination
