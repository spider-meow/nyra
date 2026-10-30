"""Isolation between organizations (AXE-534).

The backend uses the Supabase service role, which bypasses RLS: every route and every query must filter on the
organization (or on a brand checked to belong to it) itself. This test calls EVERY route of the application, as an admin and
as a client of an organization, in five situations, against a second organization B that holds data of every kind:

  1. the organization of B in the URL                                     -> 403
  2. the brand of B under the caller's own organization                    -> 403/404
  3. the caller's own brand with the identifiers of B (path, body, query)  -> 403/404 when B's identifier is in the path
  4. the caller's own, valid identifiers (the positive witness: the handler really runs and answers 200, so a 404 or a 422
     never passes for isolation, and a handler that forgets its filter has data of B to show)
  5. mixed: a resource of the caller in the path and one of B in the body or the query, and the other way round.

Every call must not answer 5xx, carry data of B (body or headers), change a row of B (tables with or without `org_id`),
or write a row that points to a row of B. A route that has no case fails the test: a new route takes a case in `BODIES`
(if it takes a body or an identifier in its query), or an entry in `PUBLIC` / `USER_LEVEL` with the reason it needs no check.

What it does not prove: Storage's own RLS policies (the backend signs with the service role, they are never exercised) and
the Supabase side of invitations (stubbed). Same setup as the other cloud tests (TEST_DATABASE_URL, in-memory Storage).
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

from test_cloud_api import _headers, _upload_bytes  # noqa: E402

from nyra import netguard  # noqa: E402
from nyra import report as report_module  # noqa: E402
from nyra.cloud import db as cloud_db  # noqa: E402
from nyra.cloud import digest  # noqa: E402
from nyra.cloud import jobs as cloud_jobs  # noqa: E402
from nyra.cloud import labels as cloud_labels  # noqa: E402
from nyra.cloud import storage as cloud_storage  # noqa: E402
from nyra.cloud.routes import __path__ as ROUTES_PATH  # noqa: E402

ORG = "/api/orgs/{org_id}"
BR = ORG + "/brands/{brand_id}"
PHASH = "ffff0000ffff0000"

# Routes with no organization to check, and why. Anything else is tested below.
PUBLIC = {
    ("GET", "/api/healthz"): "health check for the load balancer: answers {status: ok}, reads nothing",
    ("GET", "/api/workerz"): "uptime monitor: alert codes, French sentences and two figures (queue wait, disk %), no name, no id",
    ("GET", "/api/auth/config"): "public sign-in settings (Supabase URL and anon key), needed before anyone is signed in",
    ("GET", "/"): "the app shell (static HTML), no data",
    ("GET", "/{full_path}"): "the app shell for every client-side route (/robot, /login, ...) and the build's files; "
                             "/api/* is a 404 and a path out of the build folder is refused (checked below)",
}
# Routes of the signed-in user, with no organization or brand in the URL: they answer from the caller's own memberships.
USER_LEVEL = {
    ("GET", "/api/me"): "the caller's own account",
    ("GET", "/api/orgs"): "the caller's own memberships (checked below: never an organization of B)",
}
# The staff routes (`/api/staff/*`, platform admins only) are refused to every member, whatever the role, with two sets of
# identifiers. What a platform admin may do in another organization is by design (ARCHITECTURE.md, "Security model"):
# `test_a_platform_admin_opens_any_organization_without_being_a_member`.
# Their nominal behaviour is tested in test_cloud_api.py, not here.

# Routes whose nominal call is refused to an organization admin: platform admins only (each brand is billed).
OWN_REFUSED = {("POST", ORG + "/brands"), ("DELETE", BR), *((verb, BR + path) for verb, path in (
    ("GET", "/packshots/feedback"), ("PUT", "/packshots/feedback"), ("DELETE", "/packshots/feedback/{site_image_id}")))}  # the Nyra team's calibration (AXE-668)
# The job a fresh organization starts with. The five routes that launch a job by hand answer 409 while a job but an
# `index` waits in the organization, so their witness starts from an `index`. Every other route keeps the default
# `check`, which does not stop the deletions (an address cannot be removed while a read, a comparison or an index waits).
OWN_JOB = {("POST", BR + path): "index" for path in ("/jobs/crawl", "/jobs/check", "/jobs/match", "/library/{filename}/locate", "/reports")}


def _bytes(name):
    return (name, _upload_bytes().getvalue(), "image/jpeg")


# (method, path) -> function of a tenant giving the request's `json` / `data` / `files` / `params`, VALID for that tenant, with
# its identifiers in it. Given B's tenant it is the attack; given the caller's own it is the nominal call. Needed for every route
# that takes a body, and for every route whose query takes identifiers.
BODIES = {
    ("POST", ORG + "/brands"): lambda t: {"json": {"name": f"Brand {t['tag']}"}},
    ("PUT", BR): lambda t: {"json": {"name": f"Renamed {t['tag']}"}},
    ("PUT", BR + "/logo"): lambda t: {"files": {"file": _bytes("l.jpg")}},
    ("POST", BR + "/sites"): lambda t: {"json": {"url": f"https://new-{t['tag']}.test/"}},
    ("PUT", BR + "/sites/{site_id}"): lambda t: {"json": {"label": "x"}},
    ("POST", ORG + "/members"): lambda t: {"json": {"email": f"new-{t['tag']}@x.test"}},
    ("PUT", ORG + "/members/{user_id}"): lambda t: {"json": {"role": "client"}},
    ("POST", BR + "/collections"): lambda t: {"json": {"name": "x", "parent_id": str(t["col"])}},
    ("PUT", BR + "/collections/order"): lambda t: {"json": {"parent_id": None, "ids": [str(t["col"]), str(t["col2"])]}},
    ("PUT", BR + "/collections/{collection_id}"): lambda t: {"json": {"name": "x"}},
    ("POST", BR + "/collections/{collection_id}/move"): lambda t: {"json": {"parent_id": str(t["col2"])}},
    ("POST", BR + "/library/collection"): lambda t: {"json": {"filenames": [t["filename"]], "collection_id": str(t["col"])}},
    ("POST", BR + "/library/thumbs"): lambda t: {"json": {"filenames": [t["filename"]]}},
    ("POST", BR + "/library/upload"): lambda t: {"files": [("files", _bytes("up.jpg"))], "data": {"collection_id": str(t["col"])}},
    ("PUT", BR + "/library/{filename}"): lambda t: {"json": {"credit": "x"}},
    ("POST", BR + "/library/delete"): lambda t: {"json": {"filenames": [t["filename"]]}},
    ("POST", BR + "/library/expiry"): lambda t: {"json": {"filenames": [t["filename"]], "expiry_date": "2031-01-01"}},
    ("POST", BR + "/library/tags"): lambda t: {"json": {"filenames": [t["filename"]], "add": ["x"]}},
    ("POST", BR + "/library/credit"): lambda t: {"json": {"filenames": [t["filename"]], "credit": "Studio"}},
    ("POST", BR + "/library/import-csv"): lambda t: {
        "files": {"file": ("x.csv", f"filename,credit\n{t['filename']},changed\n".encode(), "text/csv")}, "params": {"apply": "true"}},
    ("PUT", ORG + "/me/notifications"): lambda t: {"json": {"monthly_email": False}},
    ("PUT", BR + "/schedule"): lambda t: {"json": {"enabled": True, "day_of_month": 9}},
    ("GET", ORG + "/audit"): lambda t: {"params": {"brand_id": str(t["brand"]), "user_id": str(t["admin"])}},
    ("GET", BR + "/jobs/estimate"): lambda t: {"params": {"site_ids": [str(t["site"])]}},
    ("POST", BR + "/jobs/crawl"): lambda t: {"json": {"site_ids": [str(t["site"])]}},
    ("POST", BR + "/jobs/check"): lambda t: {"json": {"urls": [t["page"]]}},
    ("POST", BR + "/reviews"): lambda t: {"json": {"reference_id": str(t["ref"]), "site_image_ids": [str(t["image"])],
                                                   "decision": report_module.DECISION_REMOVE}},
    ("POST", BR + "/visuals/{reference_id}/replaced"): lambda t: {"json": {"urls": [t["page"]], "replaced": True}},
    ("GET", BR + "/visuals/{reference_id}/presence"): lambda t: {"params": {"site": str(t["site"])}},
    ("GET", BR + "/site-images"): lambda t: {"params": {"site": str(t["site"])}},
    ("POST", BR + "/site-images/adopt"): lambda t: {"json": {"site_image_ids": [str(t["image"])]}},
    ("POST", BR + "/exclusions"): lambda t: {"json": {"site_image_id": str(t["image"])}},
    ("PUT", BR + "/logos/setting"): lambda t: {"json": {"enabled": True}},
    ("PUT", BR + "/packshots/setting"): lambda t: {"json": {"enabled": True}},
    ("PUT", BR + "/packshots/feedback"): lambda t: {"json": {"site_image_id": str(t["image"]), "is_packshot": True}},
    ("POST", BR + "/logos/keep"): lambda t: {"json": {"site_image_ids": [str(t["image"])], "kept": False}},
    ("POST", BR + "/reports"): lambda t: {"json": {}},
    ("PUT", ORG + "/settings"): lambda t: {"json": {"overrides": {"crawl": {"max_pages": 5}}}},
    ("POST", BR + "/labels"): lambda t: {"json": {"name": f"own-{t['tag']}"}},
    ("POST", BR + "/labels/{label_id}/images"): lambda t: {"json": {"decision": "yes", "site_image_ids": [str(t["image"])]}},
}

# The tenant key whose value fills each path parameter other than the organization and the brand.
CHILD_IDS = {"site_id": "site", "collection_id": "col", "filename": "filename", "job_id": "job", "reference_id": "ref",
             "exclusion_id": "excl", "label_id": "label", "user_id": "admin", "site_image_id": "image"}
OWN_CHILD_IDS = {**CHILD_IDS, "user_id": "client"}  # the only admin cannot be demoted or removed
# What a body or a query takes from another tenant in a mixed call: the second operand ("the image of B"), or the first
# ("the reference of B") against the caller's own second operand.
SECOND = ("image", "site", "page", "col", "col2")
FIRST = ("ref", "filename")


def _tenant(conn, org, brand, tag, job="check") -> dict:
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
                         decision=report_module.DECISION_REMOVE, reviewed_by=None)
    conn.execute("INSERT INTO reference_locations (reference_id, site_image_id, org_id, tier, inliers, ref_coverage, site_coverage)"
                 " VALUES (%s, %s, %s, 'same', 10, 0.5, 0.5)", (ref, image, org))
    cloud_db.create_report(conn, org_id=org, brand_id=brand, within_days=90, storage_path_html=f"{org}/canary-{tag}/report.html",
                           storage_path_csv=f"{org}/canary-{tag}/m.csv", storage_path_not_found_csv=f"{org}/canary-{tag}/n.csv",
                           stats={"expired_online": 2}, generated_by=None)
    cloud_db.set_overrides(conn, org, {"crawl": {"max_pages": 7}}, None)
    cloud_db.set_brand_logo(conn, brand, "data:image/png;base64,AAAA")
    ids = {
        "org": org, "brand": brand, "admin": people["admin"], "client": people["client"], "ref": ref, "site": site, "image": image,
        "col": cloud_db.add_collection(conn, org, brand, None, f"canary-{tag}-col")["id"],
        "col2": cloud_db.add_collection(conn, org, brand, None, f"canary-{tag}-col2")["id"],
        "job": cloud_jobs.enqueue(conn, org_id=org, brand_id=brand, kind=job)["id"],
        "excl": cloud_db.add_exclusion(conn, org, brand, image, reason=f"canary-{tag}", created_by=None),
        "label": cloud_labels.add_content_label(conn, org, brand, f"canary-{tag}-label")["id"],
    }
    return {
        **ids, "tag": tag, "filename": filename, "page": page,
        # B's data: a caller of A must never read these. The identifiers and the file name are the ones a request of ours
        # may legitimately send, and an answer may echo what was sent: those are leaks only when we did not send them.
        "canaries": [f"canary-{tag}.test", f"@canary-{tag}.test", f"canary-{tag}-col", f"canary-{tag}-label", f"credit canary-{tag}",
                     f"{org}/"],
        "echoed": [filename, *(str(value) for value in ids.values())],
    }


def _fresh(url, store, job="check") -> dict:
    """A new organization with its own data, for a call that may change it (destroy a reference, queue a job...)."""
    with cloud_db.connect(url) as conn:
        org = cloud_db.create_organization(conn, name="Own", slug=f"own-{uuid.uuid4().hex[:10]}")
        brand = conn.execute("SELECT id FROM brands WHERE org_id = %s", (org,)).fetchone()["id"]
        tenant = _tenant(conn, org, brand, f"own{uuid.uuid4().hex[:6]}", job)  # never a prefix of B's tag
    store[("site-images", f"{org}/{tenant['tag']}.jpg")] = _upload_bytes().getvalue()  # what adopting a site image downloads
    return tenant


@pytest.fixture(autouse=True)
def no_rate_limit(monkeypatch):
    """These tests call every route as one person, up to ~150 writes in a run, against a bucket of 120 a minute that refills 2 a second: they passed
    only on a machine slow enough, and failed on `PUT /api/staff/organizations/{org_id}/quotas` (a 429, no leak) the day it ran faster. The limiter has its
    own tests (test_ratelimit.py, test_rate_buckets.py, and the 429 case of test_cloud_api.py). Autouse, so that it runs before `client`: the app reads
    its limits from the environment when it is built."""
    monkeypatch.setenv("NYRA_RATE_READ_PER_MIN", "1000000")
    monkeypatch.setenv("NYRA_RATE_WRITE_PER_MIN", "1000000")


@pytest.fixture()
def world(cloud_database_url, cloud_org, cloud_brand, fake_storage_client):
    """(A, B): two organizations, each with a brand, two people (admin, client, no platform admin) and data of every kind."""
    with cloud_db.connect(cloud_database_url) as conn:
        org_b = cloud_db.create_organization(conn, name="Org B", slug=f"b-{uuid.uuid4().hex[:10]}")
        brand_b = conn.execute("SELECT id FROM brands WHERE org_id = %s", (org_b,)).fetchone()["id"]
        a, b = _tenant(conn, cloud_org, cloud_brand, "a"), _tenant(conn, org_b, brand_b, "b")
    for t in (a, b):
        fake_storage_client.store[("site-images", f"{t['org']}/{t['tag']}.jpg")] = _upload_bytes().getvalue()
    return a, b


class Watch:
    """What a call may not change: every row of B (the tables with an `org_id`, found in the catalog, and the ones without,
    reached through their parent), and the rows of the caller's organization that point to B."""

    def __init__(self, url: str, b: dict):
        self.url, self.b_org = url, b["org"]
        ids = "|".join(str(b[key]) for key in ("org", "brand", "admin", "client", "ref", "site", "image", "col", "col2", "job", "excl", "label"))
        with cloud_db.connect(url) as conn:
            tables = [row["table_name"] for row in conn.execute(
                """SELECT c.table_name FROM information_schema.columns c JOIN information_schema.tables t USING (table_schema, table_name)
                   WHERE c.table_schema = 'public' AND c.column_name = 'org_id' AND t.table_type = 'BASE TABLE'""").fetchall()]
        assert {"reference_images", "site_images", "matches", "jobs", "reports", "collections", "labels", "memberships"} <= set(tables)
        self.tables = tables
        mine = "(SELECT id FROM site_images WHERE org_id = %(b)s)"
        self.sql = " UNION ALL ".join(
            [f"""SELECT '{t}' AS what, md5(coalesce(string_agg(x::text, '|' ORDER BY x::text), '')) AS seen FROM "{t}" x WHERE x.org_id = %(b)s"""
             for t in tables]
            + [f"""SELECT 'caller->B {t}', count(*)::text FROM "{t}" x WHERE x.org_id = ANY(%(mine)s) AND x::text ~ %(ids)s""" for t in tables]
            + ["SELECT 'organizations', md5(x::text) FROM organizations x WHERE x.id = %(b)s",
               f"SELECT 'image_pages', md5(coalesce(string_agg(x::text, '|' ORDER BY x::text), '')) FROM image_pages x WHERE x.image_id IN {mine}",
               f"SELECT 'caller->B image_pages', count(*)::text FROM image_pages x WHERE (x.image_id IN {mine})"
               " <> (x.page_id IN (SELECT id FROM pages WHERE org_id = %(b)s))",
               "SELECT 'brand_logos', md5(coalesce(string_agg(x::text, '|'), '')) FROM brand_logos x WHERE x.brand_id = %(brand)s",
               "SELECT 'platform_staff', md5(coalesce(string_agg(x::text, '|' ORDER BY x::text), '')) FROM platform_staff x"])
        self.args = {"b": b["org"], "brand": b["brand"], "ids": ids, "mine": [b["org"]]}
        self.before = self.read()

    def read(self) -> dict:
        with cloud_db.connect(self.url) as conn:
            return {row["what"]: row["seen"] for row in conn.execute(self.sql, self.args).fetchall()}

    def rows_of(self, org) -> dict:
        """The caller's data as it is (a call with nothing but B's identifiers must leave it alone). Not the jobs, which a
        refused call may leave queued behind, nor the labels a read creates once, nor the audit log, which is meant to keep the
        trace of an attempt (B's own audit log stays watched by `changes`)."""
        sql = " UNION ALL ".join(
            f"""SELECT '{t}' AS what, md5(coalesce(string_agg(x::text, '|' ORDER BY x::text), '')) AS seen FROM "{t}" x WHERE x.org_id = %s"""
            for t in self.tables if t not in ("jobs", "labels", "image_labels", "audit_log"))
        with cloud_db.connect(self.url) as conn:
            return {row["what"]: row["seen"] for row in conn.execute(sql, [org] * sql.count("%s")).fetchall()}

    def changes(self, mine=()) -> list[str]:
        self.args["mine"] = [*mine, self.b_org]
        now = self.read()
        return [what for what in now if now[what] != self.before.get(what)]


def _params(template) -> list[str]:
    params = re.findall(r"{(\w+)}", template)
    unknown = [name for name in params if name not in {"org_id", "brand_id", *CHILD_IDS}]
    assert not unknown, f"{template}: path parameter {unknown} unknown to the isolation test (CHILD_IDS)"
    return params


def _routes(client) -> dict:
    """{(method, path template): whether it needs a case in `BODIES`} for every route of the app: those of the OpenAPI schema
    (a case when it takes a body, or in its query an identifier: a uuid, or a parameter named `*id`, `*ids`, `site`, `filename`),
    and those of every module of `nyra.cloud.routes` that the schema lacks (hidden from it, or switched off by a flag: a case
    is assumed). Today no route has a text query parameter that carries an identifier."""
    def takes_ids(param):
        return param["in"] == "query" and ("uuid" in json.dumps(param["schema"]) or re.search(r"(^|_)ids?$|^site$|^filename$", param["name"]))

    found = {(method.upper(), path): "requestBody" in op or any(takes_ids(param) for param in op.get("parameters", []))
             for path, ops in client.app.openapi()["paths"].items() for method, op in ops.items()}
    for info in pkgutil.iter_modules(ROUTES_PATH):
        module = importlib.import_module(f"nyra.cloud.routes.{info.name}")
        for route in getattr(getattr(module, "router", None), "routes", []):
            for method in route.methods - {"HEAD", "OPTIONS"}:
                found.setdefault((method, route.path), True)
    return found


class Run:
    """The calls of one test: each is checked, and what is wrong is collected in `problems` (all of them are reported)."""

    def __init__(self, client, a, b, url, store):
        self.client, self.a, self.b, self.url, self.store = client, a, b, url, store
        self.watch, self.problems = Watch(url, b), []

    def hit(self, what, method, template, ids, user, kwargs, allowed, frozen=False):
        org = ids["org_id"] if "org_id" in ids else self.a["org"]
        before = self.watch.rows_of(org) if frozen else None
        url = template.format(**{key: str(value) for key, value in ids.items()})
        response = self.client.request(method, url, headers=_headers(user) if user else {}, **kwargs)
        sent = url + repr(kwargs)
        seen = response.text + repr(dict(response.headers))
        leaked = [text for text in self.b["canaries"] if text in seen] + [text for text in self.b["echoed"] if text in seen and text not in sent]
        if response.status_code >= 500 or (allowed and response.status_code not in allowed) or leaked:
            self.problems.append(f"{method} {template} {what}: {response.status_code} {response.text[:160]!r} leaked={leaked}")
        if frozen and (moved := [t for t, seen in self.watch.rows_of(org).items() if seen != before[t]]):
            self.problems.append(f"{method} {template} {what}: the caller's own data changed {moved}")
        if changed := self.watch.changes([org, self.a["org"]]):
            self.problems.append(f"{method} {template} {what}: changed {changed}")
            self.watch.before = self.watch.read()
        return response

    def attacks(self, method, template, role):
        """Cases 1 to 3, against the caller's organization A as it is (no active job left from a previous call)."""
        a, b, params = self.a, self.b, _params(template)
        with cloud_db.connect(self.url) as conn:
            conn.execute("UPDATE jobs SET status = 'cancelled' WHERE org_id = %s AND status IN ('queued', 'running')", (a["org"],))
        child = {name: b[CHILD_IDS[name]] for name in params if name in CHILD_IDS}
        case = BODIES.get((method, template))
        kwargs = case(b) if case else {}
        plan = [("organization of B", {"org_id": b["org"], "brand_id": b["brand"], **child}, {403}, False)]
        if "brand_id" in params:
            plan.append(("brand of B under A", {"org_id": a["org"], "brand_id": b["brand"], **child}, {403, 404}, False))
        if child or any(text in repr(kwargs) for text in (*b["echoed"], b["page"])):  # an identifier of B in the body or the query
            plan.append(("B's identifiers under the brand of A", {"org_id": a["org"], "brand_id": a["brand"], **child},
                         {403, 404} if child else None, True))
        for what, ids, allowed, frozen in plan:
            self.hit(what, method, template, ids, a[role], kwargs, allowed, frozen)

    def own(self, method, template, role):
        """Case 4: a fresh organization, its own identifiers, valid body: the handler runs and answers."""
        t = _fresh(self.url, self.store, OWN_JOB.get((method, template), "check"))
        case = BODIES.get((method, template))
        ids = {"org_id": t["org"], "brand_id": t["brand"], **{name: t[OWN_CHILD_IDS[name]] for name in _params(template) if name in CHILD_IDS}}
        allowed = {403} if (method, template) in OWN_REFUSED else {200} if role == "admin" else {200, 403}
        self.hit("nominal call (witness)", method, template, ids, t[role], case(t) if case else {}, allowed)

    def mixed(self, method, template, role):
        """Case 5: the caller's resource in the path, B's in the body or the query; then B's reference and the caller's image."""
        case = BODIES.get((method, template))
        if not case:
            return
        for what, take in (("own reference, image of B", SECOND), ("reference of B, own image", FIRST)):
            t = _fresh(self.url, self.store)
            body = case({**t, **{key: self.b[key] for key in take}})
            if repr(body) == repr(case(t)):
                return
            ids = {"org_id": t["org"], "brand_id": t["brand"], **{name: t[OWN_CHILD_IDS[name]] for name in _params(template) if name in CHILD_IDS}}
            self.hit(what, method, template, ids, t[role], body, None)

    def route(self, method, template, role):
        route = f"{method} {template}"
        if template.startswith("/api/staff/"):
            for owner in (self.b, self.a):
                ids = {"user_id": owner["admin"], "org_id": owner["org"], "key": "monthly_digest", "slug": "any"}
                self.hit("staff route", method, template, ids, self.a[role], {}, {403})
            return
        if not template.startswith(ORG):
            self.problems.append(f"{route}: not covered (add it to PUBLIC or USER_LEVEL with the reason, or test it)")
            return
        no_token = self.client.request(method, template.format(**{name: self.b["org"] for name in _params(template)}))
        if no_token.status_code != 401:
            self.problems.append(f"{route}: without a session it answers {no_token.status_code}, not 401")
        self.attacks(method, template, role)
        self.own(method, template, role)
        self.mixed(method, template, role)


@pytest.mark.parametrize("role", ["admin", "client"])
def test_no_route_gives_or_changes_data_of_another_organization(labels_on, client, world, cloud_database_url, fake_storage_client,
                                                                monkeypatch, role):
    a, b = world
    spare = uuid.uuid4()  # the account every invitation "finds"
    with cloud_db.connect(cloud_database_url) as conn:
        conn.execute("INSERT INTO auth.users (id, email) VALUES (%s, %s)", (spare, f"{spare}@x.test"))
    monkeypatch.setattr("nyra.cloud.routes.members.find_or_invite_user", lambda *args: (str(spare), True))
    monkeypatch.setattr(netguard, "check_url", lambda url: None)
    monkeypatch.setattr("nyra.cloud.routes.site_images.original_from_site", lambda url, config: None)
    run = Run(client, a, b, cloud_database_url, fake_storage_client.store)
    routes = _routes(client)
    for stale in [key for key in {*PUBLIC, *USER_LEVEL, *BODIES} if key not in routes]:
        run.problems.append(f"{stale}: listed in PUBLIC, USER_LEVEL or BODIES but the route does not exist (remove it)")
    for (method, template), needs_case in sorted(routes.items()):
        if (method, template) in {*PUBLIC, *USER_LEVEL}:
            continue
        if needs_case and not template.startswith("/api/staff/") and (method, template) not in BODIES:
            run.problems.append(f"{method} {template}: takes a body or identifiers in its query, add a case to BODIES")
            continue
        run.route(method, template, role)
    assert not run.problems, "\n".join(run.problems)


def test_public_and_user_level_routes_show_nothing_of_another_organization(client, world):
    a, b = world
    for method, template in sorted(PUBLIC):
        response = client.request(method, template.replace("{full_path}", "robot"))
        allowed = {200, 503} if template == "/api/workerz" else {200}  # the probe says 503 while an alert is on (no worker here)
        assert response.status_code in allowed and not [t for t in b["canaries"] if t in response.text], template
    # the catch-all page: /api/* unknown is a 404 (not the app shell), and a path out of the build folder is refused
    assert client.get("/api/unknown").status_code == 404 and client.get("/api/orgs/unknown/x").status_code == 404
    for path in ("/..%2f..%2fpyproject.toml", "/%2e%2e%2f%2e%2e%2fpyproject.toml", "/assets/..%2f..%2f..%2fpyproject.toml"):
        got = client.get(path)
        assert got.status_code in {200, 404} and "[tool." not in got.text, path
    orgs = client.get("/api/orgs", headers=_headers(a["admin"])).json()["organizations"]
    assert [org["org_id"] for org in orgs] == [str(a["org"])] and str(b["org"]) not in json.dumps(orgs)
    me = client.get("/api/me", headers=_headers(a["client"])).json()
    assert me["user_id"] == str(a["client"]) and me["staff"] is False


@pytest.fixture()
def staff_outsider(world, cloud_database_url):
    """A platform admin who is a member of no organization (AXE-573): they open any client's space."""
    user = uuid.uuid4()
    with cloud_db.connect(cloud_database_url) as conn:
        conn.execute("INSERT INTO auth.users (id, email) VALUES (%s, %s)", (user, f"{user}@nyra.test"))
        conn.execute("INSERT INTO platform_staff (user_id) VALUES (%s)", (user,))
    yield user
    with cloud_db.connect(cloud_database_url) as conn:
        conn.execute("DELETE FROM platform_staff WHERE user_id = %s", (user,))


def test_a_platform_admin_opens_any_organization_without_being_a_member(client, world, staff_outsider, cloud_database_url):
    a, b = world
    staff = _headers(staff_outsider)
    brand_b_under_a = f"/api/orgs/{a['org']}/brands/{b['brand']}"
    with cloud_db.connect(cloud_database_url) as conn:
        conn.execute("UPDATE jobs SET status = 'cancelled' WHERE org_id = ANY(%s)", ([a["org"], b["org"]],))  # an active job would answer 409 first
    # every organization, every brand of the organization in the URL: they read it, as an administrator would
    for side in (a, b):
        assert client.get(f"/api/orgs/{side['org']}/brands/{side['brand']}/library", headers=staff).status_code == 200
        assert client.get(f"/api/orgs/{side['org']}/audit", headers=staff).status_code == 200
    # ... but never from one organization to another through the URL: the brand must belong to the organization of the path
    assert client.get(f"{brand_b_under_a}/library", headers=staff).status_code == 404
    assert client.delete(brand_b_under_a, headers=staff).status_code == 404
    assert client.put(brand_b_under_a, headers=staff, json={"name": "Hijacked"}).status_code == 404
    assert client.get(f"/api/orgs/{uuid.uuid4()}/brands/{a['brand']}/library", headers=staff).status_code == 404
    # B's people are B's: A's members, by its id, never list a B address
    members = client.get(f"/api/orgs/{a['org']}/members", headers=staff).json()["members"]
    assert members and not [m for m in members if "canary-b" in m["email"]]
    for method, kwargs in (("PUT", {"json": {"role": "client"}}), ("DELETE", {})):
        assert client.request(method, f"/api/orgs/{a['org']}/members/{b['admin']}", headers=staff, **kwargs).status_code == 404


def test_a_platform_admin_who_is_not_a_member_leaves_no_trace_among_the_members(client, world, staff_outsider, cloud_database_url):
    a, _ = world
    staff, org = _headers(staff_outsider), a["org"]
    with cloud_db.connect(cloud_database_url) as conn:
        conn.execute("UPDATE jobs SET status = 'cancelled' WHERE org_id = %s", (org,))
        before = conn.execute("SELECT count(*) AS n FROM memberships WHERE org_id = %s", (org,)).fetchone()["n"]
    # they act in the space: read, write, start a job
    assert client.post(f"/api/orgs/{org}/brands/{a['brand']}/collections", headers=staff, json={"name": "Vu par Nyra"}).status_code == 200
    assert client.post(f"/api/orgs/{org}/brands/{a['brand']}/jobs/match", headers=staff).status_code == 200
    assert client.get(f"/api/orgs/{org}/me/notifications", headers=staff).json()["monthly_email"]["eligible"] is False
    assert client.put(f"/api/orgs/{org}/me/notifications", headers=staff, json={"monthly_email": True}).status_code == 200
    members = client.get(f"/api/orgs/{org}/members", headers=staff).json()["members"]
    assert str(staff_outsider) not in [m["user_id"] for m in members] and not [m for m in members if m["you"]]
    with cloud_db.connect(cloud_database_url) as conn:
        assert conn.execute("SELECT count(*) AS n FROM memberships WHERE org_id = %s", (org,)).fetchone()["n"] == before
        assert not conn.execute("SELECT 1 FROM memberships WHERE user_id = %s", (staff_outsider,)).fetchone()
        assert not [a for a in digest.recipients(conn, org)[1] if a.endswith("@nyra.test")]  # the monthly e-mail never reaches them
    # the client's journal shows nothing of it; the back office's journal shows it, with the platform admin's address
    own = client.get(f"/api/orgs/{org}/audit", headers=_headers(a["admin"])).json()["events"]
    assert not [e for e in own if e["user_id"] == str(staff_outsider)]
    team = client.get(f"/api/staff/organizations/{org}/audit", headers=staff).json()["events"]
    assert {"job.match", "collection.create"} <= {e["action"] for e in team if e["user_id"] == str(staff_outsider)}


def test_a_platform_admin_is_named_to_nobody_as_the_author_of_a_job(client, world, staff_outsider, cloud_database_url):
    a, _ = world
    with cloud_db.connect(cloud_database_url) as conn:
        conn.execute("UPDATE jobs SET status = 'cancelled' WHERE org_id = %s", (a["org"],))
    assert client.post(f"/api/orgs/{a['org']}/brands/{a['brand']}/jobs/match", headers=_headers(staff_outsider)).status_code == 200
    refused = client.post(f"/api/orgs/{a['org']}/brands/{a['brand']}/jobs/match", headers=_headers(a["admin"]))
    assert refused.status_code == 409 and "l'équipe Nyra" in refused.json()["detail"] and "@nyra.test" not in refused.json()["detail"]


ADMIN_ONLY = {"usage", "insights", "logos"}  # what a member with the `client` role is refused (read here as the admin)


def test_nothing_the_client_reads_names_the_platform_admin_who_acted_in_its_space(client, world, staff_outsider, cloud_database_url):
    """AXE-584: wherever an author can appear (jobs, reviews, exclusions, labels, reports, brand, scans, page marks, the journal),
    a member or administrator reading afterwards finds neither the address nor the identifier of a platform admin who is no member."""
    a, _ = world
    org, brand, staff = a["org"], a["brand"], _headers(staff_outsider)
    with cloud_db.connect(cloud_database_url) as conn:
        conn.execute("UPDATE jobs SET status = 'cancelled' WHERE org_id = %s", (org,))
    base = f"/api/orgs/{org}/brands/{brand}"
    assert client.post(f"{base}/jobs/match", headers=staff).status_code == 200  # a real job and a real journal row, written by them
    assert client.post(f"{base}/collections", headers=staff, json={"name": "Vu par Nyra"}).status_code == 200
    with cloud_db.connect(cloud_database_url) as conn:  # every other place an author is kept, written as them
        conn.execute("UPDATE jobs SET created_by = %s WHERE org_id = %s", (staff_outsider, org))
        conn.execute("UPDATE excluded_hashes SET created_by = %s WHERE org_id = %s", (staff_outsider, org))
        conn.execute("UPDATE image_labels SET created_by = %s WHERE org_id = %s", (staff_outsider, org))
        conn.execute("UPDATE brands SET created_by = %s WHERE id = %s", (staff_outsider, brand))
        conn.execute("UPDATE crawl_runs SET triggered_by = %s WHERE org_id = %s", (staff_outsider, org))
        conn.execute("UPDATE reviews SET reviewed_by = %s WHERE org_id = %s", (staff_outsider, org))
        conn.execute("UPDATE page_replacements SET marked_by = %s WHERE org_id = %s", (staff_outsider, org))
        cloud_db.create_report(conn, org_id=org, brand_id=brand, within_days=30, storage_path_html="r.html", storage_path_csv="r.csv",
                               storage_path_not_found_csv="n.csv", stats={}, generated_by=staff_outsider)
    paths = [f"/api/orgs/{org}/{p}" for p in ("members", "settings", "usage", "me/notifications")] + [
        f"{base}/{p}" for p in ("sites", "insights", "overview", "jobs", "jobs/current", "scans", "library",
                                "library/export-csv", "logos", "matches", "visuals", "reports", "schedule", "site-images", "exclusions")]
    sweep = [(who, path) for who in ("client", "admin") for path in paths] + [("admin", f"/api/orgs/{org}/audit")]
    for who, path in sweep:
        answer = client.get(path, headers=_headers(a[who]))
        assert answer.status_code == (403 if who == "client" and path.rsplit("/", 1)[-1] in ADMIN_ONLY else 200), (who, path, answer.status_code)
        seen = answer.text + str(dict(answer.headers))
        assert str(staff_outsider) not in seen and "@nyra.test" not in seen, (who, path)


def test_a_platform_admin_sees_the_space_as_the_role_they_choose(client, world, staff_outsider, cloud_database_url):
    """AXE-634: the header makes the SERVER answer as an administrator or a client (real 403s), as a client read-only unless the actions
    are allowed; no header, or a value that is not a role, changes nothing; the journal says which role was borrowed."""
    a, _ = world
    org, brand = a["org"], a["brand"]
    base, usage, staff = f"/api/orgs/{org}/brands/{brand}", f"/api/orgs/{org}/usage", _headers(staff_outsider)
    as_ = lambda role, **more: {**staff, "X-Nyra-View-As": role, **more}  # noqa: E731
    with cloud_db.connect(cloud_database_url) as conn:
        conn.execute("UPDATE jobs SET status = 'cancelled' WHERE org_id = %s", (org,))
    for headers in (staff, as_("root"), as_(""), as_("admin")):  # as before: an administrator
        assert client.get(usage, headers=headers).status_code == 200
    client_view = as_("client")
    assert client.get(f"{base}/library", headers=client_view).status_code == 200  # a client reads
    assert client.get(usage, headers=client_view).status_code == 403  # ... but not what is for administrators
    refused = client.post(f"{base}/collections", headers=client_view, json={"name": "Essai"})
    assert refused.status_code == 403 and "Lecture seule" in refused.json()["detail"]  # read-only until the actions are allowed
    really = client.post(f"{base}/collections", headers=as_("client", **{"X-Nyra-View-Write": "1"}), json={"name": "Essai"})
    assert really.status_code == 403 and "administrateurs" in really.json()["detail"]  # done as a client: the route is an administrator's
    assert client.get(f"/api/staff/organizations/{org}/audit", headers=client_view).status_code == 200  # the team's own rights stay theirs
    assert client.post(f"{base}/collections", headers=as_("admin"), json={"name": "Vu en admin"}).status_code == 200
    with cloud_db.connect(cloud_database_url) as conn:
        rows = conn.execute("SELECT detail, by_staff FROM audit_log WHERE org_id = %s AND user_id = %s AND action = 'collection.create'",
                            (org, staff_outsider)).fetchall()
    assert [(row["detail"].get("view_as"), row["by_staff"]) for row in rows] == [("admin", True)]
    own = client.get(f"/api/orgs/{org}/audit", headers=_headers(a["admin"])).json()["events"]
    assert not [e for e in own if e["user_id"] == str(staff_outsider)]  # the client's journal shows nothing of the borrowed role


def test_a_header_gives_nobody_else_another_role(client, world):
    a, b = world
    usage_a, base_a = f"/api/orgs/{a['org']}/usage", f"/api/orgs/{a['org']}/brands/{a['brand']}"
    for role in ("admin", "client", "staff"):
        client_headers = {**_headers(a["client"]), "X-Nyra-View-As": role, "X-Nyra-View-Write": "1"}
        assert client.get(usage_a, headers=client_headers).status_code == 403, role  # a client stays a client
        assert client.post(f"{base_a}/collections", headers=client_headers, json={"name": "x"}).status_code == 403, role
    admin_headers = {**_headers(a["admin"]), "X-Nyra-View-As": "client"}
    assert client.get(usage_a, headers=admin_headers).status_code == 200  # an administrator stays one: the header is ignored
    assert client.post(f"{base_a}/collections", headers=admin_headers, json={"name": "Sans effet"}).status_code == 200
    for who in ("admin", "client"):  # and it opens no other organization
        stranger = {**_headers(a[who]), "X-Nyra-View-As": "admin"}
        assert client.get(f"/api/orgs/{b['org']}/usage", headers=stranger).status_code == 403
        assert client.get(f"/api/staff/organizations/{a['org']}/audit", headers=stranger).status_code == 403


def test_a_member_of_an_organization_stays_a_member_there_and_a_stranger_gets_nothing(client, world, cloud_database_url):
    a, b = world
    # a client of A (not a platform admin): A's brand under A is theirs to read, B in any way is a 403
    assert client.get(f"/api/orgs/{b['org']}/brands/{b['brand']}/library", headers=_headers(a["client"])).status_code == 403
    assert client.get(f"/api/orgs/{a['org']}/members", headers=_headers(a["client"])).status_code == 200
    assert client.get(f"/api/orgs/{a['org']}/audit", headers=_headers(a["client"])).status_code == 403
    assert client.get(f"/api/staff/organizations/open/{uuid.uuid4().hex[:8]}", headers=_headers(a["admin"])).status_code == 403


def test_a_platform_admin_opens_an_organization_by_its_slug(client, world, staff_outsider, cloud_database_url):
    a, _ = world
    with cloud_db.connect(cloud_database_url) as conn:
        slug = conn.execute("SELECT slug FROM organizations WHERE id = %s", (a["org"],)).fetchone()["slug"]
    org = client.get(f"/api/staff/organizations/open/{slug}", headers=_headers(staff_outsider)).json()["organization"]
    assert org["org_id"] == str(a["org"]) and org["role"] == "admin" and org["staff_view"] is True and org["brands"]
    assert client.get("/api/staff/organizations/open/nope-nope", headers=_headers(staff_outsider)).status_code == 404
    assert str(a["org"]) not in [o["org_id"] for o in client.get("/api/orgs", headers=_headers(staff_outsider)).json()["organizations"]]


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
    signed_b = client.post(f"/api/orgs/{b['org']}/brands/{b['brand']}/library/thumbs", headers=_headers(b["admin"]), json={"filenames": [shared]})
    assert f"/refs/{b['org']}/" in signed_b.json()["urls"][shared]
    hostile = [b["filename"], f"../../{b['org']}/{b['brand']}/{shared}", f"{b['org']}/{b['brand']}/{shared}"]
    urls = client.post(f"{base}/library/thumbs", headers=admin, json={"filenames": [shared, *hostile]}).json()["urls"]
    assert list(urls) == [shared] and f"/refs/{a['org']}/{a['brand']}/" in urls[shared] and str(b["org"]) not in urls[shared]
    original = client.get(f"{base}/library/{shared}/url", headers=admin).json()["url"]
    assert f"/refs/{a['org']}/{a['brand']}/" in original and str(b["org"]) not in original
    reports = client.get(f"{base}/reports", headers=admin).json()["reports"]
    assert reports and all(f"/reports/{a['org']}/" in url for report in reports for url in report["files"].values())
    _hostile_names_in_the_url(client, base, admin, b)


def _hostile_names_in_the_url(client, base, admin, b):
    """What each name proves. The first three reach the handler and are refused by it (its own message tells which); the
    ones with an encoded slash never match the route (`{filename}` has no slash): the catch-all answers its own 404."""
    reaches = {b["filename"]: (404, "Référence introuvable."),  # the name of B's visual: not among A's references
               "..%5C..%5Cx.jpg": (400, "Nom de fichier invalide."), "x%00.jpg": (400, "Nom de fichier invalide."),
               "notanimage.txt": (400, "Format non pris en charge : notanimage.txt")}
    for name, (status, detail) in reaches.items():
        got = client.get(f"{base}/library/{name}/url", headers=admin)
        assert (got.status_code, got.json()["detail"]) == (status, detail), name
    for name in (f"..%2F..%2F{b['org']}%2Fx.jpg", f"{b['org']}%2F{b['brand']}%2F{b['filename']}"):
        got = client.get(f"{base}/library/{name}/url", headers=admin)
        assert (got.status_code, got.json()["detail"]) == (404, "Not Found"), name  # routing, not the handler: proves only "no route"


def test_stored_paths_stay_under_the_organization(client, world, cloud_database_url, fake_storage_client):
    a, b = world
    base = f"/api/orgs/{a['org']}/brands/{a['brand']}"
    sent = client.post(f"{base}/library/upload", headers=_headers(a["admin"]),
                       files=[("files", ("../../x/up.jpg", _upload_bytes().getvalue(), "image/jpeg"))])
    assert sent.json()["saved"] == ["up.jpg"]  # the directory part of the name is dropped
    with cloud_db.connect(cloud_database_url) as conn:
        row = cloud_db.get_reference_by_filename(conn, a["brand"], "up.jpg")
    assert all(row[key].startswith(f"{a['org']}/{a['brand']}/") for key in ("storage_path", "thumb_path", "work_path"))
    refs = {path for bucket, path in fake_storage_client.store if bucket == cloud_storage.BUCKET_REFS}
    assert refs == {row["storage_path"], row["thumb_path"], row["work_path"]}
    for name in ("../x.jpg", f"../../{b['org']}/a.jpg", "a/b.jpg", "..\\..\\x.jpg", f"/{b['org']}/x.jpg", "..", ""):
        for build in (cloud_storage.ref_path, cloud_storage.ref_thumb_path, cloud_storage.ref_work_path):
            segments = build(a["org"], a["brand"], name).split("/")
            assert segments[:2] == [str(a["org"]), str(a["brand"])] and ".." not in segments and str(b["org"]) not in segments, (name, segments)
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
