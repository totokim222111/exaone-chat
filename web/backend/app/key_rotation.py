"""HTTP-header-only key failover; never retries after yielding a response body."""
import hashlib
import math
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime


def retry_seconds(value, now=None):
    try:
        seconds = float(value)
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError()
        return math.ceil(seconds)
    except (ValueError, TypeError):
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            return max(0, math.ceil(date.timestamp() - (time.time() if now is None else now)))
        except (ValueError, TypeError, OverflowError, AttributeError):
            return 60


class Exhausted(Exception):
    status = 429


class PlannerError(Exception):
    def __init__(self, message, status):
        self.status = status
        super().__init__(message)


class KeySet(list):
    def __init__(self, values, trace=None):
        super().__init__(values)
        self.trace = trace if trace is not None else {}


class KeyPool:
    def __init__(self, clock=time.monotonic):
        self.clock, self.cooling = clock, {}

    def identity(self, entry):
        return entry.get('id') or hashlib.sha256(entry['key'].encode()).hexdigest()

    def remaining(self, entry):
        return max(0, math.ceil(self.cooling.get(self.identity(entry), 0) - self.clock()))

    def pause(self, entry, seconds):
        now = self.clock()
        self.cooling = {key: until for key, until in self.cooling.items() if until > now}
        self.cooling[self.identity(entry)] = max(self.cooling.get(self.identity(entry), 0), now + seconds)


POOL = KeyPool()


def entries(keys):
    if isinstance(keys, str):
        return [{'key': keys, 'index': 1}] if keys else []
    return keys or []


def record(trace, index, seconds):
    failover = trace.setdefault('keyFailover', [])
    if not any(item['index'] == index for item in failover):
        failover.append(dict(index=index, status=429, retryAfterSec=seconds))


@asynccontextmanager
async def rotating_response(client, url, body, keys, *, pool=None, trace=None, **kwargs):
    pool = pool or POOL
    trace = trace if trace is not None else getattr(keys, 'trace', {})
    trace['keyIndex'] = None
    trace.pop('_key', None)
    keys = entries(keys)
    for index, entry in enumerate(keys, 1):
        number = entry.get('index', index)
        remaining = pool.remaining(entry)
        if remaining:
            record(trace, number, remaining)
            continue
        async with client.stream('POST', url, json=body, headers={'Authorization': 'Bearer ' + entry['key']}, **kwargs) as response:
            if response.status_code == 429:
                seconds = retry_seconds(response.headers.get('Retry-After'))
                pool.pause(entry, seconds)
                record(trace, number, seconds)
                continue
            trace['keyIndex'] = number
            trace['_key'] = entry['key']
            yield response
            return
    seconds = min((pool.remaining(entry) for entry in keys), default=0)
    raise Exhausted(f'등록된 키 {len(keys)}개가 모두 한도 초과 · 가장 빠른 재시도 {seconds}초 후')
