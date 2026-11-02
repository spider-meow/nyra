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


def row(day=2, last=None, enabled=True, brand=BRAND):
    return {"brand_id": brand, "enabled": enabled, "day_of_month": day, "last_run_at": last}


# --- the days and the hour: a deterministic spread -------------------------------------------------------------------

def test_the_days_and_the_hour_come_from_the_uuid_and_spread_over_every_slot():
    brands = [uuid.uuid5(uuid.NAMESPACE_DNS, f"brand-{i}") for i in range(1000)]
    assert [schedule.default_day(b) for b in brands[:5]] == [schedule.default_day(b) for b in brands[:5]]  # no hash(): stable
    assert schedule.default_day(BRAND) == schedule.default_day(uuid.UUID(str(BRAND)))
    firsts, seconds = Counter(schedule.default_day(b) for b in brands), Counter(schedule.second_day(b) for b in brands)
    slots = Counter(schedule.slot_time(b) for b in brands)
    assert sorted(firsts) == [1, 2, 3] and sorted(seconds) == [13, 14, 15, 16] and len(slots) == schedule.SLOTS  # the 1st to the 3rd, the 13th to the 16th
    assert max(firsts.values()) <= 2 * 1000 / 3 and max(seconds.values()) <= 2 * 1000 / 4 and max(slots.values()) <= 2 * 1000 / schedule.SLOTS
    assert all((2, 0) <= slot < (5, 0) for slot in slots)  # between 02:00 and 05:00 UTC
    # the two days are not tied to each other: every first day meets every second day
    assert len({(schedule.default_day(b), schedule.second_day(b)) for b in brands}) == 12


def test_a_day_outside_the_first_window_is_the_default_one():
    assert schedule.first_day(BRAND, 3) == 3 and schedule.first_day(BRAND, 10) == schedule.default_day(BRAND)  # a schedule from the days when 1 to 28 was allowed
    first, second = schedule.reads_of_month(BRAND, 28, 2026, 11)
    assert first.day == schedule.default_day(BRAND) and second.day == schedule.second_day(BRAND) and first.month == second.month == 11


# --- is_due: pure -----------------------------------------------------------------------------------------------------

def first(year, month, day=2):
    return schedule.reads_of_month(BRAND, day, year, month)[0]


def second(year, month, day=2):
    return schedule.reads_of_month(BRAND, day, year, month)[1]


def test_a_brand_is_due_from_each_of_its_two_dates_of_the_month_and_not_before():
    a, b = first(2026, 3), second(2026, 3)
    assert not schedule.is_due(row(), a - timedelta(seconds=1))
    assert schedule.is_due(row(), a)
    assert schedule.is_due(row(last=a), b) and not schedule.is_due(row(last=a), b - timedelta(seconds=1))  # the second read, once the first is done
    assert schedule.is_due(row(), b + timedelta(days=10))
    assert not schedule.is_due(row(enabled=False), a)


def test_a_brand_runs_once_in_each_period_whatever_the_restarts():
    a, b = first(2026, 3), second(2026, 3)
    for minutes in (3, 10, 600, 60 * 24 * 8):  # the worker restarts and looks again, up to the second date
        assert not schedule.is_due(row(last=a + timedelta(minutes=3)), min(a + timedelta(minutes=minutes), b - timedelta(seconds=1))), minutes
    for minutes in (3, 10, 600, 60 * 24 * 14):  # then up to the end of the month
        assert not schedule.is_due(row(last=b + timedelta(minutes=3)), min(b + timedelta(minutes=minutes), at(2026, 3, 31, 23, 59))), minutes
    assert not schedule.is_due(row(day=3, last=first(2026, 3, 1)), first(2026, 3, 3))  # a day changed after the run: still this period's


def test_a_worker_stopped_on_the_day_catches_up_once_when_it_returns():
    assert schedule.is_due(row(last=first(2026, 2)), first(2026, 3) + timedelta(days=1))
    assert not schedule.is_due(row(last=first(2026, 3) + timedelta(days=1)), first(2026, 3) + timedelta(days=1, hours=1))
    assert schedule.is_due(row(last=second(2026, 2)), second(2026, 3))  # stopped from the 1st to the 20th: one read at the second date, not two
    assert not schedule.is_due(row(last=second(2026, 3) + timedelta(days=5)), at(2026, 3, 31, 23, 59))


def test_a_read_missed_at_the_end_of_a_month_is_not_made_up_before_the_next_first_date():
    assert not schedule.is_due(row(last=first(2026, 3)), at(2026, 4, 1))    # the second read of March was missed: April's first date comes first
    assert schedule.is_due(row(last=first(2026, 3)), first(2026, 4))


def test_month_and_year_changes():
    assert not schedule.is_due(row(last=second(2026, 12)), at(2027, 1, 1))
    assert schedule.is_due(row(last=second(2026, 12)), first(2027, 1))
    assert schedule.is_due(row(last=first(2027, 1)), second(2027, 1))
    assert not schedule.is_due(row(last=second(2027, 1)), at(2027, 1, 31, 23, 59))


def test_both_dates_exist_in_february_of_every_kind_of_year():
    for year in (2027, 2028):  # not a leap year, then a leap year
        a, b = first(year, 2), second(year, 2)
        assert a.month == b.month == 2 and a.day <= 3 and 13 <= b.day <= 16
        assert not schedule.is_due(row(), a - timedelta(minutes=1)) and schedule.is_due(row(), a)
        assert not schedule.is_due(row(last=b), at(year, 3, 1))


def test_one_year_of_ticks_launches_twenty_four_times_twice_in_each_month():
    state, launches, now = row(day=3), [], at(2026, 6, 1)
    while now < at(2027, 6, 1):
        if schedule.is_due(state, now):
            state["last_run_at"] = now
            launches.append(now)
        now += timedelta(minutes=30)
    months = Counter((d.year, d.month) for d in launches)
    assert len(launches) == 24 and set(months.values()) == {2} and len(months) == 12
    assert all(d.day <= 3 for d in launches[::2]) and all(13 <= d.day <= 16 for d in launches[1::2])  # the 1st to the 3rd, then the 13th to the 16th


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
        d = schedule.due_date(brand, 2, 2026, 6)
        schedule.enable(conn, brand, org, 2, now=d + timedelta(days=1))  # the first date of June has passed
        assert schedule.enqueue_due(conn, d + timedelta(days=2)) == []
        assert len(schedule.enqueue_due(conn, schedule.last_due_date(brand, 2, 2026, 6))) == 1  # its first read is the next date: the second of June


def test_enable_before_the_date_reads_on_that_date(quiet):
    with cloud_db.connect(quiet) as conn:
        org, brand = make_brand(conn)
        d = schedule.due_date(brand, 3, 2026, 6)
        schedule.enable(conn, brand, org, 3, now=d - timedelta(days=1))
        assert schedule_row(conn, brand)["last_run_at"] is None
        assert schedule.enqueue_due(conn, d - timedelta(minutes=1)) == []
        assert len(schedule.enqueue_due(conn, d)) == 1


def test_enable_without_a_day_takes_the_brands_own_and_a_bad_day_is_refused(quiet):
    with cloud_db.connect(quiet) as conn:
        org, brand = make_brand(conn)
        schedule.enable(conn, brand, org, None, now=JUNE_FIRST)
        assert schedule_row(conn, brand)["day_of_month"] == schedule.default_day(brand)
        for bad in (0, 4, 10, 28, -1):
            with pytest.raises(ValueError):
                schedule.enable(conn, brand, org, bad, now=JUNE_FIRST)
        schedule.disable(conn, brand)
        assert schedule_row(conn, brand)["enabled"] is False


def test_a_schedule_cannot_point_at_another_organizations_brand(quiet):
    with cloud_db.connect(quiet) as conn:
        _, brand = make_brand(conn)
        other, _ = make_brand(conn)
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            schedule.enable(conn, brand, other, 2, now=JUNE_FIRST)


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
        schedule.enable(conn, brand, org, 3, now=at(2026, 8, 1))
        assert len(schedule.enqueue_due(conn, at(2026, 9, 20))) == 1  # read on 20 September (a worker late for the second read)
        conn.execute("UPDATE jobs SET status = 'done' WHERE brand_id = %s", (brand,))
        read = schedule_row(conn, brand)["last_run_at"]
        schedule.enable(conn, brand, org, 1, now=at(2026, 10, 24))  # on 24 October, both dates of the month have passed
        assert schedule_row(conn, brand)["last_run_at"] == read  # untouched
        assert len(schedule.enqueue_due(conn, at(2026, 10, 24, 12))) == 1  # October is read at the next pass, once
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
        schedule.enable(conn, other, other_org, 1, now=at(2026, 6, 21))
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


# --- next_run: pure, the date shown to the person ----------------------------------------------------------------------

def test_next_run_is_the_next_date_to_come_or_the_one_a_late_brand_is_due_for():
    a, b = first(2026, 3), second(2026, 3)
    assert schedule.next_run(BRAND, 2, None, a - timedelta(hours=2)) == a  # never read, first date to come
    assert schedule.next_run(BRAND, 2, first(2026, 2), a - timedelta(minutes=1)) == a  # read last month, date to come
    # read last month and the first date has passed: due at the next pass of the worker (the date is in the past)
    assert schedule.next_run(BRAND, 2, second(2026, 2), a + timedelta(days=3)) == a
    assert schedule.next_run(BRAND, 2, a + timedelta(minutes=3), a + timedelta(days=1)) == b  # the first read of March is done: the second comes
    assert schedule.next_run(BRAND, 2, a, b - timedelta(minutes=1)) == b
    assert schedule.next_run(BRAND, 2, a, b + timedelta(days=1)) == b  # the first of March only: the second is due now


def test_next_run_is_next_months_first_date_once_the_month_is_read_and_crosses_the_year():
    read = second(2026, 3) + timedelta(minutes=3)
    assert schedule.next_run(BRAND, 2, read, read + timedelta(days=1)) == first(2026, 4)
    december = second(2026, 12) + timedelta(minutes=3)
    assert schedule.next_run(BRAND, 2, december, december + timedelta(days=2)) == first(2027, 1)
    assert schedule.next_run(BRAND, 3, None, at(2026, 12, 29)) == second(2026, 12, 3)  # never read, both passed: the latest (past) date
    assert schedule.next_run(BRAND, 2, None, at(2026, 12, 1)).tzinfo is not None
    assert schedule.next_run(BRAND, 2, second(2026, 11), at(2026, 12, 1)) == first(2026, 12)  # a read missed at the end of the month is not made up


# --- a brand is always read by itself, or the team is told (AXE-684) ------------------------------------------------------

def test_coverage_counts_the_brands_with_a_site_that_nothing_reads_and_the_ones_late(quiet):
    with cloud_db.connect(quiet) as conn:
        before = schedule.coverage(conn, 30)
        _, on = make_brand(conn)
        _, off = make_brand(conn)
        make_brand(conn)  # a brand with a site and no schedule row
        make_brand(conn, site=False)  # no site: nothing to read, never counted
        _, late = make_brand(conn)
        _, fresh = make_brand(conn)
        for brand in (on, off, late, fresh):
            schedule.enable(conn, brand, conn.execute("SELECT org_id FROM brands WHERE id = %s", (brand,)).fetchone()["org_id"])
        schedule.disable(conn, off)
        for brand in (on, late, fresh):  # old brands: the 30 days are counted from the brand's age too
            conn.execute("UPDATE brands SET created_at = now() - interval '90 days' WHERE id = %s", (brand,))
        for brand, age in ((late, "40 days"), (fresh, "3 days")):
            site = conn.execute("SELECT id FROM sites WHERE brand_id = %s", (brand,)).fetchone()["id"]
            conn.execute(f"INSERT INTO crawl_runs (org_id, site_id, status, started_at, finished_at) "
                         f"SELECT org_id, id, 'done', now() - interval '{age}', now() - interval '{age}' FROM sites WHERE id = %s", (site,))
        after = schedule.coverage(conn, 30)
        assert after["unscheduled"] - before["unscheduled"] == 2  # the one switched off and the one with no schedule
        assert after["late"] - before["late"] == 2  # `late` (read 40 days ago) and `on` (an active schedule, never read, 90 days old)
        assert schedule.coverage(conn, 0)["late"] == 0  # no threshold, no late count


def test_enable_unscheduled_gives_a_row_to_the_brands_that_have_none_and_leaves_the_ones_switched_off(quiet):
    with cloud_db.connect(quiet) as conn:
        _, none = make_brand(conn)
        org, off = make_brand(conn)
        _, no_site = make_brand(conn, site=False)
        schedule.enable(conn, off, org)
        schedule.disable(conn, off)
        done = schedule.enable_unscheduled(conn, now=at(2026, 11, 20))
        assert done >= 1 and schedule_row(conn, none)["enabled"] and schedule_row(conn, none)["day_of_month"] == schedule.default_day(none)
        assert not schedule_row(conn, off)["enabled"] and schedule_row(conn, no_site) is None  # a choice is not undone, nothing to read is not scheduled
        assert schedule.enable_unscheduled(conn, now=at(2026, 11, 20)) == 0  # twice changes nothing
        events = conn.execute("SELECT user_id, detail FROM audit_log WHERE action = 'schedule.enable' AND brand_id = %s", (none,)).fetchall()
        assert len(events) == 1 and events[0]["user_id"] is None and events[0]["detail"]["default"] is True


def test_the_command_counts_without_names_and_switches_on_only_when_asked(quiet, monkeypatch):
    from typer.testing import CliRunner

    from nyra import cli

    monkeypatch.setenv("DATABASE_URL", quiet)
    with cloud_db.connect(quiet) as conn:
        _, none = make_brand(conn)
    run = lambda *args: CliRunner().invoke(cli.app, ["cloud-schedules", *args])  # noqa: E731
    counted = run()
    assert counted.exit_code == 0 and "with no schedule" in counted.output and "Nothing changed" in counted.output and "s-" not in counted.output  # no slug, no name
    with cloud_db.connect(quiet) as conn:
        assert schedule_row(conn, none) is None
    applied = run("--apply")
    assert applied.exit_code == 0 and "schedule(s) switched on" in applied.output
    with cloud_db.connect(quiet) as conn:
        assert schedule_row(conn, none)["enabled"]


# --- the next date is written: no number of schedules is left out (AXE-709) ------------------------------------------------

def test_the_migrations_column_is_there_and_a_switch_on_clears_the_date_worked_out_for_the_former_day(quiet):
    with cloud_db.connect(quiet) as conn:
        assert schedule.has_next_due(conn)
        org, brand = make_brand(conn)
        schedule.enable(conn, brand, org, 2, now=at(2026, 6, 1))
        conn.execute("UPDATE brand_schedules SET next_due_at = %s WHERE brand_id = %s", (at(2030, 1, 1), brand))
        schedule.enable(conn, brand, org, 3, now=at(2026, 6, 1))
        assert schedule_row(conn, brand)["next_due_at"] is None


def test_a_pass_gives_each_schedule_its_next_date_and_a_pass_with_nothing_due_reads_nothing(quiet, monkeypatch):
    with cloud_db.connect(quiet) as conn:
        org, brand = make_brand(conn)
        schedule.enable(conn, brand, org, 2, now=at(2026, 6, 1))
        assert schedule.enqueue_due(conn, at(2026, 6, 1, 12)) == []
        expected = schedule.next_run(brand, 2, schedule_row(conn, brand)["last_run_at"], at(2026, 6, 1, 12))
        assert schedule_row(conn, brand)["next_due_at"] == expected and expected > at(2026, 6, 1, 12)
        read = []
        real = schedule._due_rows
        monkeypatch.setattr(schedule, "_due_rows", lambda c, now, tracked: [read.append(r) or r for r in real(c, now, tracked)])
        assert schedule.enqueue_due(conn, at(2026, 6, 1, 13)) == [] and read == []  # nothing due, nothing worked out: nothing read
        due = schedule.enqueue_due(conn, expected)  # its date has come
        assert [j["brand_id"] for j in due] == [str(brand)] and schedule_row(conn, brand)["next_due_at"] > expected  # launched, and the next date written with the claim


def make_many(conn, due_second_day_at_most, n):
    """An organization with `n` + 1 schedules, all with a site-less brand but one: the last one has a site and is due on 14 November 2026 at noon, the
    others are not (their second read is on the 15th or the 16th) but were read earlier than it, so a scan in the order of the reads meets them first."""
    org = cloud_db.create_organization(conn, name="Many", slug=f"many-{uuid.uuid4().hex[:8]}")
    ids = []
    while len(ids) < n:
        candidate = uuid.uuid4()
        if schedule.second_day(candidate) >= 15:
            ids.append(candidate)
    due_brand = next(candidate for candidate in (uuid.uuid4() for _ in range(1000)) if schedule.second_day(candidate) <= due_second_day_at_most)
    everyone = ids + [due_brand]
    conn.execute("INSERT INTO brands (id, org_id, name, slug) SELECT x, %s, 'b', 'b-' || x::text FROM unnest(%s::uuid[]) AS x", (org, everyone))
    conn.execute("""INSERT INTO brand_schedules (brand_id, org_id, enabled, day_of_month, last_run_at)
                    SELECT x, %s, true, 2, CASE WHEN x = %s THEN %s::timestamptz ELSE %s::timestamptz END FROM unnest(%s::uuid[]) AS x""",
                 (org, due_brand, at(2026, 11, 2, 10), at(2026, 11, 2, 4), everyone))
    cloud_db.create_site(conn, org_id=org, brand_id=due_brand, url=f"https://{uuid.uuid4().hex[:8]}.test/")
    return due_brand


def test_a_due_brand_is_found_among_more_schedules_than_a_pass_can_scan(quiet):
    now = at(2026, 11, 14, 12)
    with cloud_db.connect(quiet) as conn:
        due_brand = make_many(conn, 13, 2500)  # 2 500 schedules that are not due, read before it: the former scan stopped at 2 000
        first = schedule.enqueue_due(conn, now)
        second = schedule.enqueue_due(conn, now)  # the first pass worked out 2 000 dates; this one the rest, and finds the brand
        assert [j["brand_id"] for j in first + second] == [str(due_brand)]
        assert conn.execute("SELECT count(*) AS n FROM brand_schedules WHERE enabled AND next_due_at IS NULL").fetchone()["n"] == 0
        assert schedule.enqueue_due(conn, now + timedelta(minutes=5)) == []


def test_without_the_column_the_former_scan_runs_and_says_which_migration_is_missing(quiet, monkeypatch, caplog):
    now = at(2026, 11, 14, 12)
    monkeypatch.setattr(schedule, "has_next_due", lambda conn: False)
    monkeypatch.setattr(schedule, "_LAST_TOLD", {})
    with cloud_db.connect(quiet) as conn, caplog.at_level(logging.WARNING, logger="nyra.cloud.schedule"):
        make_many(conn, 13, 2100)
        assert schedule.enqueue_due(conn, now) == [] and schedule.enqueue_due(conn, now) == []  # the scan stops at 2 000: that is the limit the column lifts
    assert caplog.text.count("20261102000047") == 1  # once an hour, not at every pass
