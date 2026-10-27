"""The e-mail notifications of a member (AXE-523): what a person chooses for themselves, what the Nyra team allows, who may
change which, what is traced, and that the public API of Supabase cannot unlock what the team blocked. Cloud tests: they skip
unless TEST_DATABASE_URL is set (see conftest.py)."""

# ruff: noqa: F811 (the `org_with_users` and `second_admin` fixtures are imported, then used by name)
from __future__ import annotations

import json
import uuid

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("psycopg")

import psycopg  # noqa: E402
from conftest import MIGRATIONS_DIR  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from test_audit import second_admin  # noqa: E402, F401
from test_cloud_api import _headers, org_with_users  # noqa: E402, F401

from nyra.cloud import audit as cloud_audit  # noqa: E402
from nyra.cloud import db as cloud_db  # noqa: E402

DEFAULT = {"monthly_email": {"wanted": True, "allowed": True, "eligible": True}}


def mine(org_id):
    return f"/api/orgs/{org_id}/me/notifications"


def theirs(org_id, user_id):
    return f"/api/staff/organizations/{org_id}/members/{user_id}/notifications"


def flags(url, org_id, user_id):
    with cloud_db.connect(url) as conn:
        row = conn.execute("SELECT monthly_email_allowed AS allowed, monthly_email_wanted AS wanted FROM memberships "
                           "WHERE org_id = %s AND user_id = %s", (org_id, user_id)).fetchone()
    return None if row is None else (row["allowed"], row["wanted"])


def audit_rows(url, org_id, action):
    with cloud_db.connect(url) as conn:
        return conn.execute("SELECT user_id, object_id, detail FROM audit_log WHERE org_id = %s AND action = %s ORDER BY id",
                            (org_id, action)).fetchall()


def test_everybody_starts_with_the_e_mail_allowed_and_wanted_and_only_an_administrator_is_eligible(client, org_with_users, cloud_database_url):
    org_id, _, admin_id, client_id, outsider_id = org_with_users
    assert client.get(mine(org_id), headers=_headers(admin_id)).json() == DEFAULT
    assert client.get(mine(org_id), headers=_headers(client_id)).json() == {"monthly_email": {"wanted": True, "allowed": True, "eligible": False}}
    assert flags(cloud_database_url, org_id, admin_id) == (True, True) == flags(cloud_database_url, org_id, client_id)
    assert client.get(mine(org_id), headers=_headers(outsider_id)).status_code == 403
    assert client.get(mine(org_id)).status_code == 401


def test_a_person_changes_only_their_own_line_and_it_is_traced_in_the_same_transaction(client, org_with_users, second_admin, cloud_database_url):
    org_id, _, admin_id, client_id, outsider_id = org_with_users
    assert client.put(mine(org_id), headers=_headers(second_admin), json={"monthly_email": False}).json()["monthly_email"]["wanted"] is False
    assert flags(cloud_database_url, org_id, second_admin) == (True, False)
    assert flags(cloud_database_url, org_id, admin_id) == (True, True) and flags(cloud_database_url, org_id, client_id) == (True, True)
    assert client.get(mine(org_id), headers=_headers(second_admin)).json()["monthly_email"] == {"wanted": False, "allowed": True, "eligible": True}
    [event] = audit_rows(cloud_database_url, org_id, "notifications.update")
    assert event["user_id"] == second_admin and event["object_id"] == str(second_admin) and event["detail"] == {"monthly_email": False}
    assert client.put(mine(org_id), headers=_headers(outsider_id), json={"monthly_email": False}).status_code == 403
    assert client.put(mine(org_id), json={"monthly_email": False}).status_code == 401
    # a client may choose too (the choice is kept; it only takes effect if they become administrator)
    assert client.put(mine(org_id), headers=_headers(client_id), json={"monthly_email": False}).status_code == 200
    assert flags(cloud_database_url, org_id, client_id) == (True, False) and flags(cloud_database_url, org_id, admin_id) == (True, True)


def test_the_same_choice_again_is_a_200_without_a_trace(client, org_with_users, cloud_database_url):
    org_id, _, _, client_id, _ = org_with_users
    for _ in range(2):
        assert client.put(mine(org_id), headers=_headers(client_id), json={"monthly_email": True}).status_code == 200
    assert not audit_rows(cloud_database_url, org_id, "notifications.update")
    for _ in range(2):
        assert client.put(mine(org_id), headers=_headers(client_id), json={"monthly_email": False}).status_code == 200
    assert len(audit_rows(cloud_database_url, org_id, "notifications.update")) == 1
    assert client.put(mine(org_id), headers=_headers(client_id), json={"monthly_email": True}).status_code == 200
    assert [r["detail"] for r in audit_rows(cloud_database_url, org_id, "notifications.update")] == [{"monthly_email": False}, {"monthly_email": True}]


def test_the_body_is_strict_nobody_names_a_member_and_a_person_cannot_unblock_themselves(client, org_with_users, cloud_database_url):
    org_id, _, admin_id, client_id, _ = org_with_users
    headers = _headers(admin_id)
    for bad in ({}, {"monthly_email": "false"}, {"monthly_email": 0}, {"monthly_email": None},
                {"monthly_email": False, "user_id": str(client_id)}, {"monthly_email": False, "monthly_email_allowed": True},
                {"monthly_email_allowed": True}, {"monthly_email": False, "extra": 1}):
        assert client.put(mine(org_id), headers=headers, json=bad).status_code == 422, bad
    assert client.put(f"{mine(org_id)}/{client_id}", headers=headers, json={"monthly_email": False}).status_code in {404, 405}
    assert flags(cloud_database_url, org_id, admin_id) == (True, True) and flags(cloud_database_url, org_id, client_id) == (True, True)
    assert not audit_rows(cloud_database_url, org_id, "notifications.update")


def test_a_failing_trace_cancels_the_change(client, org_with_users, cloud_database_url, monkeypatch):
    org_id, _, _, client_id, _ = org_with_users

    def broken(*args, **kwargs):
        raise RuntimeError("audit table unavailable")

    monkeypatch.setattr(cloud_audit, "record", broken)
    loud = TestClient(client.app, raise_server_exceptions=False)
    assert loud.put(mine(org_id), headers=_headers(client_id), json={"monthly_email": False}).status_code == 500
    assert flags(cloud_database_url, org_id, client_id) == (True, True)


def test_the_choice_is_per_organization(client, org_with_users, cloud_database_url):
    org_id, _, _, client_id, _ = org_with_users
    with cloud_db.connect(cloud_database_url) as conn:
        other = cloud_db.create_organization(conn, name="Other", slug=f"other-{uuid.uuid4().hex[:8]}")
        cloud_db.add_membership(conn, user_id=client_id, org_id=other, role="admin")
    assert client.put(mine(org_id), headers=_headers(client_id), json={"monthly_email": False}).status_code == 200
    assert flags(cloud_database_url, org_id, client_id) == (True, False) and flags(cloud_database_url, other, client_id) == (True, True)
    assert client.get(mine(other), headers=_headers(client_id)).json() == DEFAULT


def test_only_the_nyra_team_changes_what_is_allowed_and_it_is_traced_with_the_person(client, org_with_users, second_admin, cloud_database_url):
    org_id, _, staff_id, client_id, outsider_id = org_with_users   # `staff_id` is a platform admin
    body = {"monthly_email_allowed": False}
    assert client.put(theirs(org_id, second_admin), headers=_headers(second_admin), json=body).status_code == 403   # an administrator of the organization
    assert client.put(theirs(org_id, second_admin), headers=_headers(client_id), json=body).status_code == 403
    assert client.put(theirs(org_id, second_admin), headers=_headers(outsider_id), json=body).status_code == 403
    assert client.put(theirs(org_id, second_admin), json=body).status_code == 401
    assert flags(cloud_database_url, org_id, second_admin) == (True, True) and not audit_rows(cloud_database_url, org_id, "notifications.staff_update")

    assert client.put(theirs(org_id, second_admin), headers=_headers(staff_id), json=body).json() == {"monthly_email_allowed": False}
    assert flags(cloud_database_url, org_id, second_admin) == (False, True)
    assert client.get(mine(org_id), headers=_headers(second_admin)).json()["monthly_email"] == {"wanted": True, "allowed": False, "eligible": True}
    [event] = audit_rows(cloud_database_url, org_id, "notifications.staff_update")
    assert event["user_id"] == staff_id and event["object_id"] == str(second_admin) and event["detail"] == {"monthly_email_allowed": False}
    # the same value again: 200, no second trace; back to allowed: a second trace
    assert client.put(theirs(org_id, second_admin), headers=_headers(staff_id), json=body).status_code == 200
    assert len(audit_rows(cloud_database_url, org_id, "notifications.staff_update")) == 1
    assert client.put(theirs(org_id, second_admin), headers=_headers(staff_id), json={"monthly_email_allowed": True}).status_code == 200
    assert flags(cloud_database_url, org_id, second_admin) == (True, True) and len(audit_rows(cloud_database_url, org_id, "notifications.staff_update")) == 2
    # the person's own choice is not the team's to change, whatever is sent
    for bad in ({}, {"monthly_email_allowed": "no"}, {"monthly_email_allowed": None}, {"monthly_email_wanted": False},
                {"monthly_email_allowed": False, "monthly_email_wanted": False}):
        assert client.put(theirs(org_id, second_admin), headers=_headers(staff_id), json=bad).status_code == 422, bad


def test_the_team_s_route_only_reaches_a_member_of_that_organization(client, org_with_users, cloud_database_url):
    org_id, _, staff_id, client_id, outsider_id = org_with_users
    body = {"monthly_email_allowed": False}
    with cloud_db.connect(cloud_database_url) as conn:
        other = cloud_db.create_organization(conn, name="Other", slug=f"other-{uuid.uuid4().hex[:8]}")
        cloud_db.add_membership(conn, user_id=outsider_id, org_id=other, role="admin")
    assert client.put(theirs(org_id, outsider_id), headers=_headers(staff_id), json=body).status_code == 404   # a member of another organization
    assert client.put(theirs(org_id, uuid.uuid4()), headers=_headers(staff_id), json=body).status_code == 404
    assert client.put(theirs(uuid.uuid4(), client_id), headers=_headers(staff_id), json=body).status_code == 404
    assert flags(cloud_database_url, other, outsider_id) == (True, True) and flags(cloud_database_url, org_id, client_id) == (True, True)
    assert not audit_rows(cloud_database_url, org_id, "notifications.staff_update") and not audit_rows(cloud_database_url, other, "notifications.staff_update")
    # the same person in two organizations: only the one in the path changes
    assert client.put(theirs(other, outsider_id), headers=_headers(staff_id), json=body).status_code == 200
    assert flags(cloud_database_url, other, outsider_id) == (False, True)


def test_the_back_office_list_of_accounts_carries_both_flags(client, org_with_users, cloud_database_url):
    org_id, _, staff_id, client_id, _ = org_with_users
    assert client.put(mine(org_id), headers=_headers(client_id), json={"monthly_email": False}).status_code == 200
    assert client.put(theirs(org_id, client_id), headers=_headers(staff_id), json={"monthly_email_allowed": False}).status_code == 200
    users = {u["user_id"]: u for u in client.get("/api/staff/users", headers=_headers(staff_id)).json()["users"]}
    [membership] = [m for m in users[str(client_id)]["memberships"] if m["org_id"] == str(org_id)]
    assert (membership["monthly_email_allowed"], membership["monthly_email_wanted"]) == (False, False)
    [other] = [m for m in users[str(staff_id)]["memberships"] if m["org_id"] == str(org_id)]
    assert (other["monthly_email_allowed"], other["monthly_email_wanted"]) == (True, True)


# --- the table and its policies ---------------------------------------------------------------------------------------------

@pytest.fixture()
def api_role(cloud_database_url):
    """The role of Supabase's public API, as a test database may not have it: `authenticated` reads and writes under RLS."""
    with cloud_db.connect(cloud_database_url) as conn:
        conn.execute("DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN CREATE ROLE authenticated NOLOGIN; END IF; END $$")
        conn.execute("GRANT USAGE ON SCHEMA public, auth TO authenticated")
        conn.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON public.memberships TO authenticated")
        conn.execute("GRANT SELECT ON auth.users TO authenticated")


def as_user(url, user_id, sql, args=()):
    """Run one statement as the public API would for this signed-in person; return the number of rows it changed."""
    with cloud_db.connect(url) as conn:
        conn.execute("SELECT set_config('request.jwt.claims', %s, true)", (json.dumps({"sub": str(user_id)}),))
        conn.execute("SET LOCAL ROLE authenticated")
        return conn.execute(sql, args).rowcount


def test_the_public_api_lets_a_person_change_their_own_wish_and_nothing_the_team_decides(api_role, org_with_users, second_admin, cloud_database_url):
    org_id, _, admin_id, client_id, _ = org_with_users
    update = "UPDATE public.memberships SET {} WHERE org_id = %s AND user_id = %s"
    assert as_user(cloud_database_url, second_admin, update.format("monthly_email_wanted = false"), (org_id, second_admin)) == 1
    assert flags(cloud_database_url, org_id, second_admin) == (True, False)
    # an administrator of the organization manages memberships by RLS, but not the team's switch, nor another person's wish
    for target, column in ((second_admin, "monthly_email_allowed = false"), (client_id, "monthly_email_allowed = false"),
                           (client_id, "monthly_email_wanted = false")):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            as_user(cloud_database_url, second_admin, update.format(column), (org_id, target))
    assert as_user(cloud_database_url, client_id, update.format("monthly_email_allowed = true"), (org_id, client_id)) == 0   # a client writes nothing (RLS)
    assert flags(cloud_database_url, org_id, second_admin) == (True, False) and flags(cloud_database_url, org_id, client_id) == (True, True)
    # the team's block holds against a person who tries to lift it, and the other columns stay manageable by the administrators
    with cloud_db.connect(cloud_database_url) as conn:
        conn.execute("UPDATE memberships SET monthly_email_allowed = false WHERE org_id = %s AND user_id = %s", (org_id, second_admin))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        as_user(cloud_database_url, second_admin, update.format("monthly_email_allowed = true"), (org_id, second_admin))
    assert as_user(cloud_database_url, second_admin, update.format("role = 'client'"), (org_id, client_id)) == 1
    assert flags(cloud_database_url, org_id, second_admin) == (False, False)
    # a new row made by an administrator through the public API starts with both flags on
    other = uuid.uuid4()
    with cloud_db.connect(cloud_database_url) as conn:
        conn.execute("INSERT INTO auth.users (id, email) VALUES (%s, %s)", (other, f"{other}@x.test"))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        as_user(cloud_database_url, admin_id, "INSERT INTO public.memberships (user_id, org_id, role, monthly_email_wanted) VALUES (%s, %s, 'client', false)", (other, org_id))


def test_the_migration_can_be_applied_twice_and_keeps_the_choices(org_with_users, cloud_database_url):
    org_id, _, _, client_id, _ = org_with_users
    sql = (MIGRATIONS_DIR / "20261025000035_member_notifications.sql").read_text(encoding="utf-8")
    with cloud_db.connect(cloud_database_url) as conn:
        conn.execute("UPDATE memberships SET monthly_email_allowed = false, monthly_email_wanted = false WHERE org_id = %s AND user_id = %s",
                     (org_id, client_id))
        conn.execute(sql)
        conn.execute(sql)
        columns = {r["column_name"]: (r["data_type"], r["is_nullable"], r["column_default"]) for r in conn.execute(
            "SELECT column_name, data_type, is_nullable, column_default FROM information_schema.columns "
            "WHERE table_name = 'memberships' AND column_name LIKE 'monthly_email_%'").fetchall()}
        comments = conn.execute("SELECT col_description('public.memberships'::regclass, a.attnum) AS c FROM pg_attribute a "
                                "WHERE a.attrelid = 'public.memberships'::regclass AND a.attname LIKE 'monthly_email_%'").fetchall()
    assert columns == {"monthly_email_allowed": ("boolean", "NO", "true"), "monthly_email_wanted": ("boolean", "NO", "true")}
    assert len(comments) == 2 and all(r["c"] for r in comments)
    assert flags(cloud_database_url, org_id, client_id) == (False, False)


def test_the_back_office_list_of_accounts_still_works_before_the_migration_with_both_flags_on(client, org_with_users, cloud_database_url):
    """Code deployed before migration 35: the columns are missing, the page of accounts must not fall (value « yes », same contract)."""
    org_id, _, staff_id, client_id, _ = org_with_users
    assert client.put(theirs(org_id, client_id), headers=_headers(staff_id), json={"monthly_email_allowed": False}).status_code == 200
    with cloud_db.connect(cloud_database_url) as conn:
        conn.execute("ALTER TABLE memberships RENAME COLUMN monthly_email_allowed TO monthly_email_allowed_off")
        conn.execute("ALTER TABLE memberships RENAME COLUMN monthly_email_wanted TO monthly_email_wanted_off")
    try:
        answer = client.get("/api/staff/users", headers=_headers(staff_id))
        assert answer.status_code == 200
        users = {u["user_id"]: u for u in answer.json()["users"]}
        [membership] = [m for m in users[str(client_id)]["memberships"] if m["org_id"] == str(org_id)]
        assert (membership["monthly_email_allowed"], membership["monthly_email_wanted"]) == (True, True)
    finally:
        with cloud_db.connect(cloud_database_url) as conn:
            conn.execute("ALTER TABLE memberships RENAME COLUMN monthly_email_allowed_off TO monthly_email_allowed")
            conn.execute("ALTER TABLE memberships RENAME COLUMN monthly_email_wanted_off TO monthly_email_wanted")


def test_the_trigger_function_has_a_pinned_search_path(cloud_database_url):
    with cloud_db.connect(cloud_database_url) as conn:
        config = conn.execute("SELECT proconfig FROM pg_proc WHERE proname = 'guard_member_notifications'").fetchone()["proconfig"]
    assert config and any(item.startswith("search_path=") for item in config)


def test_the_public_api_cannot_delete_a_membership_the_team_blocked_nor_recreate_it(api_role, client, org_with_users, second_admin, cloud_database_url):
    """AXE-578: delete then recreate with the defaults used to lift the Nyra team's block."""
    org_id, _, admin_id, client_id, _ = org_with_users
    with cloud_db.connect(cloud_database_url) as conn:
        # the earlier test that applies migration 35 twice leaves its insert/update-only trigger: start from the current one
        conn.execute((MIGRATIONS_DIR / "20261027000037_member_delete_guard.sql").read_text(encoding="utf-8"))
        conn.execute((MIGRATIONS_DIR / "20261027000037_member_delete_guard.sql").read_text(encoding="utf-8"))  # twice: idempotent
        conn.execute("UPDATE memberships SET monthly_email_allowed = false WHERE org_id = %s AND user_id = %s", (org_id, second_admin))
    delete = "DELETE FROM public.memberships WHERE org_id = %s AND user_id = %s"
    for actor in (admin_id, second_admin):  # a co-administrator, and the blocked person themselves
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            as_user(cloud_database_url, actor, delete, (org_id, second_admin))
    assert flags(cloud_database_url, org_id, second_admin) == (False, True)
    # an unblocked membership is still removed by an administrator through the public API, as before
    assert as_user(cloud_database_url, admin_id, delete, (org_id, client_id)) == 1
    # the backend (service role) still removes a blocked member
    assert client.delete(f"/api/orgs/{org_id}/members/{second_admin}", headers=_headers(admin_id)).status_code == 200
