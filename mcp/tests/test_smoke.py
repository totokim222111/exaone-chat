import subprocess
import sys
from pathlib import Path


def test_stdio_smoke():
    script = Path(__file__).with_name("smoke_client.py")
    result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True,
                            encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS:" in result.stdout
