import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit


SVG = '''<svg xmlns="http://www.w3.org/2000/svg" width="120" height="120" viewBox="0 0 120 120">
  <rect x="4" y="4" width="112" height="112" rx="18" fill="#dcfce7"/>
  <circle cx="60" cy="48" r="26" fill="#16a34a"/>
  <text x="60" y="96" text-anchor="middle" font-size="14" fill="#14532d">EXAONE</text>
</svg>'''

MARKDOWN = r'''# EXAONE 살펴보기

**EXAONE**은 복잡한 문제를 단계별로 생각합니다. `reasoning_effort`로 추론 깊이를 정하고,
[공식 문서](https://docs.exaone.ai/ko/docs/k-exaone-2.5)에서 사용 방법을 확인하세요.

질량과 에너지의 관계는 $E = mc^2$ 입니다.

$$\int_0^1 x^2\,dx = \frac{1}{3}$$

```python
def greet(name: str) -> str:
    # 간단한 인사와 숫자를 출력합니다.
    repeat = 3
    return f"안녕하세요, {name}! " * repeat
```

```svg
''' + SVG + r'''
```

```markdown
# 작은 문서

- 준비
- 실행

| 단계 | 상태 |
| --- | --- |
| 검토 | 완료 |
```

| 기능 | 설명 |
| --- | --- |
| 추론 | 풀이 과정 확인 |
| 스트리밍 | 답변을 실시간으로 받기 |
| 편집 | 이전 질문을 바꿔 다시 생성 |

> 작은 질문에서 새로운 아이디어가 시작됩니다.
'''


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass  # Never log Authorization or request bodies.

    def reply(self, status, obj):
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path == "/search":
            query = parse_qs(url.query).get("q", [""])[0]
            if "mock-search-failure" in query or "검색실패" in query:
                return self.reply(503, {"error": {"message": "Search unavailable"}})
            return self.reply(200, {"results": [
                {"title": "EXAONE 공식 문서", "url": "https://docs.exaone.ai/ko/docs/k-exaone-2.5", "content": "SEARCH_SNIPPET_ONLY: EXAONE 공식 API 문서와 모델 사용법입니다."},
                {"title": "LG AI연구원", "url": "https://www.lgresearch.ai/", "content": "LG AI연구원의 EXAONE 관련 연구를 소개합니다. " * 100},
                {"title": "중복", "url": "https://www.lgresearch.ai/#duplicate", "content": "중복 결과"},
                {"title": "잘못된 주소", "url": "javascript:alert(1)", "content": "제외할 결과"},
            ]})
        self.reply(200 if url.path in ("/health", "/") else 404, {"ok": url.path in ("/health", "/")})

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            return self.reply(404, {"error": {"message": "Not found"}})
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size > 1024 * 1024:
                return self.reply(413, {"error": {"message": "Too large"}})
            body = json.loads(self.rfile.read(size))
        except (ValueError, TypeError):
            return self.reply(400, {"error": {"message": "Invalid JSON"}})
        key = self.headers.get("Authorization", "").removeprefix("Bearer ")
        if key == 'limited':
            data = b'{"error":{"message":"Rate limit"}}'
            self.send_response(429); self.send_header('Retry-After', '3'); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data); return
        if key not in ("good", "think", "slow", "svg"):
            return self.reply(401, {"result_code": "40100", "description": "Invalid API key"})
        # Enforce important outbound contract properties during smoke runs.
        official = "reasoning_effort" in body
        if official:
            valid = (body["reasoning_effort"] in ("low", "high", "max")
                     and "chat_template_kwargs" not in body and "parse_reasoning" not in body)
            if body.get("stream"):
                valid = valid and body.get("stream_options") == {"include_usage": True}
        else:
            valid = "parse_reasoning" not in body and type(body.get("chat_template_kwargs", {}).get("enable_thinking")) is bool
        if not valid:
            return self.reply(400, {"error": {"message": "Invalid mock request options"}})
        if any("reasoning" in m or "error" in m for m in body.get("messages", [])):
            return self.reply(400, {"error": {"message": "History contains private metadata"}})
        if any("EXAONE_SEARCH_QUERIES" in m.get("content", "") for m in body.get("messages", []) if m.get("role") == "system"):
            if body.get("stream") or body.get("max_tokens") != 512 or (official and body.get("reasoning_effort") != "low"):
                return self.reply(400, {"error": {"message": "Invalid query planning options"}})
            question = body["messages"][-1]["content"]
            queries = ["mock-search-failure"] if "검색실패" in question else [question[:100], "EXAONE official documentation"]
            return self.reply(200, {"choices": [{"message": {"role": "assistant", "content": json.dumps({"queries": queries})}}]})
        if not body.get("stream"):
            valid_probe = (body.get("max_tokens") == 64 and body["reasoning_effort"] == "low") if official else (
                body.get("max_tokens") == 8 and body["chat_template_kwargs"]["enable_thinking"] is False)
            if not valid_probe:
                return self.reply(400, {"error": {"message": "Invalid probe options"}})
            return self.reply(200, {"choices": [{"message": {"role": "assistant", "content": "" if official else "Hi"}}]})
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        if key == "think":
            chunks = [("content", text) for text in ["<th", "ink>숨은 ", "생각</thi", "nk># ", "태그 분리 성공\n", "<thou", "ght>추가 생각</thought", ">끝"]]
        elif key == "slow":
            user_text = next((m["content"] for m in reversed(body.get("messages", [])) if m.get("role") == "user"), "")
            chunks = [("reasoning_content", "천천히 생각합니다."), ("content", f"질문: {user_text}\n")]
            chunks += [("content", f"느린 응답 {i}\n") for i in range(12)]
        else:
            effort = body.get("reasoning_effort", "max")
            count = {"low": 1, "high": 2, "max": 3}[effort]
            answer = SVG if key == "svg" else MARKDOWN
            if any("EXAONE_SEARCH_CONTEXT" in m.get("content", "") and "[1]" in m["content"]
                   for m in body.get("messages", []) if m.get("role") == "system"):
                answer = "# 검색 답변\n\nEXAONE 공식 문서를 확인했습니다.[1] LG AI연구원의 연구도 참고했습니다.[2]\n"
            chunks = [("reasoning_content", f"{effort} 단계 {i + 1}: 답변을 생각합니다.\n") for i in range(count)]
            chunks += [("content", answer[i:i + 50]) for i in range(0, len(answer), 50)]
        if official:
            chunks = [("content", "\n"), ("content", "\n")] + chunks
        try:
            self.wfile.write(b": mock\n\n")
            self.wfile.flush()
            for field, text in chunks:
                data = {"choices": [{"delta": {field: text}}]}
                self.wfile.write(("data: " + json.dumps(data, ensure_ascii=False) + "\r\n\r\n").encode())
                self.wfile.flush()
                time.sleep(0.7 if key == "slow" else 0.025)
            if body.get("stream_options", {}).get("include_usage"):
                data = {"choices": [], "usage": {"prompt_tokens": 123, "completion_tokens": 456,
                        "reasoning_tokens": {"low": 16, "high": 32, "max": 48}[body.get("reasoning_effort", "max")]}}
                self.wfile.write(("data: " + json.dumps(data) + "\n\n").encode())
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 9000), Handler).serve_forever()
