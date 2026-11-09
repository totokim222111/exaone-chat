import json
import re
import time
from urllib.parse import urlsplit
import httpx
from .config import is_exaone_style, validate_base_url
from .streaming import SSEParser


class UpstreamError(Exception):
    def __init__(self, kind, status, message):
        self.kind, self.status, self.message = kind, status, message

    def data(self):
        return dict(kind=self.kind, status=self.status, message=self.message)


def safe_reason(body: bytes, key: str) -> str:
    text = body.decode("utf-8", errors="replace")
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            error = parsed.get("error")
            candidates = [error.get("message") if isinstance(error, dict) else None,
                          parsed.get("description"), parsed.get("message")]
            for reason in candidates:
                if isinstance(reason, str) and reason.strip():
                    text = reason
                    break
    except (ValueError, AttributeError, TypeError):
        pass
    text = text.replace(key, "[숨김]") if key else text
    text = re.sub(r"(?i)bearer\s+\S+", "Bearer [숨김]", text)
    text = re.sub(r"(?i)(authorization|api[-_ ]?key)\s*[:=]\s*[^\r\n,}]+", r"\1: [숨김]", text)
    return " ".join(text.split())[:300] or "응답 본문이 없습니다"


async def response_error(response, key):
    body = bytearray()
    async for part in response.aiter_bytes():
        body.extend(part[:65536 - len(body)])
        if len(body) >= 65536:
            break
    status = response.status_code
    kind = "auth" if status in (401, 403) else "rate_limit" if status == 429 else "upstream"
    return UpstreamError(kind, status, f"업스트림 요청에 실패했습니다 ({safe_reason(bytes(body), key)}).")


def payload(settings, messages, stream):
    official = is_exaone_style(settings["baseUrl"])
    result = dict(model=settings["model"], messages=messages, stream=stream,
                  temperature=settings["temperature"], top_p=settings["topP"],
                  max_tokens=settings["maxTokens"] if stream else 64 if official else 8)
    if official:
        result["reasoning_effort"] = settings.get("reasoningEffort", "max") if stream else "low"
        if stream:
            result["stream_options"] = {"include_usage": True}
    else:
        result["chat_template_kwargs"] = {"enable_thinking": settings["thinking"] if stream else False}
        if urlsplit(settings["baseUrl"]).hostname == "api.friendli.ai":
            result["parse_reasoning"] = True
    return result


def transport_error(exc):
    if isinstance(exc, httpx.TimeoutException):
        return UpstreamError("timeout", None, "업스트림 응답 대기 시간이 초과되었습니다.")
    return UpstreamError("network", None, "업스트림 서버에 연결할 수 없습니다.")


async def test_key(client, settings, key):
    start = time.monotonic()
    try:
        base = validate_base_url(settings["baseUrl"])
        async with client.stream("POST", base + "/chat/completions",
            headers={"Authorization": "Bearer " + key},
            json=payload(settings, [{"role": "user", "content": "hi"}], False)) as response:
            if not 200 <= response.status_code < 300:
                raise await response_error(response, key)
            if is_exaone_style(base):
                if response.status_code != 200:
                    raise UpstreamError("upstream", response.status_code, "업스트림 키 확인 요청이 HTTP 200을 반환하지 않았습니다.")
                return dict(ok=True, status=200, message="API 키를 확인했습니다.",
                            latencyMs=round((time.monotonic() - start) * 1000))
            # A successful OpenAI-compatible probe must contain an assistant message.
            raw = bytearray()
            async for part in response.aiter_bytes():
                raw.extend(part)
                if len(raw) > 1024 * 1024:
                    raise UpstreamError("upstream", response.status_code, "업스트림 테스트 응답이 너무 큽니다.")
            try:
                data = json.loads(raw)
                assert isinstance(data["choices"][0]["message"], dict)
            except (ValueError, KeyError, IndexError, TypeError, AssertionError):
                raise UpstreamError("upstream", response.status_code, "업스트림 테스트 응답 형식이 올바르지 않습니다.") from None
            return dict(ok=True, status=response.status_code, message="API 키를 확인했습니다.",
                        latencyMs=round((time.monotonic() - start) * 1000))
    except UpstreamError as exc:
        return dict(ok=False, status=exc.status, message=exc.message,
                    latencyMs=round((time.monotonic() - start) * 1000))
    except httpx.HTTPError as exc:
        error = transport_error(exc)
        return dict(ok=False, status=None, message=error.message,
                    latencyMs=round((time.monotonic() - start) * 1000))


async def deltas(client, settings, key, messages):
    base = validate_base_url(settings["baseUrl"])
    try:
        async with client.stream("POST", base + "/chat/completions",
            headers={"Authorization": "Bearer " + key}, json=payload(settings, messages, True)) as response:
            if not 200 <= response.status_code < 300:
                raise await response_error(response, key)
            if "text/event-stream" not in response.headers.get("content-type", ""):
                raise UpstreamError("upstream", response.status_code, "업스트림 서버가 SSE 응답을 반환하지 않았습니다.")
            parser = SSEParser()
            async for line in response.aiter_lines():
                data = parser.line(line)
                if data is None:
                    continue
                if data.strip() == "[DONE]":
                    return
                for delta in decode_delta(data, key):
                    yield delta
            data = parser.finish()
            if data is not None:
                if data.strip() == "[DONE]":
                    return
                for delta in decode_delta(data, key):
                    yield delta
            raise UpstreamError("upstream", response.status_code, "업스트림 응답이 완료되기 전에 연결이 종료되었습니다.")
    except httpx.HTTPError as exc:
        raise transport_error(exc) from None


def parse_usage(value):
    if not isinstance(value, dict):
        return None
    result = {}
    for source, target in (("prompt_tokens", "promptTokens"), ("completion_tokens", "completionTokens"),
                           ("reasoning_tokens", "reasoningTokens")):
        count = value.get(source)
        result[target] = count if type(count) is int and count >= 0 else None
    return result


def decode_delta(data, key):
    try:
        obj = json.loads(data)
        if "error" in obj or ("result_code" in obj and "description" in obj):
            raise UpstreamError("upstream", None, f"업스트림 오류가 발생했습니다 ({safe_reason(data.encode(), key)}).")
        result = []
        usage = parse_usage(obj.get("usage"))
        if usage is not None:
            result.append(("usage", usage))
        choices = obj.get("choices", [])
        if not choices:
            return result
        delta = choices[0].get("delta", {})
        for name, kind in (("reasoning_content", "reasoning"), ("content", "content")):
            value = delta.get(name)
            if value is not None and not isinstance(value, str):
                raise ValueError()
            if value:
                result.append((kind, value))
        return result
    except (ValueError, AttributeError, TypeError, IndexError):
        raise UpstreamError("upstream", None, "업스트림 SSE 응답 형식이 올바르지 않습니다.") from None
