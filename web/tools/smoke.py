"""Run against the nginx port with the mock compose override enabled; no paid calls."""
import copy
import http.cookiejar
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
import urllib.parse
import uuid
from concurrent.futures import ThreadPoolExecutor

BASE = os.getenv("SMOKE_BASE_URL", "http://localhost:" + os.getenv("WEB_PORT", "8080"))
MOCK = "http://mock:9000/v1"


class Client:
    def __init__(self):
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def fork(self):
        client = Client()
        for cookie in self.jar:
            client.jar.set_cookie(copy.copy(cookie))
        return client

    def request(self, method, path, data=None, csrf=True, expected=200):
        headers = {"Content-Type": "application/json"}
        if csrf:
            headers["X-Exa"] = "1"
        raw = json.dumps(data).encode() if data is not None else None
        request = urllib.request.Request(BASE + path, data=raw, headers=headers, method=method)
        try:
            response = self.opener.open(request, timeout=40)
        except urllib.error.HTTPError as error:
            response = error
        assert response.status == expected, (method, path, response.status, response.read().decode())
        if "text/event-stream" in response.headers.get("Content-Type", ""):
            return response
        with response:
            raw = response.read()
        return json.loads(raw) if raw else None

    def events(self, path, data=None):
        with self.request("POST", path, data) as stream:
            return list(read_events(stream))

    def upload(self, name, raw):
        boundary = "exa-smoke-" + uuid.uuid4().hex
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'
                'Content-Type: text/plain\r\n\r\n').encode() + raw + f"\r\n--{boundary}--\r\n".encode()
        request = urllib.request.Request(BASE + "/api/attachments", data=body, method="POST",
            headers={"X-Exa": "1", "Content-Type": "multipart/form-data; boundary=" + boundary})
        with self.opener.open(request, timeout=40) as response:
            assert response.status == 200
            return json.load(response)


def read_events(stream):
    name, data = "", []
    while True:
        raw = stream.readline()
        if not raw:
            return
        line = raw.decode().rstrip("\r\n")
        if not line:
            if data:
                yield name, json.loads("\n".join(data))
            name, data = "", []
        elif line.startswith("event: "):
            name = line[7:]
        elif line.startswith("data: "):
            data.append(line[6:])


def check(label):
    print("PASS " + label, flush=True)


def concurrent_streams(client):
    client.request("PUT", "/api/key", {"apiKey": "slow"})
    efforts = ["low", "high", "max", "low"]
    paths = ["/api/conversations/" + client.request("POST", "/api/conversations", {})["id"] for _ in efforts]
    markers = ["parallel-question-" + str(i) for i in range(4)]
    ready = [threading.Event() for _ in efforts]
    started = time.monotonic()

    def run(i, browser):
        output = []
        with browser.request("POST", paths[i] + "/messages", {
            "content": markers[i], "reasoningEffort": efforts[i], "thinking": i % 2 == 0,
        }) as stream:
            for name, data in read_events(stream):
                output.append((name, data))
                if name == "user":
                    ready[i].set()
        return output

    with ThreadPoolExecutor(max_workers=4) as executor:
        pending = [executor.submit(run, i, client.fork()) for i in range(4)]
        for signal in ready:
            assert signal.wait(8), "A concurrent stream did not start"
        assert all(not future.done() for future in pending), "All four streams must overlap"
        conflict = client.request("POST", paths[0] + "/messages", {"content": "duplicate"}, expected=409)
        assert conflict["error"]["code"] == "busy"
        skipped = client.request("POST", "/api/conversations/bulk-delete", {"ids": [path.rsplit("/", 1)[1] for path in paths]})
        assert skipped["deleted"] == 0 and len(skipped["skipped"]) == 4
        assert all(s["reason"] == "generating" for s in skipped["skipped"])
        outputs = [future.result(timeout=30) for future in pending]
    assert time.monotonic() - started < 30, "Slow streams ran serially instead of concurrently"
    for i, output in enumerate(outputs):
        assert output[-1][0] == "done", output[-1]
        done = output[-1][1]
        saved = client.request("GET", paths[i])
        assert len(saved["messages"]) == 2
        assert saved["messages"][0]["content"] == markers[i]
        assert saved["messages"][1] == done["message"]
        answer = done["message"]["content"]
        assert answer == "".join(data["delta"] for name, data in output if name == "content")
        assert markers[i] in answer and all(m not in answer for j, m in enumerate(markers) if j != i)
        assert done["message"]["reasoningEffort"] == efforts[i]
        assert not done["message"]["stopped"] and done["message"]["error"] is None
        assert done["message"]["usage"]["reasoningTokens"] == {"low": 16, "high": 32, "max": 48}[efforts[i]]
        assert saved["reasoningEffort"] == efforts[i] and saved["thinking"] == (i % 2 == 0)
        client.request("DELETE", paths[i], expected=204)
    check("same session: four slow streams finish concurrently without mixed content; same conversation 409")
    check("bulk-delete consistently skips active generations")


def search_smoke():
    a, b = Client(), Client()
    try:
        a.request("POST", "/api/session", {})
        b.request("POST", "/api/session", {})
        config = a.request("GET", "/api/config")["search"]
        assert config == {"available": True, "serverProvider": "searxng", "providers": ["searxng", "tavily", "brave"]}
        a.request("PUT", "/api/settings", {"baseUrl": MOCK})
        a.request("PUT", "/api/key", {"apiKey": "good"})
        keys = a.request("PUT", "/api/search-keys", {"tavilyKey": "smoke-tavily-1234", "braveKey": "smoke-brave-5678"})
        assert keys == {"tavily": {"hasKey": True, "last4": "1234"}, "brave": {"hasKey": True, "last4": "5678"}}
        assert a.request("GET", "/api/me")["searchKeys"] == keys
        assert a.request("POST", "/api/session", {})["searchKeys"] == keys
        assert not b.request("GET", "/api/me")["searchKeys"]["tavily"]["hasKey"]
        removed = a.request("PUT", "/api/search-keys", {"tavilyKey": ""})
        assert removed["tavily"] == {"hasKey": False, "last4": None} and removed["brave"]["hasKey"]
        a.request("PUT", "/api/search-keys", {"braveKey": ""})
        check("search keys save/delete/summary and cross-session isolation")
        test = a.request("POST", "/api/search/test", {"provider": "searxng"})
        assert test["ok"] and test["provider"] == "searxng" and test["resultCount"] == 2
        conv = a.request("POST", "/api/conversations", {"webSearch": True})
        assert conv["webSearch"] is True
        path = "/api/conversations/" + conv["id"]
        output = a.events(path + "/messages", {"content": "EXAONE 최신 정보를 알려줘", "webSearch": True})
        stages = [data["stage"] for name, data in output if name == "search"]
        assert stages == ["queries", "results"]
        names = [name for name, data in output]
        assert names.index("search") < names.index("reasoning") < names.index("done")
        done = output[-1][1]["message"]
        assert "[1]" in done["content"] and "[2]" in done["content"]
        assert done["search"]["provider"] == "searxng" and len(done["search"]["sources"]) == 2
        assert done["search"]["error"] is None
        assert type(done["timing"]["searchMs"]) is int
        assert all(len(s["snippet"]) <= 700 for s in done["search"]["sources"])
        assert a.request("GET", path)["messages"][-1] == done
        assert a.request("GET", "/api/me")["settings"]["webSearch"] is True
        b.request("GET", path, expected=404)
        check("webSearch queries/results events, cited answer, deduplicated sources and search persistence")
        failure = a.events(path + "/messages", {"content": "검색실패 동작 확인"})
        assert [data["stage"] for name, data in failure if name == "search"] == ["queries", "error"]
        assert failure[-1][0] == "done" and failure[-1][1]["message"]["error"] is None
        assert failure[-1][1]["message"]["search"]["error"]
        assert a.request("GET", path)["messages"][-1]["search"]["error"]
        plain = a.events(path + "/messages", {"content": "검색 끄기", "webSearch": False})
        assert all(name != "search" for name, data in plain)
        assert plain[-1][1]["message"]["search"] is None
        assert a.request("GET", path)["webSearch"] is False
        check("search failure still completes answer; disabling search is remembered")
    finally:
        for browser in (a, b):
            if list(browser.jar):
                browser.request("DELETE", "/api/session", expected=204)


def feature_smoke():
    a, b, public = Client(), Client(), Client()
    try:
        a.request("POST", "/api/session", {})
        b.request("POST", "/api/session", {})
        a.request("PUT", "/api/settings", {"baseUrl": MOCK})
        a.request("PUT", "/api/key", {"apiKey": "good"})
        raw = b"ATTACHMENT_PRIVATE_BODY: file evidence"
        item = a.upload("smoke-evidence.txt", raw)
        assert item["kind"] == "text" and item["size"] == len(raw) and item["textChars"] == len(raw)
        conv = a.request("POST", "/api/conversations", {})
        path = "/api/conversations/" + conv["id"]
        events = a.events(path + "/messages", {"content": "uniqueBodyNeedle attachment question", "attachmentIds": [item["id"]]})
        assert events[0][1]["message"]["attachments"][0]["id"] == item["id"]
        done = events[-1][1]["message"]
        saved = a.request("GET", path)
        assert saved["messages"][0]["attachments"][0]["name"] == "smoke-evidence.txt"
        assert saved["messages"][-1] == done
        timing = done["timing"]
        assert set(timing) == {"firstTokenMs", "reasoningMs", "answerMs", "totalMs", "searchMs"}
        assert all(type(timing[k]) is int and timing[k] >= 0 for k in timing if k != "searchMs")
        assert timing["totalMs"] >= timing["firstTokenMs"] and timing["searchMs"] is None
        with a.opener.open(BASE + "/api/attachments/" + item["id"] + "/download", timeout=10) as response:
            assert response.read() == raw
            assert response.headers["Content-Type"] == "application/octet-stream"
            assert response.headers["X-Content-Type-Options"] == "nosniff"
            assert "filename*=UTF-8''" in response.headers["Content-Disposition"]
        b.request("GET", "/api/attachments/" + item["id"] + "/download", expected=404)
        a.request("DELETE", "/api/attachments/" + item["id"], expected=409)
        check("attachment upload/send/persist/download, ownership and timing fields")
        assert a.request("PATCH", path, {"starred": True})["starred"]
        listing = a.request("GET", "/api/conversations")
        assert listing[0]["messageCount"] == 2 and listing[0]["starred"]
        for query in ("uniquebodyneedle", "smoke-evidence"):
            result = a.request("GET", "/api/search/conversations?" + urllib.parse.urlencode({"q": query}))
            assert result and result[0]["starred"]
            hit = result[0]
            assert hit["snippet"][hit["matchStart"]:hit["matchStart"] + hit["matchLength"]].lower() == query
        assert b.request("GET", "/api/search/conversations?q=uniquebodyneedle") == []
        totals = a.request("GET", "/api/usage")
        assert all(x == {"promptTokens": 123, "completionTokens": 456, "reasoningTokens": 48, "messages": 1} for x in totals.values())
        check("starred/messageCount, body/attachment-name search, isolated usage totals")
        share = a.request("POST", path + "/share", {})
        second_share = a.request("POST", path + "/share", {})
        shared_path = "/api/public/shares/" + share["token"]
        snapshot = public.request("GET", shared_path, csrf=False)
        assert len(share["token"]) == 22 and share["path"] == "/s/" + share["token"]
        encoded = json.dumps(snapshot)
        assert '"reasoning"' not in encoded and "ATTACHMENT_PRIVATE_BODY" not in encoded
        assert snapshot["messages"][0]["attachments"] == [{"name": "smoke-evidence.txt", "kind": "text", "size": len(raw)}]
        assert len(a.request("GET", "/api/shares")) == 2
        b.request("DELETE", "/api/shares/" + share["token"], expected=404)
        foreign = b.request("POST", "/api/conversations", {})["id"]
        spare = a.request("POST", "/api/conversations", {})["id"]
        assert a.request("POST", "/api/conversations/bulk-delete", {"ids": [spare, foreign]}) == {"deleted": 1, "skipped": []}
        kept = a.request("POST", "/api/conversations/bulk-delete", {"all": True, "keepStarred": True})
        assert kept == {"deleted": 0, "skipped": [{"id": conv["id"], "reason": "starred"}]}
        assert b.request("GET", "/api/conversations/" + foreign)["id"] == foreign
        a.request("PATCH", path, {"starred": False})
        assert a.request("POST", "/api/conversations/bulk-delete", {"all": True})["deleted"] == 1
        assert public.request("GET", shared_path, csrf=False) == snapshot
        a.request("DELETE", "/api/shares/" + share["token"], expected=204)
        public.request("GET", shared_path, csrf=False, expected=404)
        a.request("DELETE", "/api/session", expected=204)
        public.request("GET", "/api/public/shares/" + second_share["token"], csrf=False, expected=404)
        check("public snapshot privacy/revocation, bulk-delete preservation/isolation and session cascade")
    finally:
        for browser in (a, b):
            if list(browser.jar):
                browser.request("DELETE", "/api/session", expected=204)


def main():
    a, b = Client(), Client()
    conv = None
    try:
        assert a.request("GET", "/api/health") == {"ok": True, "db": True}
        config = a.request("GET", "/api/config")
        assert MOCK in config["allowedBaseUrls"], "Mock override is required; refusing any external probe"
        assert {"api.exaone.ai", "mock"} <= set(config["exaoneStyleHosts"])
        assert config["reasoningEfforts"] == ["low", "high", "max"]
        assert config["defaultReasoningEffort"] == "max"
        check("config EXAONE hosts and reasoning effort fields")
        a.request("GET", "/api/me", expected=401)
        me = a.request("POST", "/api/session", {})
        assert me["hasKey"] is False
        cookie = next(iter(a.jar))
        assert cookie.name == "exa_sid" and cookie.has_nonstandard_attr("HttpOnly")
        assert a.request("POST", "/api/session", {}) == me
        check("session and persistent HttpOnly cookie")
        a.request("PUT", "/api/settings", {}, csrf=False, expected=403)
        check("CSRF 403")
        assert me["settings"]["reasoningEffort"] == "max"
        # Select mock BEFORE verify so this script never calls a real provider.
        a.request("PUT", "/api/settings", {"baseUrl": MOCK})
        bad = a.request("PUT", "/api/key", {"apiKey": "bad", "verify": True}, expected=400)
        assert bad["error"]["code"] == "key_invalid"
        assert "Invalid API key" in bad["error"]["message"]
        assert not a.request("GET", "/api/me")["hasKey"]
        check("EXAONE description-format 401 verification does not save")
        assert a.request("PUT", "/api/key", {"apiKey": "good", "verify": True})["verified"]
        assert a.request("POST", "/api/key/test", {})["ok"]
        check("good key save and non-streaming test")
        a.request("PUT", "/api/settings", {"maxTokens": 0}, expected=422)
        a.request("PUT", "/api/settings", {"reasoningEffort": "medium"}, expected=422)
        a.request("PUT", "/api/settings", {"baseUrl": "http://127.0.0.1:9000/v1"}, expected=422)
        check("input limits and Base URL allowlist")
        conv = a.request("POST", "/api/conversations", {})["id"]
        path = "/api/conversations/" + conv
        text = "첫 번째 테스트 메시지 " + "가" * 35
        events = a.events(path + "/messages", {"content": text, "reasoningEffort": "high", "thinking": True})
        assert {"user", "reasoning", "content", "done"} <= {name for name, data in events}
        done = events[-1][1]
        assert done["conversation"]["title"] == text[:30]
        saved = a.request("GET", path)
        assert len(saved["messages"]) == 2
        assert saved["messages"][-1] == done["message"]
        assert saved["messages"][0]["usage"] is None
        assert done["message"]["usage"] == {"promptTokens": 123, "completionTokens": 456, "reasoningTokens": 32}
        assert done["message"]["reasoningEffort"] == "high"
        assert done["conversation"]["reasoningEffort"] == "high" and done["conversation"]["thinking"] is True
        assert saved["messages"][0]["reasoningEffort"] is None
        assert a.request("GET", "/api/me")["settings"]["reasoningEffort"] == "high"
        answer = done["message"]["content"]
        assert answer and answer == answer.lstrip()
        assert "".join(data["delta"] for name, data in events if name == "content") == answer
        for marker in ("**EXAONE**", "`reasoning_effort`", "$E = mc^2$", "$$", "```python", "```svg", "```markdown", "| 기능 |", "> "):
            assert marker in answer, marker
        assert "생각" in saved["messages"][-1]["reasoning"] and "EXAONE" in saved["messages"][-1]["content"]
        assert a.request("GET", "/api/conversations")[0]["id"] == conv
        check("SSE user/reasoning/content/done, persistence, title and list")
        check("usage persisted, leading whitespace removed, rich Markdown fixtures")
        # New-conversation defaults may change without changing an existing conversation.
        a.request("PUT", "/api/settings", {"reasoningEffort": "max"})
        a.request("PUT", "/api/key", {"apiKey": "think"})
        events = a.events(path + "/messages", {"content": "태그 테스트"})
        result = events[-1][1]["message"]
        assert result["reasoning"] == "숨은 생각추가 생각"
        assert result["content"] == "# 태그 분리 성공\n끝"
        assert result["reasoningEffort"] == "high" and result["usage"]["reasoningTokens"] == 32
        check("think/thought tags split across chunks")
        check("request reasoning reaches upstream and omitted choice uses conversation preference")
        a.request("PUT", "/api/key", {"apiKey": "slow"})
        with a.request("POST", path + "/messages", {"content": "중지 테스트"}) as stream:
            for name, data in read_events(stream):
                if name == "content":
                    break
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            saved = a.request("GET", path)
            if saved["messages"][-1]["role"] == "assistant" and saved["messages"][-1]["stopped"]:
                break
            time.sleep(0.1)
        else:
            raise AssertionError("Disconnected stream was not saved as stopped")
        partial = saved["messages"][-1]
        assert partial["content"] and not partial["error"]
        assert partial["timing"]["totalMs"] >= 0 and partial["timing"]["answerMs"] is not None
        check("disconnect cancels upstream and saves stopped partial")
        a.request("PUT", "/api/key", {"apiKey": "good"})
        events = a.events(path + "/retry", {})
        assert "user" not in [name for name, data in events]
        assert events[-1][0] == "done" and not events[-1][1]["message"]["stopped"]
        saved = a.request("GET", path)
        assert len(saved["messages"]) == 6 and saved["messages"][-1]["id"] != partial["id"]
        check("retry replaces stopped assistant without duplicating user")
        # Exercise persisted SSE error and retry after an error as well.
        a.request("DELETE", "/api/key")
        errors = a.events(path + "/messages", {"content": "키 없음"})
        assert errors[-1][0] == "error" and errors[-1][1]["kind"] == "no_key"
        assert errors[-1][1]["assistantMessage"]["error"]["kind"] == "no_key"
        assert errors[-1][1]["assistantMessage"]["timing"]["firstTokenMs"] is None
        a.request("PUT", "/api/key", {"apiKey": "bad"})
        errors = a.events(path + "/retry", {})
        assert errors[-1][1]["kind"] == "auth" and errors[-1][1]["status"] == 401
        a.request("PUT", "/api/key", {"apiKey": "good"})
        assert a.events(path + "/retry", {})[-1][0] == "done"
        check("no_key/auth error persistence and error retry")
        assert a.request("PATCH", path, {"title": "변경된 제목"})["title"] == "변경된 제목"
        check("rename")
        before = a.request("GET", path)["messages"]
        first_user = before[0]["id"]
        edit_path = path + "/messages/" + first_user + "/edit"
        a.request("POST", path + "/messages/" + before[1]["id"] + "/edit", {"content": "잘못된 편집"}, expected=400)
        events = a.events(edit_path, {"content": "편집된 첫 질문", "reasoningEffort": "low", "thinking": False})
        assert events[0][0] == "user" and events[0][1]["message"]["id"] == first_user
        assert events[-1][0] == "done"
        edited = a.request("GET", path)
        assert len(edited["messages"]) == 2 and edited["messages"][0]["content"] == "편집된 첫 질문"
        assert edited["messages"][0]["seq"] == 1 and edited["messages"][1]["seq"] == 2
        assert edited["title"] == "변경된 제목"
        assert not ({m["id"] for m in before[1:]} & {m["id"] for m in edited["messages"]})
        assert edited["messages"][-1]["usage"]["reasoningTokens"] == 16
        assert edited["messages"][-1]["reasoning"].count("단계") == 1
        assert edited["messages"][-1]["reasoningEffort"] == "low"
        assert edited["reasoningEffort"] == "low" and edited["thinking"] is False
        check("edit regenerates, deletes all later messages, preserves renamed title, uses low effort")
        a.request("PUT", "/api/key", {"apiKey": "svg"})
        svg_events = a.events(path + "/messages", {"content": "SVG만"})
        svg = svg_events[-1][1]["message"]["content"]
        assert svg.startswith("<svg ") and svg.endswith("</svg>") and "```" not in svg
        assert svg_events[-1][1]["message"]["reasoningEffort"] == "low"
        check("bare SVG fixture")
        next_conv = a.request("POST", "/api/conversations", {})
        assert next_conv["reasoningEffort"] == "low" and next_conv["thinking"] is False
        next_path = "/api/conversations/" + next_conv["id"]
        assert a.events(next_path + "/messages", {"content": "new conversation defaults"})[-1][1]["message"]["reasoningEffort"] == "low"
        a.request("DELETE", next_path, expected=204)
        check("edit choice updates session defaults; new conversation inherits the last choice")
        b.request("POST", "/api/session", {})
        b.request("GET", path, expected=404)
        b.request("PATCH", path, {"title": "침입"}, expected=404)
        b.request("DELETE", path, expected=404)
        b.request("POST", path + "/messages", {"content": "침입"}, expected=404)
        b.request("POST", path + "/retry", {}, expected=404)
        b.request("POST", edit_path, {"content": "침입"}, expected=404)
        check("cross-session GET/PATCH/DELETE/send/retry/edit isolation 404")
        concurrent_streams(a)
        a.request("DELETE", path, expected=204)
        a.request("GET", path, expected=404)
        conv = None
        check("conversation deletion")
        # Leave one conversation so cascade deletion is explicitly checked.
        orphan = a.request("POST", "/api/conversations", {})["id"]
        a.request("DELETE", "/api/session", expected=204)
        assert not list(a.jar)
        a.request("GET", "/api/me", expected=401)
        a.request("POST", "/api/session", {})
        assert not a.request("GET", "/api/me")["hasKey"]
        assert a.request("GET", "/api/conversations") == []
        a.request("GET", "/api/conversations/" + orphan, expected=404)
        check("session deletion, cookie removal, fresh settings/key/history")
    finally:
        # Best-effort cleanup of only sessions created by this run.
        for c in (a, b):
            if list(c.jar):
                try:
                    c.request("DELETE", "/api/session", expected=204)
                except Exception:
                    pass
    search_smoke()
    feature_smoke()
    print("ALL SMOKE CHECKS PASSED", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("FAIL", str(error), file=sys.stderr)
        sys.exit(1)
