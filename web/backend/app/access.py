import asyncio
import base64
import hashlib
import hmac
import math
import os
import secrets
import time
import uuid
from collections import deque
from datetime import datetime, timedelta, timezone

from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from starlette.responses import JSONResponse

from .client_address import client_ip
from .config import COOKIE_SECURE

N, R, P = 2 ** 15, 8, 1
GRANT_AGE = 400 * 24 * 3600


def username():
    return os.getenv("ACCESS_USERNAME", "").strip()


def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=N, r=R, p=P, dklen=64, maxmem=128 * 1024 * 1024)
    return f"scrypt${N}${R}${P}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password, encoded):
    try:
        method, n, r, p, salt, stored = encoded.split("$")
        if method != "scrypt" or (int(n), int(r), int(p)) != (N, R, P):
            return False
        salt, stored = base64.b64decode(salt, validate=True), base64.b64decode(stored, validate=True)
        if len(salt) != 16 or len(stored) != 64:
            return False
        digest = hashlib.scrypt(password.encode(), salt=salt, n=N, r=R, p=P, dklen=64, maxmem=128 * 1024 * 1024)
        return hmac.compare_digest(digest, stored)
    except (ValueError, TypeError, AttributeError):
        return False


class AccessError(Exception):
    def __init__(self, status, code, message, remaining=None):
        self.status, self.code, self.message, self.remaining = status, code, message, remaining


class LoginInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=200)
    password: SecretStr


class LoginLimiter:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.entries = {}

    def entry(self, ip):
        now = self.clock()
        for key in [k for k, v in self.entries.items() if v["touched"] <= now - 7200]:
            del self.entries[key]
        item = self.entries.setdefault(ip, dict(attempts=deque(), failures=deque(), locked=0, touched=now))
        item["touched"] = now
        for name, age in (("attempts", 60), ("failures", 3600)):
            while item[name] and item[name][0] <= now - age:
                item[name].popleft()
        return now, item

    @staticmethod
    def blocked(remaining):
        seconds = max(1, math.ceil(remaining))
        raise AccessError(429, "access_locked", f"로그인 시도가 잠겼습니다 (남은 시간: {seconds}초).", seconds)

    def attempt(self, ip):
        now, item = self.entry(ip)
        if item["locked"] > now:
            self.blocked(item["locked"] - now)
        if len(item["attempts"]) >= 5:
            self.blocked(item["attempts"][0] + 60 - now)
        item["attempts"].append(now)

    def failed(self, ip):
        now, item = self.entry(ip)
        item["failures"].append(now)
        if len(item["failures"]) > 20:
            item["locked"] = now + 3600
            return 3600
        return None


async def failure_delay():
    await asyncio.sleep(.5)


async def grant_for(request, touch=True):
    configured_name = username()
    token = request.cookies.get("exa_access", "")
    if not configured_name or not token or len(token) > 128:
        return None
    pool = request.app.state.pool
    row = await pool.fetchrow("SELECT * FROM access_grants WHERE token_hash=$1 AND username=$2 AND revoked_at IS NULL AND created_at>now()-interval '400 days'",
                              hashlib.sha256(token.encode()).digest(), configured_name)
    if row is not None and touch and row["last_seen_at"] < datetime.now(timezone.utc) - timedelta(hours=1):
        updated = await pool.fetchrow("UPDATE access_grants SET last_seen_at=now() WHERE id=$1 AND username=$2 AND revoked_at IS NULL AND last_seen_at<now()-interval '1 hour' RETURNING *", row["id"], configured_name)
        if updated is not None:
            row = updated
        else:
            row = await pool.fetchrow("SELECT * FROM access_grants WHERE id=$1 AND username=$2 AND revoked_at IS NULL AND created_at>now()-interval '400 days'", row["id"], configured_name)
    return row


def exempt(path):
    return path in ("/api/health", "/api/config", "/api/access") or path.startswith(("/api/access/", "/api/public/shares/"))


class AccessGate:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and username() and scope["path"].startswith("/api/") and not exempt(scope["path"]):
            grant = await grant_for(Request(scope))
            if grant is None:
                response = JSONResponse({"error": {"code": "access_required", "message": "이 브라우저에서 먼저 로그인해 주세요."}}, status_code=401,
                                        headers={"Cache-Control": "no-store"})
                return await response(scope, receive, send)
            scope.setdefault("state", {})["access_grant"] = grant
        await self.app(scope, receive, send)


def register(app):
    @app.exception_handler(AccessError)
    async def access_error(request, error):
        data = dict(code=error.code, message=error.message)
        headers = {"Cache-Control": "no-store"}
        if error.remaining is not None:
            data["retryAfterSeconds"] = error.remaining
            headers["Retry-After"] = str(error.remaining)
        return JSONResponse({"error": data}, status_code=error.status, headers=headers)

    @app.get("/api/access")
    async def status(request: Request):
        required = bool(username())
        grant = await grant_for(request) if required else None
        configured = bool(await app.state.pool.fetchval("SELECT EXISTS(SELECT 1 FROM access_users WHERE username=$1)", username())) if required else False
        return JSONResponse(dict(required=required, granted=not required or grant is not None,
            username=grant["username"] if grant is not None else None, configured=configured), headers={"Cache-Control": "no-store"})

    @app.post("/api/access/login")
    async def login(body: LoginInput, request: Request):
        if not username():
            return await status(request)
        ip = client_ip(request)
        limiter = app.state.access_limiter
        limiter.attempt(ip)
        encoded = await app.state.pool.fetchval("SELECT password_hash FROM access_users WHERE username=$1", username())
        if encoded is None:
            raise AccessError(503, "access_not_configured", "관리자가 로그인 비밀번호를 먼저 설정해야 합니다.")
        async with app.state.access_hash_slots:
            valid = await asyncio.to_thread(verify_password, body.password.get_secret_value(), encoded)
        matches_user = hmac.compare_digest(body.username.encode(), username().encode())
        if not (valid and matches_user):
            locked = limiter.failed(ip)
            await failure_delay()
            if locked is not None:
                limiter.blocked(locked)
            raise AccessError(401, "access_invalid", "아이디 또는 비밀번호를 확인해 주세요.")
        token = secrets.token_urlsafe(32)
        agent = "".join(c for c in request.headers.get("user-agent", "") if c.isprintable())[:200]
        await app.state.pool.execute("INSERT INTO access_grants(id,token_hash,username,user_agent,ip) VALUES($1,$2,$3,$4,$5)",
            uuid.uuid4(), hashlib.sha256(token.encode()).digest(), username(), agent, ip)
        response = JSONResponse(dict(required=True, granted=True, username=username(), configured=True), headers={"Cache-Control": "no-store"})
        response.set_cookie("exa_access", token, httponly=True, secure=COOKIE_SECURE, samesite="lax", path="/", max_age=GRANT_AGE)
        return response

    @app.post("/api/access/logout")
    async def logout(request: Request):
        token = request.cookies.get("exa_access", "")
        if token and len(token) <= 128:
            await app.state.pool.execute("UPDATE access_grants SET revoked_at=COALESCE(revoked_at,now()) WHERE token_hash=$1", hashlib.sha256(token.encode()).digest())
        response = JSONResponse(dict(required=bool(username()), granted=not bool(username()), username=None), headers={"Cache-Control": "no-store"})
        response.delete_cookie("exa_access", path="/", secure=COOKIE_SECURE, httponly=True, samesite="lax")
        return response
