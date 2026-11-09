import json
import pytest
from cryptography.exceptions import InvalidTag
from pydantic import ValidationError
from app import config
from app.crypto import KeyCipher, load_secret
from app.models import KeyInput, MessageInput, RenameConversation, SettingsInput
from app.streaming import SSEParser, ThinkSplitter, event
from app.upstream import UpstreamError, decode_delta, payload, safe_reason


def split(chunks):
    parser = ThinkSplitter()
    values = {"reasoning": "", "content": ""}
    for chunk in chunks:
        for kind, text in parser.feed(chunk):
            values[kind] += text
    for kind, text in parser.feed("", final=True):
        values[kind] += text
    return values["content"], values["reasoning"]


@pytest.mark.parametrize("text,content,reasoning", [
    ("hello", "hello", ""), ("<think>abc</think>answer", "answer", "abc"),
    ("before<thought>x</thought>after", "beforeafter", "x"),
    ("<think>a</think><thought>b</thought>c", "c", "ab"),
    ("<think>unclosed", "", "unclosed"), ("a<thi", "a<thi", ""),
    ("a<thinking>b", "a<thinking>b", ""), ("<think>x</thi", "", "x</thi"),
    ("literal </think>", "literal </think>", ""), ("", "", ""),
    ("<<think>한글😀</think><", "<<", "한글😀"),
])
def test_think_all_boundaries(text, content, reasoning):
    assert split([text]) == (content, reasoning)
    assert split(list(text)) == (content, reasoning)
    for i in range(len(text) + 1):
        assert split([text[:i], text[i:]]) == (content, reasoning)


def test_sse_parser():
    p = SSEParser()
    assert p.line(": ping") is None
    assert p.line("event: ignored") is None
    assert p.line("data: one") is None
    assert p.line("data:two") is None
    assert p.line("") == "one\ntwo"
    assert p.line("") is None
    p.line("data: [DONE]")
    assert p.finish() == "[DONE]"
    assert p.finish() is None
    encoded = event("content", {"delta": "한글\n"}).decode()
    assert "event: content\n" in encoded
    assert json.loads(encoded.split("data: ")[1]) == {"delta": "한글\n"}


def test_deltas():
    assert decode_delta('{"choices":[{"delta":{"reasoning_content":"r","content":"c"}}]}', "secret") == [("reasoning", "r"), ("content", "c")]
    assert decode_delta('{"choices":[]}', "secret") == []
    with pytest.raises(UpstreamError):
        decode_delta("not json", "secret")
    with pytest.raises(UpstreamError) as error:
        decode_delta('{"error":{"message":"secret"}}', "secret")
    assert "secret" not in error.value.message


@pytest.mark.parametrize("model,data", [
    (MessageInput, {"content": "x" * 32001}), (MessageInput, {"content": " "}),
    (RenameConversation, {"title": "x" * 121}), (SettingsInput, {"systemPrompt": "x" * 8001}),
    (SettingsInput, {"maxTokens": 0}), (SettingsInput, {"maxTokens": 65537}),
    (SettingsInput, {"maxTokens": 1.5}), (SettingsInput, {"maxTokens": True}),
    (SettingsInput, {"temperature": -0.1}), (SettingsInput, {"temperature": 2.1}),
    (SettingsInput, {"temperature": float("nan")}), (SettingsInput, {"topP": 1.1}),
    (SettingsInput, {"thinking": "yes"}), (SettingsInput, {"thinking": None}),
    (SettingsInput, {"model": " "}), (SettingsInput, {"baseUrl": "http://127.0.0.1/v1"}),
    (SettingsInput, {"unexpected": 1}), (KeyInput, {"apiKey": "a\r\nb"}),
])
def test_invalid_input(model, data):
    with pytest.raises(ValidationError):
        model(**data)


def test_valid_input():
    assert len(MessageInput(content="x" * 32000).content) == 32000
    assert SettingsInput(maxTokens=65536, temperature=2, topP=0, systemPrompt="x" * 8000)
    assert SettingsInput().model_dump(exclude_unset=True) == {}


@pytest.mark.parametrize("url", ["file:///etc/passwd", "http://u:p@host/v1", "https://host/v1?q=x", "https://host/#x", "http://host:bad/v1", "http://host /v1"])
def test_custom_still_validated(monkeypatch, url):
    monkeypatch.setattr(config, "CUSTOM", True)
    with pytest.raises(ValueError):
        config.validate_base_url(url)


def test_custom_and_allowlist(monkeypatch):
    assert config.validate_base_url(config.FRIENDLI + "/") == config.FRIENDLI
    monkeypatch.setattr(config, "CUSTOM", True)
    assert config.validate_base_url("http://mock:9000/v1") == "http://mock:9000/v1"


def test_crypto_roundtrip(tmp_path, monkeypatch):
    monkeypatch.delenv("APP_SECRET", raising=False)
    path = tmp_path / "secret"
    secret = load_secret(path)
    assert len(secret) == 32 and load_secret(path) == secret
    cipher = KeyCipher(secret)
    encrypted = cipher.encrypt("secret-api-key")
    assert b"secret-api-key" not in encrypted
    assert cipher.decrypt(encrypted) == "secret-api-key"
    assert cipher.encrypt("secret-api-key") != encrypted
    with pytest.raises(InvalidTag):
        cipher.decrypt(encrypted[:-1] + bytes([encrypted[-1] ^ 1]))
    with pytest.raises(InvalidTag):
        KeyCipher(b"different").decrypt(encrypted)


def test_payload_and_error_redaction():
    cfg = config.DEFAULTS.copy()
    cfg["baseUrl"] = config.FRIENDLI
    p = payload(cfg, [{"role": "user", "content": "hi"}], False)
    assert p["parse_reasoning"] is True and p["max_tokens"] == 8
    assert p["chat_template_kwargs"] == {"enable_thinking": False}
    cfg["baseUrl"] = "http://mock:9000/v1"
    assert "parse_reasoning" not in payload(cfg, [], True)
    assert "sk-secret" not in safe_reason(b'{"error":{"message":"bad sk-secret"}}', "sk-secret")
    assert "hidden" not in safe_reason(b"Authorization: Bearer hidden", "other")
    assert len(safe_reason(b"a" * 1000, "key")) == 300
