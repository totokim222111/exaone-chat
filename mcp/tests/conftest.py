import os
import socket
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch, tmp_path):
    for name in tuple(os.environ):
        if name.startswith("EXAONE_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("EXAONE_SETTINGS", str(tmp_path / "absent-settings.json"))
    # Every test has an explicit synthetic credential. Never open the user's settings.
    monkeypatch.setenv("EXAONE_BASE_URL", "http://127.0.0.1:18799")
    original_connect = socket.socket.connect

    def local_connect(sock, address):
        if isinstance(address, tuple) and address[0] not in {"127.0.0.1", "::1", "localhost"}:
            raise AssertionError("Tests prohibit non-loopback network calls")
        return original_connect(sock, address)

    monkeypatch.setattr(socket.socket, "connect", local_connect)
