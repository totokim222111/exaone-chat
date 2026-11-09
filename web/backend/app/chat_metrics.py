import time
import re


class Timing:
    def __init__(self, started=None):
        self.started = time.monotonic() if started is None else started
        self.first = self.reasoning = self.answer = self.search_started = self.search_ended = None

    def token(self, kind, now=None):
        now = time.monotonic() if now is None else now
        if self.first is None:
            self.first = now
        if kind == "reasoning" and self.reasoning is None:
            self.reasoning = now
        if kind == "content" and self.answer is None:
            self.answer = now

    def snapshot(self, now=None):
        now = time.monotonic() if now is None else now
        def ms(start, end):
            return None if start is None else max(0, round((end - start) * 1000))
        return dict(firstTokenMs=ms(self.started, self.first) if self.first is not None else None,
                    reasoningMs=ms(self.reasoning, self.answer if self.answer is not None else now),
                    answerMs=ms(self.answer, now), totalMs=ms(self.started, now),
                    searchMs=ms(self.search_started, self.search_ended if self.search_ended is not None else now))


def snippet(text, query):
    # re.IGNORECASE preserves original character offsets (unlike expanding casefold).
    match = re.search(re.escape(query), text, re.IGNORECASE)
    if match is None:
        return None
    start, end = max(0, match.start() - 60), min(len(text), match.end() + 60)
    prefix, suffix = ("…" if start else ""), ("…" if end < len(text) else "")
    return dict(snippet=prefix + text[start:end] + suffix, matchStart=len(prefix) + match.start() - start,
                matchLength=match.end() - match.start())


def share_snapshot(conv, messages):
    safe = []
    for message in messages:
        item = {key: message.get(key) for key in ("role", "content", "createdAt", "usage", "timing", "error", "stopped")}
        item["search"] = {"sources": message["search"]["sources"]} if message.get("search") else None
        item["attachments"] = [{key: a[key] for key in ("name", "kind", "size")} for a in message.get("attachments", [])]
        safe.append(item)
    return dict(title=conv["title"], createdAt=conv["createdAt"], messages=safe)
