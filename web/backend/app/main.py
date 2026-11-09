import asyncio
import hashlib
import json
import logging
import secrets
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from datetime import timezone
from urllib.parse import urlsplit

import httpx
from fastapi import Body, Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse, Response

from . import attachments, db, features
from .chat_metrics import Timing
from .config import (ALLOWED, COOKIE_SECURE, CUSTOM, DEFAULTS, DEFAULT_BASE_URL, DEFAULT_MODEL,
                     EXAONE_STYLE_HOSTS, MODELS, REASONING_EFFORTS, SEARXNG_URL, is_exaone_style)
from .crypto import KeyCipher, load_secret
from .models import (CreateConversation, EditMessageInput, KeyInput, MessageInput, RenameConversation, RetryInput,
                     SearchKeysInput, SearchTestInput, SettingsInput)
from . import search_pipeline
from .security import RequestGuard, validation_status
from .streaming import EventResponse, LeadingWhitespaceTrimmer, ThinkSplitter, event
from .upstream import UpstreamError, deltas, test_key

logging.getLogger("httpx").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app):
    app.state.cipher = KeyCipher(load_secret())
    app.state.pool = await db.connect()
    app.state.http = httpx.AsyncClient(timeout=httpx.Timeout(connect=15, read=300, write=30, pool=15),
                                     follow_redirects=False, trust_env=False)
    app.state.active = set()
    app.state.rates = {}
    app.state.search_rates = {}
    app.state.search_probe = (0, False)
    app.state.upload_rates = {}
    app.state.public_rates = {}
    app.state.extract_slots = asyncio.Semaphore(2)
    await features.clean_unbound(app.state.pool)
    cleanup = asyncio.create_task(features.periodic_cleanup(app.state.pool))
    try:
        yield
    finally:
        cleanup.cancel()
        await asyncio.gather(cleanup, return_exceptions=True)
        await app.state.http.aclose()
        await app.state.pool.close()


app = FastAPI(title="EXAONE Web API", lifespan=lifespan, docs_url=None, redoc_url=None,
              openapi_url="/api/openapi.json")
app.add_middleware(RequestGuard)


class ApiError(Exception):
    def __init__(self, status, code, message):
        self.status, self.code, self.message = status, code, message


@app.exception_handler(ApiError)
async def api_error(request, exc):
    headers = {"Cache-Control": "no-store", "X-Robots-Tag": "noindex"} if request.url.path.startswith("/api/public/shares/") else None
    return JSONResponse({"error": {"code": exc.code, "message": exc.message}}, status_code=exc.status, headers=headers)


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    # Never echo Pydantic's input field, which could contain an API key.
    status = validation_status(request.scope)
    return JSONResponse({"error": {"code": "validation", "message": "입력 형식 또는 허용 범위를 확인해 주세요."}}, status_code=status)


@app.exception_handler(HTTPException)
async def http_error(request, exc):
    return JSONResponse({"error": {"code": "not_found" if exc.status_code == 404 else "http_error",
                                  "message": "요청한 경로 또는 메서드를 확인해 주세요."}}, status_code=exc.status_code)


@app.exception_handler(Exception)
async def unknown_error(request, exc):
    return JSONResponse({"error": {"code": "internal", "message": "요청을 처리하는 중 오류가 발생했습니다."}}, status_code=500)


def settings(client):
    return DEFAULTS | client["settings"]


def me(client):
    return dict(hasKey=client["api_key_enc"] is not None, keyLast4=client["api_key_last4"],
                settings=settings(client), searchKeys=search_key_summary(client))


def search_key_summary(client):
    return {name: {"hasKey": client[name + "_key_enc"] is not None, "last4": client[name + "_key_last4"]}
            for name in ("tavily", "brave")}


def search_credentials(client):
    return {name + "_key": app.state.cipher.decrypt(client[name + "_key_enc"]) if client[name + "_key_enc"] else None
            for name in ("tavily", "brave")}


async def current_client(request: Request):
    token = request.cookies.get("exa_sid", "")
    if not token or len(token) > 128:
        raise ApiError(401, "no_session", "브라우저 세션을 먼저 생성해 주세요.")
    row = await request.app.state.pool.fetchrow(
        "UPDATE clients SET last_seen_at=now() WHERE token_hash=$1 RETURNING *",
        hashlib.sha256(token.encode()).digest())
    if row is None:
        raise ApiError(401, "no_session", "브라우저 세션을 먼저 생성해 주세요.")
    return row


def timestamp(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def conversation(row):
    return {"id": str(row["id"]), "title": row["title"], "model": row["model"],
            "reasoningEffort": row["reasoning_effort"], "thinking": row["thinking"],
            "webSearch": row["web_search"],
            "starred": row["starred"], "messageCount": row.get("message_count", 0),
            "createdAt": timestamp(row["created_at"]), "updatedAt": timestamp(row["updated_at"])}


def message(row):
    return {"id": str(row["id"]), "seq": row["seq"], "role": row["role"], "content": row["content"],
            "reasoning": row["reasoning"], "error": row["error"], "stopped": row["stopped"],
            "usage": row["usage"], "reasoningEffort": row["reasoning_effort"],
            "search": row["search"],
            "timing": row["timing"], "attachments": row.get("attachments", []),
            "createdAt": timestamp(row["created_at"])}


async def generation_settings(conn, client_id, conv, overrides):
    # Merge only explicitly supplied preferences; never hold this row lock while streaming.
    if overrides:
        row = await conn.fetchrow("UPDATE clients SET settings=settings || $1::jsonb WHERE id=$2 RETURNING *",
                                  overrides, client_id)
    else:
        row = await conn.fetchrow("SELECT * FROM clients WHERE id=$1", client_id)
    if row is None:
        raise ApiError(401, "no_session", "브라우저 세션을 먼저 생성해 주세요.")
    cfg = settings(row)
    if conv is not None:
        for name, column in (("reasoningEffort", "reasoning_effort"), ("thinking", "thinking"), ("webSearch", "web_search")):
            if conv[column] is not None:
                cfg[name] = conv[column]
    return cfg | overrides


async def owned(pool, cid, client_id):
    row = await pool.fetchrow("SELECT c.*, (SELECT count(*) FROM messages m WHERE m.conversation_id=c.id) message_count FROM conversations c WHERE c.id=$1 AND c.client_id=$2", cid, client_id)
    if row is None:
        raise ApiError(404, "not_found", "대화를 찾을 수 없습니다.")
    return row


def check_busy(cid):
    if cid in app.state.active:
        raise ApiError(409, "busy", "이 대화의 응답 생성이 끝난 뒤 다시 시도해 주세요.")


def rate_limit(client_id, *, search=False):
    now = time.monotonic()
    bucket = app.state.search_rates if search else app.state.rates
    limit = 10 if search else 20
    # Drop idle clients as well as old attempts, keeping the in-memory limiter bounded over time.
    for key in [k for k, q in bucket.items() if not q or q[-1] <= now - 60]:
        del bucket[key]
    times = bucket.setdefault(client_id, deque())
    while times and times[0] <= now - 60:
        times.popleft()
    if len(times) >= limit:
        raise ApiError(429, "rate_limit", "웹 검색은 세션당 분당 10회까지 요청할 수 있습니다." if search else "메시지는 세션당 분당 20회까지 전송할 수 있습니다.")
    times.append(now)


@app.get("/api/health")
async def health():
    try:
        ok = await app.state.pool.fetchval("SELECT 1") == 1
    except Exception:
        ok = False
    return JSONResponse({"ok": ok, "db": ok}, status_code=200 if ok else 503)


@app.get("/api/config")
async def config(request: Request):
    checked, ready = app.state.search_probe
    if time.monotonic() - checked > 15:
        ready = await search_pipeline.search_status(app.state.http, SEARXNG_URL)
        app.state.search_probe = (time.monotonic(), ready)
    has_keys = False
    if request.cookies.get("exa_sid"):
        try:
            client = await current_client(request)
            has_keys = any(client[name + "_key_enc"] for name in ("tavily", "brave"))
        except ApiError:
            pass
    return dict(defaultBaseUrl=DEFAULT_BASE_URL, allowedBaseUrls=ALLOWED, allowCustomBaseUrl=CUSTOM,
                defaultModel=DEFAULT_MODEL, models=MODELS, exaoneStyleHosts=EXAONE_STYLE_HOSTS,
                reasoningEfforts=REASONING_EFFORTS, defaultReasoningEffort="max",
                search=dict(available=bool(ready or has_keys), serverProvider="searxng" if ready else None,
                            providers=search_pipeline.PROVIDERS),
                limits=dict(maxMessageChars=32000, maxSystemPromptChars=8000))


@app.post("/api/session")
async def session(request: Request):
    try:
        client = await current_client(request)
        return me(client)
    except ApiError:
        token = secrets.token_urlsafe(32)
        client = await app.state.pool.fetchrow(
            "INSERT INTO clients(id, token_hash) VALUES($1,$2) RETURNING *",
            uuid.uuid4(), hashlib.sha256(token.encode()).digest())
        response = JSONResponse(me(client))
        response.set_cookie("exa_sid", token, max_age=365 * 24 * 3600, httponly=True,
                            secure=COOKIE_SECURE, samesite="lax", path="/")
        return response


@app.get("/api/me")
async def get_me(client=Depends(current_client)):
    return me(client)


@app.put("/api/key")
async def put_key(body: KeyInput, client=Depends(current_client)):
    if body.verify:
        result = await test_key(app.state.http, settings(client), body.apiKey)
        if not result["ok"]:
            raise ApiError(400, "key_invalid", result["message"])
    await app.state.pool.execute("UPDATE clients SET api_key_enc=$1, api_key_last4=$2 WHERE id=$3",
                                 app.state.cipher.encrypt(body.apiKey), body.apiKey[-4:], client["id"])
    return dict(hasKey=True, keyLast4=body.apiKey[-4:], verified=body.verify)


@app.delete("/api/key")
async def delete_key(client=Depends(current_client)):
    await app.state.pool.execute("UPDATE clients SET api_key_enc=NULL, api_key_last4=NULL WHERE id=$1", client["id"])
    return dict(hasKey=False)


@app.post("/api/key/test")
async def key_test(client=Depends(current_client)):
    if not client["api_key_enc"]:
        raise ApiError(400, "no_key", "API 키를 먼저 저장해 주세요.")
    return await test_key(app.state.http, settings(client), app.state.cipher.decrypt(client["api_key_enc"]))


@app.put("/api/settings")
async def put_settings(body: SettingsInput, client=Depends(current_client)):
    row = await app.state.pool.fetchrow("UPDATE clients SET settings=settings || $1::jsonb WHERE id=$2 RETURNING *",
                                       body.model_dump(exclude_unset=True), client["id"])
    return settings(row)


@app.put("/api/search-keys")
async def put_search_keys(body: SearchKeysInput, client=Depends(current_client)):
    updates = body.model_dump(exclude_unset=True)
    async with app.state.pool.acquire() as conn:
        async with conn.transaction():
            for name in ("tavily", "brave"):
                if name + "Key" in updates:
                    key = updates[name + "Key"]
                    # Column names are fixed constants, never request-supplied identifiers.
                    await conn.execute(f"UPDATE clients SET {name}_key_enc=$1,{name}_key_last4=$2 WHERE id=$3",
                        app.state.cipher.encrypt(key) if key else None, key[-4:] if key else None, client["id"])
            row = await conn.fetchrow("SELECT * FROM clients WHERE id=$1", client["id"])
    return search_key_summary(row)


@app.post("/api/search/test")
async def test_search(body: SearchTestInput = Body(default_factory=SearchTestInput), client=Depends(current_client)):
    rate_limit(client["id"], search=True)
    started = time.monotonic()
    meta = await search_pipeline.search_queries(app.state.http, ["EXAONE"],
        provider=body.provider or settings(client)["searchProvider"],
        searxng_url=SEARXNG_URL, **search_credentials(client))
    return dict(ok=bool(meta["sources"]), provider=meta["provider"], resultCount=len(meta["sources"]),
                latencyMs=round((time.monotonic() - started) * 1000), message=meta["error"] or "웹 검색을 확인했습니다.")


@app.get("/api/conversations")
async def list_conversations(client=Depends(current_client)):
    rows = await app.state.pool.fetch("SELECT c.*, (SELECT count(*) FROM messages m WHERE m.conversation_id=c.id) message_count FROM conversations c WHERE client_id=$1 ORDER BY updated_at DESC, id", client["id"])
    return [conversation(r) for r in rows]


@app.post("/api/conversations")
async def create_conversation(body: CreateConversation, client=Depends(current_client)):
    async with app.state.pool.acquire() as conn:
        async with conn.transaction():
            cfg = await generation_settings(conn, client["id"], None, body.overrides())
            row = await conn.fetchrow("INSERT INTO conversations(id,client_id,title,model,title_manual,reasoning_effort,thinking,web_search) VALUES($1,$2,$3,$4,$5,$6,$7,$8) RETURNING *",
                uuid.uuid4(), client["id"], body.title, cfg["model"], "title" in body.model_fields_set,
                cfg["reasoningEffort"], cfg["thinking"], cfg["webSearch"])
    return conversation(row)


@app.get("/api/conversations/{cid}")
async def get_conversation(cid: uuid.UUID, client=Depends(current_client)):
    conv = await owned(app.state.pool, cid, client["id"])
    rows = await app.state.pool.fetch("SELECT m.* FROM messages m JOIN conversations c ON c.id=m.conversation_id WHERE c.id=$1 AND c.client_id=$2 ORDER BY m.seq", cid, client["id"])
    rows, _ = await attachments.enrich(app.state.pool, rows, cid, client["id"])
    return conversation(conv) | {"messages": [message(r) for r in rows]}


@app.patch("/api/conversations/{cid}")
async def rename(cid: uuid.UUID, body: RenameConversation, client=Depends(current_client)):
    row = await app.state.pool.fetchrow("UPDATE conversations SET title=COALESCE($1,title),title_manual=title_manual OR $2,starred=COALESCE($3,starred),updated_at=now() WHERE id=$4 AND client_id=$5 RETURNING *",
        body.title, "title" in body.model_fields_set, body.starred, cid, client["id"])
    if row is None:
        raise ApiError(404, "not_found", "대화를 찾을 수 없습니다.")
    return conversation(await owned(app.state.pool, cid, client["id"]))


@app.delete("/api/conversations/{cid}", status_code=204)
async def delete_conversation(cid: uuid.UUID, client=Depends(current_client)):
    await owned(app.state.pool, cid, client["id"])
    check_busy(cid)
    app.state.active.add(cid)
    try:
        await app.state.pool.execute("DELETE FROM conversations WHERE id=$1 AND client_id=$2", cid, client["id"])
    finally:
        app.state.active.discard(cid)
    return Response(status_code=204)


@app.delete("/api/session", status_code=204)
async def delete_session(client=Depends(current_client)):
    await app.state.pool.execute("DELETE FROM clients WHERE id=$1", client["id"])
    app.state.rates.pop(client["id"], None)
    app.state.search_rates.pop(client["id"], None)
    app.state.upload_rates.pop(client["id"], None)
    response = Response(status_code=204)
    response.delete_cookie("exa_sid", path="/", secure=COOKIE_SECURE, httponly=True, samesite="lax")
    return response


async def prepare(cid, client, content=None, retry=False, edit_id=None, overrides=None, attachment_ids=None):
    started = time.monotonic()
    pool = app.state.pool
    await owned(pool, cid, client["id"])
    check_busy(cid)
    rate_limit(client["id"])
    app.state.active.add(cid)
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                conv = await owned(conn, cid, client["id"])
                rows = list(await conn.fetch("SELECT m.* FROM messages m JOIN conversations c ON c.id=m.conversation_id WHERE c.id=$1 AND c.client_id=$2 ORDER BY m.seq", cid, client["id"]))
                if edit_id is not None:
                    index = next((i for i, r in enumerate(rows) if r["id"] == edit_id), None)
                    if index is None:
                        raise ApiError(404, "not_found", "메시지를 찾을 수 없습니다.")
                    if rows[index]["role"] != "user":
                        raise ApiError(400, "not_user_message", "사용자 메시지만 편집할 수 있습니다.")
                    rows = rows[:index + 1]
                    user = await conn.fetchrow("UPDATE messages m SET content=$1 FROM conversations c WHERE m.id=$2 AND m.conversation_id=c.id AND c.id=$3 AND c.client_id=$4 RETURNING m.*",
                                               content, edit_id, cid, client["id"])
                    await conn.execute("DELETE FROM messages m USING conversations c WHERE m.conversation_id=c.id AND c.id=$1 AND c.client_id=$2 AND m.seq>$3", cid, client["id"], user["seq"])
                    rows[-1] = user
                elif retry:
                    if not rows or rows[-1]["role"] != "assistant" or len(rows) < 2 or rows[-2]["role"] != "user":
                        raise ApiError(409, "retry_unavailable", "다시 생성할 마지막 응답이 없습니다.")
                    last = rows.pop()
                    await conn.execute("DELETE FROM messages m USING conversations c WHERE m.id=$1 AND m.conversation_id=c.id AND c.client_id=$2", last["id"], client["id"])
                    user = rows[-1]
                else:
                    seq = rows[-1]["seq"] + 1 if rows else 1
                    user = await conn.fetchrow("INSERT INTO messages(id,conversation_id,seq,role,content) SELECT $1,c.id,$2,'user',$3 FROM conversations c WHERE c.id=$4 AND c.client_id=$5 RETURNING *", uuid.uuid4(), seq, content, cid, client["id"])
                    rows.append(user)
                await attachments.bind(conn, client["id"], user["id"], attachment_ids, ApiError)
                rows, files = await attachments.enrich(conn, rows, cid, client["id"], include_text=True)
                user = rows[-1]
                if not user["content"].strip() and not user["attachments"]:
                    raise ApiError(400, "validation", "메시지 또는 첨부 파일을 입력해 주세요.")
                cfg = await generation_settings(conn, client["id"], conv, overrides or {})
                if cfg["webSearch"]:
                    rate_limit(client["id"], search=True)
                await conn.execute("UPDATE conversations SET title=CASE WHEN $1 AND NOT title_manual THEN $2 ELSE title END,model=$3,reasoning_effort=$4,thinking=$5,web_search=$6,updated_at=now() WHERE id=$7 AND client_id=$8",
                    not retry and len(rows) == 1, (user["content"] or user["attachments"][0]["name"])[:30], cfg["model"],
                    cfg["reasoningEffort"], cfg["thinking"], cfg["webSearch"], cid, client["id"])
        history = attachments.build_history(rows, files, user["id"])
        if cfg["systemPrompt"]:
            history.insert(0, {"role": "system", "content": cfg["systemPrompt"]})
        key = app.state.cipher.decrypt(client["api_key_enc"]) if client["api_key_enc"] else None
        credentials = search_credentials(client) if cfg["webSearch"] else {}
        return EventResponse(lambda q: generate(q, cid, client["id"], user, cfg, key, history, retry, credentials, started),
                             lambda: app.state.active.discard(cid))
    except BaseException:
        app.state.active.discard(cid)
        raise


async def generate(queue, cid, client_id, user, cfg, key, history, retry, search_keys=None, started=None):
    metrics = Timing(started)
    parts = {"reasoning": [], "content": []}
    splitter = ThinkSplitter()
    trimmer = LeadingWhitespaceTrimmer()
    usage = None
    failure = None
    stopped = False
    saved = None
    search = None
    search_pending = False

    def collect(kind, text):
        if kind == "content":
            text = trimmer.feed(text)
        if text:
            parts[kind].append(text)
        return text

    async def emit(kind, text):
        text = collect(kind, text)
        if text:
            metrics.token(kind)
            await queue.put(event(kind, {"delta": text}))

    try:
        if not retry:
            await queue.put(event("user", {"message": message(user)}))
        if not key:
            raise UpstreamError("no_key", None, "API 키를 먼저 저장해 주세요.")
        if cfg["webSearch"]:
            metrics.search_started = time.monotonic()
            search = dict(queries=[], provider=None, sources=[], error=None)
            search_pending = True
            question = history[-1]["content"]
            body = search_pipeline.query_body(cfg["model"], history, question,
                is_exaone_style(cfg["baseUrl"]), urlsplit(cfg["baseUrl"]).hostname == "api.friendli.ai")
            search["queries"] = await search_pipeline.plan_queries(app.state.http, cfg["baseUrl"], key, body, question)
            await queue.put(event("search", {"stage": "queries", "queries": search["queries"]}))
            search = await search_pipeline.search_queries(app.state.http, search["queries"],
                provider=cfg["searchProvider"], searxng_url=SEARXNG_URL, **(search_keys or {}))
            search_pending = False
            metrics.search_ended = time.monotonic()
            if search["error"]:
                await queue.put(event("search", {"stage": "error", "message": search["error"]}))
            else:
                await queue.put(event("search", {"stage": "results", "provider": search["provider"], "sources": search["sources"]}))
            history = search_pipeline.with_search_context(history, search)
        async for kind, text in deltas(app.state.http, cfg, key, history):
            if kind == "usage":
                usage = text
            elif kind == "reasoning":
                await emit(kind, text)
            else:
                for output_kind, value in splitter.feed(text):
                    await emit(output_kind, value)
        for kind, text in splitter.feed("", final=True):
            await emit(kind, text)
    except asyncio.CancelledError:
        stopped = True
        if search_pending:
            search["error"] = "검색이 완료되기 전에 중지되었습니다."
        for kind, text in splitter.feed("", final=True):
            collect(kind, text)
    except UpstreamError as exc:
        failure = exc.data()
        for kind, text in splitter.feed("", final=True):
            collect(kind, text)
    except Exception:
        failure = dict(kind="upstream", status=None, message="응답을 생성하는 중 오류가 발생했습니다.")
    timing = metrics.snapshot()
    async def persist():
        async with app.state.pool.acquire() as conn:
            async with conn.transaction():
                saved = await conn.fetchrow("INSERT INTO messages(id,conversation_id,seq,role,content,reasoning,error,stopped,usage,reasoning_effort,search,timing) SELECT $1,c.id,$2,'assistant',$3,$4,$5,$6,$7,$8,$9,$10 FROM conversations c WHERE c.id=$11 AND c.client_id=$12 RETURNING *",
                    uuid.uuid4(), user["seq"] + 1, "".join(parts["content"]), "".join(parts["reasoning"]), failure, stopped, usage,
                    cfg["reasoningEffort"] if is_exaone_style(cfg["baseUrl"]) else None, search, timing, cid, client_id)
                await conn.execute("UPDATE conversations SET updated_at=now() WHERE id=$1 AND client_id=$2", cid, client_id)
                conv = await owned(conn, cid, client_id) if saved is not None else None
        return saved, conv

    save_task = asyncio.create_task(persist())
    try:
        saved, conv = await asyncio.shield(save_task)
    except asyncio.CancelledError:
        # Disconnect may arrive while the database transaction is committing.
        stopped = True
        saved, conv = await save_task
        if saved is not None and not saved["stopped"]:
            saved = await app.state.pool.fetchrow("UPDATE messages m SET stopped=true FROM conversations c WHERE m.id=$1 AND m.conversation_id=c.id AND c.client_id=$2 RETURNING m.*", saved["id"], client_id)
    if not stopped and saved is not None:
        if failure:
            await queue.put(event("error", failure | {"assistantMessage": message(saved)}))
        else:
            await queue.put(event("done", {"message": message(saved), "conversation": conversation(conv)}))
    if not stopped:
        await queue.put(None)


@app.post("/api/conversations/{cid}/messages")
async def post_message(cid: uuid.UUID, body: MessageInput, client=Depends(current_client)):
    return await prepare(cid, client, content=body.content, overrides=body.overrides(), attachment_ids=body.attachmentIds)


@app.post("/api/conversations/{cid}/retry")
async def retry_message(cid: uuid.UUID, body: RetryInput = Body(default_factory=RetryInput), client=Depends(current_client)):
    return await prepare(cid, client, retry=True, overrides=body.overrides())


@app.post("/api/conversations/{cid}/messages/{message_id}/edit")
async def edit_message(cid: uuid.UUID, message_id: uuid.UUID, body: EditMessageInput, client=Depends(current_client)):
    return await prepare(cid, client, content=body.content, edit_id=message_id, overrides=body.overrides(), attachment_ids=body.attachmentIds)


features.register(app, current_client, ApiError, conversation, message, owned, timestamp)
