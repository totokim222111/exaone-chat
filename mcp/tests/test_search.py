import asyncio
import importlib.util
import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest
import server

search = server.search_pipeline


@pytest.mark.parametrize("text,expected", [
    ('{"queries":["one","two","three","four"]}', ["one", "two", "three"]),
    ('{"queries":[]}', []), ("broken", ["question"]),
    ('{"queries":[" one ","one"]}', ["one"]), ('{"queries":[null]}', ["question"]),
])
def test_query_json_and_fallback(text, expected):
    assert search.parse_queries(text, "question") == expected


def test_provider_selection_and_result_bounds():
    assert search.choose_provider("auto", "t", "b", "url") == "tavily"
    assert search.choose_provider("auto", None, "b", "url") == "brave"
    assert search.choose_provider("auto", None, None, "url") == "searxng"
    groups = [[{"url": "https://Example.com/#one", "content": "x" * 1000},
               {"url": "https://example.com/#two"}]]
    groups.append([{"url": f"https://example.com/{i}"} for i in range(10)])
    results = search.merge_results(groups, 6)
    assert len(results) == 6 and len(results[0]["snippet"]) == 700
    assert len({s["url"] for s in results}) == 6


def test_windows_search_settings_env_priority_and_secret_repr(monkeypatch, tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"searchProvider": "brave", "searxngUrl": "http://localhost:9999",
        "tavilyKey": "encrypted-tavily", "braveKey": "encrypted-brave"}), encoding="utf-8")
    monkeypatch.setenv("EXAONE_SETTINGS", str(settings))
    monkeypatch.setattr(server, "unprotect_key", lambda s: "decrypted-" + s)
    cfg = server.load_config()
    assert cfg.search_provider == "brave" and cfg.searxng_url == "http://localhost:9999"
    assert cfg.tavily_key == "decrypted-encrypted-tavily" and cfg.brave_key == "decrypted-encrypted-brave"
    assert cfg.tavily_key not in repr(cfg) and cfg.brave_key not in repr(cfg)
    monkeypatch.setenv("EXAONE_SEARCH_PROVIDER", "tavily")
    monkeypatch.setenv("SEARXNG_URL", "http://localhost:8888")
    monkeypatch.setenv("TAVILY_API_KEY", "env-tavily")
    monkeypatch.setenv("BRAVE_API_KEY", "env-brave")
    cfg = server.load_config()
    assert cfg.search_provider == "tavily" and cfg.searxng_url == "http://localhost:8888"
    assert cfg.tavily_key == "env-tavily" and cfg.brave_key == "env-brave"


def test_failed_search_still_requests_answer_and_reports_failure(monkeypatch):
    monkeypatch.setenv("EXAONE_API_KEY", "synthetic-key")

    async def failed(cfg, body, prompt, limit):
        return {"queries": [prompt], "provider": "searxng", "sources": [], "error": "검색 실패"}

    async def complete(cfg, body):
        assert "검색 실패" in body["messages"][0]["content"]
        return server.Completion("일반 지식 답변", "hidden reasoning", {"completion_tokens": 4}, .1)

    monkeypatch.setattr(server, "prepare_search", failed)
    monkeypatch.setattr(server, "request_completion", complete)
    result = asyncio.run(server.ask_exaone("question", web_search=True))
    assert "일반 지식 답변" in result and "검색 안내: 검색 실패" in result
    assert "검색어: 1개" in result and "hidden reasoning" not in result


def test_compact_format_never_appends_snippets_and_redacts_keys():
    cfg = server.Config("llm-secret", "test", "http://localhost", "model", tavily_key="tavily-secret", brave_key="brave-secret")
    meta = {"queries": ["q"], "provider": "searxng", "sources": [
        {"n": 1, "title": "Source", "url": "https://example.com/", "snippet": "PRIVATE_SNIPPET"}], "error": None}
    result = server.format_search_completion(server.Completion("Answer [1] llm-secret tavily-secret brave-secret", "", {"prompt_tokens": 2}, .1), cfg, meta, 1.2)
    assert "출처:\n[1] Source — https://example.com/" in result
    assert "PRIVATE_SNIPPET" not in result
    assert all(s not in result for s in (cfg.api_key, cfg.tavily_key, cfg.brave_key))
    assert len([line for line in result.splitlines() if line.strip()]) == 4


def test_search_tool_schema():
    tool = next(t for t in asyncio.run(server.mcp.list_tools()) if t.name == "ask_exaone")
    props = tool.inputSchema["properties"]
    assert props["web_search"]["default"] is False
    assert props["search_results"]["minimum"] == 1 and props["search_results"]["maximum"] == 10
    assert "Claude가 따로 검색할 필요 없음" in tool.description


def test_search_planner_error_uses_question():
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500))) as client:
            assert await search.plan_queries(client, "http://localhost", "fake", {}, "question") == ["question"]
    asyncio.run(run())


def test_ask_exaone_web_search_real_local_mock_integration(monkeypatch):
    path = Path(__file__).resolve().parents[2] / "web/tools/mock_upstream/server.py"
    spec = importlib.util.spec_from_file_location("exaone_local_mock", path)
    mock = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mock)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), mock.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_port}"
    monkeypatch.setenv("EXAONE_API_KEY", "good")
    monkeypatch.setenv("EXAONE_BASE_URL", base + "/v1")
    monkeypatch.setenv("EXAONE_STYLE_HOSTS", "127.0.0.1")
    monkeypatch.setenv("SEARXNG_URL", base)
    try:
        result = asyncio.run(server.ask_exaone("EXAONE latest information", web_search=True, search_results=2))
        assert "검색 답변" in result and "[1]" in result and "[2]" in result
        assert "출처:\n[1]" in result and "검색 제공자: searxng" in result
        assert "검색어: 2개" in result and "123/456/32" in result
        assert "SEARCH_SNIPPET_ONLY" not in result
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(2)
