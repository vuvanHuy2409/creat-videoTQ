"""Idempotent local import. Existing JSON, media and .env files are never edited."""

import argparse
import json
from datetime import datetime
from pathlib import Path

from src.preferences import load_preferences
from src.storage import BASE_DIR, Store


def import_legacy(root=BASE_DIR, environment=None):
    root = Path(root).resolve()
    environment = load_preferences(root / ".env") if environment is None else environment
    store = Store()
    store.import_settings(environment)
    store.seed_voices(environment)
    roots = [(root / "output", None)]
    for key, language in (("OUTPUT_DIR", None), ("VIETNAMESE_OUTPUT_DIR", "vi")):
        if environment.get(key):
            path = Path(environment[key]).expanduser()
            roots.append((path if path.is_absolute() else root / path, language))
    candidates = {}
    for directory, forced_language in roots:
        if not directory.is_dir():
            continue
        for folder in directory.iterdir():
            if not folder.is_dir() or folder.name.startswith("."):
                continue
            if folder.name == "VN" and forced_language is None:
                for child in folder.iterdir():
                    if child.is_dir() and not child.name.startswith("."):
                        candidates[child.resolve()] = "vi"
            else:
                language = forced_language or ("vi" if (folder / "transcript_vi.json").exists() else "jp")
                candidates[folder.resolve()] = language
    for record in store.projects():
        path = Path(record["path"])
        if path.is_dir():
            candidates[path] = record["language"]
    imported = 0
    for folder, language in candidates.items():
        has_project_data = any((folder / filename).exists() for filename in (
            "transcript_original.json", "TRANSLATE_PENDING.txt", "dubbed_video.mp4", "report.json"))
        has_project_data = has_project_data or any(file.suffix.lower() in (".mp4", ".mkv", ".mov", ".webm") for file in folder.iterdir() if file.is_file())
        if not has_project_data:
            continue
        status = "done" if (folder / "dubbed_video.mp4").exists() else "pending" if (folder / "TRANSLATE_PENDING.txt").exists() else "asr_done"
        project_id = store.sync_project(folder, language, status, include_artifacts=True)
        store.import_report(project_id, folder / "report.json", language)
        imported += 1
    previews = store.import_previews(root / "output" / ".voice_previews")
    return {"database": str(store.path), "projects": imported, "previews_imported": previews,
            "counts": store.counts()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup", action="store_true", help="Create a checked SQLite snapshot before importing")
    args = parser.parse_args()
    environment = load_preferences()
    if args.backup:
        destination = BASE_DIR / "data" / "backups" / ("before-import-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".sqlite3")
        Store().backup(destination)
    print(json.dumps(import_legacy(environment=environment), ensure_ascii=False, indent=2))
