import asyncio
import json
import httpx
import pytest
from pydantic import ValidationError
from app import config
from app.models import SettingsInput
from app.streaming import LeadingWhitespaceTrimmer, ThinkSplitter
from app.upstream import decode_delta, deltas, parse_usage, payload, safe_reason, test_key as probe_key


@pytest.mark.parametrize("host,official,friendli", [
    ("https://api.exaone.ai/v1", True, False),
    ("https://API.EXAONE.AI/v1", True, False),
    ("http://mock:9000/v1", True, False),
    (config.FRIENDLI, False, True),
    ("https://api.friendli.ai.evil.test/v1", False, False),
    ("https://another.example/v1", False, False),
])
@pytest.mark.parametrize("stream", [True, False])
def test_host_payload(monkeypatch, host, official, friendli, stream):
    monkeypatch.setattr(config, "EXAONE_STYLE_HOSTS", ["api.exaone.ai", "mock"])
    cfg = config.DEFAULTS | {"baseUrl": host, "reasoningEffort": "high", "thinking": False}
    history = [{"role": "user", "content": "hello"}]
    before = cfg.copy()
    result = payload(cfg, history, stream)
    assert cfg == before and history == [{"role": "user", "content": "hello"}]
    assert result["messages"] == history and result["stream"] == stream
    assert result["max_tokens"] == (8192 if stream else 64 if official else 8)
    if official:
        assert result["reasoning_effort"] == ("high" if stream else "low")
        assert "chat_template_kwargs" not in result and "parse_reasoning" not in result
        assert result.get("stream_options") == ({"include_usage": True} if stream else None)
    else:
        assert result["chat_template_kwargs"] == {"enable_thinking": False}
        assert "reasoning_effort" not in result and "stream_options" not in result
        assert ("parse_reasoning" in result) == friendli


@pytest.mark.parametrize("data,expected", [
    ({"error": {"message": "first"}, "description": "second", "message": "third"}, "first"),
    ({"error": {"code": 1}, "description": "second", "message": "third"}, "second"),
    ({"error": None, "description": "second"}, "second"),
    ({"error": "bad", "description": None, "message": "third"}, "third"),
    ({"error": {"message": " "}, "description": "second"}, "second"),
    ({"description": 42, "message": "third"}, "third"),
])
def test_error_priority(data, expected):
    assert safe_reason(json.dumps(data).encode(), "test-key") == expected


def test_error_fallback_and_redaction():
    assert safe_reason(b"plain failure", "test-key") == "plain failure"
    assert safe_reason(b"x" * 500, "test-key") == "x" * 300
    result = safe_reason(b'{"result_code":"40100","description":"test-key rejected"}', "test-key")
    assert "test-key" not in result and "rejected" in result


@pytest.mark.parametrize("raw,expected", [
    (None, None), ([], None),
    ({}, {"promptTokens": None, "completionTokens": None, "reasoningTokens": None}),
    ({"prompt_tokens": 10, "completion_tokens": 20, "reasoning_tokens": 4},
     {"promptTokens": 10, "completionTokens": 20, "reasoningTokens": 4}),
    ({"prompt_tokens": 0}, {"promptTokens": 0, "completionTokens": None, "reasoningTokens": None}),
    ({"prompt_tokens": True, "completion_tokens": -1, "reasoning_tokens": "5"},
     {"promptTokens": None, "completionTokens": None, "reasoningTokens": None}),
])
def test_usage(raw, expected):
    assert parse_usage(raw) == expected
    delta = decode_delta(json.dumps({"choices": [], "usage": raw}), "test-key")
    assert delta == ([] if expected is None else [("usage", expected)])


def test_usage_alongside_content():
    result = decode_delta(json.dumps({"choices": [{"delta": {"content": "hi"}}], "usage": {"completion_tokens": 7}}), "key")
    assert ("content", "hi") in result
    assert ("usage", {"promptTokens": None, "completionTokens": 7, "reasoningTokens": None}) in result


@pytest.mark.parametrize("chunks,expected", [
    (["\n", "\n", "  ", "answer", "\n  code\n"], "answer\n  code\n"),
    (["\n\nanswer", " more"], "answer more"),
    ([" ", "\t", "\n"], ""),
    (["", "", "hello"], "hello"),
])
def test_leading_whitespace(chunks, expected):
    trimmer = LeadingWhitespaceTrimmer()
    assert "".join(trimmer.feed(c) for c in chunks) == expected


def test_whitespace_with_think_splitter():
    splitter, trimmer = ThinkSplitter(), LeadingWhitespaceTrimmer()
    content, reasoning = [], []
    for chunk in ["\n", "\n<th", "ink>thinking</think>\n", "\nanswer", "\nnext", ""]:
        for kind, text in splitter.feed(chunk):
            (content if kind == "content" else reasoning).append(trimmer.feed(text) if kind == "content" else text)
    assert "".join(content) == "answer\nnext"
    assert "".join(reasoning) == "thinking"


@pytest.mark.parametrize("saved,should_change", [
    ({}, True), ({"baseUrl": config.FRIENDLI}, True),
    ({"model": config.LEGACY_MODEL}, True),
    ({"baseUrl": config.FRIENDLI, "model": config.LEGACY_MODEL, "temperature": 0.5}, True),
    ({"baseUrl": "http://mock:9000/v1", "model": config.LEGACY_MODEL}, False),
    ({"baseUrl": config.FRIENDLI, "model": "custom-model"}, False),
    ({"baseUrl": config.EXAONE, "model": "k-exaone_v2.5"}, False),
])
def test_legacy_settings(saved, should_change):
    before = saved.copy()
    result = config.migrate_legacy_settings(saved)
    assert saved == before
    if should_change:
        assert result == saved | {"baseUrl": config.DEFAULT_BASE_URL, "model": config.DEFAULT_MODEL}
    else:
        assert result == saved
    assert config.migrate_legacy_settings(result) == result


@pytest.mark.parametrize("value", ["low", "high", "max"])
def test_valid_reasoning_effort(value):
    assert SettingsInput(reasoningEffort=value).reasoningEffort == value


@pytest.mark.parametrize("value", ["medium", "off", "LOW", 1, None])
def test_invalid_reasoning_effort(value):
    with pytest.raises(ValidationError):
        SettingsInput(reasoningEffort=value)


def test_official_probe_accepts_empty_http_200(monkeypatch):
    monkeypatch.setattr(config, "EXAONE_STYLE_HOSTS", ["mock"])
    monkeypatch.setattr(config, "ALLOWED", ["http://mock:9000/v1"])

    async def run():
        def respond(request):
            body = json.loads(request.content)
            assert body["max_tokens"] == 64 and body["reasoning_effort"] == "low"
            return httpx.Response(200, content=b"")
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            result = await probe_key(client, config.DEFAULTS | {"baseUrl": "http://mock:9000/v1"}, "test-key")
            assert result["ok"] and result["status"] == 200
    asyncio.run(run())


def test_upstream_usage_only_tail(monkeypatch):
    monkeypatch.setattr(config, "ALLOWED", ["http://mock:9000/v1"])

    async def run():
        raw = ('data: {"choices":[{"delta":{"reasoning_content":"r"}}]}\n\n'
               'data: {"choices":[{"delta":{"content":"\\n\\nanswer"}}]}\n\n'
               'data: {"choices":[],"usage":{"prompt_tokens":1,"completion_tokens":2}}\n\n'
               'data: [DONE]\n\n')
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=raw, headers={"Content-Type": "text/event-stream"}))) as client:
            values = [d async for d in deltas(client, config.DEFAULTS | {"baseUrl": "http://mock:9000/v1"}, "key", [])]
            assert values == [("reasoning", "r"), ("content", "\n\nanswer"),
                              ("usage", {"promptTokens": 1, "completionTokens": 2, "reasoningTokens": None})]
    asyncio.run(run())
