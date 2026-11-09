import pytest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from key_rotation import POOL


@pytest.fixture(autouse=True)
def isolated_key_cooling(monkeypatch, tmp_path):
    import os
    for name in list(os.environ):
        if name.startswith('EXAONE_'):
            monkeypatch.delenv(name)
    monkeypatch.setenv('EXAONE_SETTINGS', str(tmp_path / 'absent-settings.json'))
    monkeypatch.setenv('EXAONE_BASE_URL', 'http://127.0.0.1:18799')
    monkeypatch.delenv('SEARXNG_URL', raising=False)
    monkeypatch.delenv('TAVILY_API_KEY', raising=False)
    monkeypatch.delenv('BRAVE_API_KEY', raising=False)
    POOL.cooling.clear()
    yield
    POOL.cooling.clear()
