"""The scheduled reads: every brand whose schedule is on is read twice a month, by the worker, through the ordinary queue.

A brand has two dates in the month: the first read between the 1st and the 3rd, the second between the 13th and the 16th (and
an hour, a quarter-hour slot between 02:00 and 05:00 UTC). The dates are a spread, so that the brands of all the customers do not
start at the same minute: the first day (1 to 3, the one the team may change), the second (13 to 16) and the slot come from the
brand's uuid, never from `hash()`, which differs from one process to the next.

A brand is due from the date of its latest read of the month that has passed, if it was not queued since the start of that date's
half of the month (the 1st or the 13th): the month holds two periods. A worker that was stopped on the day thus queues the read as
soon as it is back, once; a period is never read twice, whatever the restarts or a change of day, and a read missed in one half is not
made up in the next: the next date comes first.

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

FIRST_DAYS = (1, 2, 3)        # the first read of the month, between the 1st and the 3rd
SECOND_DAYS = (13, 14, 15, 16)  # the second, between the 13th and the 16th
SLOT_SPREAD = 7               # the slot is drawn from the uuid divided by this (it was the number of default days: kept so that no brand changes slot)
FIRST_HOUR, SLOT_MINUTES, SLOTS = 2, 15, 12  # 12 quarter-hours: 02:00 to 04:45 UTC
# After a long stop of the worker, dozens of brands can be due at once. Each pass queues at most this many; the rest go
# at the next pass (5 minutes later), so the queue is not flooded.
MAX_PER_TICK = 3
SCAN_LIMIT, MAX_SCAN_PAGES = 100, 20  # rows read at a time, and pages looked at in a pass (the longest unread first)
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
    """The brand's own day for its first read (1 to 3)."""
    return FIRST_DAYS[uuid.UUID(str(brand_id)).int % len(FIRST_DAYS)]


def second_day(brand_id: uuid.UUID) -> int:
    """The brand's day for its second read (13 to 16), drawn apart from the first so that the two spreads are not tied."""
    return SECOND_DAYS[(uuid.UUID(str(brand_id)).int // len(FIRST_DAYS)) % len(SECOND_DAYS)]


def first_day(brand_id: uuid.UUID, day: int) -> int:
    """The day of the first read: the stored one, or the default when it is outside 1 to 3 (a schedule made when a day from 1 to 28 was allowed)."""
    return day if day in FIRST_DAYS else default_day(brand_id)


def slot_time(brand_id: uuid.UUID) -> tuple[int, int]:
    """(hour, minute) UTC of the brand's slot."""
    slot = (uuid.UUID(str(brand_id)).int // SLOT_SPREAD) % SLOTS
    minutes = FIRST_HOUR * 60 + slot * SLOT_MINUTES
    return minutes // 60, minutes % 60


def reads_of_month(brand_id: uuid.UUID, day: int, year: int, month: int) -> tuple[datetime, datetime]:
    """The dates of the brand's two reads of the month (the first on `day`, see `first_day`)."""
    hour, minute = slot_time(brand_id)
    return (datetime(year, month, first_day(brand_id, day), hour, minute, tzinfo=timezone.utc),
            datetime(year, month, second_day(brand_id), hour, minute, tzinfo=timezone.utc))


def due_date(brand_id: uuid.UUID, day: int, year: int, month: int) -> datetime:
    """The date of the first read of the month."""
    return reads_of_month(brand_id, day, year, month)[0]


def last_due_date(brand_id: uuid.UUID, day: int, year: int, month: int) -> datetime:
    """The date of the last read of the month: the monthly e-mail waits for it."""
    return reads_of_month(brand_id, day, year, month)[1]


def period_start(brand_id: uuid.UUID, day: int, now: datetime) -> Optional[datetime]:
    """Pure: the date of the latest read of this month that has passed, None before the first one."""
    now = now.astimezone(timezone.utc)
    passed = [at for at in reads_of_month(brand_id, day, now.year, now.month) if at <= now]
    return passed[-1] if passed else None


def due_since(brand_id: uuid.UUID, day: int, now: datetime) -> Optional[datetime]:
    """Pure: the start of the half of the month the latest passed read date belongs to (the 1st or the 13th, at 00:00 UTC), None before the first
    date. A brand is due when it was not queued since: a read on the 1st is the half's read even if the team then moves the brand's day to the 3rd."""
    latest = period_start(brand_id, day, now)
    return None if latest is None else latest.replace(day=1 if latest.day < SECOND_DAYS[0] else SECOND_DAYS[0], hour=0, minute=0)


def is_due(row: dict, now: datetime) -> bool:
    """Pure: scheduled on, a read date of this month passed, and not queued since the start of that date's half of the month."""
    since = due_since(row["brand_id"], row["day_of_month"], now) if row["enabled"] else None
    return since is not None and (row["last_run_at"] is None or row["last_run_at"] < since)


def next_run(brand_id: uuid.UUID, day: int, last_run_at: Optional[datetime], now: datetime) -> datetime:
    """Pure: when a brand whose schedule is on is read next. A brand due now gets its latest date (already past if it is late: it then goes
    at the next pass of the worker, see `is_due`); otherwise the next date to come."""
    now = now.astimezone(timezone.utc)
    since = due_since(brand_id, day, now)
    if since is not None and (last_run_at is None or last_run_at < since):
        return period_start(brand_id, day, now)
    year, month = (now.year + 1, 1) if now.month == 12 else (now.year, now.month + 1)
    coming = [*reads_of_month(brand_id, day, now.year, now.month), due_date(brand_id, day, year, month)]
    return next(at for at in coming if at > now)


def _check_day(day: int) -> int:
    if not isinstance(day, int) or isinstance(day, bool) or day not in FIRST_DAYS:
        raise ValueError(f"day of the first read must be {FIRST_DAYS[0]} to {FIRST_DAYS[-1]}: {day!r}")
    return day


def enable(conn, brand_id: uuid.UUID, org_id: uuid.UUID, day_of_month: Optional[int] = None,
           now: Optional[datetime] = None) -> None:
    """Switch the scheduled reads on (the brand's own default day if none is given). A brand NEVER read, switched on after one of the
    dates of this month, is marked as read now: its first read is the next date, not at once. A brand already read (even months
    ago) keeps its `last_run_at`, whatever the day chosen or a switch off and on: the ordinary rule applies, so if a date has
    passed since its last read it is read at the next pass, and if not it waits for the next date. Nothing is skipped and nothing
    is read twice."""
    day = _check_day(default_day(brand_id) if day_of_month is None else day_of_month)
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    passed = period_start(brand_id, day, now) is not None
    conn.execute(
        """INSERT INTO brand_schedules (brand_id, org_id, enabled, day_of_month, last_run_at) VALUES (%s, %s, true, %s, %s)
           ON CONFLICT (brand_id) DO UPDATE SET enabled = true, day_of_month = EXCLUDED.day_of_month, updated_at = now(),
               last_run_at = COALESCE(brand_schedules.last_run_at, EXCLUDED.last_run_at)""",
        (brand_id, org_id, day, now if passed else None),
    )
    if has_next_due(conn):  # the date worked out for the former day is not this one's: the worker works it out again
        conn.execute("UPDATE brand_schedules SET next_due_at = NULL WHERE brand_id = %s", (brand_id,))


def enable_new_brand(conn, org_id: uuid.UUID, brand_id: uuid.UUID, user_id: Optional[uuid.UUID], now: Optional[datetime] = None) -> None:
    """The schedule every brand starts with (AXE-684): on, on its own default day, traced like a switch on made by hand (`default` says it was not chosen).
    Called where a brand is created, in the transaction of its creation: without it nobody would read the brand, a client being unable to launch a read."""
    enable(conn, brand_id, org_id, now=now)
    audit.record(conn, org_id, user_id, "schedule.enable", "brand_schedule", brand_id, brand_id=brand_id, day_of_month=default_day(brand_id), default=True)


def disable(conn, brand_id: uuid.UUID) -> None:
    conn.execute("UPDATE brand_schedules SET enabled = false, updated_at = now() WHERE brand_id = %s", (brand_id,))


def _launch(conn, row: dict, now: datetime, tracked: bool = False) -> Optional[dict]:
    """Claim the brand for this period and queue its read, in the caller's transaction (a savepoint). The claim repeats
    the due test: of two workers only the one that gets the row first sees it still due. `tracked`: the next date is written with the claim."""
    claimed = conn.execute(
        """UPDATE brand_schedules SET last_run_at = %s, updated_at = now()
           WHERE brand_id = %s AND enabled AND day_of_month = %s AND (last_run_at IS NULL OR last_run_at < %s)
           RETURNING brand_id""",
        (now, row["brand_id"], row["day_of_month"], due_since(row["brand_id"], row["day_of_month"], now)),
    ).fetchone()
    if claimed is None:
        return None
    if tracked:
        _remember(conn, row, now, now)
    job, reason = None, ""
    if conn.execute("SELECT 1 FROM sites WHERE brand_id = %s LIMIT 1", (row["brand_id"],)).fetchone() is None:
        reason = "no_site"
        log.warning("scheduled read of brand %s: no site to read, marked as done until the next date", row["brand_id"])
    else:
        try:
            job = jobs.enqueue(conn, org_id=row["org_id"], brand_id=row["brand_id"], kind="crawl", params=JOB_PARAMS)
        except jobs.JobConflict as conflict:
            # Only a scheduled read of this brand is this period's read. A read started by hand may be partial (some
            # addresses): the pass is cancelled, `last_run_at` stays, and the brand is tried again at the next pass.
            if not conflict.job["params"].get("scheduled") or conflict.job["kind"] != "crawl":
                raise _Deferred(conflict.job["id"]) from None
            reason = "waiting"
            log.info("scheduled read of brand %s: a scheduled read already waits, none added", row["brand_id"])
    audit.record(conn, row["org_id"], None, "schedule.run", "brand_schedule", row["brand_id"],
                 brand_id=row["brand_id"], queued=int(job is not None), reason=reason)
    return job


def has_next_due(conn) -> bool:
    """Whether migration 20261102000047 (`brand_schedules.next_due_at`) is applied. Asked once a pass and once a switch on: cheap, and the code runs before it is."""
    return conn.execute("SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'brand_schedules' "
                        "AND column_name = 'next_due_at'").fetchone() is not None


def _scan_all(conn):
    """Without the column: the active schedules, the longest unread first, `SCAN_LIMIT` at a time and at most `MAX_SCAN_PAGES` pages. Which brands are due
    depends on dates drawn from each brand's own uuid (`is_due`), so the database cannot narrow it down: it is looked at here, in order.
    ponytail: offset paging while the launches move rows to the back of the order can skip a brand for one pass (it is found at the next); past
    `SCAN_LIMIT * MAX_SCAN_PAGES` = 2,000 active schedules the newest ones are not looked at. Upgrade: the column, see `_due_rows`."""
    for page in range(MAX_SCAN_PAGES):
        rows = conn.execute(
            """SELECT brand_id, org_id, enabled, day_of_month, last_run_at FROM brand_schedules WHERE enabled
               ORDER BY last_run_at NULLS FIRST, day_of_month, brand_id LIMIT %s OFFSET %s""",
            (SCAN_LIMIT, page * SCAN_LIMIT),
        ).fetchall()
        yield from rows
        if len(rows) < SCAN_LIMIT:
            return


def _due_rows(conn, now: datetime, tracked: bool):
    """The schedules to look at. With `next_due_at` (AXE-709): only those whose date has passed or has not been worked out yet (null), `SCAN_LIMIT`
    at a time and not twice in a pass, so a pass on which nothing is due reads almost nothing and no number of schedules is left out. Each one is
    then either launched or given its next date (`_remember`), which takes it out of what is read. Without the column (migration not applied):
    the full scan of `_scan_all`, told once an hour."""
    if not tracked:
        if once_per_hour("missing-next-due"):
            log.warning("scheduled reads: column brand_schedules.next_due_at is missing, apply migration 20261102000047; every active schedule is looked at, up to %d",
                        SCAN_LIMIT * MAX_SCAN_PAGES)
        yield from _scan_all(conn)
        return
    seen: list[uuid.UUID] = []
    for _ in range(MAX_SCAN_PAGES):
        rows = conn.execute(
            """SELECT brand_id, org_id, enabled, day_of_month, last_run_at FROM brand_schedules
               WHERE enabled AND (next_due_at IS NULL OR next_due_at <= %s) AND brand_id <> ALL(%s)
               ORDER BY next_due_at NULLS FIRST, last_run_at NULLS FIRST, brand_id LIMIT %s""",
            (now, seen, SCAN_LIMIT),
        ).fetchall()
        yield from rows
        if len(rows) < SCAN_LIMIT:
            return
        seen += [row["brand_id"] for row in rows]


def _remember(conn, row: dict, last_run_at, now: datetime) -> None:
    """Write the brand's next date (`next_run`) so that it is not looked at again before it."""
    conn.execute("UPDATE brand_schedules SET next_due_at = %s WHERE brand_id = %s",
                 (next_run(row["brand_id"], row["day_of_month"], last_run_at, now), row["brand_id"]))


def enqueue_due(conn, now: datetime, limit: int = MAX_PER_TICK) -> list[dict]:
    """Queue the scheduled read of the brands that are due, at most `limit` of them: the jobs queued. A brand whose launch
    fails is rolled back alone (it stays due and is tried again at the next pass), logged, and the others go on."""
    now = now.astimezone(timezone.utc)
    queued: list[dict] = []
    tracked = has_next_due(conn)
    for row in _due_rows(conn, now, tracked):
        if len(queued) >= limit:
            break
        if not is_due(row, now):
            if tracked:
                _remember(conn, row, row["last_run_at"], now)
            continue
        try:
            with conn.transaction():
                job = _launch(conn, row, now, tracked)
        except _Deferred as way:
            log.debug("scheduled read of brand %s: job %s is in the way, tried again at the next pass", row["brand_id"], way)
            continue
        except Exception:  # noqa: BLE001 - one brand's failure must not stop the others; it is logged and retried
            # ponytail: a brand that fails for good stays among the oldest read, so 100 such brands would hold back the next ones
            # (`limit` per pass). Upgrade: a failure counter or a last-attempt date. Until then the noise is bounded: one full
            # trace per brand and hour, the other passes only a line.
            if once_per_hour(f"fail:{row['brand_id']}"):
                log.exception("scheduled read of brand %s failed; it will be tried again", row["brand_id"])
            else:
                log.warning("scheduled read of brand %s failed again; it will be tried again", row["brand_id"])
            continue
        if job is not None:
            queued.append(job)
    return queued


def coverage(conn, late_days: int = 0) -> dict:
    """How many brands with a site are not read by themselves (AXE-684): `unscheduled` have no active schedule (none, or switched off), `late` have
    an active one but no finished read for `late_days` days (0: not counted). Only numbers: no brand, no organization. A brand with no site has
    nothing to read and a brand younger than `late_days` has had no time to be read yet."""
    row = conn.execute(
        """SELECT count(*) FILTER (WHERE s.brand_id IS NULL OR NOT s.enabled) AS unscheduled,
                  count(*) FILTER (WHERE %(days)s > 0 AND s.enabled AND b.created_at < now() - make_interval(days => %(days)s)
                                   AND COALESCE((SELECT max(r.finished_at) FROM crawl_runs r JOIN sites si ON si.id = r.site_id
                                                 WHERE si.brand_id = b.id AND r.status = 'done'), '-infinity') < now() - make_interval(days => %(days)s)) AS late
           FROM brands b LEFT JOIN brand_schedules s ON s.brand_id = b.id
           WHERE EXISTS (SELECT 1 FROM sites si WHERE si.brand_id = b.id)""",
        {"days": late_days},
    ).fetchone()
    return {"unscheduled": row["unscheduled"], "late": row["late"]}


def enable_unscheduled(conn, now: Optional[datetime] = None) -> int:
    """Switch the schedule on for every brand with a site that has no `brand_schedules` row at all, each on its own default day, traced in the
    audit log as made by the system: the brands that have a row, switched off on purpose, are left as they are. Returns how many."""
    brands = conn.execute(
        """SELECT b.id, b.org_id FROM brands b
           WHERE NOT EXISTS (SELECT 1 FROM brand_schedules s WHERE s.brand_id = b.id)
             AND EXISTS (SELECT 1 FROM sites si WHERE si.brand_id = b.id) ORDER BY b.created_at, b.id"""
    ).fetchall()
    for brand in brands:
        enable_new_brand(conn, brand["org_id"], brand["id"], None, now)
    return len(brands)


@lru_cache(maxsize=8)
def _switch(raw: str) -> bool:
    """Cached by value: the worker asks every pass and a bad value is told once."""
    value = raw.strip().lower()
    if value in _OFF:
        return False
    if value not in _ON:
        log.warning("NYRA_SCHEDULER=%r is not one of 0/false/off or 1/true/on: the scheduled reads stay on", raw)
    return True


def scheduler_enabled() -> bool:
    """The general switch `NYRA_SCHEDULER`: `0`, `false` or `off` stops the scheduled reads; anything else keeps them on."""
    return _switch(os.environ.get("NYRA_SCHEDULER", ""))
