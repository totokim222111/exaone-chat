import json
from starlette.responses import JSONResponse

MAX_BODY = 1024 * 1024
MAX_UPLOAD_BODY = 10 * 1024 * 1024 + 65536


def invalid_constant(value):
    raise ValueError("JSON 상수는 유한한 값이어야 합니다.")


def validation_status(scope):
    return 400 if scope["method"] == "POST" and scope["path"].startswith("/api/conversations") else 422


class RequestGuard:
    """Pure ASGI middleware: bounded input without wrapping streaming responses."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope["headers"])

        async def reject(status, code, message):
            await JSONResponse({"error": {"code": code, "message": message}}, status_code=status)(scope, receive, send)

        mutate = scope["method"] in ("POST", "PUT", "PATCH", "DELETE")
        if mutate and headers.get(b"x-exa") != b"1":
            return await reject(403, "csrf", "요청에 X-Exa: 1 헤더가 필요합니다.")
        upload = scope["method"] == "POST" and scope["path"] == "/api/attachments"
        maximum = MAX_UPLOAD_BODY if upload else MAX_BODY
        size_message = "첨부 요청은 10MB 파일 한도를 초과할 수 없습니다." if upload else "요청 본문은 1MB 이하여야 합니다."
        try:
            if int(headers.get(b"content-length", b"0")) > maximum:
                return await reject(413, "body_too_large", size_message)
        except ValueError:
            return await reject(400, "validation", "요청 본문 길이가 올바르지 않습니다.")
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > maximum:
                return await reject(413, "body_too_large", size_message)
            if not message.get("more_body", False):
                break
        content_type = headers.get(b"content-type", b"").split(b";")[0].strip().lower()
        if upload and content_type != b"multipart/form-data":
            return await reject(415, "multipart_required", "첨부 파일은 multipart/form-data 형식이어야 합니다.")
        if not upload and body and content_type != b"application/json":
            return await reject(415, "json_required", "요청 본문은 JSON 형식이어야 합니다.")
        if body and not upload:
            try:
                json.loads(body, parse_constant=invalid_constant)
            except (ValueError, UnicodeDecodeError, RecursionError):
                return await reject(validation_status(scope), "validation", "올바른 JSON 본문을 입력해 주세요.")
        consumed = False

        async def replay():
            nonlocal consumed
            if not consumed:
                consumed = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)
