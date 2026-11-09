import asyncio
import json
from types import SimpleNamespace
import httpx
import pytest
from starlette.requests import Request
from starlette.responses import JSONResponse
from app.security import MAX_BODY, RequestGuard
from app.streaming import EventResponse, event
from app import main


async def echo(scope, receive, send):
    body = await Request(scope, receive).body()
    await JSONResponse({"length": len(body)})(scope, receive, send)


@pytest.mark.parametrize("headers,body,status,code", [
    ({}, b"{}", 403, "csrf"),
    ({"X-Exa": "0"}, b"{}", 403, "csrf"),
    ({"X-Exa": "1", "Content-Type": "text/plain"}, b"{}", 415, "json_required"),
    ({"X-Exa": "1", "Content-Type": "application/json"}, b"not JSON", 422, "validation"),
    ({"X-Exa": "1", "Content-Type": "application/json"}, b'{"x":NaN}', 422, "validation"),
    ({"X-Exa": "1", "Content-Type": "application/json"}, b"x" * (MAX_BODY + 1), 413, "body_too_large"),
])
def test_request_guard(headers, body, status, code):
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=RequestGuard(echo)), base_url="http://test") as client:
            response = await client.post("/api/session", headers=headers, content=body)
            assert response.status_code == status
            assert response.json()["error"]["code"] == code
    asyncio.run(run())


def test_chunked_size_limit():
    async def run():
        async def chunks():
            yield b"x" * (MAX_BODY // 2)
            yield b"x" * (MAX_BODY // 2 + 1)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=RequestGuard(echo)), base_url="http://test") as client:
            response = await client.post("/api/session", headers={"X-Exa": "1", "Content-Type": "application/json"}, content=chunks())
            assert response.status_code == 413
    asyncio.run(run())


def test_guard_replays_exact_body():
    async def run():
        body = json.dumps({"content": "한글"}).encode()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=RequestGuard(echo)), base_url="http://test") as client:
            response = await client.post("/api/session", headers={"X-Exa": "1", "Content-Type": "application/json; charset=utf-8"}, content=body)
            assert response.json() == {"length": len(body)}
    asyncio.run(run())


def test_disconnect_waits_for_partial_save():
    async def run():
        got_delta = asyncio.Event()
        saved = []
        cleaned = []

        async def producer(queue):
            try:
                await queue.put(event("content", {"delta": "partial"}))
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                # Simulate a durable async save before releasing the conversation.
                await asyncio.sleep(0.01)
                saved.append("partial")

        async def send(item):
            if item["type"] == "http.response.body":
                got_delta.set()

        async def receive():
            await got_delta.wait()
            return {"type": "http.disconnect"}

        def cleanup():
            assert saved == ["partial"]
            cleaned.append(True)

        await asyncio.wait_for(EventResponse(producer, cleanup)({}, receive, send), 1)
        assert cleaned == [True]
    asyncio.run(run())


def test_producer_failure_does_not_leave_endless_stream():
    async def run():
        cleaned = []

        async def producer(queue):
            raise RuntimeError("database unavailable")

        async def receive():
            await asyncio.Event().wait()

        async def send(item):
            pass

        with pytest.raises(RuntimeError, match="database unavailable"):
            await asyncio.wait_for(EventResponse(producer, lambda: cleaned.append(True))({}, receive, send), 1)
        assert cleaned == [True]
    asyncio.run(run())


def test_rate_limit_session_isolation_and_expiry(monkeypatch):
    monkeypatch.setattr(main.app.state, "rates", {}, raising=False)
    clock = SimpleNamespace(value=100)
    monkeypatch.setattr(main.time, "monotonic", lambda: clock.value)
    for i in range(20):
        main.rate_limit("a")
    with pytest.raises(main.ApiError) as error:
        main.rate_limit("a")
    assert error.value.status == 429
    main.rate_limit("b")
    clock.value += 61
    main.rate_limit("a")
    assert len(main.app.state.rates["a"]) == 1
    assert "b" not in main.app.state.rates
