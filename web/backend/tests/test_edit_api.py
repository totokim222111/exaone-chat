"""Endpoint tests use the compose PostgreSQL, isolated browser sessions and a fake LLM."""
import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi.testclient import TestClient
from app import config, main
from app.upstream import payload


def events(response):
    assert response.status_code == 200, response.text
    result = []
    for block in response.text.split("\n\n"):
        lines = block.splitlines()
        name = next((line[7:] for line in lines if line.startswith("event: ")), None)
        data = next((line[6:] for line in lines if line.startswith("data: ")), None)
        if name and data:
            result.append((name, json.loads(data)))
    return result


@pytest.fixture
def api(monkeypatch):
    history = []
    generations = []

    async def fake_deltas(client, cfg, key, messages):
        history.append(messages)
        generations.append({"settings": cfg.copy(), "payload": payload(cfg, messages, True)})
        yield "reasoning", "reasoning"
        yield "content", "\n"
        yield "content", "\nanswer\n  indentation"
        yield "usage", {"promptTokens": 3, "completionTokens": 7, "reasoningTokens": 2}

    monkeypatch.setattr(main, "deltas", fake_deltas)
    with TestClient(main.app, headers={"X-Exa": "1"}) as client:
        assert client.post("/api/session", json={}).status_code == 200
        assert client.put("/api/key", json={"apiKey": "unit-integration-key"}).status_code == 200
        client.history = history
        client.generations = generations
        try:
            yield client
        finally:
            client.delete("/api/session")


def create(client, body=None):
    response = client.post("/api/conversations", json=body or {})
    assert response.status_code == 200
    return "/api/conversations/" + response.json()["id"]


def send(client, path, text):
    return events(client.post(path + "/messages", json={"content": text}))[-1][1]["message"]


def test_edit_truncates_history_preserves_id_and_usage(api):
    path = create(api)
    send(api, path, "first")
    send(api, path, "second")
    before = api.get(path).json()["messages"]
    first = before[0]
    api.put("/api/settings", json={"systemPrompt": "system text"})
    output = events(api.post(path + "/messages/" + first["id"] + "/edit", json={"content": "changed"}))
    assert [name for name, data in output][0] == "user"
    assert output[0][1]["message"]["id"] == first["id"]
    assert output[-1][0] == "done"
    after = api.get(path).json()
    assert after["title"] == "changed"
    assert len(after["messages"]) == 2
    assert after["messages"][0]["content"] == "changed"
    assert after["messages"][0]["id"] == first["id"]
    assert after["messages"][0]["createdAt"] == first["createdAt"]
    assert after["messages"][0]["usage"] is None
    assert after["messages"][1]["seq"] == 2
    assert after["messages"][1]["usage"] == {"promptTokens": 3, "completionTokens": 7, "reasoningTokens": 2}
    assert after["messages"][1]["content"] == "answer\n  indentation"
    assert "".join(data["delta"] for name, data in output if name == "content") == after["messages"][1]["content"]
    assert {m["id"] for m in before[1:]}.isdisjoint({m["id"] for m in after["messages"]})
    assert api.history[-1] == [{"role": "system", "content": "system text"}, {"role": "user", "content": "changed"}]


def test_edit_middle_keeps_only_preceding_context(api):
    path = create(api)
    send(api, path, "first")
    send(api, path, "second")
    send(api, path, "third")
    before = api.get(path).json()["messages"]
    output = events(api.post(path + "/messages/" + before[2]["id"] + "/edit", json={"content": "edited second"}))
    after = api.get(path).json()["messages"]
    assert len(after) == 4 and after[:2] == before[:2]
    assert after[2]["id"] == before[2]["id"] and after[2]["seq"] == 3
    assert output[-1][1]["message"]["seq"] == 4
    assert api.history[-1] == [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "answer\n  indentation"},
        {"role": "user", "content": "edited second"},
    ]


@pytest.mark.parametrize("rename", [False, True])
def test_edit_preserves_manual_titles(api, rename):
    path = create(api, {} if rename else {"title": "manual title"})
    send(api, path, "first")
    if rename:
        api.patch(path, json={"title": "manual title"})
    first = api.get(path).json()["messages"][0]["id"]
    events(api.post(path + "/messages/" + first + "/edit", json={"content": "new first"}))
    assert api.get(path).json()["title"] == "manual title"


def test_edit_assistant_400_missing_or_wrong_conversation_404(api):
    path = create(api)
    assistant = send(api, path, "first")
    before = api.get(path).json()["messages"]
    response = api.post(path + "/messages/" + assistant["id"] + "/edit", json={"content": "invalid"})
    assert response.status_code == 400 and response.json()["error"]["code"] == "not_user_message"
    response = api.post(path + "/messages/" + str(uuid.uuid4()) + "/edit", json={"content": "missing"})
    assert response.status_code == 404
    other = create(api)
    response = api.post(other + "/messages/" + before[0]["id"] + "/edit", json={"content": "wrong conversation"})
    assert response.status_code == 404
    assert api.get(path).json()["messages"] == before


def test_edit_other_session_404(api):
    path = create(api)
    send(api, path, "first")
    mid = api.get(path).json()["messages"][0]["id"]
    original_sid = api.cookies.get("exa_sid")
    try:
        api.post("/api/session", json={}, headers={"Cookie": "exa_sid=invalid"})
        response = api.post(path + "/messages/" + mid + "/edit", json={"content": "intrusion"})
        assert response.status_code == 404
    finally:
        api.delete("/api/session")
        api.cookies.clear()
        api.cookies.set("exa_sid", original_sid)


def test_edit_during_generation_409(api, monkeypatch):
    import asyncio
    path = create(api)
    send(api, path, "first")
    first = api.get(path).json()["messages"][0]["id"]
    started, release = threading.Event(), threading.Event()

    async def blocked(client, cfg, key, messages):
        started.set()
        await asyncio.to_thread(release.wait)
        yield "content", "finished"

    monkeypatch.setattr(main, "deltas", blocked)
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(api.post, path + "/messages", json={"content": "ongoing"})
        try:
            assert started.wait(5)
            response = api.post(path + "/messages/" + first + "/edit", json={"content": "conflict"})
            assert response.status_code == 409 and response.json()["error"]["code"] == "busy"
        finally:
            release.set()
        assert pending.result(timeout=5).status_code == 200
    assert api.get(path).json()["messages"][0]["content"] == "first"


@pytest.mark.parametrize("content", ["", " ", "x" * 32001])
def test_edit_input_validation_does_not_delete_messages(api, content):
    path = create(api)
    send(api, path, "first")
    before = api.get(path).json()["messages"]
    response = api.post(path + "/messages/" + before[0]["id"] + "/edit", json={"content": content})
    assert response.status_code == 400
    assert api.get(path).json()["messages"] == before


def test_conversation_reasoning_remembered_and_new_defaults(api):
    path = create(api)
    output = events(api.post(path + "/messages", json={"content": "low choice", "reasoningEffort": "low", "thinking": False}))
    assert output[-1][1]["message"]["reasoningEffort"] == "low"
    assert output[0][1]["message"]["reasoningEffort"] is None
    assert output[-1][1]["conversation"]["reasoningEffort"] == "low"
    assert output[-1][1]["conversation"]["thinking"] is False
    assert api.generations[-1]["payload"]["reasoning_effort"] == "low"
    defaults = api.get("/api/me").json()["settings"]
    assert defaults["reasoningEffort"] == "low" and defaults["thinking"] is False
    next_path = create(api)
    next_conv = api.get(next_path).json()
    assert next_conv["reasoningEffort"] == "low" and next_conv["thinking"] is False
    assert send(api, next_path, "new defaults")["reasoningEffort"] == "low"
    # Changing new-conversation defaults must not change choices in existing conversations.
    api.put("/api/settings", json={"reasoningEffort": "max", "thinking": True})
    assert send(api, path, "remember original choice")["reasoningEffort"] == "low"
    assert api.generations[-1]["settings"]["thinking"] is False
    assert api.get("/api/me").json()["settings"]["reasoningEffort"] == "max"
    listing = api.get("/api/conversations").json()
    assert all(c["reasoningEffort"] == "low" and c["thinking"] is False for c in listing)


@pytest.mark.parametrize("operation", ["messages", "edit", "retry"])
def test_all_generation_endpoints_accept_options(api, operation):
    path = create(api)
    send(api, path, "first")
    user = api.get(path).json()["messages"][0]["id"]
    target = path + (f"/messages/{user}/edit" if operation == "edit" else "/" + operation)
    body = {"reasoningEffort": "high", "thinking": False}
    if operation != "retry":
        body["content"] = "changed choice"
    output = events(api.post(target, json=body))
    assert output[-1][1]["message"]["reasoningEffort"] == "high"
    assert api.generations[-1]["payload"]["reasoning_effort"] == "high"
    assert api.generations[-1]["settings"]["thinking"] is False
    assert api.get(path).json()["reasoningEffort"] == "high"
    assert api.get("/api/me").json()["settings"]["reasoningEffort"] == "high"
    # Omitted retry body remains supported, and uses the conversation's choice.
    again = events(api.post(path + "/retry"))
    assert again[-1][1]["message"]["reasoningEffort"] == "high"


def test_create_options_and_other_conversation_isolation(api):
    original = create(api, {"reasoningEffort": "low", "thinking": False})
    other = create(api, {"reasoningEffort": "high", "thinking": True})
    defaults = api.get("/api/me").json()["settings"]
    assert defaults["reasoningEffort"] == "high" and defaults["thinking"] is True
    assert send(api, original, "original")["reasoningEffort"] == "low"
    assert api.generations[-1]["settings"]["thinking"] is False
    assert send(api, other, "other")["reasoningEffort"] == "high"
    newest = api.get(create(api)).json()
    assert newest["reasoningEffort"] == "high" and newest["thinking"] is True


def test_non_exaone_thinking_is_remembered_and_message_effort_is_null(api):
    api.put("/api/settings", json={"baseUrl": config.FRIENDLI})
    path = create(api)
    output = events(api.post(path + "/messages", json={"content": "thinking off", "reasoningEffort": "low", "thinking": False}))
    assert output[-1][1]["message"]["reasoningEffort"] is None
    assert api.generations[-1]["payload"]["chat_template_kwargs"] == {"enable_thinking": False}
    assert "reasoning_effort" not in api.generations[-1]["payload"]
    send(api, path, "remember off")
    assert api.generations[-1]["payload"]["chat_template_kwargs"] == {"enable_thinking": False}
    events(api.post(path + "/retry", json={"thinking": True}))
    assert api.generations[-1]["payload"]["chat_template_kwargs"] == {"enable_thinking": True}
    assert api.get(path).json()["thinking"] is True
    assert api.get("/api/me").json()["settings"]["thinking"] is True
    assert api.get(create(api)).json()["thinking"] is True


def test_null_legacy_conversation_falls_back_to_session_defaults(api):
    import hashlib
    path = create(api)
    cid = uuid.UUID(path.rsplit("/", 1)[1])
    token_hash = hashlib.sha256(api.cookies.get("exa_sid").encode()).digest()
    api.portal.call(main.app.state.pool.execute,
        "UPDATE conversations c SET reasoning_effort=NULL,thinking=NULL FROM clients s WHERE c.id=$1 AND c.client_id=s.id AND s.token_hash=$2", cid, token_hash)
    old = api.get(path).json()
    assert old["reasoningEffort"] is None and old["thinking"] is None
    api.put("/api/settings", json={"reasoningEffort": "high", "thinking": False})
    assert send(api, path, "legacy defaults")["reasoningEffort"] == "high"
    remembered = api.get(path).json()
    assert remembered["reasoningEffort"] == "high" and remembered["thinking"] is False


@pytest.mark.parametrize("operation", ["create", "messages", "edit", "retry"])
@pytest.mark.parametrize("invalid", [
    {"reasoningEffort": "medium"}, {"reasoningEffort": None},
    {"thinking": "false"}, {"thinking": 1}, {"thinking": None},
])
def test_invalid_generation_options_400_without_mutation(api, operation, invalid):
    path = create(api)
    send(api, path, "first")
    before = api.get(path).json()
    defaults = api.get("/api/me").json()["settings"]
    user = before["messages"][0]["id"]
    target = "/api/conversations" if operation == "create" else path + (
        f"/messages/{user}/edit" if operation == "edit" else "/" + operation)
    body = invalid | ({"content": "invalid options"} if operation in ("messages", "edit") else {})
    response = api.post(target, json=body)
    assert response.status_code == 400 and response.json()["error"]["code"] == "validation"
    assert api.get(path).json() == before
    assert api.get("/api/me").json()["settings"] == defaults


def test_malformed_generation_json_is_400(api):
    path = create(api)
    response = api.post(path + "/messages", content=b"not json", headers={"Content-Type": "application/json"})
    assert response.status_code == 400 and response.json()["error"]["code"] == "validation"


@pytest.fixture
def search_api(api, monkeypatch):
    api.search_calls = []

    async def plan(client, base, key, body, question):
        assert body["max_tokens"] == 512 and body["reasoning_effort"] == "low" and body["stream"] is False
        return [question]

    async def search(client, queries, **options):
        api.search_calls.append(options)
        provider = main.search_pipeline.choose_provider(options["provider"], options.get("tavily_key"),
            options.get("brave_key"), options["searxng_url"])
        failed = "fail" in queries[0]
        return {"queries": queries, "provider": provider, "sources": [] if failed else [
            {"n": 1, "title": "Source", "url": "https://example.com/", "domain": "example.com", "snippet": "search evidence"}],
            "error": "검색 실패" if failed else None}

    monkeypatch.setattr(main.search_pipeline, "plan_queries", plan)
    monkeypatch.setattr(main.search_pipeline, "search_queries", search)
    return api


def test_search_events_persist_and_only_current_sources_in_prompt(search_api):
    api = search_api
    api.put("/api/settings", json={"systemPrompt": "my instructions"})
    path = create(api, {"webSearch": True})
    assert api.get(path).json()["webSearch"] is True
    output = events(api.post(path + "/messages", json={"content": "search this"}))
    stages = [data["stage"] for name, data in output if name == "search"]
    assert stages == ["queries", "results"]
    names = [name for name, data in output]
    assert names.index("search") < names.index("reasoning")
    saved = api.get(path).json()["messages"]
    assert saved[0]["search"] is None
    assert saved[-1]["search"]["queries"] == ["search this"]
    assert saved[-1] == output[-1][1]["message"]
    assert api.history[-1][0] == {"role": "system", "content": "my instructions"}
    assert main.search_pipeline.CONTEXT_MARKER in api.history[-1][1]["content"]
    send(api, path, "new search")
    assert sum(main.search_pipeline.CONTEXT_MARKER in m["content"] for m in api.history[-1]) == 1
    assert api.get("/api/me").json()["settings"]["webSearch"] is True
    assert api.get(create(api)).json()["webSearch"] is True


def test_search_failure_continues_and_web_search_false_is_remembered(search_api):
    api = search_api
    path = create(api)
    output = events(api.post(path + "/messages", json={"content": "fail search", "webSearch": True}))
    assert [data["stage"] for name, data in output if name == "search"] == ["queries", "error"]
    assert output[-1][0] == "done" and output[-1][1]["message"]["error"] is None
    assert output[-1][1]["message"]["search"]["error"]
    output = events(api.post(path + "/retry", json={"webSearch": False}))
    assert all(name != "search" for name, data in output)
    assert output[-1][1]["message"]["search"] is None
    assert api.get(path).json()["webSearch"] is False
    assert api.get("/api/me").json()["settings"]["webSearch"] is False


def test_search_keys_encrypt_update_summary_and_session_isolation(search_api):
    import hashlib
    api = search_api
    tavily, brave = "synthetic-tavily-1234", "synthetic-brave-5678"
    response = api.put("/api/search-keys", json={"tavilyKey": tavily, "braveKey": brave})
    assert response.status_code == 200
    expected = {"tavily": {"hasKey": True, "last4": "1234"}, "brave": {"hasKey": True, "last4": "5678"}}
    assert response.json() == expected and tavily not in response.text and brave not in response.text
    assert api.get("/api/me").json()["searchKeys"] == expected
    assert api.post("/api/session", json={}).json()["searchKeys"] == expected
    token = hashlib.sha256(api.cookies.get("exa_sid").encode()).digest()
    row = api.portal.call(main.app.state.pool.fetchrow, "SELECT * FROM clients WHERE token_hash=$1", token)
    assert tavily.encode() not in row["tavily_key_enc"]
    assert main.app.state.cipher.decrypt(row["tavily_key_enc"]) == tavily
    assert main.app.state.cipher.decrypt(row["brave_key_enc"]) == brave
    original = api.cookies.get("exa_sid")
    try:
        api.post("/api/session", json={}, headers={"Cookie": "exa_sid=invalid"})
        assert all(not item["hasKey"] for item in api.get("/api/me").json()["searchKeys"].values())
        api.put("/api/search-keys", json={"tavilyKey": ""})
    finally:
        api.delete("/api/session")
        api.cookies.clear()
        api.cookies.set("exa_sid", original)
    assert api.get("/api/me").json()["searchKeys"] == expected
    response = api.put("/api/search-keys", json={"tavilyKey": ""}).json()
    assert response["tavily"] == {"hasKey": False, "last4": None} and response["brave"]["hasKey"]
    api.post("/api/search/test", json={})
    assert api.search_calls[-1]["brave_key"] == brave and api.search_calls[-1]["tavily_key"] is None
    assert api.put("/api/search-keys", json={"braveKey": ""}).json()["brave"]["hasKey"] is False


def test_search_rate_limit_separate_from_chat(search_api):
    api = search_api
    for i in range(10):
        assert api.post("/api/search/test", json={"provider": "searxng"}).json()["ok"]
    response = api.post("/api/search/test", json={})
    assert response.status_code == 429
    path = create(api)
    assert send(api, path, "ordinary chat")["content"]
    response = api.post(path + "/messages", json={"content": "over quota", "webSearch": True})
    assert response.status_code == 429
    assert len(api.get(path).json()["messages"]) == 2


@pytest.mark.parametrize("operation", ["messages", "edit", "retry"])
def test_search_flag_on_all_generation_endpoints(search_api, operation):
    api = search_api
    path = create(api)
    send(api, path, "first")
    user = api.get(path).json()["messages"][0]["id"]
    target = path + (f"/messages/{user}/edit" if operation == "edit" else "/" + operation)
    body = {"webSearch": True} | ({"content": "search"} if operation != "retry" else {})
    output = events(api.post(target, json=body))
    assert output[-1][1]["message"]["search"] is not None


@pytest.mark.parametrize("value", ["yes", 1, None])
def test_invalid_search_flag_rejected(api, value):
    assert api.post("/api/conversations", json={"webSearch": value}).status_code == 400
    assert api.put("/api/settings", json={"webSearch": value}).status_code == 422


def test_client_cannot_change_search_server(api):
    assert api.put("/api/settings", json={"searxngUrl": "http://evil/"}).status_code == 422
