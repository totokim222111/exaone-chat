"""EXAONE stdio MCP server. Importing this module never reads credentials."""
from __future__ import annotations

import asyncio
import base64
import ctypes
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import quote, urlsplit

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import BaseModel, Field

DEFAULT_BASE_URL = "https://api.exaone.ai/v1"
DEFAULT_MODEL = "k-exaone_v2.5"
DEFAULT_SETTINGS = Path(__file__).resolve().parent.parent / "windows/dist/data/settings.json"
TOTAL_TIMEOUT = 600.0
IDLE_TIMEOUT = 120.0


class ExaoneError(Exception):
    def __init__(self, message: str, status: int | None = None):
        self.status = status
        super().__init__(message)


def redact(text: str, key: str | None) -> str:
    if key:
        variants = {key, json.dumps(key, ensure_ascii=True)[1:-1], quote(key, safe="")}
        for value in sorted(variants, key=len, reverse=True):
            text = text.replace(value, "[키 숨김]")
    return re.sub(r"(?i)Bearer\s+[^\s\"'<>]+", "Bearer [키 숨김]", text)


def unprotect_key(encoded: str) -> str:
    """C# ProtectedData CurrentUser, null entropy; no pywin32 dependency."""
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

    try:
        data = base64.b64decode(encoded, validate=True)
        buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
        incoming = Blob(len(data), buffer)
        outgoing = Blob()
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        decrypt = crypt32.CryptUnprotectData
        decrypt.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob),
                            ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        decrypt.restype = wintypes.BOOL
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
        # CRYPTPROTECT_UI_FORBIDDEN: a background stdio process must never show UI.
        if not decrypt(ctypes.byref(incoming), None, None, None, None, 1, ctypes.byref(outgoing)):
            raise ValueError("DPAPI failed")
        try:
            return ctypes.string_at(outgoing.pbData, outgoing.cbData).decode("utf-8").strip()
        finally:
            ctypes.memset(outgoing.pbData, 0, outgoing.cbData)
            kernel32.LocalFree(outgoing.pbData)
    except Exception:
        raise ExaoneError("윈도우 앱 API 키를 현재 Windows 사용자로 복호화할 수 없습니다") from None


@dataclass(frozen=True)
class Config:
    api_key: str | None = field(repr=False)
    key_source: str
    base_url: str
    model: str
    style_hosts: frozenset[str] = frozenset({"api.exaone.ai"})
    warning: str | None = None

    @property
    def exaone_style(self) -> bool:
        return (urlsplit(self.base_url).hostname or "").lower() in self.style_hosts


def load_config() -> Config:
    settings: dict = {}
    warning = None
    settings_path = Path(os.environ.get("EXAONE_SETTINGS", str(DEFAULT_SETTINGS)))
    try:
        with settings_path.open(encoding="utf-8-sig") as source:
            settings = json.load(source)
        if not isinstance(settings, dict):
            raise ValueError("expected object")
    except FileNotFoundError:
        pass
    except (OSError, ValueError):
        warning = "윈도우 앱 설정 파일을 읽을 수 없어 환경변수와 기본값을 사용했습니다"
        settings = {}

    def setting(name: str) -> str:
        value = settings.get(name)
        return value.strip() if isinstance(value, str) else ""

    key = os.environ.get("EXAONE_API_KEY", "").strip()
    key_source = "환경변수" if key else "없음"
    if not key and setting("apiKey"):
        try:
            key = unprotect_key(setting("apiKey"))
            key_source = "윈도우 앱 설정" if key else "없음"
        except ExaoneError as exc:
            warning = str(exc)
    base = os.environ.get("EXAONE_BASE_URL", "").strip() or setting("baseUrl") or DEFAULT_BASE_URL
    model = os.environ.get("EXAONE_MODEL", "").strip() or setting("model") or DEFAULT_MODEL
    hosts = {"api.exaone.ai"}
    hosts.update(host.lower() for host in re.split(r"[,;\s]+", os.environ.get("EXAONE_STYLE_HOSTS", "")) if host)
    return Config(key or None, key_source, base.rstrip("/"), model, frozenset(hosts), warning)


class HistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


def build_body(config: Config, prompt: str, system: str | None = None,
               reasoning_effort: str = "high", max_tokens: int = 8192,
               temperature: float | None = None, history: list[HistoryMessage] | None = None) -> dict:
    messages = []
    if system is not None:
        messages.append({"role": "system", "content": system})
    messages.extend(message.model_dump() for message in history or [])
    messages.append({"role": "user", "content": prompt})
    body = {"model": config.model, "messages": messages, "max_tokens": max_tokens,
            "stream": True, "stream_options": {"include_usage": True}}
    if config.exaone_style:
        body["reasoning_effort"] = reasoning_effort
    if temperature is not None:
        body["temperature"] = temperature
    return body


def extract_error(body: str, key: str | None) -> str:
    try:
        obj = json.loads(body)
    except ValueError:
        obj = None
    message = None
    if isinstance(obj, dict):
        error = obj.get("error")
        candidates = [error.get("message") if isinstance(error, dict) else None,
                      obj.get("description"), obj.get("message")]
        message = next((value for value in candidates if isinstance(value, str) and value.strip()), None)
    # Redact before truncating so a key spanning the 300-character boundary cannot leak.
    return " ".join(redact(message if message is not None else body, key).split())[:300] or "서버가 오류 내용을 보내지 않았습니다"


@dataclass
class Completion:
    content: str
    reasoning: str
    usage: dict[str, int]
    elapsed: float


async def consume_sse(response: httpx.Response, key: str | None = None) -> Completion:
    content: list[str] = []
    reasoning: list[str] = []
    usage: dict[str, int] = {}
    data_lines: list[str] = []

    def event() -> bool:
        if not data_lines:
            return False
        data = "\n".join(data_lines)
        data_lines.clear()
        if data.strip() == "[DONE]":
            return True
        try:
            obj = json.loads(data)
            if not isinstance(obj, dict):
                raise ValueError("expected object")
            if "error" in obj or (obj.get("result_code") and "choices" not in obj):
                raise ExaoneError(extract_error(data, key), response.status_code)
            for choice in obj.get("choices", []):
                if choice.get("index", 0) != 0:
                    continue
                delta = choice.get("delta") or {}
                for name, target in (("content", content), ("reasoning_content", reasoning)):
                    value = delta.get(name)
                    if isinstance(value, str):
                        target.append(value)
            update = obj.get("usage") or {}
            for name in ("prompt_tokens", "completion_tokens", "reasoning_tokens"):
                value = update.get(name)
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    usage[name] = value
            details = update.get("completion_tokens_details") or {}
            if "reasoning_tokens" not in usage and isinstance(details.get("reasoning_tokens"), int):
                usage["reasoning_tokens"] = details["reasoning_tokens"]
        except (ValueError, TypeError, AttributeError):
            raise ExaoneError("스트리밍 응답 형식이 올바르지 않습니다", response.status_code) from None
        return False

    async for line in response.aiter_lines():
        if line == "":
            if event():
                return Completion("".join(content).lstrip("\r\n"), "".join(reasoning), usage, 0)
        elif line.startswith("data:"):
            data_lines.append(line[5:].removeprefix(" "))
    if event():
        return Completion("".join(content).lstrip("\r\n"), "".join(reasoning), usage, 0)
    raise ExaoneError("스트리밍 응답이 완료 표시 없이 끊겼습니다", response.status_code)


async def request_completion(config: Config, body: dict, *,
                             transport: httpx.AsyncBaseTransport | None = None) -> Completion:
    if not config.api_key:
        raise ExaoneError(config.warning or "EXAONE API 키가 없습니다")
    parsed = urlsplit(config.base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.query or parsed.fragment or parsed.username:
        raise ExaoneError("Base URL은 사용자 정보·쿼리 없는 HTTP 또는 HTTPS 주소여야 합니다")
    started = time.monotonic()
    try:
        async with asyncio.timeout(TOTAL_TIMEOUT):
            async with httpx.AsyncClient(timeout=httpx.Timeout(IDLE_TIMEOUT), transport=transport,
                                         follow_redirects=False, trust_env=False) as client:
                async with client.stream("POST", config.base_url + "/chat/completions", json=body,
                                         headers={"Authorization": f"Bearer {config.api_key}",
                                                  "Accept": "text/event-stream"}) as response:
                    if response.status_code < 200 or response.status_code >= 300:
                        raw = (await response.aread()).decode("utf-8", errors="replace")
                        message = extract_error(raw, config.api_key)
                        if response.status_code in {401, 403}:
                            message = "API 키를 확인하세요: " + message
                        raise ExaoneError(message, response.status_code)
                    result = await consume_sse(response, config.api_key)
                    result.elapsed = time.monotonic() - started
                    return result
    except ExaoneError:
        raise
    except TimeoutError:
        raise ExaoneError("전체 요청 제한 시간 600초를 초과했습니다") from None
    except httpx.TimeoutException:
        raise ExaoneError("120초 동안 서버 응답이 없거나 연결 제한 시간을 초과했습니다") from None
    except httpx.HTTPError:
        raise ExaoneError("서버 연결 또는 스트리밍 전송에 실패했습니다") from None


def error_text(error: ExaoneError, config: Config) -> str:
    code = str(error.status) if error.status is not None else "없음"
    detail = " ".join(redact(str(error), config.api_key).split())
    return f"EXAONE 요청에 실패했습니다 (상태코드: {code}): {detail}."


def format_completion(result: Completion, config: Config, effort: str, include_reasoning: bool) -> str:
    parts = []
    if include_reasoning and result.reasoning:
        parts.append("## 생각 과정\n" + result.reasoning)
    parts.append(result.content or "(답변 본문 없음: max_tokens를 늘려 다시 요청하세요.)")
    applied = effort if config.exaone_style else "미전송"
    tokens = result.usage
    parts.append(f"모델: {config.model} | 추론 강도: {applied} | "
                 f"토큰(생각/답변): {tokens.get('reasoning_tokens', '미제공')}/{tokens.get('completion_tokens', '미제공')} "
                 f"| 입력 토큰: {tokens.get('prompt_tokens', '미제공')} | 걸린 시간: {result.elapsed:.2f}초")
    return redact("\n\n".join(parts), config.api_key)


mcp = FastMCP("EXAONE", instructions="LG EXAONE에 질문하거나 연결 상태를 확인합니다. Query LG EXAONE or check its connection.")


@mcp.tool(description="LG EXAONE에 독립적인 질문·분석·코딩 검토·한국어 작업을 요청하거나 다른 모델의 의견을 얻습니다. "
          "Ask LG EXAONE for an independent answer, analysis, code review, Korean writing, or a second opinion. "
          "history는 이전 user/assistant 대화이며 system은 지침입니다. 추론은 항상 켜지고 max_tokens에 포함됩니다. "
          "Reasoning is always enabled and counts toward max_tokens; include_reasoning exposes the model's reasoning output.")
async def ask_exaone(
    prompt: Annotated[str, Field(min_length=1, description="질문 또는 작업 / Question or task")],
    system: str | None = None,
    reasoning_effort: Literal["low", "high", "max"] = "high",
    max_tokens: Annotated[int, Field(ge=1, le=65536)] = 8192,
    include_reasoning: bool = False,
    temperature: Annotated[float, Field(allow_inf_nan=False)] | None = None,
    history: list[HistoryMessage] | None = None,
) -> str:
    config = load_config()
    try:
        body = build_body(config, prompt, system, reasoning_effort, max_tokens, temperature, history)
        result = await request_completion(config, body)
        return format_completion(result, config, reasoning_effort, include_reasoning)
    except ExaoneError as exc:
        raise ToolError(error_text(exc, config)) from None
    except Exception:
        # Never forward arbitrary exception reprs: HTTP/settings exceptions can contain secrets.
        raise ToolError("EXAONE 요청 처리 중 내부 오류가 발생했습니다 (상태코드: 없음).") from None


@mcp.tool(description="EXAONE 연결 문제를 진단하고 키 출처·끝 4자리·Base URL·모델을 확인합니다. "
          "Diagnose EXAONE connectivity and inspect key source, last four characters, base URL, and model. "
          "max_tokens=64, reasoning_effort=low의 짧은 실제 요청 1회를 보냅니다. "
          "Sends one short live request (max_tokens=64, reasoning_effort=low); may consume API tokens.")
async def exaone_status() -> str:
    config = load_config()
    suffix = config.api_key[-4:] if config.api_key and len(config.api_key) > 4 else "숨김" if config.api_key else "없음"
    lines = [f"키 출처: {config.key_source}", f"키 끝 4자리: {suffix}",
             f"Base URL: {config.base_url}", f"모델: {config.model}"]
    if config.warning:
        lines.append(f"설정 안내: {config.warning}")
    started = time.monotonic()
    try:
        body = build_body(config, "연결 확인입니다. OK라고 짧게 답하세요.", reasoning_effort="low", max_tokens=64)
        await request_completion(config, body)
        lines.append(f"연결 확인: 성공 | 지연 시간: {time.monotonic() - started:.2f}초")
    except ExaoneError as exc:
        lines.append(f"연결 확인: 실패 | {error_text(exc, config)} | 지연 시간: {time.monotonic() - started:.2f}초")
    except Exception:
        lines.append(f"연결 확인: 실패 | 내부 오류 (상태코드: 없음) | 지연 시간: {time.monotonic() - started:.2f}초")
    return redact("\n".join(lines), config.api_key)


if __name__ == "__main__":
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.CRITICAL)
    logging.getLogger("httpcore").setLevel(logging.CRITICAL)
    mcp.run(transport="stdio")
