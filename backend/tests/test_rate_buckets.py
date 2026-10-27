"""The token buckets of the rate limit kept in Postgres (migration 38, AXE-586): taking a token is atomic, the table is purged,
and the public API has no access. The clock is the function's `p_now` argument, so every case is deterministic."""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("psycopg")
pytest.importorskip("fastapi")

from conftest import MIGRATIONS_DIR  # noqa: E402

from nyra.cloud import db as cloud_db  # noqa: E402

T0 = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)
TAKE = "SELECT public.take_rate_token(%s, %s, %s, %s) AS wait"


@pytest.fixture()
def person(cloud_database_url):
    who = str(uuid.uuid4())
    yield who
    with cloud_db.connect(cloud_database_url) as conn:
        conn.execute("DELETE FROM rate_buckets WHERE identity = %s", (who,))


def take(url, who, kind="read", capacity=120, at=T0) -> float:
    with cloud_db.connect(url) as conn:
        return conn.execute(TAKE, (who, kind, capacity, at)).fetchone()["wait"]


def test_200_simultaneous_calls_against_a_limit_of_120_let_exactly_120_through(cloud_database_url, person):
    with ThreadPoolExecutor(max_workers=20) as pool:
        waits = list(pool.map(lambda _: take(cloud_database_url, person), range(200)))
    assert sum(1 for wait in waits if wait == 0) == 120
    assert all(wait == pytest.approx(0.5) for wait in waits if wait > 0)  # 120 a minute: a token every half second


def test_the_bucket_refills_with_time_and_never_beyond_its_capacity(cloud_database_url, person):
    for _ in range(3):
        assert take(cloud_database_url, person, capacity=3) == 0
    assert take(cloud_database_url, person, capacity=3) == pytest.approx(20)  # 3 a minute: one token every 20 s
    assert take(cloud_database_url, person, capacity=3, at=T0 + timedelta(seconds=5)) == pytest.approx(15)  # a refusal took nothing and did not extend the wait
    assert take(cloud_database_url, person, capacity=3, at=T0 + timedelta(seconds=20)) == 0
    assert take(cloud_database_url, person, capacity=3, at=T0 + timedelta(seconds=20)) > 0
    later = T0 + timedelta(seconds=620)  # long idle: the full burst, not more
    assert [take(cloud_database_url, person, capacity=3, at=later) for _ in range(3)] == [0, 0, 0]
    assert take(cloud_database_url, person, capacity=3, at=later) > 0


def test_a_clock_that_goes_back_neither_refills_nor_drains(cloud_database_url, person):
    assert take(cloud_database_url, person, capacity=2, at=T0 + timedelta(seconds=10)) == 0
    assert take(cloud_database_url, person, capacity=2, at=T0) == 0   # a call that started earlier, served later
    assert take(cloud_database_url, person, capacity=2, at=T0) > 0


def test_each_person_and_each_kind_has_their_own_bucket(cloud_database_url, person):
    other = str(uuid.uuid4())
    try:
        assert take(cloud_database_url, person, "read", 1) == 0 and take(cloud_database_url, person, "read", 1) > 0
        assert take(cloud_database_url, person, "write", 1) == 0
        assert take(cloud_database_url, other, "read", 1) == 0
    finally:
        with cloud_db.connect(cloud_database_url) as conn:
            conn.execute("DELETE FROM rate_buckets WHERE identity = %s", (other,))


def test_a_kind_other_than_read_or_write_is_refused(cloud_database_url, person):
    with pytest.raises(Exception, match="rate_buckets_kind_check"):
        take(cloud_database_url, person, "delete", 5)


def test_the_purge_empties_buckets_idle_for_two_minutes_and_keeps_the_others(cloud_database_url):
    now = datetime.now(timezone.utc)
    old, recent = str(uuid.uuid4()), str(uuid.uuid4())
    try:
        take(cloud_database_url, old, at=now - timedelta(minutes=3))
        take(cloud_database_url, recent, at=now - timedelta(seconds=30))
        with cloud_db.connect(cloud_database_url) as conn:
            deleted = conn.execute("SELECT public.purge_rate_buckets() AS n").fetchone()["n"]
            left = {r["identity"] for r in conn.execute("SELECT identity FROM rate_buckets WHERE identity = ANY(%s)", ([old, recent],)).fetchall()}
        assert deleted >= 1 and left == {recent}
    finally:
        with cloud_db.connect(cloud_database_url) as conn:
            conn.execute("DELETE FROM rate_buckets WHERE identity = ANY(%s)", ([old, recent],))


def test_the_public_api_roles_have_no_access(cloud_database_url):
    with cloud_db.connect(cloud_database_url) as conn:
        roles = [r["rolname"] for r in conn.execute("SELECT rolname FROM pg_roles WHERE rolname IN ('anon', 'authenticated')").fetchall()]
        rls = conn.execute("SELECT relrowsecurity FROM pg_class WHERE oid = 'public.rate_buckets'::regclass").fetchone()["relrowsecurity"]
        for role in roles:
            assert not conn.execute("SELECT has_table_privilege(%s, 'public.rate_buckets', 'SELECT,INSERT,UPDATE,DELETE') AS p", (role,)).fetchone()["p"]
            for function in ("public.take_rate_token(text, text, integer, timestamptz)", "public.purge_rate_buckets()"):
                assert not conn.execute("SELECT has_function_privilege(%s, %s, 'EXECUTE') AS p", (role, function)).fetchone()["p"]
    assert rls


def test_the_migration_can_be_applied_twice_and_keeps_the_buckets(cloud_database_url, person):
    take(cloud_database_url, person, capacity=1)
    sql = (MIGRATIONS_DIR / "20261027000038_rate_buckets.sql").read_text(encoding="utf-8")
    with cloud_db.connect(cloud_database_url) as conn:
        conn.execute(sql)
        conn.execute(sql)
    assert take(cloud_database_url, person, capacity=1) > 0  # still empty: the table and its rows survived


# --- the limiter on top of the buckets (AXE-587) ----------------------------------------------------------------------------

import logging  # noqa: E402

import jwt as pyjwt  # noqa: E402
import psycopg  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from nyra.cloud.api import CloudSettings, create_app  # noqa: E402
from nyra.cloud.ratelimit import DatabaseRateLimiter, backend_from_env  # noqa: E402

SECRET = "test-secret-at-least-32-bytes-long-enough!!"


class Clock:
    now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_the_backend_comes_from_the_environment_and_an_unknown_value_keeps_memory(caplog):
    assert backend_from_env({}) == "memory" and backend_from_env({"NYRA_RATE_BACKEND": " Database "}) == "database"
    with caplog.at_level(logging.WARNING, logger="nyra.api"):
        assert backend_from_env({"NYRA_RATE_BACKEND": "redis"}) == "memory"
    assert "redis" in caplog.text


def _app(url, monkeypatch):
    monkeypatch.setenv("NYRA_RATE_BACKEND", "database")
    monkeypatch.setenv("NYRA_RATE_READ_PER_MIN", "3")
    return TestClient(create_app(CloudSettings(database_url=url, supabase_url="https://x.supabase.co", service_role_key="key", jwt_secret=SECRET)))


def test_two_instances_of_the_app_share_one_limit(cloud_database_url, person, monkeypatch):
    first, second = _app(cloud_database_url, monkeypatch), _app(cloud_database_url, monkeypatch)
    token = pyjwt.encode({"sub": person, "aud": "authenticated", "exp": int(datetime.now(timezone.utc).timestamp()) + 3600}, SECRET, algorithm="HS256")
    headers = {"Authorization": f"Bearer {token}"}
    # 3 reads a minute for the person, whichever instance answers: the fourth is refused (in memory each instance would allow 3)
    statuses = [(first, second)[i % 2].get("/api/me", headers=headers).status_code for i in range(4)]
    assert statuses[:3] == [200] * 3 and statuses[3] == 429
    assert first.get("/api/healthz").status_code == 200  # never limited


def test_a_database_that_fails_falls_back_on_the_process_buckets_with_one_warning(caplog):
    calls, clock = [], Clock()

    def down():
        calls.append(1)
        raise psycopg.OperationalError("down")

    limiter = DatabaseRateLimiter({"read": 2, "write": 2}, down, clock=clock)
    with caplog.at_level(logging.WARNING, logger="nyra.api"):
        assert [limiter.wait("a", "read") for _ in range(2)] == [0.0, 0.0]
        assert limiter.wait("a", "read") > 0  # the limit still holds, in this process
    assert len(calls) == 1  # the database is left alone for a while after a failure
    assert caplog.text.count("did not answer") == 1
    clock.now += 31
    limiter.wait("b", "read")
    assert len(calls) == 2  # tried again


def test_an_unreachable_database_costs_at_most_the_pool_timeout_once(monkeypatch):
    import time

    from nyra.cloud import ratelimit

    monkeypatch.setattr(ratelimit, "DB_TIMEOUT", 0.3)
    url = "postgresql://x:x@127.0.0.1:1/none?connect_timeout=1"
    limiter = DatabaseRateLimiter({"read": 5, "write": 5}, lambda: cloud_db.pool(url).connection(timeout=ratelimit.DB_TIMEOUT))
    started = time.monotonic()
    try:
        assert limiter.wait("a", "read") == 0.0
        assert limiter.wait("a", "read") == 0.0
        assert time.monotonic() - started < 2
    finally:
        cloud_db.close_pools()  # the pool retries in the background, and would log into a closed stream after the run
