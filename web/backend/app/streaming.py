import asyncio
import json
from starlette.responses import Response


class ThinkSplitter:
    """Keep only a potential tag prefix buffered across arbitrary chunk boundaries."""
    def __init__(self):
        self.buffer = ""
        self.tag = None

    def feed(self, text, final=False):
        self.buffer += text
        out = []
        while self.buffer:
            tags = [f"</{self.tag}>"] if self.tag else ["<think>", "<thought>"]
            hits = [(self.buffer.find(t), t) for t in tags if t in self.buffer]
            kind = "reasoning" if self.tag else "content"
            if hits:
                pos, tag = min(hits)
                if pos:
                    out.append((kind, self.buffer[:pos]))
                self.buffer = self.buffer[pos + len(tag):]
                self.tag = None if self.tag else tag[1:-1]
                continue
            keep = 0
            if not final:
                for tag in tags:
                    for size in range(1, min(len(tag), len(self.buffer) + 1)):
                        if self.buffer.endswith(tag[:size]):
                            keep = max(keep, size)
            count = len(self.buffer) - keep
            if count:
                out.append((kind, self.buffer[:count]))
                self.buffer = self.buffer[count:]
            break
        return out


class LeadingWhitespaceTrimmer:
    def __init__(self):
        self.started = False

    def feed(self, text):
        if not self.started:
            text = text.lstrip()
            self.started = bool(text)
        return text


class SSEParser:
    """Parse decoded SSE lines (including multiline data and an EOF flush)."""
    def __init__(self):
        self.data = []

    def line(self, line):
        if line == "":
            if self.data:
                data, self.data = "\n".join(self.data), []
                return data
        elif line.startswith("data:"):
            value = line[5:]
            self.data.append(value[1:] if value.startswith(" ") else value)
        return None

    def finish(self):
        return self.line("")


def event(name, data):
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode()


class EventResponse(Response):
    """Own worker cancellation explicitly, then await its durable partial save."""
    media_type = "text/event-stream"

    def __init__(self, producer, cleanup):
        super().__init__(content=None, headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
        self.raw_headers = [(k, v) for k, v in self.raw_headers if k != b"content-length"]
        self.producer = producer
        self.cleanup = cleanup

    async def __call__(self, scope, receive, send):
        queue = asyncio.Queue(maxsize=32)
        worker = None
        sender = None
        watcher = None

        async def watch():
            while True:
                if (await receive())["type"] == "http.disconnect":
                    return

        async def pump():
            while True:
                try:
                    value = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    value = b": ping\n\n"
                if value is None:
                    await send({"type": "http.response.body", "body": b"", "more_body": False})
                    return
                await send({"type": "http.response.body", "body": value, "more_body": True})

        try:
            await send({"type": "http.response.start", "status": 200, "headers": self.raw_headers})
            worker = asyncio.create_task(self.producer(queue))
            sender = asyncio.create_task(pump())
            watcher = asyncio.create_task(watch())
            done, _ = await asyncio.wait([sender, watcher, worker], return_when=asyncio.FIRST_COMPLETED)
            if worker in done:
                # Surface a failed save instead of leaving the browser on endless pings.
                worker.result()
                await asyncio.wait([sender, watcher], return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (sender, watcher, worker):
                if task is not None and not task.done():
                    task.cancel()
            try:
                await asyncio.gather(*(task for task in (sender, watcher, worker) if task is not None),
                                     return_exceptions=True)
            finally:
                self.cleanup()
