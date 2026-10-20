"""Isolation between organizations (AXE-534).

The backend uses the Supabase service role, which bypasses RLS: every route and every query must filter on the
organization (or on a brand checked to belong to it) itself. Nothing else catches a forgotten filter, so this test
calls EVERY route of the application as a member of organization A with the identifiers of organization B, and fails

  - on any answer that is not a refusal (403/404) where an identifier of B travels in the URL, or that is a 5xx,
  - on any answer that carries data of B,
  - when the call changed a row of B, or wrote a row of A that points to a row of B,
  - when a route is not covered: a new route must get a case in `BODIES` (if it takes a body or an identifier in its
    query) or an entry in `PUBLIC` / `USER_LEVEL` with the reason it needs no organization check.

Same setup as the other cloud tests (TEST_DATABASE_URL, in-memory Storage).
"""

from __future__ import annotations

import importlib
import json
import pkgutil
import re
import uuid

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("psycopg")

from test_cloud_api import _headers, _upload_bytes, client, labels_on  # noqa: E402, F401

from nyra import report as report_module  # noqa: E402
from nyra.cloud import db as cloud_db  # noqa: E402
from nyra.cloud import jobs as cloud_jobs  # noqa: E402
from nyra.cloud import labels as cloud_labels  # noqa: E402
from nyra.cloud import storage as cloud_storage  # noqa: E402
from nyra.cloud.routes import __path__ as ROUTES_PATH  # noqa: E402

BR = "/api/orgs/{org_id}/brands/{brand_id}"
PHASH = "ffff0000ffff0000"

# Routes with no organization to check, and why. Anything else is tested below.
PUBLIC = {
    ("GET", "/api/healthz"): "health check for the load balancer: answers {status: ok}, reads nothing",
    ("GET", "/api/workerz"): "uptime monitor: a queue age in seconds, no name, no id",
    ("GET", "/api/auth/config"): "public sign-in settings (Supabase URL and anon key), needed before anyone is signed in",
    ("GET", "/"): "the app shell (static HTML), no data",
    ("GET", "/{full_path}"): "the app shell for every client-side route (/robot, /login, ...), static files of the build; /api/* is a 404",
}
# Routes of the signed-in user, with no organization or brand in the URL: they answer from the caller's own memberships.
USER_LEVEL = {
    ("GET", "/api/me"): "the caller's own account",
    ("GET", "/api/orgs"): "the caller's own memberships (checked below: never an organization of B)",
}
# The staff routes (`/api/staff/*`, platform admins only) are refused to every member of A, whatever the role: tested below.
# What a platform admin may do in another organization is by design (see ARCHITECTURE.md, "Security model").

# (method, path) -> function of B's identifiers giving the request's `json` / `data` / `files` / `params`, with the identifiers of B
# in it. None: no identifier of another organization can travel in this request. Needed for every route that takes a body, and
# for every route whose query takes identifiers.
def _upload(b):
    return {"files": [("files", ("canary.jpg", _upload_bytes().getvalue(), "image/jpeg"))], "data": {"collection_id": str(b["col"])}}


BODIES = {
    ("POST", "/api/orgs/{org_id}/brands"): None,
    ("PUT", BR): lambda b: {"json": {"name": "x"}},
    ("PUT", BR + "/logo"): None,
    ("POST", BR + "/sites"): None,
    ("PUT", BR + "/sites/{site_id}"): lambda b: {"json": {"label": "x"}},
    ("POST", "/api/orgs/{org_id}/members"): None,
    ("PUT", "/api/orgs/{org_id}/members/{user_id}"): lambda b: {"json": {"role": "client"}},
    ("POST", BR + "/collections"): lambda b: {"json": {"name": "x", "parent_id": str(b["col"])}},
    ("PUT", BR + "/collections/{collection_id}"): lambda b: {"json": {"name": "x"}},
    ("POST", BR + "/collections/{collection_id}/move"): lambda b: {"json": {"parent_id": str(b["col"])}},
    ("POST", BR + "/library/collection"): lambda b: {"json": {"filenames": [b["filename"]], "collection_id": str(b["col"])}},
    ("POST", BR + "/library/thumbs"): lambda b: {"json": {"filenames": [b["filename"]]}},
    ("POST", BR + "/library/upload"): _upload,
    ("PUT", BR + "/library/{filename}"): lambda b: {"json": {"credit": "x"}},
    ("POST", BR + "/library/delete"): lambda b: {"json": {"filenames": [b["filename"]]}},
    ("POST", BR + "/library/expiry"): lambda b: {"json": {"filenames": [b["filename"]], "expiry_date": "2031-01-01"}},
    ("POST", BR + "/library/tags"): lambda b: {"json": {"filenames": [b["filename"]], "add": ["x"]}},
    ("POST", BR + "/library/import-csv"): lambda b: {
        "files": {"file": ("x.csv", f"filename,credit\n{b['filename']},changed\n".encode(), "text/csv")}, "params": {"apply": "true"}},
    ("GET", BR + "/jobs/estimate"): lambda b: {"params": {"site_ids": [str(b["site"])]}},
    ("POST", BR + "/jobs/crawl"): lambda b: {"json": {"site_ids": [str(b["site"])]}},
    ("POST", BR + "/jobs/check"): lambda b: {"json": {"urls": [b["page"]]}},
    ("POST", BR + "/reviews"): lambda b: {"json": {"reference_id": str(b["ref"]), "site_image_ids": [str(b["image"])],
                                                   "decision": sorted(report_module.DECISIONS)[0]}},
    ("POST", BR + "/visuals/{reference_id}/replaced"): lambda b: {"json": {"urls": [b["page"]], "replaced": True}},
    ("GET", BR + "/visuals/{reference_id}/presence"): lambda b: {"params": {"site": str(b["site"])}},
    ("GET", BR + "/site-images"): lambda b: {"params": {"site": str(b["site"])}},
    ("POST", BR + "/site-images/adopt"): lambda b: {"json": {"site_image_ids": [str(b["image"])]}},
    ("POST", BR + "/exclusions"): lambda b: {"json": {"site_image_id": str(b["image"])}},
    ("PUT", BR + "/logos/setting"): None,
    ("POST", BR + "/logos/keep"): lambda b: {"json": {"site_image_ids": [str(b["image"])], "kept": False}},
    ("POST", BR + "/reports"): None,
    ("PUT", "/api/orgs/{org_id}/settings"): None,
    ("POST", BR + "/labels"): None,
    ("POST", BR + "/labels/{label_id}/images"): lambda b: {"json": {"decision": "yes", "site_image_ids": [str(b["image"])]}},
}

# The tenant key whose value fills each path parameter other than the organization and the brand.
CHILD_IDS = {"site_id": "site", "collection_id": "col", "filename": "filename", "job_id": "job", "reference_id": "ref",
             "exclusion_id": "excl", "label_id": "label", "user_id": "admin"}


def _tenant(conn, org, brand, tag) -> dict:
    """An organization with one of everything its routes handle; every text it holds contains `canary-{tag}`."""
    people = {}
    for role in ("admin", "client"):
        people[role] = uuid.uuid4()
        conn.execute("INSERT INTO auth.users (id, email) VALUES (%s, %s)", (people[role], f"{role}@canary-{tag}.test"))
        cloud_db.add_membership(conn, user_id=people[role], org_id=org, role=role)
    filename = f"canary-{tag}.jpg"
    ref = cloud_db.upsert_reference_image(
        conn, org_id=org, brand_id=brand, filename=filename, storage_path=f"{org}/{brand}/{filename}", expiry_date="2030-01-01",
        credit=f"credit canary-{tag}", notes=None, phash=PHASH, dhash=PHASH, thumb_path=f"{org}/{brand}/thumbs/{filename}.jpg",
        work_path=f"{org}/{brand}/work/{filename}.jpg")
    site = cloud_db.create_site(conn, org_id=org, brand_id=brand, url=f"https://canary-{tag}.test/")
    image = conn.execute(
        """INSERT INTO site_images (org_id, site_id, url, storage_path, thumb_path, content_hash, phash, dhash, logo_score)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 0.99) RETURNING id""",
        (org, site, f"https://canary-{tag}.test/v.jpg", f"{org}/{tag}.jpg", f"{org}/thumbs/{tag}.jpg", tag, PHASH, PHASH)).fetchone()["id"]
    page = f"https://canary-{tag}.test/page"
    page_id = conn.execute("INSERT INTO pages (org_id, site_id, url, status) VALUES (%s, %s, %s, 'done') RETURNING id",
                           (org, site, page)).fetchone()["id"]
    cloud_db.link_image_page(conn, image, page_id)
    cloud_db.write_matches(conn, org, [(ref, image, "phash", 1.0, "haut")])
    cloud_db.set_reviews(conn, brand_id=brand, reference_id=ref, site_image_ids=[image],
                         decision=sorted(report_module.DECISIONS)[0], reviewed_by=None)
    conn.execute("INSERT INTO reference_locations (reference_id, site_image_id, org_id, tier, inliers, ref_coverage, site_coverage)"
                 " VALUES (%s, %s, %s, 'same', 10, 0.5, 0.5)", (ref, image, org))
    cloud_db.create_report(conn, org_id=org, brand_id=brand, within_days=90, storage_path_html=f"{org}/canary-{tag}/report.html",
                           storage_path_csv=f"{org}/canary-{tag}/m.csv", storage_path_not_found_csv=f"{org}/canary-{tag}/n.csv",
                           stats={"expired_online": 2}, generated_by=None)
    cloud_db.set_overrides(conn, org, {"crawl": {"max_pages": 7}}, None)
    return {
        "tag": tag, "org": org, "brand": brand, "admin": people["admin"], "client": people["client"], "filename": filename,
        "ref": ref, "site": site, "image": image, "page": page, "col": cloud_db.add_collection(conn, org, brand, None, f"canary-{tag}-col")["id"],
        "job": cloud_jobs.enqueue(conn, org_id=org, brand_id=brand, kind="match")["id"],
        "excl": cloud_db.add_exclusion(conn, org, brand, image, reason=f"canary-{tag}", created_by=None),
        "label": cloud_labels.add_content_label(conn, org, brand, f"canary-{tag}-label")["id"],
        # what B's own answers would hold, and a caller of A must never read
        "canaries": [filename, f"canary-{tag}.test", f"@canary-{tag}.test", f"canary-{tag}-col", f"canary-{tag}-label",
                     f"credit canary-{tag}", f"{org}/"],
    }


@pytest.fixture()
def world(cloud_database_url, cloud_org, cloud_brand):
    """(A, B): two organizations, each with a brand, two people (admin, client, no platform admin) and data of every kind."""
    with cloud_db.connect(cloud_database_url) as conn:
        org_b = cloud_db.create_organization(conn, name="Org B", slug=f"b-{uuid.uuid4().hex[:10]}")
        brand_b = conn.execute("SELECT id FROM brands WHERE org_id = %s", (org_b,)).fetchone()["id"]
        a, b = _tenant(conn, cloud_org, cloud_brand, "a"), _tenant(conn, org_b, brand_b, "b")
        # A has no active job (some routes answer 409 about it before they look at the identifier of B); B keeps its own.
        conn.execute("UPDATE jobs SET status = 'cancelled' WHERE org_id = %s", (cloud_org,))
        return a, b


class Watch:
    """What a call may not change: the rows of B, and the rows of A that point to B."""

    def __init__(self, url: str, a: dict, b: dict):
        self.url, self.b = url, b
        self.ids = "|".join(str(b[key]) for key in ("org", "brand", "admin", "client", "ref", "site", "image", "col", "job", "excl", "label"))
        with cloud_db.connect(url) as conn:
            tables = [row["table_name"] for row in conn.execute(
                """SELECT c.table_name FROM information_schema.columns c JOIN information_schema.tables t USING (table_schema, table_name)
                   WHERE c.table_schema = 'public' AND c.column_name = 'org_id' AND t.table_type = 'BASE TABLE'""").fetchall()]
        assert {"reference_images", "site_images", "matches", "jobs", "reports", "collections", "labels", "memberships"} <= set(tables)
        self.sql = " UNION ALL ".join(
            [f"""SELECT '{t}' AS what, md5(coalesce(string_agg(x::text, '|' ORDER BY x::text), '')) AS seen FROM "{t}" x WHERE x.org_id = %(b)s"""
             for t in tables]
            + [f"""SELECT 'A->B {t}', count(*)::text FROM "{t}" x WHERE x.org_id = %(a)s AND x::text ~ %(ids)s""" for t in tables]
            + ["SELECT 'organizations', md5(x::text) FROM organizations x WHERE x.id = %(b)s"])
        self.args = {"a": a["org"], "b": b["org"], "ids": self.ids}
        self.before = self.read()

    def read(self) -> dict:
        with cloud_db.connect(self.url) as conn:
            return {row["what"]: row["seen"] for row in conn.execute(self.sql, self.args).fetchall()}

    def changes(self) -> list[str]:
        now = self.read()
        return [what for what in now if now[what] != self.before.get(what)]


def _call(client, method, template, ids, token_of, kwargs):
    url = template.format(**{key: str(value) for key, value in ids.items()})
    headers = _headers(token_of) if token_of else {}
    return url, client.request(method, url, headers=headers, **kwargs)


def _routes(client) -> dict:
    """{(method, path template): whether it needs a case in `BODIES`} for every route of the app: those of the OpenAPI schema
    (a case when it takes a body or an identifier in its query), and those of every module of `nyra.cloud.routes` that the
    schema lacks (hidden from it, or switched off by a flag: a case is assumed)."""
    found = {(method.upper(), path): "requestBody" in op or any(
        param["in"] == "query" and "uuid" in json.dumps(param["schema"]) for param in op.get("parameters", []))
        for path, ops in client.app.openapi()["paths"].items() for method, op in ops.items()}
    for info in pkgutil.iter_modules(ROUTES_PATH):
        module = importlib.import_module(f"nyra.cloud.routes.{info.name}")
        for route in getattr(getattr(module, "router", None), "routes", []):
            for method in route.methods - {"HEAD", "OPTIONS"}:
                found.setdefault((method, route.path), True)
    return found


def _plan(method, template, a, b):
    """The attacks on one route: (name, identifiers of the URL, body case, statuses allowed or None for 'any non-5xx')."""
    params = re.findall(r"{(\w+)}", template)
    unknown = [name for name in params if name not in {"org_id", "brand_id", *CHILD_IDS}]
    if unknown:
        raise AssertionError(f"{method} {template}: path parameter {unknown} unknown to the isolation test (CHILD_IDS)")
    child = {name: b[CHILD_IDS[name]] for name in params if name in CHILD_IDS}
    case = BODIES.get((method, template))
    refused = {403, 404}
    plan = [("organization of B", {"org_id": b["org"], "brand_id": b["brand"], **child}, case, {403})]
    if "brand_id" in params:
        plan.append(("brand of B in the organization of A", {"org_id": a["org"], "brand_id": b["brand"], **child}, case, refused))
    if child or case:
        plan.append(("children of B in the brand of A", {"org_id": a["org"], "brand_id": a["brand"], **child}, case,
                     refused if child else None))
    return plan


@pytest.mark.parametrize("token_key", ["admin", "client"])
def test_no_route_gives_or_changes_data_of_another_organization(labels_on, client, world, cloud_database_url, token_key):
    a, b = world
    watch = Watch(cloud_database_url, a, b)
    problems: list[str] = []
    routes = _routes(client)
    for stale in [key for key in {*PUBLIC, *USER_LEVEL, *BODIES} if key not in routes]:
        problems.append(f"{stale}: listed in PUBLIC, USER_LEVEL or BODIES but the route does not exist (remove it)")

    for (method, template), needs_case in sorted(routes.items()):
        route = f"{method} {template}"
        if (method, template) in {*PUBLIC, *USER_LEVEL}:
            continue
        if needs_case and not template.startswith("/api/staff/") and (method, template) not in BODIES:
            problems.append(f"{route}: takes a body or identifiers in its query, add a case to BODIES (None if no identifier of B can travel in it)")
            continue
        if template.startswith("/api/staff/"):
            plan = [("platform admin routes", {"user_id": b["admin"]}, None, {403})]
        elif template.startswith("/api/orgs/{org_id}"):
            plan = _plan(method, template, a, b)
        else:
            problems.append(f"{route}: not covered (add it to PUBLIC or USER_LEVEL with the reason, or test it)")
            continue
        _, no_token = _call(client, method, template, plan[0][1], None, {})
        if no_token.status_code != 401:
            problems.append(f"{route}: without a session it answers {no_token.status_code}, not 401")
        for name, ids, case, allowed in plan:
            kwargs = case(b) if case else {}
            url, response = _call(client, method, template, ids, a[token_key], kwargs)
            sent = url + repr(kwargs)
            leaked = [text for text in b["canaries"] if text in response.text and text not in sent]
            if response.status_code >= 500 or (allowed and response.status_code not in allowed) or leaked:
                problems.append(f"{route} as {token_key}, {name}: {response.status_code} {response.text[:200]!r} leaked={leaked}")
            if changed := watch.changes():
                problems.append(f"{route} as {token_key}, {name}: changed {changed}")
                watch.before = watch.read()
    assert not problems, "\n".join(problems)


def test_public_and_user_level_routes_show_nothing_of_another_organization(client, world):
    a, b = world
    for method, template in sorted(PUBLIC):
        response = client.request(method, template.replace("{full_path}", "robot"))
        assert response.status_code == 200 and not [t for t in b["canaries"] if t in response.text], template
    orgs = client.get("/api/orgs", headers=_headers(a["admin"])).json()["organizations"]
    assert [org["org_id"] for org in orgs] == [str(a["org"])] and str(b["org"]) not in json.dumps(orgs)
    me = client.get("/api/me", headers=_headers(a["client"])).json()
    assert me["user_id"] == str(a["client"]) and me["staff"] is False


def test_a_platform_admin_cannot_delete_or_rename_a_brand_through_another_organization(client, world, cloud_database_url):
    a, b = world
    staff = a["admin"]
    with cloud_db.connect(cloud_database_url) as conn:
        conn.execute("INSERT INTO platform_staff (user_id) VALUES (%s)", (staff,))
        conn.execute("UPDATE jobs SET status = 'cancelled' WHERE org_id = %s", (b["org"],))  # an active job would answer 409 first
    try:
        url = f"/api/orgs/{a['org']}/brands/{b['brand']}"
        assert client.delete(url, headers=_headers(staff)).status_code == 404
        assert client.put(url, headers=_headers(staff), json={"name": "Hijacked"}).status_code == 404
        with cloud_db.connect(cloud_database_url) as conn:
            assert conn.execute("SELECT name FROM brands WHERE id = %s", (b["brand"],)).fetchone()["name"] != "Hijacked"
    finally:
        with cloud_db.connect(cloud_database_url) as conn:
            conn.execute("DELETE FROM platform_staff WHERE user_id = %s", (staff,))


def test_storage_paths_and_signed_urls_never_leave_the_organization(client, world, cloud_database_url, fake_storage_client):
    a, b = world
    base = f"/api/orgs/{a['org']}/brands/{a['brand']}"
    admin = _headers(a["admin"])
    shared = "shared.jpg"
    # B holds a visual with the same name as one of A, signed first: a cache keyed by name alone would hand A the URL of B.
    with cloud_db.connect(cloud_database_url) as conn:
        for t in (b, a):
            cloud_db.upsert_reference_image(conn, org_id=t["org"], brand_id=t["brand"], filename=shared, expiry_date=None, credit=None, notes=None,
                                            storage_path=f"{t['org']}/{t['brand']}/{shared}", thumb_path=f"{t['org']}/{t['brand']}/thumbs/{shared}.jpg")
            if t is b:
                client.post(f"/api/orgs/{b['org']}/brands/{b['brand']}/library/thumbs", headers=_headers(b["admin"]), json={"filenames": [shared]})
    hostile = [b["filename"], f"../../{b['org']}/{b['brand']}/{shared}", f"{b['org']}/{b['brand']}/{shared}"]
    urls = client.post(f"{base}/library/thumbs", headers=admin, json={"filenames": [shared, *hostile]}).json()["urls"]
    assert list(urls) == [shared] and f"/refs/{a['org']}/{a['brand']}/" in urls[shared] and str(b["org"]) not in urls[shared]
    original = client.get(f"{base}/library/{shared}/url", headers=admin).json()["url"]
    assert f"/refs/{a['org']}/{a['brand']}/" in original and str(b["org"]) not in original
    for name in (b["filename"], f"..%2F..%2F{b['org']}%2F{shared}", f"{b['org']}%2F{b['brand']}%2F{shared}", "..", "%2e%2e"):
        got = client.get(f"{base}/library/{name}/url", headers=admin)
        assert got.status_code in {400, 404} and "fake-storage" not in got.text, name
    reports = client.get(f"{base}/reports", headers=admin).json()["reports"]
    assert reports and all(f"/reports/{a['org']}/" in url for report in reports for url in report["files"].values())

    # What an upload writes: row and objects, all under `{org}/{brand}/`; a hostile name cannot step out of it.
    sent = client.post(f"{base}/library/upload", headers=admin, files=[("files", ("../../x/up.jpg", _upload_bytes().getvalue(), "image/jpeg"))])
    assert sent.json()["saved"] == ["up.jpg"]
    with cloud_db.connect(cloud_database_url) as conn:
        row = cloud_db.get_reference_by_filename(conn, a["brand"], "up.jpg")
    assert all(row[key].startswith(f"{a['org']}/{a['brand']}/") for key in ("storage_path", "thumb_path", "work_path"))
    assert {path for bucket, path in fake_storage_client.store if bucket == cloud_storage.BUCKET_REFS} \
        == {row["storage_path"], row["thumb_path"], row["work_path"]}
    for name in ("../x.jpg", f"../../{b['org']}/a.jpg", "a/b.jpg", "..\\..\\x.jpg", f"/{b['org']}/x.jpg", "..", ""):
        for build in (cloud_storage.ref_path, cloud_storage.ref_thumb_path, cloud_storage.ref_work_path):
            path = build(a["org"], a["brand"], name)
            segments = path.split("/")
            assert segments[:2] == [str(a["org"]), str(a["brand"])] and ".." not in segments and str(b["org"]) not in segments, (name, path)
    assert cloud_storage.site_work_path(a["org"], "h").startswith(f"{a['org']}/")
    assert cloud_storage.site_thumb_path(a["org"], "h").startswith(f"{a['org']}/")


def test_org_admins_are_listed_for_the_periodic_review_and_only_for_the_nyra_team(client, world, cloud_database_url):
    a, b = world
    for user in (a["admin"], a["client"]):
        assert client.get("/api/staff/users", headers=_headers(user)).status_code == 403
    staff = uuid.uuid4()
    with cloud_db.connect(cloud_database_url) as conn:
        conn.execute("INSERT INTO auth.users (id, email) VALUES (%s, %s)", (staff, f"{staff}@nyra.test"))
        conn.execute("INSERT INTO platform_staff (user_id) VALUES (%s)", (staff,))
    try:
        users = client.get("/api/staff/users", headers=_headers(staff)).json()["users"]
    finally:
        with cloud_db.connect(cloud_database_url) as conn:
            conn.execute("DELETE FROM platform_staff WHERE user_id = %s", (staff,))
    admins = {(m["org_id"], u["email"]): m["since"] for u in users for m in u["memberships"] if m["role"] == "admin"}
    assert {(str(t["org"]), f"admin@canary-{t['tag']}.test") for t in (a, b)} <= set(admins)
    assert all(admins[(str(t["org"]), f"admin@canary-{t['tag']}.test")] for t in (a, b))
