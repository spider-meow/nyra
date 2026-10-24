"""Rate limiting of the API: a token bucket per signed-in person, one for reads, one for writes.

Who is counted: the user of a JWT that has just been verified, never an IP address. Behind Caddy
the client IP is not known to uvicorn (it ignores `X-Forwarded-For` from another container), and
trusting that header would let anyone pick their own bucket (docs/DEPLOYMENT.md). Requests without a
valid token are not counted: they end in 401 before touching anything, and `/api/healthz`,
`/api/workerz`, `/api/auth/config` and the static interface are never limited.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections import OrderedDict
from typing import Callable, Mapping, Optional

from fastapi import HTTPException, Request

log = logging.getLogger("nyra.api")

# Per person and per minute. `GET jobs/current` is polled every 2 s (30 a minute): 300 leaves room for
# several tabs and screens. Writes (launching a job, sending files, editing) are fewer by nature.
DEFAULTS = {"read": 300, "write": 120}
ENV_NAMES = {"read": "NYRA_RATE_READ_PER_MIN", "write": "NYRA_RATE_WRITE_PER_MIN"}
EXEMPT_PATHS = frozenset({"/api/healthz", "/api/workerz", "/api/auth/config"})
MAX_KEYS = 10_000  # one entry per person and kind: a few dozen bytes each, well under a megabyte
PURGE_EVERY = 60.0  # seconds; a bucket idle for a minute is full again, so forgetting it changes nothing


def limits_from_env(environ: Mapping[str, str]) -> dict[str, int]:
    """Requests per minute for each kind; an invalid value (not a whole number of 1 or more) logs a warning and keeps the default."""
    limits = dict(DEFAULTS)
    for kind, name in ENV_NAMES.items():
        raw = environ.get(name)
        if raw is None or raw.strip() == "":
            continue
        try:
            value = int(raw)
        except ValueError:
            value = 0
        if value < 1:
            log.warning("%s=%r is not a whole number of 1 or more; keeping the default (%d a minute)", name, raw, DEFAULTS[kind])
            continue
        limits[kind] = value
    return limits


class RateLimiter:
    """Token buckets: `limits[kind]` requests a minute, with a burst of the same size.

    ponytail: the buckets live in this process's memory. With N web processes the real limit is N times
    higher (one instance runs today, docs/DEPLOYMENT.md), and a restart forgets them. Upgrade path: the
    counters in Postgres or Redis, or a limit in the reverse proxy. The table is capped at `max_keys`
    (the oldest idle entry goes first) and swept every `PURGE_EVERY` seconds.
    """

    def __init__(self, limits: Mapping[str, int], *, max_keys: int = MAX_KEYS, clock: Callable[[], float] = time.monotonic):
        self._limits = dict(limits)
        self._max_keys = max_keys
        self._clock = clock
        self._lock = threading.Lock()
        self._buckets: OrderedDict[tuple[str, str], tuple[float, float]] = OrderedDict()  # key -> (tokens, last seen)
        self._last_purge = clock()

    def size(self) -> int:
        return len(self._buckets)

    def wait(self, identity: str, kind: str) -> float:
        """0.0 when the request may go on (one token is taken); otherwise the seconds until a token is back (nothing is taken)."""
        capacity = self._limits[kind]
        rate = capacity / 60.0
        with self._lock:
            now = self._clock()
            self._purge(now)
            key = (identity, kind)
            tokens, seen = self._buckets.pop(key, (float(capacity), now))
            tokens = min(float(capacity), tokens + (now - seen) * rate)
            allowed = tokens >= 1.0
            self._buckets[key] = (tokens - 1.0 if allowed else tokens, now)  # re-inserted last: the table stays in order of last use
            while len(self._buckets) > self._max_keys:
                self._buckets.popitem(last=False)
            return 0.0 if allowed else (1.0 - tokens) / rate

    def _purge(self, now: float) -> None:
        if now - self._last_purge < PURGE_EVERY:
            return
        self._last_purge = now
        full = [key for key, (tokens, seen) in self._buckets.items()
                if tokens + (now - seen) * self._limits[key[1]] / 60.0 >= self._limits[key[1]]]
        for key in full:
            del self._buckets[key]


def enforce(limiter: RateLimiter, identify: Callable[[str], Optional[str]]):
    """An app-wide FastAPI dependency. `identify(token)` returns who a bearer token belongs to, or None when it is not valid.

    App-level dependencies run before the route's own, so a 429 is raised before the route (or its body, or the
    database) is touched: a refused request has done nothing, and the interface may safely replay it, POST included."""

    def dependency(request: Request) -> None:
        path = request.url.path
        header = request.headers.get("authorization", "")
        if not path.startswith("/api/") or path in EXEMPT_PATHS or not header.startswith("Bearer "):
            return
        who = identify(header.removeprefix("Bearer ").strip())
        if who is None:
            return
        wait = limiter.wait(who, "read" if request.method in ("GET", "HEAD", "OPTIONS") else "write")
        if wait > 0:
            seconds = max(1, math.ceil(wait))
            raise HTTPException(
                status_code=429,
                detail=f"Trop de requêtes en peu de temps. Patientez {seconds} seconde{'s' if seconds > 1 else ''} avant de réessayer.",
                headers={"Retry-After": str(seconds)},
            )

    return dependency
