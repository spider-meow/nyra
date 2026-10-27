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
import uuid
from collections import OrderedDict
from typing import Callable, Mapping, Optional

import psycopg
from fastapi import HTTPException, Request

log = logging.getLogger("nyra.api")

# Per person and per minute. `GET jobs/current` is polled every 2 s (30 a minute): 300 leaves room for
# several tabs and screens. Writes (launching a job, sending files, editing) are fewer by nature.
DEFAULTS = {"read": 300, "write": 120}
ENV_NAMES = {"read": "NYRA_RATE_READ_PER_MIN", "write": "NYRA_RATE_WRITE_PER_MIN"}
BACKENDS = ("memory", "database")
DB_TIMEOUT = 1.0  # seconds to get a pooled connection for one bucket; a database that does not answer must not hold every request
DB_RETRY_AFTER = 30.0  # seconds a failed database is left alone: the process's own buckets limit meanwhile
EXEMPT_PATHS = frozenset({"/api/healthz", "/api/workerz", "/api/auth/config"})
MAX_KEYS = 10_000  # one entry per person and kind: a few dozen bytes each, well under a megabyte
PURGE_EVERY = 60.0  # seconds; a bucket idle for a minute is full again, so forgetting it changes nothing
REPORT_EVERY = 60.0  # seconds; refusals of one person and kind are reported once per period, with their number


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


def backend_from_env(environ: Mapping[str, str]) -> str:
    """`NYRA_RATE_BACKEND`: where the buckets live. `memory` (default) or `database`; any other value logs a warning and keeps `memory`."""
    raw = environ.get("NYRA_RATE_BACKEND", "").strip().lower()
    if raw in BACKENDS:
        return raw
    if raw:
        log.warning("NYRA_RATE_BACKEND=%r is not one of %s; keeping memory.", raw, ", ".join(BACKENDS))
    return "memory"


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
        self._refusals: OrderedDict[tuple[str, str], tuple[int, float]] = OrderedDict()  # key -> (refused since the last report, last report)
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

    def refused(self, identity: str, kind: str) -> int:
        """Counts a refusal. Returns how many refusals to report (those since the last report, this one included) at most
        once per `REPORT_EVERY` for a person and kind, else 0. ponytail: refusals counted after the last report of a
        person who then stops are never reported (the entry is forgotten after a period); same cap as the buckets."""
        with self._lock:
            now = self._clock()
            key = (identity, kind)
            count, reported = self._refusals.pop(key, (0, None))
            count += 1
            due = reported is None or now - reported >= REPORT_EVERY
            self._refusals[key] = (0, now) if due else (count, reported)
            while len(self._refusals) > self._max_keys:
                self._refusals.popitem(last=False)
            return count if due else 0

    def _purge(self, now: float) -> None:
        if now - self._last_purge < PURGE_EVERY:
            return
        self._last_purge = now
        full = [key for key, (tokens, seen) in self._buckets.items()
                if tokens + (now - seen) * self._limits[key[1]] / 60.0 >= self._limits[key[1]]]
        for key in full:
            del self._buckets[key]
        for key in [key for key, (_, reported) in self._refusals.items() if now - reported >= REPORT_EVERY]:
            del self._refusals[key]


def _in_org(request: Request) -> str:
    """' in organization <uuid>' when the path names one (anything else is left out of the log)."""
    try:
        return f" in organization {uuid.UUID(str(request.path_params.get('org_id')))}"
    except ValueError:
        return ""


class DatabaseRateLimiter(RateLimiter):
    """The same buckets kept in Postgres (`take_rate_token`, migration 38), so that every web process shares one limit and a
    restart forgets nothing. `connect()` gives a pooled connection as a context manager (with a short wait for it).

    If the database fails, the process falls back on its own memory buckets for `DB_RETRY_AFTER` seconds (so the limit is
    still there, per process, as before) and logs one warning a minute at most; requests are never refused because of it.
    ponytail: one database round trip per limited request (measured in docs/DEPLOYMENT.md); batching per person and minute
    in memory would remove it but lets a person overshoot by the batch size."""

    def __init__(self, limits: Mapping[str, int], connect: Callable, **kwargs):
        super().__init__(limits, **kwargs)
        self._connect = connect
        self._down_until = 0.0
        self._last_warning = -math.inf
        self._last_db_purge = self._clock()

    def wait(self, identity: str, kind: str) -> float:
        now = self._clock()
        if now < self._down_until:
            return super().wait(identity, kind)
        try:
            with self._connect() as conn:
                wait = conn.execute("SELECT public.take_rate_token(%s, %s, %s) AS wait", (identity, kind, self._limits[kind])).fetchone()["wait"]
                if now - self._last_db_purge >= PURGE_EVERY:
                    self._last_db_purge = now
                    conn.execute("SELECT public.purge_rate_buckets()")
            return wait
        except psycopg.Error as error:  # includes the pool's timeout: logged, and the memory buckets take over for a while
            self._down_until = now + DB_RETRY_AFTER
            if now - self._last_warning >= PURGE_EVERY:
                self._last_warning = now
                log.warning("rate limit: the database did not answer (%s); using this process's own limit for %d seconds.",
                            type(error).__name__, DB_RETRY_AFTER)
            return super().wait(identity, kind)


def enforce(limiter: RateLimiter, identify: Callable[[str], Optional[str]]):
    """An app-wide FastAPI dependency. `identify(token)` returns who a bearer token belongs to, or None when it is not valid.

    App-level dependencies run before the route function, so a 429 is raised before the route code and the database are
    touched: a refused request has done nothing, and the interface may replay it, POST included. Not before the body,
    though: FastAPI reads a multipart body (`request.form()`, fastapi/routing.py) before it solves the dependencies, so a
    refused file upload has already been received; the interface does not replay those (frontend/src/lib/api.ts)."""

    def dependency(request: Request) -> None:
        path = request.url.path
        header = request.headers.get("authorization", "")
        if not path.startswith("/api/") or path in EXEMPT_PATHS or not header.startswith("Bearer "):
            return
        who = identify(header.removeprefix("Bearer ").strip())
        if who is None:
            return
        kind = "read" if request.method in ("GET", "HEAD", "OPTIONS") else "write"
        wait = limiter.wait(who, kind)
        if wait > 0:
            reported = limiter.refused(who, kind)
            if reported:
                log.warning("rate limit: %d %s request(s) refused for user %s%s", reported, kind, who, _in_org(request))
            seconds = max(1, math.ceil(wait))
            raise HTTPException(
                status_code=429,
                detail=f"Trop de requêtes en peu de temps. Patientez {seconds} seconde{'s' if seconds > 1 else ''} avant de réessayer.",
                headers={"Retry-After": str(seconds)},
            )

    return dependency
