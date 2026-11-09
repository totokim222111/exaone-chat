"""Run with .venv/Scripts/python.exe tests/smoke_client.py.

Starts the existing Windows mock unchanged, then uses the official SDK's
stdio client against server.py. Never reads user settings or calls real APIs.
"""
import argparse
import asyncio
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from mock_bridge import legacy_bridge

ROOT = Path(__file__).resolve().parents[1]
SHARED_MOCK = ROOT.parent / "windows/tools/mock_server.py"


def result_text(result):
    return "\n".join(item.text for item in result.content if item.type == "text")


async def check_session(base_url, settings_path, *, bad_key=False):
    # Explicitly remove inherited EXAONE variables, including real credentials.
    env = {name: value for name, value in os.environ.items() if not name.startswith("EXAONE_")}
    key = "wrong-secret-5678" if bad_key else "good"
    env.update(EXAONE_API_KEY=key, EXAONE_BASE_URL=base_url, EXAONE_STYLE_HOSTS="127.0.0.1",
               EXAONE_SETTINGS=str(settings_path), EXAONE_MODEL="k-exaone_v2.5", PYTHONUTF8="1")
    params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "server.py")], env=env, cwd=str(ROOT))
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8", dir=ROOT / "tests") as errors:
        async with stdio_client(params, errlog=errors) as (read, write):
            async with ClientSession(read, write) as session:
                initialized = await session.initialize()
                assert initialized.serverInfo.name == "EXAONE"
                tools = await session.list_tools()
                assert {tool.name for tool in tools.tools} == {"ask_exaone", "exaone_status"}
                if bad_key:
                    failed = await session.call_tool("ask_exaone", {"prompt": "오류 확인"})
                    text = result_text(failed)
                    assert failed.isError is True
                    assert "401" in text and "API 키를 확인하세요" in text
                    assert key not in text
                    status = await session.call_tool("exaone_status", {})
                    text = result_text(status)
                    assert not status.isError and "연결 확인: 실패" in text and "401" in text
                    assert "키 끝 4자리: 5678" in text and key not in text
                else:
                    answer = await session.call_tool("ask_exaone", {
                        "prompt": "한국어로 짧게 답하세요.", "system": "검증용 지침",
                        "history": [{"role": "user", "content": "이전 질문"}, {"role": "assistant", "content": "이전 답변"}],
                    })
                    text = result_text(answer)
                    assert not answer.isError, text
                    assert "사용량 답변" in text and "모델: k-exaone_v2.5" in text
                    assert "추론 강도: high" in text and "1234/567" in text and "걸린 시간:" in text
                    assert "생각 과정" not in text and "사용량 생각" not in text
                    reasoning = await session.call_tool("ask_exaone", {"prompt": "생각 포함", "include_reasoning": True})
                    text = result_text(reasoning)
                    assert not reasoning.isError and "## 생각 과정" in text and "사용량 생각" in text
                    invalid = await session.call_tool("ask_exaone", {"prompt": "검증", "max_tokens": 65537})
                    assert invalid.isError is True
                    status = await session.call_tool("exaone_status", {})
                    text = result_text(status)
                    assert not status.isError and "연결 확인: 성공" in text
                    assert "키 출처: 환경변수" in text and "지연 시간:" in text
        errors.seek(0)
        stderr = errors.read()
        assert key not in stderr


async def smoke(upstream):
    with tempfile.TemporaryDirectory(dir=ROOT / "tests") as directory:
        settings = Path(directory) / "nonexistent-settings.json"
        with legacy_bridge(upstream) as (base_url, requests):
            await check_session(base_url, settings)
            await check_session(base_url, settings, bad_key=True)
            assert len(requests) == 5
            assert requests[0]["messages"][0]["role"] == "system"
            probes = [body for body in requests if body["max_tokens"] == 64]
            assert len(probes) == 2
            assert all(body["reasoning_effort"] == "low" for body in probes)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18799)
    parser.add_argument("--existing-mock", action="store_true", help="Use an already-running mock instead of launching it")
    args = parser.parse_args()
    process = None
    try:
        if not args.existing_mock:
            with socket.socket() as probe:
                if probe.connect_ex(("127.0.0.1", args.port)) == 0:
                    raise RuntimeError("Mock port is occupied; choose --port or --existing-mock explicitly")
            process = subprocess.Popen([sys.executable, str(SHARED_MOCK), "--port", str(args.port)],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            deadline = time.monotonic() + 10
            while True:
                with socket.socket() as probe:
                    if probe.connect_ex(("127.0.0.1", args.port)) == 0:
                        break
                if process.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError("Shared mock could not start")
                time.sleep(.05)
        asyncio.run(smoke(f"http://127.0.0.1:{args.port}"))
        print("PASS: initialize, tools/list (2), answer/metadata, reasoning, validation, 401 isError, status success/failure")
        print("Shared Windows mock unchanged; legacy request adaptation is confined to tests/mock_bridge.py.")
    finally:
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


if __name__ == "__main__":
    main()
