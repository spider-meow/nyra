"""The monthly read: when a brand is due (pure), the atomic launch by the worker, and its place in the queue.

The database tests skip unless TEST_DATABASE_URL is set (see conftest.py).
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone

import psycopg
import pytest
from psycopg import OperationalError

from nyra.cloud import db as cloud_db
from nyra.cloud import jobs as cloud_jobs
from nyra.cloud import schedule

UTC = timezone.utc
BRAND = uuid.UUID("12345678-1234-4234-8234-123456789abc")


def at(year, month, day, hour=0, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def row(day=10, last=None, enabled=True, brand=BRAND):
    return {"brand_id": brand, "enabled": enabled, "day_of_month": day, "last_run_at": last}


# --- the day and the hour: a deterministic spread --------------------------------------------------------------------

def test_the_default_day_and_the_hour_come_from_the_uuid_and_spread_over_every_slot():
    brands = [uuid.uuid5(uuid.NAMESPACE_DNS, f"brand-{i}") for i in range(1000)]
    assert [schedule.default_day(b) for b in brands[:5]] == [schedule.default_day(b) for b in brands[:5]]  # no hash(): stable
    assert schedule.default_day(BRAND) == schedule.default_day(uuid.UUID(str(BRAND)))
    days = Counter(schedule.default_day(b) for b in brands)
    slots = Counter(schedule.slot_time(b) for b in brands)
    assert sorted(days) == list(range(1, 8)) and len(slots) == schedule.SLOTS
    assert max(days.values()) <= 2 * 1000 / 7 and max(slots.values()) <= 2 * 1000 / schedule.SLOTS
    assert all((2, 0) <= slot < (5, 0) for slot in slots)  # between 02:00 and 05:00 UTC


# --- is_due: pure -----------------------------------------------------------------------------------------------------

def due(year, month, day=10):
    return schedule.due_date(BRAND, day, year, month)


def test_a_brand_is_due_from_its_date_and_hour_of_the_month_and_not_before():
    d = due(2026, 3)
    assert not schedule.is_due(row(), d - timedelta(seconds=1))
    assert schedule.is_due(row(), d)
    assert schedule.is_due(row(), d + timedelta(days=15))
    assert not schedule.is_due(row(enabled=False), d)


def test_a_brand_runs_once_a_month_whatever_the_restarts():
    d = due(2026, 3)
    ran = row(last=d + timedelta(minutes=3))
    for minutes in (3, 10, 600, 60 * 24 * 20):  # the worker restarts and looks again
        assert not schedule.is_due(ran, d + timedelta(minutes=minutes))
    assert not schedule.is_due(row(day=20, last=due(2026, 3, 5)), due(2026, 3, 20))  # a day changed after the run: still this month's


def test_a_worker_stopped_on_the_day_catches_up_once_when_it_returns():
    last_month = due(2026, 2)
    assert schedule.is_due(row(last=last_month), due(2026, 3) + timedelta(days=4))
    assert not schedule.is_due(row(last=due(2026, 3) + timedelta(days=4)), due(2026, 3) + timedelta(days=4, hours=1))


def test_month_and_year_changes():
    assert not schedule.is_due(row(last=due(2026, 12)), at(2027, 1, 9))
    assert schedule.is_due(row(last=due(2026, 12)), due(2027, 1))
    assert not schedule.is_due(row(last=due(2027, 1)), at(2027, 1, 31, 23, 59))


@pytest.mark.parametrize("year", [2027, 2028])  # not a leap year, then a leap year
def test_the_28th_exists_in_february(year):
    d = due(year, 2, 28)
    assert d.month == 2 and d.day == 28
    assert not schedule.is_due(row(day=28), d - timedelta(minutes=1))
    assert schedule.is_due(row(day=28), d)
    assert not schedule.is_due(row(day=28, last=d), at(year, 3, 20))  # next due: 28 March


def test_one_year_of_ticks_launches_twelve_times_never_twice_in_a_month():
    state, launches, now = row(day=28), [], at(2026, 6, 1)
    while now < at(2027, 6, 1):
        if schedule.is_due(state, now):
            state["last_run_at"] = now
            launches.append(now)
        now += timedelta(minutes=30)
    assert len(launches) == 12 and len({(d.year, d.month) for d in launches}) == 12


# --- the database ------------------------------------------------------------------------------------------------------

@pytest.fixture()
def quiet(cloud_database_url, monkeypatch):
    """Nothing else is scheduled or queued anywhere, and the caps are wide: these tests look at their own brands."""
    monkeypatch.setenv("NYRA_MAX_HEAVY_JOBS", "100")
    monkeypatch.setenv("NYRA_MAX_MODEL_JOBS", "100")
    with cloud_db.connect(cloud_database_url) as conn:
        conn.execute("UPDATE brand_schedules SET enabled = false")
        conn.execute("UPDATE jobs SET status = 'cancelled' WHERE status IN ('queued', 'running')")
    return cloud_database_url


def make_brand(conn, *, site=True):
    org = cloud_db.create_organization(conn, name="S", slug=f"s-{uuid.uuid4().hex[:8]}")
    brand = conn.execute("SELECT id FROM brands WHERE org_id = %s", (org,)).fetchone()["id"]
    if site:
        cloud_db.create_site(conn, org_id=org, brand_id=brand, url=f"https://{uuid.uuid4().hex[:8]}.test/")
    return org, brand


def schedule_row(conn, brand):
    return conn.execute("SELECT * FROM brand_schedules WHERE brand_id = %s", (brand,)).fetchone()


def jobs_of(conn, brand):
    return conn.execute("SELECT * FROM jobs WHERE brand_id = %s ORDER BY created_at, id", (brand,)).fetchall()


def audit_rows(conn, org):
    return conn.execute("SELECT * FROM audit_log WHERE org_id = %s AND action = 'schedule.run'", (org,)).fetchall()


def enabled_brand(conn, day=3, **kwargs):
    """A brand scheduled on `day`, enabled in the past: its next date is in the month after `JUNE`."""
    org, brand = make_brand(conn, **kwargs)
    schedule.enable(conn, brand, org, day, now=JUNE_FIRST)
    return org, brand


JUNE_FIRST = at(2026, 6, 1)


def test_a_due_brand_gets_one_scheduled_crawl_made_by_the_system_and_a_trace(quiet):
    with cloud_db.connect(quiet) as conn:
        org, brand = enabled_brand(conn)
        now = schedule.due_date(brand, 3, 2026, 6) + timedelta(minutes=1)
        queued = schedule.enqueue_due(conn, now)
        again = schedule.enqueue_due(conn, now + timedelta(minutes=5))
        job, = jobs_of(conn, brand)
        trace, = audit_rows(conn, org)
        state = schedule_row(conn, brand)
    assert [j["id"] for j in queued] == [str(job["id"])] and again == []
    assert (job["kind"], job["status"], job["created_by"], job["org_id"]) == ("crawl", "queued", None, org)
    assert job["params"] == {"fresh": True, "then_match": True, "scheduled": True}
    assert state["last_run_at"] == now
    assert trace["user_id"] is None and trace["brand_id"] == brand and trace["detail"]["queued"] == 1


def test_a_brand_not_yet_due_or_switched_off_gets_nothing(quiet):
    with cloud_db.connect(quiet) as conn:
        _, early = enabled_brand(conn)
        _, off = enabled_brand(conn)
        schedule.disable(conn, off)
        d = schedule.due_date(early, 3, 2026, 6)
        assert schedule.enqueue_due(conn, d - timedelta(minutes=1)) == []
        assert [j["brand_id"] for j in schedule.enqueue_due(conn, d + timedelta(minutes=1))] == [str(early)]
        assert jobs_of(conn, off) == []


def test_two_workers_looking_at_the_same_moment_queue_one_read(quiet):
    with cloud_db.connect(quiet) as conn:
        org, brand = enabled_brand(conn)
    now, start, results = at(2026, 6, 20), threading.Barrier(2), []

    def worker():
        start.wait()
        with cloud_db.connect(quiet) as conn:
            results.append(len(schedule.enqueue_due(conn, now)))

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    with cloud_db.connect(quiet) as conn:
        assert len(jobs_of(conn, brand)) == 1 and len(audit_rows(conn, org)) == 1
    assert sorted(results) == [0, 1]


def test_a_worker_that_read_the_row_before_another_queued_it_queues_nothing(quiet):
    """The race, made certain: both saw the brand due; the claim repeats the test, so only the first one wins."""
    with cloud_db.connect(quiet) as conn:
        org, brand = enabled_brand(conn)
        stale = schedule_row(conn, brand)
        now = at(2026, 6, 20)
        assert schedule._launch(conn, stale, now) is not None
        assert schedule._launch(conn, stale, now + timedelta(minutes=1)) is None
        assert len(jobs_of(conn, brand)) == 1 and len(audit_rows(conn, org)) == 1


def test_a_scheduled_read_already_waiting_is_not_doubled_and_the_month_counts_as_done(quiet):
    with cloud_db.connect(quiet) as conn:
        org, brand = enabled_brand(conn)
        waiting = cloud_jobs.enqueue(conn, org_id=org, brand_id=brand, kind="crawl", params={"scheduled": True})
        assert schedule.enqueue_due(conn, at(2026, 6, 20)) == []
        assert [str(j["id"]) for j in jobs_of(conn, brand)] == [waiting["id"]]
        assert schedule_row(conn, brand)["last_run_at"] == at(2026, 6, 20)
        assert audit_rows(conn, org)[0]["detail"]["queued"] == 0


@pytest.mark.parametrize("status", ["queued", "running"])
def test_a_read_started_by_hand_is_not_the_months_read_the_brand_is_tried_again(quiet, status, caplog):
    """A partial or manual crawl of the brand (queued or running) must not make the month count as read."""
    with cloud_db.connect(quiet) as conn:
        org, brand = enabled_brand(conn)
        manual = cloud_jobs.enqueue(conn, org_id=org, brand_id=brand, kind="crawl", manual=True, params={"site_ids": []})
        conn.execute("UPDATE jobs SET status = %s WHERE id = %s", (status, manual["id"]))
        with caplog.at_level(logging.WARNING, logger="nyra.cloud.schedule"):
            assert schedule.enqueue_due(conn, at(2026, 6, 20)) == []
            assert schedule.enqueue_due(conn, at(2026, 6, 20, 1)) == []
        assert schedule_row(conn, brand)["last_run_at"] is None and audit_rows(conn, org) == []
        assert [str(j["id"]) for j in jobs_of(conn, brand)] == [manual["id"]]
        conn.execute("UPDATE jobs SET status = 'done' WHERE id = %s", (manual["id"],))
        assert len(schedule.enqueue_due(conn, at(2026, 6, 20, 2))) == 1  # tried again once the way is clear
    assert caplog.text == ""  # no warning, no trace at each pass


def test_a_brand_without_a_site_queues_nothing_and_is_not_retried_in_a_loop(quiet, caplog):
    with cloud_db.connect(quiet) as conn:
        org, brand = enabled_brand(conn, site=False)
        with caplog.at_level(logging.WARNING, logger="nyra.cloud.schedule"):
            assert schedule.enqueue_due(conn, at(2026, 6, 20)) == []
        assert schedule_row(conn, brand)["last_run_at"] == at(2026, 6, 20)
        assert schedule.enqueue_due(conn, at(2026, 6, 20, 1)) == [] and jobs_of(conn, brand) == []
    assert "no site" in caplog.text


def test_at_most_three_brands_are_queued_per_pass(quiet):
    with cloud_db.connect(quiet) as conn:
        brands = [enabled_brand(conn)[1] for _ in range(7)]
        now = at(2026, 6, 25)
        sizes = [len(schedule.enqueue_due(conn, now)) for _ in range(4)]
        queued = sum(len(jobs_of(conn, b)) for b in brands)
    assert sizes == [3, 3, 1, 0] and queued == 7 and schedule.MAX_PER_TICK == 3


def test_a_failure_rolls_the_brand_back_logs_it_and_the_brand_is_tried_again(quiet, monkeypatch, caplog):
    def boom(*args, **kwargs):
        raise RuntimeError("queue down")

    with cloud_db.connect(quiet) as conn:
        org, brand = enabled_brand(conn)
        monkeypatch.setattr(cloud_jobs, "enqueue", boom)
        with caplog.at_level(logging.ERROR, logger="nyra.cloud.schedule"):
            assert schedule.enqueue_due(conn, at(2026, 6, 20)) == []
        assert schedule_row(conn, brand)["last_run_at"] is None and jobs_of(conn, brand) == [] and audit_rows(conn, org) == []
        monkeypatch.undo()
        assert len(schedule.enqueue_due(conn, at(2026, 6, 20, 1))) == 1
    assert "queue down" in caplog.text


# --- enable / disable ----------------------------------------------------------------------------------------------------

def test_enable_waits_for_the_next_date_when_this_month_s_has_passed(quiet):
    with cloud_db.connect(quiet) as conn:
        org, brand = make_brand(conn)
        d = schedule.due_date(brand, 10, 2026, 6)
        schedule.enable(conn, brand, org, 10, now=d + timedelta(days=1))  # the 10th has passed
        assert schedule.enqueue_due(conn, d + timedelta(days=2)) == []
        assert len(schedule.enqueue_due(conn, schedule.due_date(brand, 10, 2026, 7))) == 1


def test_enable_before_the_date_reads_on_that_date(quiet):
    with cloud_db.connect(quiet) as conn:
        org, brand = make_brand(conn)
        d = schedule.due_date(brand, 10, 2026, 6)
        schedule.enable(conn, brand, org, 10, now=d - timedelta(days=1))
        assert schedule_row(conn, brand)["last_run_at"] is None
        assert schedule.enqueue_due(conn, d - timedelta(minutes=1)) == []
        assert len(schedule.enqueue_due(conn, d)) == 1


def test_enable_without_a_day_takes_the_brands_own_and_a_bad_day_is_refused(quiet):
    with cloud_db.connect(quiet) as conn:
        org, brand = make_brand(conn)
        schedule.enable(conn, brand, org, None, now=JUNE_FIRST)
        assert schedule_row(conn, brand)["day_of_month"] == schedule.default_day(brand)
        for bad in (0, 29, -1):
            with pytest.raises(ValueError):
                schedule.enable(conn, brand, org, bad, now=JUNE_FIRST)
        schedule.disable(conn, brand)
        assert schedule_row(conn, brand)["enabled"] is False


def test_a_schedule_cannot_point_at_another_organizations_brand(quiet):
    with cloud_db.connect(quiet) as conn:
        _, brand = make_brand(conn)
        other, _ = make_brand(conn)
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            schedule.enable(conn, brand, other, 5, now=JUNE_FIRST)


# --- the switch ----------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [("", True), ("1", True), ("true", True), ("ON", True),
                                             ("0", False), ("false", False), (" Off ", False)])
def test_the_scheduler_switch(monkeypatch, value, expected):
    monkeypatch.setenv("NYRA_SCHEDULER", value)
    assert schedule.scheduler_enabled() is expected


def test_an_unknown_value_runs_the_scheduler_and_warns_once(monkeypatch, caplog):
    monkeypatch.setenv("NYRA_SCHEDULER", "maybe-xyz")
    with caplog.at_level(logging.WARNING, logger="nyra.cloud.schedule"):
        assert schedule.scheduler_enabled() is True and schedule.scheduler_enabled() is True
    assert caplog.text.count("maybe-xyz") == 1


def test_the_worker_does_nothing_when_the_switch_is_off_and_never_dies_on_an_error(monkeypatch, caplog):
    from nyra.cloud import worker

    calls = []
    monkeypatch.setattr(schedule, "enqueue_due", lambda *a, **k: calls.append(1) or [])
    runner = worker.Worker.__new__(worker.Worker)
    runner.database_url = "postgresql://nowhere.invalid/x"

    def down(url):
        raise OperationalError("the database does not answer")

    monkeypatch.setattr(worker.cloud_db, "connect", down)
    monkeypatch.setenv("NYRA_SCHEDULER", "off")
    runner._enqueue_scheduled()
    assert calls == []
    monkeypatch.setenv("NYRA_SCHEDULER", "on")
    with caplog.at_level(logging.ERROR, logger="nyra.worker"):
        runner._enqueue_scheduled()  # the database does not answer: logged, not raised
    assert "monthly" in caplog.text


# --- the place in the queue and the person shown -------------------------------------------------------------------------

def test_a_scheduled_read_goes_after_the_reads_started_by_hand_whatever_the_organization(quiet):
    with cloud_db.connect(quiet) as conn:
        planned = []
        for _ in range(2):  # two organizations whose scheduled read has waited the longest
            org, brand = make_brand(conn)
            job = cloud_jobs.enqueue(conn, org_id=org, brand_id=brand, kind="crawl", params={"scheduled": True, "fresh": True})
            conn.execute("UPDATE jobs SET created_at = clock_timestamp() WHERE id = %s", (job["id"],))
            planned.append(job["id"])
        org, brand = make_brand(conn)
        manual = cloud_jobs.enqueue(conn, org_id=org, brand_id=brand, kind="crawl", manual=True, params={"fresh": True})
        conn.execute("UPDATE jobs SET created_at = clock_timestamp() WHERE id = %s", (manual["id"],))
    order = []
    for _ in range(3):
        with cloud_db.connect(quiet) as conn:
            job = cloud_jobs.claim(conn)
            order.append(job["id"])
            cloud_jobs.finish(conn, job["id"], status="done", message="ok")
    assert order == [manual["id"], *planned]


def test_a_job_without_author_is_shown_as_the_system_not_as_a_crash(quiet):
    with cloud_db.connect(quiet) as conn:
        org, brand = make_brand(conn)
        running = cloud_jobs.enqueue(conn, org_id=org, brand_id=brand, kind="crawl", params={"scheduled": True})
        conn.execute("UPDATE jobs SET status = 'running' WHERE id = %s", (running["id"],))
        other = cloud_db.create_brand(conn, org_id=org, name="Autre", slug="autre")
        current = cloud_jobs.current(conn, org, other, admin=True)
        member = cloud_jobs.current(conn, org, other, admin=False)
        with pytest.raises(cloud_jobs.JobConflict) as conflict:
            cloud_jobs.enqueue(conn, org_id=org, brand_id=other, kind="crawl", manual=True)
    for shown in (current["organization"][0], member["organization"][0], cloud_jobs.holder(conflict.value.job, admin=False)):
        assert "lecture mensuelle automatique" in shown["text"] and "None" not in shown["text"]
    assert cloud_jobs.holder(conflict.value.job, admin=True)["by"] == "la lecture mensuelle automatique"


def test_a_queued_scheduled_read_does_not_stop_a_manual_launch_which_goes_first_but_a_running_one_does(quiet):
    with cloud_db.connect(quiet) as conn:
        org, brand = make_brand(conn)
        other = cloud_db.create_brand(conn, org_id=org, name="Autre", slug="autre")
        planned = cloud_jobs.enqueue(conn, org_id=org, brand_id=brand, kind="crawl", params={"scheduled": True})
        conn.execute("UPDATE jobs SET created_at = now() - interval '1 hour' WHERE id = %s", (planned["id"],))
        manual = cloud_jobs.enqueue(conn, org_id=org, brand_id=other, kind="crawl", manual=True)  # accepted
        with pytest.raises(cloud_jobs.JobConflict):  # a second manual launch still meets the first (AXE-529)
            cloud_jobs.enqueue(conn, org_id=org, brand_id=brand, kind="report", manual=True)
    served = []
    for _ in range(2):
        with cloud_db.connect(quiet) as conn:
            job = cloud_jobs.claim(conn)
            served.append(job and job["id"])
            if job:
                cloud_jobs.finish(conn, job["id"], status="done", message="ok")
    assert served == [manual["id"], planned["id"]]  # the planned read goes after, whatever its age
    with cloud_db.connect(quiet) as conn:
        running = cloud_jobs.enqueue(conn, org_id=org, brand_id=brand, kind="crawl", params={"scheduled": True})
        conn.execute("UPDATE jobs SET status = 'running' WHERE id = %s", (running["id"],))
        with pytest.raises(cloud_jobs.JobConflict):  # a running one still blocks
            cloud_jobs.enqueue(conn, org_id=org, brand_id=other, kind="crawl", manual=True)
        with pytest.raises(psycopg.errors.UniqueViolation):  # and the index still allows one running job per organization
            conn.execute("INSERT INTO jobs (org_id, brand_id, kind, status) VALUES (%s, %s, 'match', 'running')", (org, other))


# --- enable after a change of day, a missing table, the order of the passes -------------------------------------------

def test_changing_the_day_of_a_brand_already_read_does_not_skip_a_month(quiet):
    with cloud_db.connect(quiet) as conn:
        org, brand = make_brand(conn)
        schedule.enable(conn, brand, org, 25, now=at(2026, 8, 1))
        assert len(schedule.enqueue_due(conn, at(2026, 9, 26))) == 1  # read on 25 September
        conn.execute("UPDATE jobs SET status = 'done' WHERE brand_id = %s", (brand,))
        read = schedule_row(conn, brand)["last_run_at"]
        schedule.enable(conn, brand, org, 10, now=at(2026, 10, 24))  # on 24 October, day 10 has passed
        assert schedule_row(conn, brand)["last_run_at"] == read  # untouched
        assert len(schedule.enqueue_due(conn, at(2026, 10, 24, 12))) == 1  # October is read at the next pass
        assert schedule.enqueue_due(conn, at(2026, 10, 24, 13)) == []


def test_enabling_again_after_a_read_this_month_waits_and_a_never_read_brand_waits_for_the_next_date(quiet):
    with cloud_db.connect(quiet) as conn:
        org, brand = make_brand(conn)
        schedule.enable(conn, brand, org, 3, now=at(2026, 6, 1))
        assert len(schedule.enqueue_due(conn, at(2026, 6, 20))) == 1
        conn.execute("UPDATE jobs SET status = 'done' WHERE brand_id = %s", (brand,))
        schedule.disable(conn, brand)
        schedule.enable(conn, brand, org, 3, now=at(2026, 6, 21))
        assert schedule.enqueue_due(conn, at(2026, 6, 22)) == []  # already read in June
        assert len(schedule.enqueue_due(conn, at(2026, 7, 5))) == 1
        # Never read, enabled with its date passed: the first read is the next date, and a change of day keeps that.
        other_org, other = make_brand(conn)
        schedule.enable(conn, other, other_org, 3, now=at(2026, 6, 20))
        schedule.enable(conn, other, other_org, 4, now=at(2026, 6, 21))
        assert schedule_row(conn, other)["last_run_at"] == at(2026, 6, 20)
        assert schedule.enqueue_due(conn, at(2026, 6, 25)) == []


def test_a_missing_table_is_one_short_warning_an_hour_and_any_other_error_keeps_its_trace(monkeypatch, caplog, cloud_database_url):
    from nyra.cloud import worker

    with cloud_db.connect(cloud_database_url) as conn:
        try:
            conn.execute("SELECT 1 FROM brand_schedules_that_is_missing")
        except psycopg.errors.UndefinedTable as error:
            missing = error
    clock = [1000.0]
    monkeypatch.setattr(schedule.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(schedule, "_LAST_TOLD", {})
    runner = worker.Worker.__new__(worker.Worker)
    runner.database_url = "unused"

    def raising(error):
        def connect(url):
            raise error
        return connect

    monkeypatch.setattr(worker.cloud_db, "connect", raising(missing))
    with caplog.at_level(logging.WARNING, logger="nyra.worker"):
        for _ in range(5):  # five passes, 5 minutes apart
            runner._enqueue_scheduled()
            clock[0] += 300
        assert caplog.text.count("20261024000033") == 1 and not any(r.exc_info for r in caplog.records)
        clock[0] += 3600
        runner._enqueue_scheduled()
        assert caplog.text.count("20261024000033") == 2
        caplog.clear()
        monkeypatch.setattr(worker.cloud_db, "connect", raising(OperationalError("down")))
        runner._enqueue_scheduled()
    assert any(r.exc_info for r in caplog.records)


def test_the_longest_unread_brands_go_first_and_a_failing_brand_traces_once_an_hour(quiet, monkeypatch, caplog):
    with cloud_db.connect(quiet) as conn:
        old, never, *recent = [enabled_brand(conn, day=1)[1] for _ in range(12)]  # many, so the order is not luck
        conn.execute("UPDATE brand_schedules SET last_run_at = %s WHERE brand_id = %s", (at(2026, 4, 1), old))
        conn.execute("UPDATE brand_schedules SET last_run_at = %s WHERE brand_id = ANY(%s)", (at(2026, 5, 20), recent))
        first = [str(j["brand_id"]) for j in schedule.enqueue_due(conn, at(2026, 6, 20), limit=2)]
    assert first == [str(never), str(old)]
    clock = [1000.0]
    monkeypatch.setattr(schedule.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(schedule, "_LAST_TOLD", {})

    def broken(*args, **kwargs):
        raise RuntimeError("broken")

    monkeypatch.setattr(cloud_jobs, "enqueue", broken)
    with cloud_db.connect(quiet) as conn, caplog.at_level(logging.WARNING, logger="nyra.cloud.schedule"):
        for _ in range(4):
            schedule.enqueue_due(conn, at(2026, 6, 20, 1))
            clock[0] += 300
        traces = [r.args for r in caplog.records if r.exc_info]
        assert traces and len(traces) == len(set(traces))  # one trace per brand in the hour, not one per pass
        clock[0] += 3600
        schedule.enqueue_due(conn, at(2026, 6, 20, 2))
        assert len([r for r in caplog.records if r.exc_info]) == 2 * len(traces)
