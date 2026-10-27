"""Rate limiting of the API: the token bucket itself (pure, fake clock), the environment settings,
and the app wired with it (no database needed: an unreachable one answers 503, which is "not limited")."""

from __future__ import annotations

import logging
import time
import uuid

import jwt as pyjwt
import psycopg
import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from nyra.cloud.api import CloudSettings, create_app  # noqa: E402
from nyra.cloud.ratelimit import DEFAULTS, RateLimiter, limits_from_env  # noqa: E402

SECRET = "test-secret-at-least-32-bytes-long-enough!!"


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _limiter(read=3, write=2, **kwargs) -> tuple[RateLimiter, Clock]:
    clock = Clock()
    return RateLimiter({"read": read, "write": write}, clock=clock, **kwargs), clock


def test_under_the_limit_everything_passes_and_over_it_says_how_long_to_wait():
    limiter, _ = _limiter(read=3)
    assert [limiter.wait("a", "read") for _ in range(3)] == [0.0, 0.0, 0.0]
    wait = limiter.wait("a", "read")
    assert wait == pytest.approx(60 / 3)  # 3 per minute: one token every 20 s


def test_the_window_frees_up_with_time():
    limiter, clock = _limiter(read=3)
    for _ in range(3):
        limiter.wait("a", "read")
    assert limiter.wait("a", "read") > 0
    clock.now += 20  # one token is back
    assert limiter.wait("a", "read") == 0.0
    assert limiter.wait("a", "read") > 0
    clock.now += 600  # long idle: back to the full burst, not more
    assert [limiter.wait("a", "read") for _ in range(3)] == [0.0] * 3
    assert limiter.wait("a", "read") > 0


def test_two_identities_and_two_kinds_do_not_get_in_each_others_way():
    limiter, _ = _limiter(read=1, write=1)
    assert limiter.wait("a", "read") == 0.0
    assert limiter.wait("a", "read") > 0
    assert limiter.wait("b", "read") == 0.0
    assert limiter.wait("a", "write") == 0.0


def test_a_refused_request_does_not_extend_the_wait():
    limiter, clock = _limiter(read=2)
    limiter.wait("a", "read"), limiter.wait("a", "read")
    for _ in range(50):
        limiter.wait("a", "read")
    clock.now += 30  # one token every 30 s: a hammering client is served as soon as the first one is back
    assert limiter.wait("a", "read") == 0.0


def test_counters_are_purged_when_idle_and_capped_in_number():
    limiter, clock = _limiter(read=3, max_keys=10)
    for n in range(8):
        limiter.wait(f"user-{n}", "read")
    clock.now += 61  # all full again: nothing left to remember
    limiter.wait("late", "read")
    assert limiter.size() == 1
    for n in range(500):  # a flood of identities that never come back
        limiter.wait(f"flood-{n}", "read")
        assert limiter.size() <= 10


def test_settings_from_the_environment_fall_back_with_a_warning(caplog):
    assert limits_from_env({}) == DEFAULTS
    assert limits_from_env({"NYRA_RATE_READ_PER_MIN": "50", "NYRA_RATE_WRITE_PER_MIN": "7"}) == {"read": 50, "write": 7}
    with caplog.at_level(logging.WARNING):
        got = limits_from_env({"NYRA_RATE_READ_PER_MIN": "beaucoup", "NYRA_RATE_WRITE_PER_MIN": "0"})
    assert got == DEFAULTS
    assert "NYRA_RATE_READ_PER_MIN" in caplog.text and "NYRA_RATE_WRITE_PER_MIN" in caplog.text


def test_the_defaults_leave_room_for_the_two_second_polling():
    # jobs/current every 2 s is 30 per minute; the screens and a second tab come on top
    assert DEFAULTS["read"] >= 3 * 30 and DEFAULTS["write"] < DEFAULTS["read"]


def _token(user_id) -> str:
    return pyjwt.encode({"sub": str(user_id), "aud": "authenticated", "exp": int(time.time()) + 3600}, SECRET, algorithm="HS256")


@pytest.fixture()
def app_client(monkeypatch):
    """The real app; the database is down, so a request that passes the limiter ends in 503, not 429."""

    def down(url):
        raise psycopg.OperationalError("database down")

    monkeypatch.setattr("nyra.cloud.db.connect", down)
    monkeypatch.setenv("NYRA_RATE_READ_PER_MIN", "3")
    monkeypatch.setenv("NYRA_RATE_WRITE_PER_MIN", "2")
    settings = CloudSettings(database_url="postgresql://x:x@db.invalid/none",
                             supabase_url="https://x.supabase.co", service_role_key="key", jwt_secret=SECRET)
    return TestClient(create_app(settings))


def _bearer(user_id=None) -> dict:
    return {"Authorization": f"Bearer {_token(user_id or uuid.uuid4())}"}


def test_the_app_answers_429_in_french_with_retry_after_above_the_limit(app_client):
    headers = _bearer()
    assert [app_client.get("/api/me", headers=headers).status_code for _ in range(3)] == [503] * 3
    refused = app_client.get("/api/me", headers=headers)
    assert refused.status_code == 429
    assert int(refused.headers["Retry-After"]) >= 1
    assert "Trop de requêtes" in refused.json()["detail"]


def test_writes_have_their_own_lower_limit_and_each_user_their_own(app_client):
    headers = _bearer()
    org = uuid.uuid4()
    assert [app_client.post(f"/api/orgs/{org}/brands/{org}/jobs/crawl", headers=headers).status_code for _ in range(2)] == [503, 503]
    assert app_client.post(f"/api/orgs/{org}/brands/{org}/jobs/crawl", headers=headers).status_code == 429
    assert app_client.get("/api/me", headers=headers).status_code == 503  # reads are another bucket
    assert app_client.get("/api/me", headers=_bearer()).status_code == 503  # another person


def test_health_checks_the_login_page_and_unsigned_requests_are_never_limited(app_client):
    for path in ("/api/healthz", "/api/workerz", "/api/auth/config", "/"):
        assert 429 not in {app_client.get(path).status_code for _ in range(20)}, path
    forged = {"Authorization": "Bearer not-a-token"}
    assert {app_client.get("/api/me", headers=forged).status_code for _ in range(10)} == {401}


def test_a_signed_in_monitor_is_not_limited_on_the_health_checks(app_client):
    headers = _bearer()
    assert 429 not in {app_client.get("/api/healthz", headers=headers).status_code for _ in range(20)}


def test_a_forwarded_header_cannot_pick_another_bucket(app_client):
    headers = _bearer()
    for n in range(3):
        app_client.get("/api/me", headers={**headers, "X-Forwarded-For": f"10.0.0.{n}"})
    assert app_client.get("/api/me", headers={**headers, "X-Forwarded-For": "10.9.9.9"}).status_code == 429


def test_refusals_are_reported_once_a_minute_with_their_number():
    limiter, clock = _limiter(read=1)
    assert limiter.refused("a", "read") == 1  # the first one is reported at once
    assert [limiter.refused("a", "read") for _ in range(499)] == [0] * 499
    assert limiter.refused("b", "read") == 1  # another person has their own count
    clock.now += 60
    assert limiter.refused("a", "read") == 500  # 499 counted in silence + this one
    assert limiter.refused("a", "read") == 0


def test_forgotten_refusal_counts_are_purged_and_capped():
    limiter, clock = _limiter(read=1, max_keys=3)
    for who in "abcd":
        limiter.refused(who, "read")
    assert len(limiter._refusals) == 3
    clock.now += 120
    limiter.wait("z", "read")  # a purge runs inside wait
    assert len(limiter._refusals) == 0


def test_the_app_logs_one_warning_a_minute_for_a_person_over_the_limit(app_client, caplog):
    headers, org = _bearer(), uuid.uuid4()
    with caplog.at_level(logging.WARNING, logger="nyra.api"):
        for _ in range(503):
            app_client.get(f"/api/orgs/{org}/members", headers=headers)
    messages = [r.getMessage() for r in caplog.records if "rate limit" in r.getMessage()]
    assert len(messages) == 1
    assert f"in organization {org}" in messages[0] and "read" in messages[0]
    assert headers["Authorization"].split()[-1] not in messages[0]


def test_a_path_that_is_not_an_organization_id_stays_out_of_the_log(app_client, caplog):
    headers = _bearer()
    with caplog.at_level(logging.WARNING, logger="nyra.api"):
        for _ in range(5):
            app_client.get("/api/orgs/not-an-id-forged/members", headers=headers)
    messages = [r.getMessage() for r in caplog.records if "rate limit" in r.getMessage()]
    assert len(messages) == 1 and "organization" not in messages[0] and "forged" not in messages[0]
