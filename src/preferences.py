"""Import legacy non-secret defaults and overlay SQLite preferences."""

from pathlib import Path
import os

from dotenv import dotenv_values

from src.storage import BASE_DIR, Store


def load_preferences(path=None):
    path = Path(path or BASE_DIR / ".env")
    values = {key: value or "" for key, value in dotenv_values(path).items()} if path.exists() else {}
    if values.get("STUDIO_DB_PATH"):
        os.environ.setdefault("STUDIO_DB_PATH", values["STUDIO_DB_PATH"])
    store = Store()
    store.import_settings(values)
    merged = {**values, **store.settings()}
    defaults = {"TTS_SPEED": "1.0", "TTS_OVERFLOW_MAX_SPEED": "1.4", "SUBTITLE_FONT": "Phudu",
                "SUBTITLE_FONT_SIZE": "56", "AUDIO_SAMPLE_RATE": "16000", "DEFAULT_SOURCE_LANG": "en-US",
                "OUTPUT_DIR": "./output", "LUCYLAB_API_URL": "https://api.lucylab.io/json-rpc",
                "TTS_VOICE": "ja-JP-KeitaNeural"}
    merged = {**merged, **{key: merged.get(key) or value for key, value in defaults.items()}}
    store.seed_voices(merged)
    return merged
