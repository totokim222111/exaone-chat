import asyncio
import hashlib
import secrets
import time
import uuid
from collections import deque
from urllib.parse import quote

from fastapi import Depends, File, Query, Request, UploadFile
from starlette.responses import JSONResponse, Response

from . import attachments
from .chat_metrics import share_snapshot, snippet
from .models import BulkDeleteInput
from .client_address import client_ip


def limit(bucket, key, count, error):
    now = time.monotonic()
    for old in [k for k, q in bucket.items() if not q or q[-1] <= now - 60]:
        del bucket[old]
    times = bucket.setdefault(key, deque())
    while times and times[0] <= now - 60:
        times.popleft()
    if len(times) >= count:
        raise error(429, "rate_limit", "요청이 너무 많으니 잠시 후 다시 시도해 주세요.")
    times.append(now)


async def clean_unbound(pool):
    await pool.execute("DELETE FROM attachments WHERE message_id IS NULL AND created_at < now()-interval '24 hours'")


async def periodic_cleanup(pool):
    while True:
        await asyncio.sleep(600)
        try:
            await clean_unbound(pool)
        except Exception:
            # Transient DB outages must not permanently disable the next cleanup.
            pass


def register(app, current_client, error, conv_json, msg_json, owned, timestamp):
    @app.exception_handler(attachments.AttachmentError)
    async def attachment_error(request, exc):
        return JSONResponse({"error": {"code": exc.code, "message": exc.message}}, status_code=exc.status)

    @app.post("/api/attachments")
    async def upload(request: Request, file: UploadFile = File(...), client=Depends(current_client)):
        limit(app.state.upload_rates, client["id"], 20, error)
        try:
            form = await request.form()
            if len(form.multi_items()) != 1:
                raise error(400, "validation", "file 필드에 파일 한 개를 첨부해 주세요.")
            raw = await file.read(attachments.MAX_FILE + 1)
            name = attachments.clean_name(file.filename)
            if len(raw) > attachments.MAX_FILE:
                raise attachments.AttachmentError("file_too_large", "파일은 10MB 이하여야 합니다.", 413)
            async with app.state.extract_slots:
                result = await attachments.extract_isolated(name, raw)
            row = await app.state.pool.fetchrow("INSERT INTO attachments(id,client_id,name,mime,size,kind,sha256,text,raw,text_chars,truncated) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11) RETURNING id,name,mime,size,kind,text_chars,truncated",
                uuid.uuid4(), client["id"], name, result["mime"], len(raw), result["kind"], hashlib.sha256(raw).digest(),
                result["text"], raw, result["textChars"], result["truncated"])
            return attachments.metadata(row) | {"preview": result["preview"]}
        finally:
            await file.close()

    @app.get("/api/attachments/{aid}/download")
    async def download(aid: uuid.UUID, client=Depends(current_client)):
        row = await app.state.pool.fetchrow("SELECT name,raw FROM attachments WHERE id=$1 AND client_id=$2", aid, client["id"])
        if row is None:
            raise error(404, "not_found", "첨부 파일을 찾을 수 없습니다.")
        return Response(row["raw"], media_type="application/octet-stream", headers={
            "Content-Disposition": "attachment; filename*=UTF-8''" + quote(row["name"], safe=""),
            "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})

    @app.delete("/api/attachments/{aid}", status_code=204)
    async def delete_attachment(aid: uuid.UUID, client=Depends(current_client)):
        async with app.state.pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow("SELECT message_id FROM attachments WHERE id=$1 AND client_id=$2 FOR UPDATE", aid, client["id"])
                if row is None:
                    raise error(404, "not_found", "첨부 파일을 찾을 수 없습니다.")
                if row["message_id"] is not None:
                    raise error(409, "attachment_in_use", "메시지에 연결된 첨부 파일은 삭제할 수 없습니다.")
                await conn.execute("DELETE FROM attachments WHERE id=$1 AND client_id=$2", aid, client["id"])
        return Response(status_code=204)

    @app.post("/api/conversations/bulk-delete")
    async def bulk_delete(body: BulkDeleteInput, client=Depends(current_client)):
        reserved, skipped = [], []
        try:
            async with app.state.pool.acquire() as conn:
                async with conn.transaction():
                    rows = await conn.fetch("SELECT id,starred FROM conversations WHERE client_id=$1 AND ($2 OR id=ANY($3::uuid[])) FOR UPDATE", client["id"], body.all, body.ids)
                    for row in rows:
                        reason = "starred" if body.keepStarred and row["starred"] else "generating" if row["id"] in app.state.active else None
                        if reason:
                            skipped.append({"id": str(row["id"]), "reason": reason})
                        else:
                            reserved.append(row["id"])
                            app.state.active.add(row["id"])
                    removed = await conn.fetch("DELETE FROM conversations WHERE client_id=$1 AND id=ANY($2::uuid[]) RETURNING id", client["id"], reserved)
            return dict(deleted=len(removed), skipped=skipped)
        finally:
            for cid in reserved:
                app.state.active.discard(cid)

    @app.get("/api/search/conversations")
    async def find_conversations(q: str = Query(min_length=2, max_length=100), limit: int = Query(default=30, ge=1, le=100), client=Depends(current_client)):
        pattern = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        rows = await app.state.pool.fetch("""
            SELECT * FROM (
              SELECT c.id conversation_id,c.title,c.starred,NULL::uuid message_id,NULL::text role,
                     c.title match_text,c.created_at,c.updated_at,0 priority
                FROM conversations c WHERE c.client_id=$1 AND c.title ILIKE $2
              UNION ALL
              SELECT c.id,c.title,c.starred,m.id,m.role,m.content,m.created_at,c.updated_at,1
                FROM messages m JOIN conversations c ON c.id=m.conversation_id
                WHERE c.client_id=$1 AND m.content ILIKE $2
              UNION ALL
              SELECT c.id,c.title,c.starred,m.id,m.role,a.name,m.created_at,c.updated_at,2
                FROM attachments a JOIN messages m ON m.id=a.message_id JOIN conversations c ON c.id=m.conversation_id
                WHERE c.client_id=$1 AND a.client_id=$1 AND a.name ILIKE $2
            ) hits ORDER BY updated_at DESC,priority,created_at DESC LIMIT $3
            """, client["id"], pattern, limit)
        result = []
        for row in rows:
            match = snippet(row["match_text"], q)
            if match is not None:
                result.append(dict(conversationId=str(row["conversation_id"]), title=row["title"], starred=row["starred"],
                    messageId=str(row["message_id"]) if row["message_id"] else None, role=row["role"],
                    createdAt=timestamp(row["created_at"]), **match))
        return result

    @app.post("/api/conversations/{cid}/share")
    async def create_share(cid: uuid.UUID, client=Depends(current_client)):
        async with app.state.pool.acquire() as conn:
            async with conn.transaction(isolation="repeatable_read"):
                conv = await owned(conn, cid, client["id"])
                rows = await conn.fetch("SELECT m.* FROM messages m JOIN conversations c ON c.id=m.conversation_id WHERE c.id=$1 AND c.client_id=$2 ORDER BY m.seq", cid, client["id"])
                enriched, _ = await attachments.enrich(conn, rows, cid, client["id"])
                snapshot = share_snapshot(conv_json(conv), [msg_json(r) for r in enriched])
                token = secrets.token_urlsafe(16)
                row = await conn.fetchrow("INSERT INTO shares(token,client_id,conversation_id,title,snapshot) VALUES($1,$2,$3,$4,$5) RETURNING created_at",
                    token, client["id"], cid, conv["title"], snapshot)
        return dict(token=token, path="/s/" + token, createdAt=timestamp(row["created_at"]))

    @app.get("/api/shares")
    async def list_shares(client=Depends(current_client)):
        rows = await app.state.pool.fetch("SELECT * FROM shares WHERE client_id=$1 ORDER BY created_at DESC", client["id"])
        return [dict(token=r["token"], path="/s/" + r["token"], conversationId=str(r["conversation_id"]), title=r["title"],
            createdAt=timestamp(r["created_at"]), revoked=r["revoked_at"] is not None) for r in rows]

    @app.delete("/api/shares/{token}", status_code=204)
    async def revoke(token: str, client=Depends(current_client)):
        row = await app.state.pool.fetchrow("UPDATE shares SET revoked_at=COALESCE(revoked_at,now()) WHERE token=$1 AND client_id=$2 RETURNING token", token, client["id"])
        if row is None:
            raise error(404, "not_found", "공유를 찾을 수 없습니다.")
        return Response(status_code=204)

    @app.get("/api/public/shares/{token}")
    async def public_share(token: str, request: Request):
        host = client_ip(request)
        limit(app.state.public_rates, host, 60, error)
        row = await app.state.pool.fetchrow("SELECT snapshot FROM shares WHERE token=$1 AND revoked_at IS NULL", token)
        if row is None:
            raise error(404, "not_found", "공유를 찾을 수 없습니다.")
        return JSONResponse(row["snapshot"], headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex"})

    @app.get("/api/usage")
    async def usage(client=Depends(current_client)):
        rows = await app.state.pool.fetch("""
            WITH owned_messages AS (
                SELECT m.* FROM messages m JOIN conversations c ON c.id=m.conversation_id
                WHERE c.client_id=$1 AND m.role='assistant'
            ), periods(name,since) AS (VALUES
                ('today',date_trunc('day',now() AT TIME ZONE 'Asia/Seoul') AT TIME ZONE 'Asia/Seoul'),
                ('month',date_trunc('month',now() AT TIME ZONE 'Asia/Seoul') AT TIME ZONE 'Asia/Seoul'),
                ('total','-infinity'::timestamptz))
            SELECT p.name, COALESCE(sum((m.usage->>'promptTokens')::numeric),0) prompt,
                COALESCE(sum((m.usage->>'completionTokens')::numeric),0) completion,
                COALESCE(sum((m.usage->>'reasoningTokens')::numeric),0) reasoning, count(m.id) messages
            FROM periods p LEFT JOIN owned_messages m ON m.created_at>=p.since GROUP BY p.name
            """, client["id"])
        return {r["name"]: dict(promptTokens=int(r["prompt"]), completionTokens=int(r["completion"]),
            reasoningTokens=int(r["reasoning"]), messages=r["messages"]) for r in rows}
