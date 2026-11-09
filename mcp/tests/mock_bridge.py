"""Test-only adapter for the Windows mock's legacy request contract.

The original mock is read-only and expects mock-key/mock-exaone, temperature,
top_p, chat_template_kwargs and max_tokens=8192, even for a status probe.
Validate the MCP server's EXAONE contract *before* adapting these fields.
All responses, including 401, come from the original mock process.
"""
import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import httpx


@contextmanager
def legacy_bridge(upstream):
    if urlsplit(upstream).hostname != "127.0.0.1":
        raise ValueError("Mock upstream must be loopback")
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                assert self.path == "/chat/completions"
                assert body["model"] == "k-exaone_v2.5"
                assert body["stream"] is True
                assert body["stream_options"] == {"include_usage": True}
                assert body["reasoning_effort"] in {"low", "high", "max"}
                assert 1 <= body["max_tokens"] <= 65536
                assert "chat_template_kwargs" not in body
                assert "top_p" not in body
                requests.append(body)
                valid_key = self.headers.get("Authorization") == "Bearer good"
                mode = "usage" if valid_key else "exaone401"
                legacy = {**body, "model": "mock-exaone", "temperature": 1, "top_p": .95,
                          "max_tokens": 8192, "chat_template_kwargs": {"enable_thinking": True}}
                with httpx.Client(trust_env=False, timeout=10) as client:
                    response = client.post(upstream.rstrip("/") + f"/{mode}/chat/completions", json=legacy,
                                           headers={"Authorization": "Bearer mock-key"})
                payload, status = response.content, response.status_code
                content_type = response.headers.get("Content-Type", "application/json")
            except Exception:
                payload, status, content_type = b'{"message":"legacy bridge validation failed"}', 400, "application/json"
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
