import asyncio
import base64
import ctypes
import json
import os
from ctypes import wintypes

import httpx
import pytest
from mcp.server.fastmcp.exceptions import ToolError

import server


def run(coro):
    return asyncio.run(coro)


def protect(value):
    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

    data = value.encode("utf-8")
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    incoming, outgoing = Blob(len(data), buffer), Blob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptProtectData.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.POINTER(Blob),
                                        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    assert crypt32.CryptProtectData(ctypes.byref(incoming), None, None, None, None, 1, ctypes.byref(outgoing))
    try:
        return base64.b64encode(ctypes.string_at(outgoing.pbData, outgoing.cbData)).decode()
    finally:
        kernel32.LocalFree(outgoing.pbData)


@pytest.mark.skipif(os.name != "nt", reason="Windows CurrentUser DPAPI")
def test_dpapi_settings_and_suffix(monkeypatch, tmp_path, caplog):
    key = "test-key-1234"
    encrypted = protect(key)
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"apiKey": encrypted, "baseUrl": "http://127.0.0.1:18799", "model": "saved-model"}), encoding="utf-8-sig")
    monkeypatch.setenv("EXAONE_SETTINGS", str(path))
    config = server.load_config()
    assert config.api_key == key
    assert config.key_source == "윈도우 앱 설정"
    assert key not in repr(config)

    async def fake_request(config, body):
        assert body["max_tokens"] == 64
        return server.Completion("OK", "", {}, .1)

    monkeypatch.setattr(server, "request_completion", fake_request)
    result = run(server.exaone_status())
    assert "키 끝 4자리: 1234" in result
    assert "연결 확인: 성공" in result
    assert key not in result + caplog.text
    assert encrypted not in result + caplog.text


def test_environment_priority(monkeypatch, tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"apiKey": "intentionally-invalid-base64", "baseUrl": "http://saved/v1", "model": "saved"}))
    monkeypatch.setenv("EXAONE_SETTINGS", str(path))
    monkeypatch.setenv("EXAONE_API_KEY", "environment-secret")
    monkeypatch.setenv("EXAONE_MODEL", "environment-model")
    config = server.load_config()
    assert config.api_key == "environment-secret"
    assert config.key_source == "환경변수"
    assert config.model == "environment-model"
    assert config.base_url == "http://127.0.0.1:18799"
    assert config.warning is None  # Invalid encrypted key was never touched.
    monkeypatch.delenv("EXAONE_BASE_URL")
    monkeypatch.delenv("EXAONE_MODEL")
    config = server.load_config()
    assert (config.base_url, config.model) == ("http://saved/v1", "saved")


def test_defaults_and_absent_key(monkeypatch):
    monkeypatch.delenv("EXAONE_BASE_URL")
    config = server.load_config()
    assert config.base_url == server.DEFAULT_BASE_URL
    assert config.model == "k-exaone_v2.5"
    assert config.key_source == "없음"
    assert "연결 확인: 실패" in run(server.exaone_status())
    with pytest.raises(ToolError, match="상태코드: 없음"):
        run(server.ask_exaone("hello"))


@pytest.mark.parametrize("payload", ["not json", "[]", '{"apiKey":false}'])
def test_bad_settings_safe(monkeypatch, tmp_path, payload):
    path = tmp_path / "settings.json"
    path.write_text(payload)
    monkeypatch.setenv("EXAONE_SETTINGS", str(path))
    config = server.load_config()
    assert config.key_source == "없음"


def test_corrupt_dpapi_error_does_not_echo_ciphertext():
    with pytest.raises(server.ExaoneError) as error:
        server.unprotect_key("not-valid-ciphertext")
    assert "not-valid-ciphertext" not in str(error.value)


@pytest.mark.parametrize("base,extra,expected", [
    ("https://api.exaone.ai/v1", "", True),
    ("https://API.EXAONE.AI:443/v1", "", True),
    ("https://api.exaone.ai.evil.test/v1", "", False),
    ("http://127.0.0.1:18799", "", False),
    ("http://127.0.0.1:18799", "localhost,127.0.0.1;other.test", True),
])
def test_request_body_host_and_history(monkeypatch, base, extra, expected):
    monkeypatch.setenv("EXAONE_BASE_URL", base)
    monkeypatch.setenv("EXAONE_STYLE_HOSTS", extra)
    body = server.build_body(server.load_config(), "new", "instructions", "max", 65536, .4,
                             [server.HistoryMessage(role="user", content="old"),
                              server.HistoryMessage(role="assistant", content="reply")])
    assert ("reasoning_effort" in body) is expected
    if expected:
        assert body["reasoning_effort"] == "max"
    assert body["messages"] == [{"role": "system", "content": "instructions"}, {"role": "user", "content": "old"},
                                {"role": "assistant", "content": "reply"}, {"role": "user", "content": "new"}]
    assert body["stream_options"] == {"include_usage": True}
    assert body["stream"] is True
    assert body["temperature"] == .4
    assert body["max_tokens"] == 65536
    assert "chat_template_kwargs" not in body


@pytest.mark.parametrize("body,expected", [
    ('{"error":{"message":"first"},"description":"second","message":"third"}', "first"),
    ('{"result_code":"INVALID","description":"second","message":"third"}', "second"),
    ('{"error":{},"message":"third"}', "third"),
    ('{"error":{"message":""},"description":"second"}', "second"),
    ("raw response", "raw response"),
])
def test_error_priority(body, expected):
    assert server.extract_error(body, None) == expected


def test_redaction_before_truncation():
    key = "secret-key-1234567890"
    text = server.extract_error("x" * 295 + key + "tail", key)
    assert key not in text
    assert "secret" not in text
    assert len(text) == 300
    assert key not in server.redact(json.dumps({"message": key}), key)


def test_stream_error_redacts_before_truncation():
    key = "stream-secret-1234567890"
    response = httpx.Response(200, content=sse_event({"error": {"message": "x" * 295 + key}}))
    with pytest.raises(server.ExaoneError) as raised:
        run(server.consume_sse(response, key))
    assert "stream" not in str(raised.value)
    assert key not in str(raised.value)


class Pieces(httpx.AsyncByteStream):
    def __init__(self, data, size=3):
        self.data, self.size = data, size

    async def __aiter__(self):
        for offset in range(0, len(self.data), self.size):
            yield self.data[offset:offset + self.size]


def sse_event(obj):
    return "data: " + json.dumps(obj, ensure_ascii=False) + "\r\n\r\n"


def test_fragmented_sse_usage_leading_whitespace(monkeypatch):
    monkeypatch.setenv("EXAONE_API_KEY", "test-key-1234")
    data = ": heartbeat\r\n\r\n" + sse_event({"choices": [{"delta": {"reasoning_content": "생각"}}]})
    for text in ["\n", "\n", "  첫 줄", "\n\n  둘째 줄  "]:
        data += sse_event({"choices": [{"delta": {"content": text}}]})
    for usage in [{"completion_tokens": 2}, {"prompt_tokens": 12, "completion_tokens": 567, "reasoning_tokens": 1234}]:
        data += sse_event({"choices": [], "usage": usage})
    data += "data: [DONE]\r\n\r\n"

    def handle(request):
        assert request.headers["Authorization"] == "Bearer test-key-1234"
        return httpx.Response(200, stream=Pieces(data.encode()), headers={"Content-Type": "text/event-stream"})

    config = server.load_config()
    result = run(server.request_completion(config, server.build_body(config, "hello"), transport=httpx.MockTransport(handle)))
    assert result.content == "  첫 줄\n\n  둘째 줄  "
    assert result.reasoning == "생각"
    assert result.usage == {"prompt_tokens": 12, "completion_tokens": 567, "reasoning_tokens": 1234}
    assert "생각 과정" not in server.format_completion(result, config, "high", False)
    assert "## 생각 과정\n생각" in server.format_completion(result, config, "high", True)
    assert "1234/567" in server.format_completion(result, config, "high", False)


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_http_errors_mask_key(monkeypatch, status):
    key = "test-key-1234"
    monkeypatch.setenv("EXAONE_API_KEY", key)
    config = server.load_config()

    def handle(request):
        return httpx.Response(status, json={"result_code": "ERROR", "description": f"bad key {key}"})

    with pytest.raises(server.ExaoneError) as raised:
        run(server.request_completion(config, {}, transport=httpx.MockTransport(handle)))
    message = server.error_text(raised.value, config)
    assert str(status) in message
    assert key not in message
    assert ("API 키를 확인하세요" in message) is (status in {401, 403})


@pytest.mark.parametrize("data", ["data: broken\n\n", 'data: {"choices":[]}\n\n'])
def test_invalid_or_truncated_sse(data):
    response = httpx.Response(200, content=data)
    with pytest.raises(server.ExaoneError):
        run(server.consume_sse(response))


def test_timeout_paths(monkeypatch):
    config = server.Config("synthetic-key", "환경변수", "http://127.0.0.1", "model")

    async def slow(request):
        await asyncio.sleep(.1)
        return httpx.Response(200)

    monkeypatch.setattr(server, "TOTAL_TIMEOUT", .01)
    with pytest.raises(server.ExaoneError, match="전체 요청 제한"):
        run(server.request_completion(config, {}, transport=httpx.MockTransport(slow)))

    def idle(request):
        raise httpx.ReadTimeout("never expose raw exception synthetic-key")

    monkeypatch.setattr(server, "TOTAL_TIMEOUT", 600)
    with pytest.raises(server.ExaoneError, match="120초") as raised:
        run(server.request_completion(config, {}, transport=httpx.MockTransport(idle)))
    assert "synthetic-key" not in str(raised.value)


def test_model_content_and_error_redaction(monkeypatch):
    monkeypatch.setenv("EXAONE_API_KEY", "test-key-1234")

    async def reply(config, body):
        return server.Completion("echo test-key-1234", "echo test-key-1234", {}, 0)

    monkeypatch.setattr(server, "request_completion", reply)
    result = run(server.ask_exaone("hello", include_reasoning=True))
    assert "test-key-1234" not in result


def test_tool_schema_and_validation():
    tools = {tool.name: tool for tool in run(server.mcp.list_tools())}
    assert set(tools) == {"ask_exaone", "exaone_status"}
    props = tools["ask_exaone"].inputSchema["properties"]
    assert props["max_tokens"]["minimum"] == 1
    assert props["max_tokens"]["maximum"] == 65536
    # Optional: omitted means EXAONE_DEFAULT_EFFORT (default high).
    effort = props["reasoning_effort"]
    enums = [branch.get("enum") for branch in effort.get("anyOf", [effort]) if "enum" in branch]
    assert enums == [["low", "high", "max"]]
    for arguments in [{"prompt": "hi", "max_tokens": 0}, {"prompt": "hi", "max_tokens": 65537},
                      {"prompt": "hi", "reasoning_effort": "invalid"},
                      {"prompt": "hi", "history": [{"role": "system", "content": "x"}]}]:
        with pytest.raises(ToolError):
            run(server.mcp._tool_manager.call_tool("ask_exaone", arguments))
