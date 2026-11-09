import asyncio
import json
import httpx
import pytest
from app import search_pipeline as search


@pytest.mark.parametrize("text,expected", [
    ('{"queries":["한국어","second","third","fourth"]}', ["한국어", "second", "third"]),
    ('```json\n{"queries":[" same ","same"]}\n```', ["same"]),
    ('{"queries":[]}', []), ("bad", ["question"]),
    ('{"queries":"wrong"}', ["question"]), ('{"queries":[1]}', ["question"]),
])
def test_queries(text, expected):
    assert search.parse_queries(text, "question") == expected


def test_query_body_and_context_are_current_only():
    history = [{"role": "system", "content": "user instructions"},
               {"role": "assistant", "content": "old answer [1]"}, {"role": "user", "content": "question"}]
    body = search.query_body("model", history, "question", True)
    assert body["stream"] is False and body["max_tokens"] == 512 and body["reasoning_effort"] == "low"
    assert body["messages"][-1] == {"role": "user", "content": "question"}
    assert len([m for m in body["messages"] if m["content"] == "question"]) == 1
    meta = {"error": None, "sources": [{"n": 1, "title": "New", "url": "https://example.com/", "snippet": "new snippet"}]}
    result = search.with_search_context(history, meta, today="2026-11-09")
    assert result[0] == history[0] and result[2:] == history[1:]
    assert "2026-11-09" in result[1]["content"] and "[1] New" in result[1]["content"]
    assert "new snippet" not in str(history)


@pytest.mark.parametrize("provider,tavily,brave,url,expected", [
    ("auto", "t", "b", "url", "tavily"), ("auto", None, "b", "url", "brave"),
    ("auto", None, None, "url", "searxng"), ("auto", None, None, "", None),
    ("tavily", None, "b", "url", None), ("searxng", "t", "b", "url", "searxng"),
])
def test_provider_order(provider, tavily, brave, url, expected):
    assert search.choose_provider(provider, tavily, brave, url) == expected


def test_merge_limits_and_sanitizes():
    rows = [{"title": "<b>Title</b>", "url": "https://Example.com/a#one", "content": "x" * 800},
            {"title": "Duplicate", "url": "https://example.com/a#two"},
            {"url": "file:///secret"}, {"url": "https://user:password@example.com/a"}]
    rows += [{"url": f"https://example.com/{i}"} for i in range(12)]
    results = search.merge_results([rows], 8)
    assert len(results) == 8 and [s["n"] for s in results] == list(range(1, 9))
    assert results[0] == {"n": 1, "title": "Title", "url": "https://example.com/a", "domain": "example.com", "snippet": "x" * 700}


@pytest.mark.parametrize("provider", ["searxng", "tavily", "brave"])
def test_search_adapters_parallel_dedup(provider):
    async def run():
        active, maximum = 0, 0

        async def respond(request):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(.01)
            active -= 1
            if provider == "tavily":
                assert request.method == "POST"
                body = json.loads(request.content)
                assert body["api_key"] == "t" and body["search_depth"] == "basic" and body["max_results"] == 5
            if provider == "brave":
                assert request.headers["X-Subscription-Token"] == "b" and request.url.params["count"] == "5"
            rows = [{"url": "https://example.com/", "title": "one", "content": "text", "description": "text"}]
            return httpx.Response(200, json={"web": {"results": rows}} if provider == "brave" else {"results": rows})

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            result = await search.search_queries(client, ["one", "two", "three"], provider, "t", "b", "http://mock")
        assert result["error"] is None and len(result["sources"]) == 1 and maximum == 3
    asyncio.run(run())


def test_query_failure_falls_back_and_search_failure_is_data():
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(503))) as client:
            assert await search.plan_queries(client, "http://mock/v1", "fake", {}, "question") == ["question"]
            meta = await search.search_queries(client, ["question"], searxng_url="http://mock")
        assert meta["error"] and meta["sources"] == []
        messages = search.with_search_context([{"role": "user", "content": "question"}], meta)
        assert "일반 지식으로만" in messages[0]["content"]
    asyncio.run(run())
