"""The monthly read: every brand whose schedule is on is read once a month, by the worker, through the ordinary queue.

A brand has a day of the month (1 to 28, so every month has it) and an hour, a quarter-hour slot between 02:00 and 05:00
UTC. Both are a spread, so that the brands of all the customers do not start at the same minute: the default day
(first week) and the slot come from the brand's uuid, never from `hash()`, which differs from one process to the next.

A brand is due from its date and hour of the current month on, if it was not queued already this month. A worker that
was stopped on the day thus queues the read as soon as it is back, once; and a month is never read twice, whatever the
restarts or a change of day.

The launch is atomic: `enqueue_due` claims a brand with an `UPDATE ... WHERE <the due test>` and queues its `crawl` in
the same transaction, so two workers cannot queue the same brand and a failure queues nothing and leaves the brand due.
The job is an ordinary one (`created_by` NULL: started by the system, `scheduled` in its params): it waits behind any
job of the same organization, behind the global caps and the domain rule of `jobs.py`, and `claim` serves it after the
jobs started by hand. The worker only looks every few minutes and not during a job: a read can start a few hours late.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from datetime import datetime, timezone
from functools import lru_cache
from typing import Optional

from . import audit, jobs

log = logging.getLogger("nyra.cloud.schedule")

DEFAULT_DAYS = 7  # the default day is in the first week
FIRST_HOUR, SLOT_MINUTES, SLOTS = 2, 15, 12  # 12 quarter-hours: 02:00 to 04:45 UTC
# After a long stop of the worker, dozens of brands can be due at once. Each pass queues at most this many; the rest go
# at the next pass (5 minutes later), so the queue is not flooded.
MAX_PER_TICK = 3
SCAN_LIMIT = 100  # rows looked at in a pass (the due ones come first)
JOB_PARAMS = {"fresh": True, "then_match": True, "scheduled": True}
_OFF, _ON = ("0", "false", "off"), ("", "1", "true", "on")
# What was told lately, so that a failure repeated at every pass (every 5 minutes) is told once an hour, not 12 times.
TELL_EVERY_SECONDS, TELL_MAX_KEYS = 3600, 256
_LAST_TOLD: dict[str, float] = {}


def once_per_hour(key: str, every: float = TELL_EVERY_SECONDS) -> bool:
    """True the first time `key` is asked in an hour (`every` seconds, for what is told once a day) and records it. The
    memory is bounded: when full it is emptied, which at worst tells one more time."""
    now = time.monotonic()
    if now - _LAST_TOLD.get(key, -every) < every:
        return False
    if len(_LAST_TOLD) >= TELL_MAX_KEYS:
        _LAST_TOLD.clear()
    _LAST_TOLD[key] = now
    return True


class _Deferred(Exception):
    """A read started by hand is in the way: this brand's pass is cancelled (savepoint) and tried again later."""


def default_day(brand_id: uuid.UUID) -> int:
    return 1 + uuid.UUID(str(brand_id)).int % DEFAULT_DAYS


def slot_time(brand_id: uuid.UUID) -> tuple[int, int]:
    """(hour, minute) UTC of the brand's slot."""
    slot = (uuid.UUID(str(brand_id)).int // DEFAULT_DAYS) % SLOTS
    minutes = FIRST_HOUR * 60 + slot * SLOT_MINUTES
    return minutes // 60, minutes % 60


def due_date(brand_id: uuid.UUID, day: int, year: int, month: int) -> datetime:
    hour, minute = slot_time(brand_id)
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def _month_start(now: datetime) -> datetime:
    return datetime(now.year, now.month, 1, tzinfo=timezone.utc)


def is_due(row: dict, now: datetime) -> bool:
    """Pure: scheduled on, this month's date and hour passed, and not queued yet this month."""
    now = now.astimezone(timezone.utc)
    if not row["enabled"] or due_date(row["brand_id"], row["day_of_month"], now.year, now.month) > now:
        return False
    return row["last_run_at"] is None or row["last_run_at"] < _month_start(now)


def _check_day(day: int) -> int:
    if not isinstance(day, int) or isinstance(day, bool) or not 1 <= day <= 28:
        raise ValueError(f"day of the month must be from 1 to 28: {day!r}")
    return day


def enable(conn, brand_id: uuid.UUID, org_id: uuid.UUID, day_of_month: Optional[int] = None,
           now: Optional[datetime] = None) -> None:
    """Switch the monthly read on (the brand's own default day if none is given). A brand NEVER read whose date of this
    month has already passed is marked as read now: its first read is on the chosen day of the next month, not at once.
    A brand already read (even months ago) keeps its `last_run_at`, whatever the day chosen or a switch off and on: the
    ordinary rule applies, so if the new date has passed and it was not read this month it is read at the next pass,
    and if it was read this month it waits for the next one. Nothing is skipped and nothing is read twice."""
    day = _check_day(default_day(brand_id) if day_of_month is None else day_of_month)
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    passed = due_date(brand_id, day, now.year, now.month) <= now
    conn.execute(
        """INSERT INTO brand_schedules (brand_id, org_id, enabled, day_of_month, last_run_at) VALUES (%s, %s, true, %s, %s)
           ON CONFLICT (brand_id) DO UPDATE SET enabled = true, day_of_month = EXCLUDED.day_of_month, updated_at = now(),
               last_run_at = COALESCE(brand_schedules.last_run_at, EXCLUDED.last_run_at)""",
        (brand_id, org_id, day, now if passed else None),
    )


def disable(conn, brand_id: uuid.UUID) -> None:
    conn.execute("UPDATE brand_schedules SET enabled = false, updated_at = now() WHERE brand_id = %s", (brand_id,))


def _launch(conn, row: dict, now: datetime) -> Optional[dict]:
    """Claim the brand for this month and queue its read, in the caller's transaction (a savepoint). The claim repeats
    the due test: of two workers only the one that gets the row first sees it still due."""
    claimed = conn.execute(
        """UPDATE brand_schedules SET last_run_at = %s, updated_at = now()
           WHERE brand_id = %s AND enabled AND day_of_month = %s AND (last_run_at IS NULL OR last_run_at < %s)
           RETURNING brand_id""",
        (now, row["brand_id"], row["day_of_month"], _month_start(now)),
    ).fetchone()
    if claimed is None:
        return None
    job, reason = None, ""
    if conn.execute("SELECT 1 FROM sites WHERE brand_id = %s LIMIT 1", (row["brand_id"],)).fetchone() is None:
        reason = "no_site"
        log.warning("monthly read of brand %s: no site to read, marked as done for the month", row["brand_id"])
    else:
        try:
            job = jobs.enqueue(conn, org_id=row["org_id"], brand_id=row["brand_id"], kind="crawl", params=JOB_PARAMS)
        except jobs.JobConflict as conflict:
            # Only a scheduled read of this brand is the month's read. A read started by hand may be partial (some
            # addresses): the pass is cancelled, `last_run_at` stays, and the brand is tried again at the next pass.
            if not conflict.job["params"].get("scheduled") or conflict.job["kind"] != "crawl":
                raise _Deferred(conflict.job["id"]) from None
            reason = "waiting"
            log.info("monthly read of brand %s: a scheduled read already waits, none added", row["brand_id"])
    audit.record(conn, row["org_id"], None, "schedule.run", "brand_schedule", row["brand_id"],
                 brand_id=row["brand_id"], queued=int(job is not None), reason=reason)
    return job


def enqueue_due(conn, now: datetime, limit: int = MAX_PER_TICK) -> list[dict]:
    """Queue the monthly read of the brands that are due, at most `limit` of them: the jobs queued. A brand whose launch
    fails is rolled back alone (it stays due and is tried again at the next pass), logged, and the others go on."""
    now = now.astimezone(timezone.utc)
    rows = conn.execute(
        """SELECT brand_id, org_id, enabled, day_of_month, last_run_at FROM brand_schedules
           WHERE enabled AND day_of_month <= %s AND (last_run_at IS NULL OR last_run_at < %s)
           ORDER BY last_run_at NULLS FIRST, day_of_month, brand_id LIMIT %s""",
        (now.day, _month_start(now), SCAN_LIMIT),
    ).fetchall()
    queued: list[dict] = []
    for row in rows:
        if len(queued) >= limit:
            break
        if not is_due(row, now):
            continue
        try:
            with conn.transaction():
                job = _launch(conn, row, now)
        except _Deferred as way:
            log.debug("monthly read of brand %s: job %s is in the way, tried again at the next pass", row["brand_id"], way)
            continue
        except Exception:  # noqa: BLE001 - one brand's failure must not stop the others; it is logged and retried
            # ponytail: a brand that fails for good stays in the first SCAN_LIMIT rows (the oldest read come first), so
            # 100 such brands would hold back the next ones. Upgrade: a failure counter or a last-attempt date. Until then
            # the noise is bounded: one full trace per brand and hour, the other passes only a line.
            if once_per_hour(f"fail:{row['brand_id']}"):
                log.exception("monthly read of brand %s failed; it will be tried again", row["brand_id"])
            else:
                log.warning("monthly read of brand %s failed again; it will be tried again", row["brand_id"])
            continue
        if job is not None:
            queued.append(job)
    return queued


@lru_cache(maxsize=8)
def _switch(raw: str) -> bool:
    """Cached by value: the worker asks every pass and a bad value is told once."""
    value = raw.strip().lower()
    if value in _OFF:
        return False
    if value not in _ON:
        log.warning("NYRA_SCHEDULER=%r is not one of 0/false/off or 1/true/on: the monthly read stays on", raw)
    return True


def scheduler_enabled() -> bool:
    """The general switch `NYRA_SCHEDULER`: `0`, `false` or `off` stops the monthly reads; anything else keeps them on."""
    return _switch(os.environ.get("NYRA_SCHEDULER", ""))
