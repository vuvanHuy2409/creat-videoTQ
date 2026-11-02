import os
import tempfile
import pytest

_test_store = tempfile.TemporaryDirectory(prefix="auto-dub-test-db-")
os.environ["STUDIO_DB_PATH"] = os.path.join(_test_store.name, "collection.sqlite3")


@pytest.fixture(autouse=True)
def isolated_studio_database(monkeypatch, tmp_path):
    """Tests and config collection must never write to the user's database."""
    monkeypatch.setenv("STUDIO_DB_PATH", str(tmp_path / "studio.sqlite3"))

# Đặt biến môi trường giả để có thể import config.py khi thu thập test
# mà không cần dùng thông tin xác thực thật.
os.environ.setdefault("AZURE_SPEECH_KEY", "test-key")
os.environ.setdefault("AZURE_SPEECH_REGION", "japaneast")
