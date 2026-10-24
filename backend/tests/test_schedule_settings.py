"""The setting of the monthly read of a brand: who reads and who changes it, what it validates, what it traces, and that
it agrees with `schedule.enable`. Cloud tests: they skip unless TEST_DATABASE_URL is set (see conftest.py)."""

# ruff: noqa: F811 (the `org_with_users` fixture of test_cloud_api is imported, then used by name)
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from psycopg.types.json import Jsonb

pytest.importorskip("fastapi")
pytest.importorskip("psycopg")

from test_cloud_api import _headers, org_with_users  # noqa: E402, F401

from nyra.cloud import db as cloud_db  # noqa: E402
from nyra.cloud import schedule  # noqa: E402


def _audit_rows(url, org_id, action):
    with cloud_db.connect(url) as conn:
        return conn.execute("SELECT user_id, brand_id, object_id, detail FROM audit_log WHERE org_id = %s AND action = %s",
                            (org_id, action)).fetchall()


def _marked(url, brand_id):
    """The `last_run_at` of the schedule row: the rule of the due date, not a read (see `schedule.enable`)."""
    with cloud_db.connect(url) as conn:
        return conn.execute("SELECT last_run_at FROM brand_schedules WHERE brand_id = %s", (brand_id,)).fetchone()["last_run_at"]


def _job(url, org_id, brand_id, status, scheduled, finished):
    with cloud_db.connect(url) as conn:
        conn.execute("INSERT INTO jobs (org_id, brand_id, kind, status, params, finished_at) VALUES (%s, %s, 'crawl', %s, %s, %s)",
                     (org_id, brand_id, status, Jsonb({"scheduled": True} if scheduled else {}), finished))


def test_last_read_is_the_last_finished_scheduled_read_and_nothing_else(client, org_with_users, cloud_brand, cloud_database_url):
    org_id, base, admin_id, client_id, _ = org_with_users
    headers = _headers(admin_id)
    day = lambda d: datetime(2026, 9, d, 3, 15, tzinfo=timezone.utc)  # noqa: E731
    client.put(f"{base}/schedule", headers=headers, json={"enabled": True, "day_of_month": 1})
    with cloud_db.connect(cloud_database_url) as conn:  # enabled after the date: marked, never read
        conn.execute("UPDATE brand_schedules SET last_run_at = now() WHERE brand_id = %s", (cloud_brand,))
    assert client.get(f"{base}/schedule", headers=headers).json()["last_read_at"] is None
    _job(cloud_database_url, org_id, cloud_brand, "done", False, day(20))  # by hand: does not count
    _job(cloud_database_url, org_id, cloud_brand, "error", True, day(21))
    _job(cloud_database_url, org_id, cloud_brand, "cancelled", True, day(22))
    _job(cloud_database_url, org_id, cloud_brand, "queued", True, None)
    assert client.get(f"{base}/schedule", headers=headers).json()["last_read_at"] is None
    _job(cloud_database_url, org_id, cloud_brand, "done", True, day(3))
    _job(cloud_database_url, org_id, cloud_brand, "done", True, day(5))
    got = client.get(f"{base}/schedule", headers=_headers(client_id)).json()
    assert datetime.fromisoformat(got["last_read_at"]) == day(5)
    assert "last_run_at" not in got
    # Another organization's scheduled read is never shown here.
    with cloud_db.connect(cloud_database_url) as conn:
        other = cloud_db.create_organization(conn, name="Other", slug=f"other-{uuid.uuid4().hex[:8]}")
        other_brand = conn.execute("SELECT id FROM brands WHERE org_id = %s", (other,)).fetchone()["id"]
    _job(cloud_database_url, other, other_brand, "done", True, day(28))
    assert datetime.fromisoformat(client.get(f"{base}/schedule", headers=headers).json()["last_read_at"]) == day(5)


def test_a_brand_without_a_row_reads_as_off_with_the_default_day_for_every_member(client, org_with_users, cloud_brand):
    org_id, base, admin_id, client_id, outsider_id = org_with_users
    for user in (admin_id, client_id):
        answer = client.get(f"{base}/schedule", headers=_headers(user))
        assert answer.status_code == 200
        assert answer.json() == {"enabled": False, "day_of_month": schedule.default_day(cloud_brand),
                                 "default_day": schedule.default_day(cloud_brand), "next_run_at": None, "last_read_at": None}
    assert client.get(f"{base}/schedule", headers=_headers(outsider_id)).status_code == 403
    assert client.get(f"{base}/schedule").status_code == 401
    assert client.get(f"/api/orgs/{org_id}/brands/{uuid.uuid4()}/schedule", headers=_headers(admin_id)).status_code == 404


def test_only_an_administrator_changes_it_and_it_is_traced_in_the_same_transaction(client, org_with_users, cloud_brand,
                                                                                  cloud_database_url):
    org_id, base, admin_id, client_id, outsider_id = org_with_users
    body = {"enabled": True, "day_of_month": 12}
    assert client.put(f"{base}/schedule", headers=_headers(client_id), json=body).status_code == 403
    assert client.put(f"{base}/schedule", headers=_headers(outsider_id), json=body).status_code == 403
    assert client.put(f"{base}/schedule", json=body).status_code == 401
    assert client.get(f"{base}/schedule", headers=_headers(admin_id)).json()["enabled"] is False  # nothing changed

    on = client.put(f"{base}/schedule", headers=_headers(admin_id), json=body)
    assert on.status_code == 200
    assert on.json() == client.get(f"{base}/schedule", headers=_headers(client_id)).json()  # the same object, seen by a member
    assert on.json()["enabled"] is True and on.json()["day_of_month"] == 12
    if _marked(cloud_database_url, cloud_brand) is None:  # the date of this month has not passed: it is this month's
        assert on.json()["next_run_at"] == schedule.next_run(cloud_brand, 12, None, datetime.now(timezone.utc)).isoformat()
    assert datetime.fromisoformat(on.json()["next_run_at"]).tzinfo is not None

    off = client.put(f"{base}/schedule", headers=_headers(admin_id), json={"enabled": False, "day_of_month": 12})
    assert off.status_code == 200 and off.json()["enabled"] is False and off.json()["next_run_at"] is None
    assert off.json()["day_of_month"] == 12  # the chosen day is kept

    [enabled] = _audit_rows(cloud_database_url, org_id, "schedule.enable")
    [disabled] = _audit_rows(cloud_database_url, org_id, "schedule.disable")
    assert enabled["user_id"] == admin_id and enabled["brand_id"] == cloud_brand and enabled["object_id"] == str(cloud_brand)
    assert enabled["detail"] == {"day_of_month": 12} and disabled["detail"] == {}


def test_the_body_is_validated_with_a_sentence_and_nothing_is_written(client, org_with_users, cloud_database_url):
    org_id, base, admin_id, _, _ = org_with_users
    headers = _headers(admin_id)
    for day in (0, 29, -3, 400):
        answer = client.put(f"{base}/schedule", headers=headers, json={"enabled": True, "day_of_month": day})
        assert answer.status_code == 400 and answer.json()["detail"] == "Le jour du mois doit être compris entre 1 et 28."
    for bad in ({"enabled": True, "day_of_month": "5"}, {"enabled": True, "day_of_month": 5.5}, {"enabled": True, "day_of_month": True},
                {"enabled": "yes", "day_of_month": 5}, {"day_of_month": 5}, {"enabled": False, "day_of_month": "5"},
                {"enabled": True, "day_of_month": 5, "org_id": str(uuid.uuid4())}):
        assert client.put(f"{base}/schedule", headers=headers, json=bad).status_code == 422, bad
    for incomplete in ({"enabled": True}, {"enabled": True, "day_of_month": None}):  # a day is needed to switch on
        answer = client.put(f"{base}/schedule", headers=headers, json=incomplete)
        assert answer.status_code == 400 and answer.json()["detail"] == "Le jour du mois doit être compris entre 1 et 28."
    assert client.put(f"{base}/schedule", headers=headers, json={"enabled": False, "day_of_month": 29}).status_code == 400
    for edge in (1, 28):
        assert client.put(f"{base}/schedule", headers=headers, json={"enabled": True, "day_of_month": edge}).status_code == 200
    off = client.put(f"{base}/schedule", headers=headers, json={"enabled": False})  # no day needed to switch off
    assert off.status_code == 200 and off.json()["enabled"] is False and off.json()["day_of_month"] == 28
    with cloud_db.connect(cloud_database_url) as conn:
        assert conn.execute("SELECT count(*) AS n FROM audit_log WHERE org_id = %s AND action LIKE 'schedule.%%'",
                            (org_id,)).fetchone()["n"] == 3  # only the valid calls that changed something


def test_a_put_that_changes_nothing_writes_no_trace(client, org_with_users, cloud_brand, cloud_database_url):
    org_id, base, admin_id, _, _ = org_with_users
    headers = _headers(admin_id)
    put = lambda **body: client.put(f"{base}/schedule", headers=headers, json=body)  # noqa: E731
    count = lambda action: len(_audit_rows(cloud_database_url, org_id, action))  # noqa: E731
    first = put(enabled=False)  # a brand with no row, switched off: nothing to do
    assert first.status_code == 200 and first.json()["enabled"] is False and count("schedule.disable") == 0
    with cloud_db.connect(cloud_database_url) as conn:
        assert conn.execute("SELECT count(*) AS n FROM brand_schedules WHERE brand_id = %s", (cloud_brand,)).fetchone()["n"] == 0
    on = put(enabled=True, day_of_month=9)
    assert on.status_code == 200 and put(enabled=True, day_of_month=9).json() == on.json() and count("schedule.enable") == 1
    assert put(enabled=True, day_of_month=15).json()["day_of_month"] == 15  # only the day changes: one more trace, the new day
    assert [row["detail"] for row in _audit_rows(cloud_database_url, org_id, "schedule.enable")].count({"day_of_month": 15}) == 1
    assert count("schedule.enable") == 2
    off = put(enabled=False)
    assert off.status_code == 200 and off.json()["day_of_month"] == 15 and put(enabled=False).json() == off.json()
    assert put(enabled=False, day_of_month=3).json() == off.json()  # a day sent with the switch off changes nothing
    assert count("schedule.disable") == 1
    assert put(enabled=True, day_of_month=15).status_code == 200 and count("schedule.enable") == 3  # on again: a change


def test_putting_twice_is_the_same_and_it_agrees_with_enable(client, org_with_users, cloud_brand, cloud_database_url):
    org_id, base, admin_id, _, _ = org_with_users
    body = {"enabled": True, "day_of_month": 1}
    first = client.put(f"{base}/schedule", headers=_headers(admin_id), json=body).json()
    second = client.put(f"{base}/schedule", headers=_headers(admin_id), json=body).json()
    assert first == second
    now = datetime.now(timezone.utc)
    passed = schedule.due_date(cloud_brand, 1, now.year, now.month) <= now
    # A brand never read whose date of the month has passed is marked as read: its first read is next month's.
    assert (_marked(cloud_database_url, cloud_brand) is not None) is passed
    assert first["last_read_at"] is None  # the mark is not a read
    month_ahead = datetime.fromisoformat(first["next_run_at"])
    if passed:
        assert (month_ahead.year, month_ahead.month) > (now.year, now.month)
    with cloud_db.connect(cloud_database_url) as conn:
        assert conn.execute("SELECT count(*) AS n FROM brand_schedules WHERE brand_id = %s", (cloud_brand,)).fetchone()["n"] == 1
    # A brand read a while ago keeps its last read when the day changes; the date shown follows the ordinary rule.
    with cloud_db.connect(cloud_database_url) as conn:
        conn.execute("UPDATE brand_schedules SET last_run_at = %s WHERE brand_id = %s", (datetime(2020, 1, 5, tzinfo=timezone.utc), cloud_brand))
    late = client.put(f"{base}/schedule", headers=_headers(admin_id), json={"enabled": True, "day_of_month": 20}).json()
    assert _marked(cloud_database_url, cloud_brand).year == 2020 and late["last_read_at"] is None
    assert datetime.fromisoformat(late["next_run_at"]) == schedule.due_date(cloud_brand, 20, now.year, now.month)
