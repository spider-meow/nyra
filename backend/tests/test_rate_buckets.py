"""The token buckets of the rate limit kept in Postgres (migration 38, AXE-586): taking a token is atomic, the table is purged,
and the public API has no access. The clock is the function's `p_now` argument, so every case is deterministic."""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("psycopg")

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
