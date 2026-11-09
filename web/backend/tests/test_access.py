import asyncio
import base64
import hashlib
import secrets
import uuid

import pytest
from app import access, access_admin, main
from app.client_address import client_ip
from test_edit_api import api, create


@pytest.fixture
def gate(api, monkeypatch):
    name = "test-" + uuid.uuid4().hex
    password = secrets.token_urlsafe(20)
    monkeypatch.setenv("ACCESS_USERNAME", name)
    monkeypatch.setenv("TRUST_PROXY_HEADERS", "true")
    api.portal.call(main.app.state.pool.execute, "INSERT INTO access_users(username,password_hash) VALUES($1,$2)", name, access.hash_password(password))
    api.access_name, api.access_secret = name, password
    api.failed_delays = []

    async def delay():
        api.failed_delays.append(.5)

    monkeypatch.setattr(access, "failure_delay", delay)
    try:
        yield api
    finally:
        api.portal.call(main.app.state.pool.execute, "DELETE FROM access_users WHERE username=$1", name)
        monkeypatch.setenv("ACCESS_USERNAME", "")


def login(api, **extra):
    return api.post("/api/access/login", json={"username": api.access_name, "password": api.access_secret}, **extra)


def grant_row(api):
    return api.portal.call(main.app.state.pool.fetchrow, "SELECT * FROM access_grants WHERE username=$1 ORDER BY created_at DESC LIMIT 1", api.access_name)


def test_scrypt_format_roundtrip_and_corruption():
    value = secrets.token_urlsafe(20)
    encoded = access.hash_password(value)
    method, n, r, p, salt, digest = encoded.split("$")
    assert (method, int(n), int(r), int(p)) == ("scrypt", 32768, 8, 1)
    assert len(base64.b64decode(salt)) == 16 and len(base64.b64decode(digest)) == 64
    assert access.verify_password(value, encoded)
    assert not access.verify_password(secrets.token_urlsafe(20), encoded)
    assert not access.verify_password(value, encoded.replace("32768", "65536"))
    assert not access.verify_password(value, "malformed")
    assert value not in encoded


@pytest.mark.parametrize("trusted,forwarded,expected", [
    (False, "203.0.113.1, 10.0.0.1", "10.0.0.9"),
    (True, "203.0.113.1, 10.0.0.1", "203.0.113.1"),
    (True, "2001:db8::1, 10.0.0.1", "2001:db8::1"),
    (True, "invalid, 203.0.113.1", "10.0.0.9"),
])
def test_xff_trust_policy(monkeypatch, trusted, forwarded, expected):
    monkeypatch.setenv("TRUST_PROXY_HEADERS", str(trusted).lower())
    scope = {"client": ("10.0.0.9", 1234), "headers": [(b"x-forwarded-for", forwarded.encode()), (b"x-real-ip", b"192.0.2.10")]}
    assert client_ip(scope) == expected


def test_disabled_gate_preserves_session_behavior(api, monkeypatch):
    monkeypatch.setenv("ACCESS_USERNAME", "")
    assert api.get("/api/access").json() == {"required": False, "granted": True, "username": None, "configured": False}
    assert api.get("/api/me").status_code == 200


def test_enabled_gate_blocks_and_config_is_redacted(gate):
    api = gate
    assert api.get("/api/access").json() == {"required": True, "granted": False, "username": None, "configured": True}
    for path in ("/api/me", "/api/conversations", "/api/shares", "/api/usage", "/api/openapi.json"):
        response = api.get(path)
        assert response.status_code == 401 and response.json()["error"]["code"] == "access_required"
    assert api.get("/api/health").json() == {"ok": True, "db": True}
    assert api.post("/api/session", content=b"invalid JSON", headers={"Content-Type": "application/json"}).status_code == 401
    cfg = api.get("/api/config")
    assert cfg.status_code == 200 and cfg.json()["allowedBaseUrls"] == [] and cfg.json()["models"] == []
    assert "mock:9000" not in cfg.text and main.SEARXNG_URL not in cfg.text


def test_unconfigured_is_503(gate):
    gate.portal.call(main.app.state.pool.execute, "DELETE FROM access_users WHERE username=$1", gate.access_name)
    assert not gate.get("/api/access").json()["configured"]
    response = login(gate)
    assert response.status_code == 503 and response.json()["error"]["code"] == "access_not_configured"


@pytest.mark.parametrize("secure", [False, True])
def test_success_cookie_hash_and_logout(gate, monkeypatch, secure):
    monkeypatch.setattr(access, "COOKIE_SECURE", secure)
    response = login(gate, headers={"X-Forwarded-For": "203.0.113.9, 172.18.0.2", "User-Agent": "browser" * 60})
    assert response.status_code == 200 and response.json()["granted"]
    header = response.headers["set-cookie"]
    assert "HttpOnly" in header and "SameSite=lax" in header and "Path=/" in header and "Max-Age=34560000" in header
    assert ("Secure" in header) == secure
    token = gate.cookies.get("exa_access")
    row = grant_row(gate)
    assert len(row["token_hash"]) == 32 and row["token_hash"] == hashlib.sha256(token.encode()).digest()
    assert token.encode() != row["token_hash"]
    assert row["ip"] == "203.0.113.9" and len(row["user_agent"]) == 200
    cookie = {"Cookie": "exa_access=" + token + "; exa_sid=" + gate.cookies.get("exa_sid")}
    assert gate.get("/api/me", headers=cookie).status_code == 200
    assert gate.post("/api/access/logout", json={}, headers=cookie).status_code == 200
    assert gate.get("/api/me", headers=cookie).status_code == 401


def test_failed_login_generic_message_delay_and_minute_lock(gate):
    responses = []
    for i in range(5):
        response = gate.post("/api/access/login", json={"username": "unknown" if i % 2 else gate.access_name,
            "password": secrets.token_urlsafe(20)})
        assert response.status_code == 401
        responses.append(response.json())
    assert all(r == responses[0] for r in responses)
    assert responses[0]["error"]["code"] == "access_invalid" and gate.failed_delays == [.5] * 5
    response = login(gate)
    assert response.status_code == 429 and response.json()["error"]["code"] == "access_locked"
    assert 1 <= response.json()["error"]["retryAfterSeconds"] <= 60
    assert int(response.headers["Retry-After"]) >= 1


def test_hourly_failure_lock_and_expiry():
    clock = [100.0]
    limiter = access.LoginLimiter(lambda: clock[0])
    for i in range(20):
        assert limiter.failed("ip") is None
    assert limiter.failed("ip") == 3600
    with pytest.raises(access.AccessError) as error:
        limiter.attempt("ip")
    assert error.value.code == "access_locked" and error.value.remaining == 3600
    limiter.attempt("other-ip")
    clock[0] += 3601
    limiter.attempt("ip")
    assert limiter.failed("ip") is None


def test_hourly_last_seen_and_cli_revoke(gate, capsys):
    assert login(gate).status_code == 200
    before = grant_row(gate)
    gate.get("/api/me")
    gate.get("/api/me")
    assert grant_row(gate)["last_seen_at"] == before["last_seen_at"]
    gate.portal.call(main.app.state.pool.execute, "UPDATE access_grants SET last_seen_at=now()-interval '2 hours' WHERE id=$1", before["id"])
    gate.get("/api/me")
    touched = grant_row(gate)["last_seen_at"]
    assert touched >= before["last_seen_at"]
    gate.get("/api/me")
    assert grant_row(gate)["last_seen_at"] == touched
    args = access_admin.parser().parse_args(["revoke", str(before["id"])])
    assert asyncio.run(access_admin.execute(args)) == 0
    assert gate.get("/api/me").status_code == 401
    assert gate.access_secret not in capsys.readouterr().out


def test_public_share_bypasses_gate(gate):
    assert login(gate).status_code == 200
    path = create(gate)
    share = gate.post(path + "/share").json()
    response = gate.get("/api/public/shares/" + share["token"], headers={"Cookie": ""})
    assert response.status_code == 200
    assert gate.get("/api/public/shares/missing", headers={"Cookie": ""}).status_code == 404
    assert gate.get("/api/me", headers={"Cookie": ""}).status_code == 401


def test_cli_getpass_only_and_password_hash_saved(gate, monkeypatch, capsys):
    value = secrets.token_urlsafe(20)
    supplied = iter([value, value])
    monkeypatch.setattr(access_admin.getpass, "getpass", lambda prompt: next(supplied))
    args = access_admin.parser().parse_args(["set-password", "--username", gate.access_name])
    assert asyncio.run(access_admin.execute(args)) == 0
    output = capsys.readouterr()
    assert output.out == "비밀번호를 저장했습니다\n" and value not in output.out + output.err
    encoded = gate.portal.call(main.app.state.pool.fetchval, "SELECT password_hash FROM access_users WHERE username=$1", gate.access_name)
    assert access.verify_password(value, encoded)
    assert value not in encoded
    assert "--password" not in access_admin.parser().format_help()


def test_cli_mismatch_short_password_and_revoke_all(monkeypatch, capsys):
    supplied = iter([secrets.token_urlsafe(12), secrets.token_urlsafe(12)])
    monkeypatch.setattr(access_admin.getpass, "getpass", lambda prompt: next(supplied))
    args = access_admin.parser().parse_args(["set-password", "--username", "test"])
    assert asyncio.run(access_admin.execute(args)) == 1
    value = secrets.token_hex(1)
    monkeypatch.setattr(access_admin.getpass, "getpass", lambda prompt: value)
    assert asyncio.run(access_admin.execute(args)) == 1
    commands = []

    class Pool:
        async def execute(self, sql):
            commands.append(sql)
        async def close(self):
            pass

    async def connect():
        return Pool()

    monkeypatch.setattr(access_admin.db, "connect", connect)
    assert asyncio.run(access_admin.execute(access_admin.parser().parse_args(["revoke-all"]))) == 0
    assert commands == ["UPDATE access_grants SET revoked_at=now() WHERE revoked_at IS NULL"]
    assert value not in capsys.readouterr().out


def test_sse_proxy_headers_remain(api):
    path = create(api)
    response = api.post(path + "/messages", json={"content": "header test"})
    assert response.headers["x-accel-buffering"] == "no" and response.headers["cache-control"] == "no-cache"
