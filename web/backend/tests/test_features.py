import asyncio
import hashlib
import json
import uuid
from contextlib import contextmanager

import pytest
from app import features, main
from test_edit_api import api, create, events, send
from test_files import sample


def upload(api, name="file.txt", raw=b"ATTACHMENT_PRIVATE_BODY"):
    response = api.post("/api/attachments", files={"file": (name, raw, "application/octet-stream")})
    assert response.status_code == 200, response.text
    return response.json()


@contextmanager
def other_session(api):
    original = api.cookies.get("exa_sid")
    api.post("/api/session", json={}, headers={"Cookie": "exa_sid=invalid"})
    try:
        yield
    finally:
        api.delete("/api/session")
        api.cookies.clear()
        api.cookies.set("exa_sid", original)


@pytest.mark.parametrize("kind", ["text", "pdf", "docx", "xlsx", "pptx"])
def test_upload_download_each_kind(api, kind):
    name = "한글.txt" if kind == "text" else "file." + kind
    raw = b"hello" if kind == "text" else sample(kind)
    item = upload(api, name, raw)
    assert item["kind"] == kind and item["size"] == len(raw) and item["textChars"] > 0
    response = api.get("/api/attachments/" + item["id"] + "/download")
    assert response.content == raw and response.headers["content-type"] == "application/octet-stream"
    assert "filename*=UTF-8''" in response.headers["content-disposition"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert api.delete("/api/attachments/" + item["id"]).status_code == 204


def test_attachment_send_edit_keep_remove_and_retry(api):
    item = upload(api)
    path = create(api)
    result = events(api.post(path + "/messages", json={"content": "question", "attachmentIds": [item["id"]]}))
    user = result[0][1]["message"]
    assert user["attachments"][0]["id"] == item["id"] and "preview" not in user["attachments"][0]
    assert "<첨부 파일: file.txt" in api.history[-1][-1]["content"]
    assert "ATTACHMENT_PRIVATE_BODY" in api.history[-1][-1]["content"]
    before = api.get(path).json()["messages"]
    assert before[0]["attachments"] == user["attachments"] and before[1]["attachments"] == []
    assert api.delete("/api/attachments/" + item["id"]).status_code == 409
    events(api.post(path + "/retry"))
    assert "ATTACHMENT_PRIVATE_BODY" in api.history[-1][-1]["content"]
    events(api.post(path + "/messages/" + user["id"] + "/edit", json={"content": "edited"}))
    assert api.get(path).json()["messages"][0]["attachments"] == user["attachments"]
    # File-only edits may retain their attachments by omitting attachmentIds.
    events(api.post(path + "/messages/" + user["id"] + "/edit", json={"content": ""}))
    events(api.post(path + "/messages/" + user["id"] + "/edit", json={"content": "removed", "attachmentIds": []}))
    assert api.get(path).json()["messages"][0]["attachments"] == []
    assert "ATTACHMENT_PRIVATE_BODY" not in str(api.history[-1])
    assert api.delete("/api/attachments/" + item["id"]).status_code == 204


def test_file_only_send_and_attachment_ownership_and_reuse(api):
    item = upload(api)
    path = create(api)
    assert events(api.post(path + "/messages", json={"attachmentIds": [item["id"]]}))[-1][0] == "done"
    other = create(api)
    assert api.post(other + "/messages", json={"content": "reuse", "attachmentIds": [item["id"]]}).status_code == 400
    with other_session(api):
        assert api.get("/api/attachments/" + item["id"] + "/download").status_code == 404
        assert api.delete("/api/attachments/" + item["id"]).status_code == 404
        own = create(api)
        assert api.post(own + "/messages", json={"content": "steal", "attachmentIds": [item["id"]]}).status_code == 404


def test_upload_errors_csrf_and_limits(api):
    response = api.post("/api/attachments", files={"file": ("image.jpg", b"fake")})
    assert response.status_code == 415 and response.json()["error"]["code"] == "image_not_supported"
    response = api.post("/api/attachments", files={"file": ("file.txt", b"x" * (10 * 1024 * 1024 + 1))})
    assert response.status_code == 413
    response = api.post("/api/attachments", files={"file": ("file.txt", b"hi")}, headers={"X-Exa": "0"})
    assert response.status_code == 403
    api.portal.call(asyncio.sleep, 0)
    main.app.state.upload_rates.clear()
    for i in range(20):
        # Rejected extensions also consume the per-session upload allowance.
        assert api.post("/api/attachments", files={"file": ("file.exe", b"bad")}).status_code == 415
    assert api.post("/api/attachments", files={"file": ("file.txt", b"hi")}).status_code == 429


def test_unbound_cleanup_does_not_remove_attached(api):
    stale, bound = upload(api), upload(api)
    path = create(api)
    events(api.post(path + "/messages", json={"attachmentIds": [bound["id"]]}))
    token_hash = hashlib.sha256(api.cookies.get("exa_sid").encode()).digest()
    api.portal.call(main.app.state.pool.execute,
        "UPDATE attachments a SET created_at=now()-interval '25 hours' FROM clients c WHERE a.client_id=c.id AND c.token_hash=$1", token_hash)
    api.portal.call(features.clean_unbound, main.app.state.pool)
    assert api.get("/api/attachments/" + stale["id"] + "/download").status_code == 404
    assert api.get("/api/attachments/" + bound["id"] + "/download").status_code == 200


def test_starred_count_and_title_content_filename_search(api):
    path = create(api, {"title": "UniqueTitle"})
    item = upload(api, "UniqueFile.txt")
    events(api.post(path + "/messages", json={"content": "prefix UNiquebody suffix", "attachmentIds": [item["id"]]}))
    assert api.patch(path, json={"starred": True}).json()["starred"] is True
    listing = api.get("/api/conversations").json()[0]
    assert listing["messageCount"] == 2 and listing["starred"]
    for query, role in (("uniquetitle", None), ("uniquebody", "user"), ("uniquefile", "user")):
        response = api.get("/api/search/conversations", params={"q": query})
        assert response.status_code == 200
        hit = response.json()[0]
        assert hit["role"] == role and hit["starred"]
        assert hit["snippet"][hit["matchStart"]:hit["matchStart"] + hit["matchLength"]].lower() == query
    with other_session(api):
        assert api.get("/api/search/conversations", params={"q": "unique"}).json() == []
    assert api.get("/api/search/conversations", params={"q": "x"}).status_code == 422


def test_share_public_snapshot_delete_revoke_and_session_cleanup(api):
    path = create(api)
    item = upload(api)
    events(api.post(path + "/messages", json={"content": "question", "attachmentIds": [item["id"]]}))
    share = api.post(path + "/share").json()
    assert len(share["token"]) == 22 and share["path"] == "/s/" + share["token"]
    public = "/api/public/shares/" + share["token"]
    result = api.get(public, headers={"Cookie": "", "X-Exa": ""})
    assert result.status_code == 200 and result.headers["Cache-Control"] == "no-store"
    assert result.headers["X-Robots-Tag"] == "noindex"
    snapshot = result.json()
    assert "ATTACHMENT_PRIVATE_BODY" not in result.text and '"reasoning"' not in result.text
    assert snapshot["messages"][0]["attachments"] == [{"name": "file.txt", "kind": "text", "size": len(b"ATTACHMENT_PRIVATE_BODY")}]
    assert snapshot["messages"][1]["timing"] and snapshot["messages"][1]["usage"]
    with other_session(api):
        assert api.get("/api/shares").json() == []
        assert api.delete("/api/shares/" + share["token"]).status_code == 404
    assert api.get("/api/shares").json()[0]["revoked"] is False
    assert api.delete(path).status_code == 204
    assert api.get(public).json() == snapshot
    assert api.delete("/api/shares/" + share["token"]).status_code == 204
    revoked = api.get(public)
    assert revoked.status_code == 404 and revoked.headers["Cache-Control"] == "no-store"
    assert api.get("/api/shares").json()[0]["revoked"] is True
    path = create(api)
    second = api.post(path + "/share").json()
    assert api.delete("/api/session").status_code == 204
    assert api.get("/api/public/shares/" + second["token"]).status_code == 404


def test_public_share_ip_rate_limit(api, monkeypatch):
    monkeypatch.setenv("TRUST_PROXY_HEADERS", "true")
    for _ in range(60):
        assert api.get("/api/public/shares/missing", headers={"X-Forwarded-For": "127.0.0.2"}).status_code == 404
    assert api.get("/api/public/shares/missing", headers={"X-Forwarded-For": "127.0.0.2"}).status_code == 429
    assert api.get("/api/public/shares/missing", headers={"X-Forwarded-For": "127.0.0.3"}).status_code == 404


def test_bulk_delete_skips_starred_generating_and_other_sessions(api):
    starred, generating, ordinary = create(api), create(api), create(api)
    api.patch(starred, json={"starred": True})
    foreign = None
    with other_session(api):
        foreign = create(api).rsplit("/", 1)[1]
        # The other session is cleaned up by the context manager.
    gid = uuid.UUID(generating.rsplit("/", 1)[1])
    main.app.state.active.add(gid)
    try:
        response = api.post("/api/conversations/bulk-delete", json={"all": True, "keepStarred": True}).json()
        assert response["deleted"] == 1
        assert {s["reason"] for s in response["skipped"]} == {"starred", "generating"}
    finally:
        main.app.state.active.discard(gid)
    response = api.post("/api/conversations/bulk-delete", json={"ids": [foreign, starred.rsplit("/", 1)[1]]}).json()
    assert response == {"deleted": 1, "skipped": []}
    assert api.get(generating).status_code == 200 and api.get(ordinary).status_code == 404
    assert api.post("/api/conversations/bulk-delete", json={"all": True}).json()["deleted"] == 1


def test_usage_timing_and_seoul_periods(api):
    path = create(api)
    first = send(api, path, "first")
    timing = first["timing"]
    assert set(timing) == {"firstTokenMs", "reasoningMs", "answerMs", "totalMs", "searchMs"}
    assert all(type(timing[k]) is int and timing[k] >= 0 for k in timing if k != "searchMs")
    assert timing["searchMs"] is None
    totals = api.get("/api/usage").json()
    assert all(t == {"promptTokens": 3, "completionTokens": 7, "reasoningTokens": 2, "messages": 1} for t in totals.values())
    # Move one answer before this Seoul month: it contributes only to total.
    cid = uuid.UUID(path.rsplit("/", 1)[1])
    token_hash = hashlib.sha256(api.cookies.get("exa_sid").encode()).digest()
    api.portal.call(main.app.state.pool.execute, "UPDATE messages m SET created_at=(date_trunc('month',now() AT TIME ZONE 'Asia/Seoul') AT TIME ZONE 'Asia/Seoul')-interval '1 second' FROM conversations c JOIN clients s ON s.id=c.client_id WHERE m.conversation_id=c.id AND c.id=$1 AND s.token_hash=$2 AND m.role='assistant'", cid, token_hash)
    second = send(api, path, "second")
    totals = api.get("/api/usage").json()
    assert totals["total"]["messages"] == 2 and totals["total"]["promptTokens"] == 6
    assert totals["today"]["messages"] == totals["month"]["messages"] == 1
    with other_session(api):
        assert api.get("/api/usage").json()["total"]["messages"] == 0
