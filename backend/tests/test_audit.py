"""The audit log: who did what. The main question it answers ("who ignored this image?"), that every route
changing something writes a row (a new route that forgets fails here), that a failing trace cancels the action,
what it refuses to keep, who may read it, and the retention."""

# ruff: noqa: F811 (the `org_with_users` fixture of test_cloud_api is imported, then used by name)
from __future__ import annotations

import ast
import importlib
import inspect
import pkgutil
import textwrap
import uuid

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("psycopg")

from fastapi.routing import APIRoute  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from test_cloud_api import _headers, _upload_bytes, org_with_users  # noqa: E402, F401

import nyra.cloud.routes as routes_package  # noqa: E402
from nyra.cloud import audit as cloud_audit  # noqa: E402
from nyra.cloud import db as cloud_db  # noqa: E402
from nyra.cloud.routes import common as common_routes  # noqa: E402
from nyra.cloud.routes import library as library_routes  # noqa: E402
from nyra.cloud.routes import site_images as site_images_routes  # noqa: E402


def _seed_matched_images(conn, org_id, brand_id):
    """A library visual, two site images that look like it, and their matches."""
    logo = "ffff0000ffff0000"
    ref = cloud_db.upsert_reference_image(conn, org_id=org_id, brand_id=brand_id, filename="logo.png",
                                          storage_path=f"{org_id}/logo.png", expiry_date=None, credit=None,
                                          notes=None, phash=logo, dhash=logo)
    site = cloud_db.create_site(conn, org_id=org_id, brand_id=brand_id, url="https://t.test/")
    images = [
        conn.execute("INSERT INTO site_images (org_id, site_id, url, phash, dhash) VALUES (%s, %s, %s, %s, %s) RETURNING id",
                     (org_id, site, url, phash, phash)).fetchone()["id"]
        for url, phash in (("https://t.test/a.png", logo), ("https://t.test/b.png", "ffff0000ffff0001"))
    ]
    cloud_db.write_matches(conn, org_id, [(ref, image, "phash", 1.0, "haut") for image in images])
    return ref, images


@pytest.fixture()
def second_admin(cloud_database_url, org_with_users):
    """An administrator of the organization who is not on the Nyra team."""
    org_id = org_with_users[0]
    user_id = uuid.uuid4()
    with cloud_db.connect(cloud_database_url) as conn:
        conn.execute("INSERT INTO auth.users (id, email) VALUES (%s, %s)", (user_id, "claire@client.test"))
        cloud_db.add_membership(conn, user_id=user_id, org_id=org_id, role="admin")
    return user_id


def test_who_ignored_this_image(client, org_with_users, second_admin, cloud_brand, cloud_database_url):
    org_id, base, admin_id, client_id, outsider_id = org_with_users
    with cloud_db.connect(cloud_database_url) as conn:
        _, images = _seed_matched_images(conn, org_id, cloud_brand)
    ignored = client.post(f"{base}/exclusions", headers=_headers(second_admin), json={"site_image_id": str(images[0]), "reason": "Logo"})
    assert ignored.status_code == 200
    client.post(f"{base}/exclusions", headers=_headers(admin_id), json={"site_image_id": str(images[1])})

    # The administrator of the organization reads the log and finds the author and the date.
    url = f"/api/orgs/{org_id}/audit"
    events = client.get(url, headers=_headers(second_admin), params={"action": "exclusion.add"}).json()["events"]
    mine = [event for event in events if event["object_id"] == str(images[0])]
    assert len(mine) == 1
    assert mine[0]["email"] == "claire@client.test" and mine[0]["user_id"] == str(second_admin)
    assert mine[0]["at"].startswith(__import__("datetime").date.today().isoformat())
    assert mine[0]["brand_id"] == str(cloud_brand) and mine[0]["detail"]["exclusion"] == ignored.json()["id"]
    assert "Logo" not in str(mine[0])  # the reason typed by the person is content: not kept
    # ...and can filter by author.
    by_author = client.get(url, headers=_headers(second_admin), params={"user_id": str(second_admin)}).json()["events"]
    assert {event["email"] for event in by_author} == {"claire@client.test"}

    # Members who are not administrators, and strangers, read nothing; the Nyra team reads it from the back office.
    assert client.get(url, headers=_headers(client_id)).status_code == 403
    assert client.get(url, headers=_headers(outsider_id)).status_code == 403
    assert client.get(url).status_code == 401
    assert client.get(f"/api/staff/organizations/{org_id}/audit", headers=_headers(client_id)).status_code == 403
    staff_view = client.get(f"/api/staff/organizations/{org_id}/audit", headers=_headers(admin_id)).json()["events"]
    assert {event["email"] for event in staff_view} >= {"claire@client.test", f"{admin_id}@x.com"}
    assert client.get(f"/api/staff/organizations/{uuid.uuid4()}/audit", headers=_headers(admin_id)).status_code == 404


def test_the_log_is_bounded_filtered_and_kept_apart_per_organization(client, org_with_users, cloud_database_url, cloud_brand):
    org_id, base, admin_id, _, _ = org_with_users
    with cloud_db.connect(cloud_database_url) as conn:
        other_org = cloud_db.create_organization(conn, name="Other", slug=f"other-{uuid.uuid4().hex[:8]}")
        cloud_audit.record(conn, other_org, admin_id, "library.delete", "reference")
        for _ in range(5):
            cloud_audit.record(conn, org_id, admin_id, "library.delete", "reference", brand_id=cloud_brand, count=1)
    url = f"/api/orgs/{org_id}/audit"
    page = client.get(url, headers=_headers(admin_id), params={"limit": 2}).json()
    assert len(page["events"]) == 2 and page["has_more"] is True
    assert client.get(url, headers=_headers(admin_id), params={"limit": 201}).status_code == 422
    assert client.get(url, headers=_headers(admin_id), params={"offset": 50_001}).status_code == 422
    everything = client.get(url, headers=_headers(admin_id), params={"limit": 200}).json()["events"]
    assert len(everything) == 5 and {event["brand"] for event in everything} == {"Test Org"}  # nothing of the other organization
    future = client.get(url, headers=_headers(admin_id), params={"since": "2999-01-01T00:00:00"}).json()
    assert future == {"events": [], "has_more": False}
    assert client.get(url, headers=_headers(admin_id), params={"brand_id": str(uuid.uuid4())}).json()["events"] == []


def test_a_trace_that_cannot_be_written_cancels_the_action_and_says_so(client, org_with_users, cloud_database_url, cloud_brand,
                                                                      monkeypatch):
    org_id, base, admin_id, _, _ = org_with_users
    with cloud_db.connect(cloud_database_url) as conn:
        _, images = _seed_matched_images(conn, org_id, cloud_brand)

    def broken(*args, **kwargs):
        raise RuntimeError("audit table unavailable")

    monkeypatch.setattr(cloud_audit, "record", broken)
    loud = TestClient(client.app, raise_server_exceptions=False)
    answer = loud.post(f"{base}/exclusions", headers=_headers(admin_id), json={"site_image_id": str(images[0])})
    assert answer.status_code == 500 and "Erreur interne" in answer.json()["detail"]
    with cloud_db.connect(cloud_database_url) as conn:
        assert conn.execute("SELECT count(*) AS n FROM excluded_hashes WHERE brand_id = %s", (cloud_brand,)).fetchone()["n"] == 0
        assert conn.execute("SELECT count(*) AS n FROM matches WHERE org_id = %s", (org_id,)).fetchone()["n"] == 2  # not purged


def test_the_log_keeps_identifiers_and_counters_never_content(cloud_database_url, cloud_org):
    with cloud_db.connect(cloud_database_url) as conn:
        cloud_audit.record(conn, cloud_org, None, "x.y", "thing", uuid.uuid4(), count=3, flag=True, ref=uuid.uuid4())
        for bad in ({"filename": "x" * 81}, {"names": ["a.jpg"]}, {"body": {"a": 1}}):
            with pytest.raises(ValueError):
                cloud_audit.record(conn, cloud_org, None, "x.y", "thing", **bad)


def test_purge_removes_old_rows_in_bounded_batches(cloud_database_url, cloud_org, monkeypatch):
    with cloud_db.connect(cloud_database_url) as conn:
        for _ in range(5):
            cloud_audit.record(conn, cloud_org, None, "x.y", "thing")
        conn.execute("UPDATE audit_log SET created_at = now() - interval '13 months' WHERE org_id = %s AND id %% 5 <> 0", (cloud_org,))
    monkeypatch.setattr(cloud_audit, "PURGE_BATCH", 2)  # 4 old rows: two full batches, then an empty one
    assert cloud_audit.purge(cloud_database_url) == 4
    with cloud_db.connect(cloud_database_url) as conn:
        assert conn.execute("SELECT count(*) AS n FROM audit_log WHERE org_id = %s", (cloud_org,)).fetchone()["n"] == 1
    assert cloud_audit.purge(cloud_database_url) == 0


# Routes that change nothing of an organization's data.
EXEMPT = {
    ("POST", "/api/orgs/{org_id}/brands/{brand_id}/library/thumbs"): "reads: signs the thumbnails of a list of files",
}
# Helpers that audit for the route that calls them; each is checked like a route below.
HELPERS = {
    "enqueue": common_routes.enqueue,
    "_delete_filenames": library_routes._delete_filenames,
    "_store_upload": library_routes._store_upload,
    "_adopt_one": site_images_routes._adopt_one,
}


def _audits_on_its_connection(function) -> bool:
    """True if the function calls `.audit(conn, …)` / `cloud_audit.record(conn, …)` inside a `with … as conn` block, or
    calls one of HELPERS (a name: `cloud_jobs.enqueue(…)`, the low-level one that does not audit, does not count).
    Read from the syntax tree, so a comment or a string cannot pass for a call."""
    tree = ast.parse(textwrap.dedent(function if isinstance(function, str) else inspect.getsource(function)))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in HELPERS:
            return True
        if isinstance(node, ast.With):
            bound = {item.optional_vars.id for item in node.items if isinstance(item.optional_vars, ast.Name)}
            for call in (n for n in ast.walk(node) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)):
                audit = call.func.attr == "audit" or (call.func.attr == "record" and getattr(call.func.value, "id", "") == "cloud_audit")
                if audit and call.args and isinstance(call.args[0], ast.Name) and call.args[0].id in bound:
                    return True
    return False


def _changing_routes():
    for module in pkgutil.iter_modules(routes_package.__path__):
        router = getattr(importlib.import_module(f"{routes_package.__name__}.{module.name}"), "router", None)
        for route in getattr(router, "routes", []):
            if isinstance(route, APIRoute):
                for method in route.methods & {"POST", "PUT", "PATCH", "DELETE"}:
                    yield (method, route.path), route


def test_every_route_that_changes_something_writes_an_audit_row():
    seen = dict(_changing_routes())
    assert len(seen) > 40  # the enumeration works (47 at the time of writing)
    unaudited = {key for key, route in seen.items() if key not in EXEMPT and not _audits_on_its_connection(route.endpoint)}
    assert not unaudited, f"routes that change data without `scope.audit(conn, …)` / `cloud_audit.record(conn, …)`: {sorted(unaudited)}"
    assert not set(EXEMPT) - set(seen), "an exempted route no longer exists: remove it from EXEMPT"
    assert not {key for key in EXEMPT if _audits_on_its_connection(seen[key].endpoint)}, "exempted but audited: remove it"
    assert all(_audits_on_its_connection(helper) for helper in HELPERS.values()), "a helper in HELPERS must audit on its own connection"


def test_the_checker_is_not_fooled():
    commented = "def f(conn):  # scope.audit(conn, 'x', 'y')\n    cloud_jobs.enqueue(conn)\n"
    other_connection = "def f(scope):\n    with open_conn() as conn:\n        scope.audit(unrelated, 'x.y', 'z')\n"
    proper = "def f(scope):\n    with open_conn() as conn:\n        scope.audit(conn, 'x.y', 'z')\n"
    assert not _audits_on_its_connection(commented) and not _audits_on_its_connection(other_connection)
    assert _audits_on_its_connection(proper)


def test_a_day_of_changes_leaves_one_row_each(client, org_with_users, second_admin, cloud_brand, cloud_database_url, monkeypatch):
    """The routes, played: each action of the list leaves its row (the static test above says none was forgotten)."""
    from nyra import netguard

    monkeypatch.setattr(netguard, "check_url", lambda url: None)
    org_id, base, admin_id, client_id, outsider_id = org_with_users
    monkeypatch.setattr("nyra.cloud.routes.members.find_or_invite_user", lambda *args: (str(outsider_id), False))
    as_admin, as_client = _headers(second_admin), _headers(client_id)
    with cloud_db.connect(cloud_database_url) as conn:
        ref, images = _seed_matched_images(conn, org_id, cloud_brand)
    jpg = lambda: [("files", ("a.jpg", _upload_bytes(), "image/jpeg"))]  # noqa: E731
    steps = [
        client.post(f"{base}/library/upload", headers=as_admin, files=jpg()),
        client.put(f"{base}/library/a.jpg", headers=as_admin, json={"credit": "secret credit", "tags": ["x"]}),
        client.post(f"{base}/library/expiry", headers=as_admin, json={"filenames": ["a.jpg"], "unlimited_rights": True}),
        client.post(f"{base}/library/tags", headers=as_admin, json={"filenames": ["a.jpg"], "add": ["y"]}),
        client.post(f"{base}/library/credit", headers=as_admin, json={"filenames": ["a.jpg"], "credit": "Studio"}),
        client.post(f"{base}/library/import-csv?apply=true", headers=as_admin, files={"file": ("r.csv", b"filename,credit\na.jpg,me\n")}),
        client.post(f"{base}/collections", headers=as_admin, json={"name": "Été"}),
        client.post(f"{base}/sites", headers=as_admin, json={"url": "https://other.test/"}),
        client.put(f"{base}/logos/setting", headers=as_admin, json={"enabled": True}),
        client.put(f"{base}/packshots/setting", headers=as_admin, json={"enabled": True}),
        client.post(f"{base}/reviews", headers=as_client, json={"reference_id": str(ref), "site_image_ids": [str(images[0])], "decision": "ecarte"}),
        client.put(f"/api/orgs/{org_id}/settings", headers=as_admin, json={"overrides": {"match": {"phash_threshold": 5}}}),
        client.post(f"{base}/jobs/index", headers=as_admin),
        client.post(f"/api/orgs/{org_id}/members", headers=as_admin, json={"email": "new@client.test"}),
        client.post(f"{base}/library/delete", headers=as_admin, json={"filenames": ["a.jpg"]}),
    ]
    assert [step.status_code for step in steps] == [200] * len(steps), [step.text for step in steps]
    listed = client.get(f"/api/orgs/{org_id}/audit", headers=as_admin, params={"limit": 200}).json()["events"]
    actions = {event["action"] for event in listed}
    assert actions >= {"library.upload", "library.update", "library.expiry", "library.credit", "library.tags", "library.import_csv", "collection.create",
                       "site.add", "logos.setting", "packshots.setting", "review.set", "settings.update", "job.index", "member.invite", "library.delete"}
    review = next(event for event in listed if event["action"] == "review.set")
    assert review["email"] == f"{client_id}@x.com" and review["detail"]["decision"] == "ecarte"  # a plain member's decision is traced too
    assert "secret credit" not in str(listed) and "a.jpg" not in str(listed)  # no content, no file names


def test_a_failing_trace_on_an_upload_refuses_that_file_and_keeps_no_reference(client, org_with_users, cloud_database_url, monkeypatch):
    org_id, base, admin_id, _, _ = org_with_users

    def broken(*args, **kwargs):
        raise RuntimeError("audit table unavailable")

    monkeypatch.setattr(cloud_audit, "record", broken)
    answer = client.post(f"{base}/library/upload", headers=_headers(admin_id), files=[("files", ("a.jpg", _upload_bytes(), "image/jpeg"))])
    body = answer.json()
    assert answer.status_code == 200 and body["saved"] == [] and body["failed"][0]["filename"] == "a.jpg"  # told, not silent
    with cloud_db.connect(cloud_database_url) as conn:
        assert conn.execute("SELECT count(*) AS n FROM reference_images WHERE org_id = %s", (org_id,)).fetchone()["n"] == 0


def test_a_full_page_still_says_there_is_a_next_one(client, org_with_users, cloud_database_url):
    org_id, _, admin_id, _, _ = org_with_users
    with cloud_db.connect(cloud_database_url) as conn:
        conn.execute("INSERT INTO audit_log (org_id, action, object_type) SELECT %s, 'x.y', 'thing' FROM generate_series(1, 201)", (org_id,))
    page = client.get(f"/api/orgs/{org_id}/audit", headers=_headers(admin_id), params={"limit": 200}).json()
    assert len(page["events"]) == 200 and page["has_more"] is True
    assert client.get(f"/api/orgs/{org_id}/audit", headers=_headers(admin_id), params={"limit": 200, "offset": 200}).json()["has_more"] is False


def test_the_team_s_own_privileges_are_traced_without_an_organization(client, org_with_users, cloud_database_url, monkeypatch):
    org_id, _, admin_id, _, outsider_id = org_with_users
    monkeypatch.setattr("nyra.cloud.routes.staff.find_or_invite_user", lambda *args: (str(outsider_id), False))
    assert client.post("/api/staff/admins", headers=_headers(admin_id), json={"email": "o@x.com"}).status_code == 200
    assert client.delete(f"/api/staff/admins/{outsider_id}", headers=_headers(admin_id)).status_code == 200
    with cloud_db.connect(cloud_database_url) as conn:
        rows = conn.execute("SELECT action, user_id, object_id FROM audit_log WHERE org_id IS NULL AND object_id = %s ORDER BY id", (str(outsider_id),)).fetchall()
        conn.execute("DELETE FROM audit_log WHERE org_id IS NULL")
    assert [(row["action"], row["user_id"]) for row in rows] == [("staff.add", admin_id), ("staff.remove", admin_id)]


def test_only_one_purge_runs_at_a_time_and_each_batch_reports_alive(cloud_database_url, cloud_org):
    import psycopg

    with cloud_db.connect(cloud_database_url) as conn:
        for _ in range(3):
            cloud_audit.record(conn, cloud_org, None, "x.y", "thing")
        conn.execute("UPDATE audit_log SET created_at = now() - interval '13 months' WHERE org_id = %s", (cloud_org,))
    with psycopg.connect(cloud_database_url, autocommit=True) as other:
        other.execute("SELECT pg_advisory_lock(%s)", (cloud_audit.PURGE_LOCK,))
        assert cloud_audit.purge(cloud_database_url) == 0  # another purge holds the lock
        other.execute("SELECT pg_advisory_unlock(%s)", (cloud_audit.PURGE_LOCK,))
    beats = []
    assert cloud_audit.purge(cloud_database_url, between=lambda: beats.append(1)) == 3 and beats


def test_codes_and_identifiers_are_checked(cloud_database_url, cloud_org):
    with cloud_db.connect(cloud_database_url) as conn:
        for bad in ({"action": "Library delete"}, {"action": "a@b.c"}, {"object_type": "x" * 41 + " "}, {"object_id": "i" * 81}):
            args = {"action": "x.y", "object_type": "thing", "object_id": None} | bad
            with pytest.raises(ValueError):
                cloud_audit.record(conn, cloud_org, None, args["action"], args["object_type"], args["object_id"])
