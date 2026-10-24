"""Erasing an organization and exporting its data (nyra/cloud/offboarding.py): against a real Postgres, the in-memory
Storage and a fake Auth admin. Nothing of another organization may be touched or exported."""

from __future__ import annotations

import csv
import io
import json
import logging
import uuid

import pytest

pytest.importorskip("psycopg")

from test_audit import _seed_matched_images  # noqa: E402
from test_cloud_api import _headers, _upload_bytes  # noqa: E402

from nyra.cloud import audit as cloud_audit  # noqa: E402
from nyra.cloud import db as cloud_db  # noqa: E402
from nyra.cloud import offboarding  # noqa: E402
from nyra.cloud import storage as cloud_storage  # noqa: E402


def _seed(conn, name: str):
    """An organization with something in every table that has an organization's data; (org_id, brand_id, user_ids)."""
    org_id = cloud_db.create_organization(conn, name=name, slug=f"{name.lower()}-{uuid.uuid4().hex[:8]}")
    brand_id = conn.execute("SELECT id FROM brands WHERE org_id = %s", (org_id,)).fetchone()["id"]
    sole, shared = uuid.uuid4(), uuid.uuid4()
    for user_id in (sole, shared):
        conn.execute("INSERT INTO auth.users (id, email) VALUES (%s, %s)", (user_id, f"{user_id}@{name.lower()}.test"))
    cloud_db.add_membership(conn, user_id=sole, org_id=org_id, role="admin")
    cloud_db.add_membership(conn, user_id=shared, org_id=org_id, role="client")
    ref, images = _seed_matched_images(conn, org_id, brand_id)
    site_id = conn.execute("SELECT id FROM sites WHERE brand_id = %s", (brand_id,)).fetchone()["id"]
    page = conn.execute("INSERT INTO pages (org_id, site_id, url) VALUES (%s, %s, 'https://t.test/p') RETURNING id", (org_id, site_id)).fetchone()["id"]
    conn.execute("INSERT INTO image_pages (image_id, page_id) VALUES (%s, %s)", (images[0], page))
    conn.execute("INSERT INTO reviews (reference_id, site_image_id, org_id, decision, reviewed_by) VALUES (%s, %s, %s, 'ecarte', %s)",
                 (ref, images[0], org_id, sole))
    conn.execute("INSERT INTO crawl_runs (org_id, site_id) VALUES (%s, %s)", (org_id, site_id))
    job = conn.execute("INSERT INTO jobs (org_id, brand_id, kind, status) VALUES (%s, %s, 'index', 'cancelled') RETURNING id",
                       (org_id, brand_id)).fetchone()["id"]
    assert job
    cloud_db.create_report(conn, org_id=org_id, brand_id=brand_id, within_days=30, storage_path_html=f"{org_id}/r/report.html",
                           storage_path_csv=f"{org_id}/r/matches.csv", storage_path_not_found_csv=f"{org_id}/r/not_found.csv",
                           stats={"n": 1}, generated_by=sole)
    cloud_db.add_exclusion(conn, org_id, brand_id, images[1], reason="Logo", created_by=sole)
    cloud_db.add_collection(conn, org_id, brand_id, None, "Été")
    label = conn.execute("INSERT INTO labels (org_id, brand_id, kind, name) VALUES (%s, %s, 'content', 'carafe') RETURNING id",
                         (org_id, brand_id)).fetchone()["id"]
    conn.execute("INSERT INTO image_labels (org_id, label_id, site_image_id, positive) VALUES (%s, %s, %s, true)", (org_id, label, images[0]))
    conn.execute("INSERT INTO reference_locations (reference_id, site_image_id, org_id, tier, inliers, ref_coverage, site_coverage) "
                 "VALUES (%s, %s, %s, 'review', 10, 0.5, 0.5)", (ref, images[0], org_id))
    conn.execute("INSERT INTO page_replacements (reference_id, page_id, org_id) VALUES (%s, %s, %s)", (ref, page, org_id))
    conn.execute("INSERT INTO brand_logos (brand_id, data_uri) VALUES (%s, 'data:image/png;base64,AA==')", (brand_id,))
    cloud_db.set_overrides(conn, org_id, {"match": {"phash_threshold": 5}}, sole)
    cloud_db.set_match_signature(conn, org_id, brand_id, "sig")
    cloud_audit.record(conn, org_id, sole, "library.upload", "reference", brand_id=brand_id, saved=1)
    return org_id, brand_id, sole, shared


def _put_files(storage, org_id, brand_id):
    for bucket, path in (("refs", f"{org_id}/{brand_id}/a.jpg"), ("refs", f"{org_id}/{brand_id}/thumbs/a.jpg.jpg"),
                         ("refs", f"{org_id}/{brand_id}/work/a.jpg.jpg"), ("site-images", f"{org_id}/work/h1.jpg"),
                         ("site-images", f"{org_id}/thumbs/h1.jpg"), ("reports", f"{org_id}/r/report.html"),
                         ("reports", f"{org_id}/r/matches.csv"), ("reports", f"{org_id}/r/not_found.csv"),
                         ("refs", f"{org_id}/logo.png"), ("site-images", f"{org_id}/h2.jpg")):
        storage.store[(bucket, path)] = f"bytes of {path}".encode()


@pytest.fixture()
def two_organizations(cloud_database_url, fake_storage_client):
    with cloud_db.connect(cloud_database_url) as conn:
        leaving, staying = _seed(conn, "Leaving"), _seed(conn, "Staying")
        conn.execute("INSERT INTO platform_staff (user_id) VALUES (%s)", (leaving[3],))  # a Nyra team member among its members
    for _, org_id, brand_id, *_ in ((None, leaving[0], leaving[1]), (None, staying[0], staying[1])):
        _put_files(fake_storage_client, org_id, brand_id)
    yield leaving, staying
    with cloud_db.connect(cloud_database_url) as conn:
        conn.execute("DELETE FROM platform_staff WHERE user_id = %s", (leaving[3],))
        conn.execute("DELETE FROM organizations WHERE id = ANY(%s)", ([leaving[0], staying[0]],))


def _slug(url, org_id):
    with cloud_db.connect(url) as conn:
        return conn.execute("SELECT slug, name FROM organizations WHERE id = %s", (org_id,)).fetchone()


def test_every_table_is_covered_by_the_erasure_and_nothing_can_block_it(cloud_database_url):
    """Read from the catalog: a table added tomorrow without a cascade (or a place in the lists) fails here."""
    with cloud_db.connect(cloud_database_url) as conn:
        tables = {row["table_name"] for row in conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' AND table_type = 'BASE TABLE'").fetchall()}
        by_org = set(offboarding._org_tables(conn))
        blocking = conn.execute(
            """SELECT conrelid::regclass::text AS t FROM pg_constraint WHERE contype = 'f' AND connamespace = 'public'::regnamespace
               AND confdeltype IN ('a', 'r')""").fetchall()
        no_cascade = [table for table in by_org if not conn.execute(
            """SELECT 1 FROM pg_constraint WHERE contype = 'f' AND conrelid = %s::regclass AND confrelid = 'organizations'::regclass
               AND confdeltype = 'c' AND conkey = ARRAY[(SELECT attnum FROM pg_attribute WHERE attrelid = %s::regclass AND attname = 'org_id')]""",
            (table, table)).fetchone()]
        indirect_cascade = [table for table in offboarding.INDIRECT if table != "organizations" and not conn.execute(
            "SELECT 1 FROM pg_constraint WHERE contype = 'f' AND conrelid = %s::regclass AND confdeltype = 'c'", (table,)).fetchone()]
    assert tables == by_org | set(offboarding.INDIRECT) | offboarding.GLOBAL, tables - by_org - set(offboarding.INDIRECT) - offboarding.GLOBAL
    assert not blocking, "a foreign key without ON DELETE CASCADE/SET NULL would stop the deletion of an organization"
    assert not no_cascade and not indirect_cascade, (no_cascade, indirect_cascade)
    assert {"audit_log", "reference_images", "reviews", "excluded_hashes", "jobs"} <= by_org


def test_dry_run_changes_nothing_and_a_wrong_name_is_refused(cloud_database_url, fake_storage_client, two_organizations):
    (org_id, *_), _ = two_organizations
    row = _slug(cloud_database_url, org_id)
    before = dict(fake_storage_client.store)
    report = offboarding.delete_organization(cloud_database_url, fake_storage_client, row["slug"], confirm_name=None, dry_run=True)
    assert report["dry_run"] and report["files"] == {"refs": 4, "site-images": 3, "reports": 3}
    assert report["rows"]["reference_images"] == 1 and report["rows"]["organizations"] == 1 and report["rows"]["image_pages"] == 1
    assert len(report["users"]) == 1  # the member of nothing else; not the one on the Nyra team
    assert fake_storage_client.store == before and fake_storage_client.deleted_users == []
    for wrong in (None, "leaving", row["name"] + " "):
        with pytest.raises(offboarding.OffboardingError, match="Confirmation refusée"):
            offboarding.delete_organization(cloud_database_url, fake_storage_client, row["slug"], confirm_name=wrong)
    with pytest.raises(offboarding.OffboardingError, match="Aucune organisation"):
        offboarding.delete_organization(cloud_database_url, fake_storage_client, "nope", confirm_name="x", dry_run=True)
    assert fake_storage_client.store == before
    with cloud_db.connect(cloud_database_url) as conn:
        conn.execute("UPDATE jobs SET status = 'running' WHERE org_id = %s", (org_id,))
    with pytest.raises(offboarding.OffboardingError, match="en cours"):
        offboarding.delete_organization(cloud_database_url, fake_storage_client, row["slug"], confirm_name=row["name"])
    assert fake_storage_client.store == before and fake_storage_client.deleted_users == []


def test_erasing_an_organization_leaves_nothing_of_it_and_everything_of_the_others(
        cloud_database_url, fake_storage_client, two_organizations, caplog, monkeypatch):
    monkeypatch.setattr(cloud_storage, "LIST_BATCH", 2)  # many pages of listing, folders among the files
    (org_id, _, sole, shared), (other_id, other_brand, *_) = two_organizations
    row = _slug(cloud_database_url, org_id)
    with cloud_db.connect(cloud_database_url) as conn:
        before = offboarding.count_rows(conn, other_id)
    assert sum(before.values()) > 20 and all(before[table] for table in ("reviews", "image_pages", "brand_logos", "audit_log"))

    with caplog.at_level(logging.INFO, logger="nyra.offboarding"):
        report = offboarding.delete_organization(cloud_database_url, fake_storage_client, row["slug"], confirm_name=row["name"], operator="arthur")

    assert report["files"] == {"refs": 4, "site-images": 3, "reports": 3} and report["users_failed"] == []
    assert report["rows"]["matches"] == 2 and sum(report["rows"].values()) > 20
    with cloud_db.connect(cloud_database_url) as conn:
        assert not any(offboarding.count_rows(conn, org_id).values())  # no row of any table, whichever carries the org_id
        assert offboarding.count_rows(conn, other_id) == before  # the other organization is untouched
        trace = conn.execute("SELECT * FROM deleted_organizations WHERE org_id = %s", (org_id,)).fetchone()
    assert (trace["name"], trace["slug"], trace["operator"]) == (row["name"], row["slug"], "arthur")
    assert "reference_images" in trace["report"]["rows"] and "@" not in json.dumps(trace["report"])
    assert any("ORGANIZATION DELETED" in record.message and str(org_id) in record.message for record in caplog.records)
    assert not [key for key in fake_storage_client.store if key[1].startswith(str(org_id))]
    assert len([key for key in fake_storage_client.store if key[1].startswith(str(other_id))]) == 10
    assert fake_storage_client.deleted_users == [str(sole)]  # not the member of the other organization, not the Nyra team member
    assert shared not in {uuid.UUID(u) for u in fake_storage_client.deleted_users}
    with cloud_db.connect(cloud_database_url) as conn:
        conn.execute("DELETE FROM auth.users WHERE id = %s", (sole,))  # what Auth really does with the account
    # run again on an erased organization: nothing to do, nothing duplicated, still the exact name asked for
    again = offboarding.delete_organization(cloud_database_url, fake_storage_client, row["slug"], confirm_name=row["name"])
    assert again["files"] == {"refs": 0, "site-images": 0, "reports": 0} and again["users"] == []
    with pytest.raises(offboarding.OffboardingError, match="Confirmation"):
        offboarding.delete_organization(cloud_database_url, fake_storage_client, row["slug"], confirm_name="x")
    with cloud_db.connect(cloud_database_url) as conn:
        assert conn.execute("SELECT count(*) AS n FROM deleted_organizations WHERE org_id = %s", (org_id,)).fetchone()["n"] == 1


def _traces(url, org_id):
    with cloud_db.connect(url) as conn:
        return (conn.execute("SELECT count(*) AS n FROM deleted_organizations WHERE org_id = %s", (org_id,)).fetchone()["n"],
                conn.execute("SELECT count(*) AS n FROM memberships WHERE org_id = %s", (org_id,)).fetchone()["n"],
                conn.execute("SELECT count(*) AS n FROM organizations WHERE id = %s", (org_id,)).fetchone()["n"])


def test_an_account_that_cannot_be_deleted_is_reported_then_deleted_by_the_next_run(cloud_database_url, fake_storage_client, two_organizations):
    (org_id, _, sole, _), _ = two_organizations
    row = _slug(cloud_database_url, org_id)

    def refuse(user_id):
        raise RuntimeError("auth down")

    fake_storage_client.delete_user = refuse
    report = offboarding.delete_organization(cloud_database_url, fake_storage_client, row["slug"], confirm_name=row["name"])
    assert [item["error"] for item in report["users_failed"]] == ["auth down"] and _traces(cloud_database_url, org_id) == (1, 0, 0)
    del fake_storage_client.delete_user  # Auth is back
    again = offboarding.delete_organization(cloud_database_url, fake_storage_client, row["slug"], confirm_name=row["name"])
    assert fake_storage_client.deleted_users == [str(sole)] and again["users_failed"] == []


def test_a_failure_at_any_step_leaves_a_closed_organization_that_the_next_run_finishes(
        cloud_database_url, fake_storage_client, two_organizations, monkeypatch):
    (org_id, *_), (other_id, *_) = two_organizations
    row = _slug(cloud_database_url, org_id)
    real_delete_prefix, real_count = cloud_storage.delete_prefix, offboarding.count_rows

    def storage_down(client, bucket, prefix, **kwargs):  # step 2 fails on the second bucket
        if bucket == "site-images":
            raise RuntimeError("storage down")
        return real_delete_prefix(client, bucket, prefix, **kwargs)

    monkeypatch.setattr(cloud_storage, "delete_prefix", storage_down)
    with pytest.raises(offboarding.OffboardingError, match="relancez") as failed:
        offboarding.delete_organization(cloud_database_url, fake_storage_client, row["slug"], confirm_name=row["name"])
    assert failed.value.report["rows"] and _traces(cloud_database_url, org_id) == (1, 0, 1)  # closed: alive, no member, one trace
    monkeypatch.setattr(cloud_storage, "delete_prefix", real_delete_prefix)

    def leftover(conn, org):  # step 3: the cascade "leaves" a row, so the whole transaction is cancelled
        return {"x": 1}

    monkeypatch.setattr(offboarding, "count_rows", leftover)
    with pytest.raises(offboarding.OffboardingError, match="Des lignes restent"):
        offboarding.delete_organization(cloud_database_url, fake_storage_client, row["slug"], confirm_name=row["name"])
    assert _traces(cloud_database_url, org_id) == (1, 0, 1)  # the organization is still whole, minus its members
    monkeypatch.setattr(offboarding, "count_rows", real_count)

    report = offboarding.delete_organization(cloud_database_url, fake_storage_client, row["slug"], confirm_name=row["name"])
    assert _traces(cloud_database_url, org_id) == (1, 0, 0) and report["users_failed"] == []
    assert not [key for key in fake_storage_client.store if key[1].startswith(str(org_id))]
    with cloud_db.connect(cloud_database_url) as conn:
        assert conn.execute("SELECT count(*) AS n FROM reference_images WHERE org_id = %s", (other_id,)).fetchone()["n"] == 1


def test_during_the_erasure_nobody_can_upload_and_an_account_that_joined_elsewhere_is_kept(
        cloud_database_url, fake_storage_client, two_organizations, monkeypatch, client):
    (org_id, brand_id, sole, _), (other_id, *_) = two_organizations
    row = _slug(cloud_database_url, org_id)
    real, seen = cloud_storage.delete_prefix, {}

    def hooked(storage, bucket, prefix, **kwargs):
        if not seen and not kwargs.get("dry_run"):  # between the closing and the files: someone tries, someone joins
            seen["upload"] = client.post(f"/api/orgs/{org_id}/brands/{brand_id}/library/upload", headers=_headers(sole),
                                         files=[("files", ("late.jpg", _upload_bytes(), "image/jpeg"))]).status_code
            with cloud_db.connect(cloud_database_url) as conn:
                cloud_db.add_membership(conn, user_id=sole, org_id=other_id, role="client")
        return real(storage, bucket, prefix, **kwargs)

    monkeypatch.setattr(cloud_storage, "delete_prefix", hooked)
    report = offboarding.delete_organization(cloud_database_url, fake_storage_client, row["slug"], confirm_name=row["name"])
    assert seen["upload"] == 403 and fake_storage_client.deleted_users == [] and report["users"] == []
    assert not [key for key in fake_storage_client.store if key[1].startswith(str(org_id))]


def test_only_the_exact_slug_and_a_plain_operator_name_are_accepted(cloud_database_url, fake_storage_client, two_organizations):
    (org_id, *_), _ = two_organizations
    row = _slug(cloud_database_url, org_id)
    for slug in (row["slug"].upper(), row["slug"] + "-", row["slug"] + "!!"):
        with pytest.raises(offboarding.OffboardingError, match="exact"):
            offboarding.delete_organization(cloud_database_url, fake_storage_client, slug, confirm_name=row["name"], dry_run=True)
    for operator in ("arthur@axel.fr", "arthur reichard", "x" * 41):
        with pytest.raises(offboarding.OffboardingError, match="identifiant court"):
            offboarding.delete_organization(cloud_database_url, fake_storage_client, row["slug"], confirm_name=row["name"], operator=operator)


def test_a_prefix_that_will_not_empty_is_an_error(fake_storage_client, monkeypatch):
    fake_storage_client.store[("refs", "org/a.jpg")] = b"x"
    bucket = fake_storage_client.from_("refs")
    monkeypatch.setattr(type(bucket), "remove", lambda self, paths: [])  # Storage that accepts the call and removes nothing
    with pytest.raises(RuntimeError, match="not empty"):
        cloud_storage.delete_prefix(fake_storage_client, "refs", "org")


def test_the_export_holds_the_organization_and_nothing_else(cloud_database_url, fake_storage_client, two_organizations, tmp_path):
    (org_id, brand_id, *_), (other_id, *_) = two_organizations
    row = _slug(cloud_database_url, org_id)
    manifest = offboarding.export_organization(cloud_database_url, fake_storage_client, row["slug"], tmp_path, operator="arthur")

    assert manifest["complete"] and manifest["organization"]["id"] == str(org_id) and manifest["files"]["copied"] == 4
    assert manifest["files"]["failures"] == [] and json.loads((tmp_path / "manifeste.json").read_text()) == manifest
    assert set(manifest["tables"]) == {"marques.csv", "sites.csv", "references.csv", "decisions.csv", "exclusions.csv",
                                       "rapports.csv", "membres.csv", "journal.csv"}
    read = lambda name: list(csv.reader(io.StringIO((tmp_path / name).read_text(encoding="utf-8-sig"))))  # noqa: E731
    references = read("references.csv")
    assert references[1][1] == "logo.png" and references[1][9] == f"files/refs/{org_id}/logo.png" and len(references) == 2
    assert [r[1] for r in read("decisions.csv")[1:]] == ["logo.png"] and read("decisions.csv")[1][3] == "ecarte"
    assert read("exclusions.csv")[1][2] == "Logo" and len(read("membres.csv")) == 3
    journal = read("journal.csv")
    assert {line[3] for line in journal[1:]} == {"library.upload", "organization.export"}  # the export is in the log it hands over
    # the files: the reference original and the reports, under the paths the CSV names; no thumbnail, no other organization
    copied = sorted(str(p.relative_to(tmp_path)) for p in (tmp_path / "files").rglob("*") if p.is_file())
    assert copied == sorted([f"files/reports/{org_id}/r/report.html", f"files/reports/{org_id}/r/matches.csv",
                             f"files/reports/{org_id}/r/not_found.csv", f"files/refs/{org_id}/logo.png"])
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert str(other_id) not in path.read_text(encoding="utf-8", errors="ignore").replace(str(org_id), ""), path


def test_an_interrupted_export_resumes_and_a_missing_file_is_reported(cloud_database_url, fake_storage_client, two_organizations, tmp_path):
    (org_id, brand_id, *_), _ = two_organizations
    row = _slug(cloud_database_url, org_id)
    del fake_storage_client.store[("reports", f"{org_id}/r/matches.csv")]
    first = offboarding.export_organization(cloud_database_url, fake_storage_client, row["slug"], tmp_path)
    assert not first["complete"] and [f["path"] for f in first["files"]["failures"]] == [f"{org_id}/r/matches.csv"]
    assert first["files"]["copied"] == 3
    fake_storage_client.store[("reports", f"{org_id}/r/matches.csv")] = b"back"
    second = offboarding.export_organization(cloud_database_url, fake_storage_client, row["slug"], tmp_path)
    assert second["complete"] and second["files"]["copied"] == 1 and second["files"]["already_there"] == 3
    # a file bigger than the limit, and a total over the ceiling, are errors too
    other = tmp_path / "small"
    limited = offboarding.export_organization(cloud_database_url, fake_storage_client, row["slug"], other, max_file_bytes=3)
    assert not limited["complete"] and limited["files"]["copied"] == 0 and len(limited["files"]["failures"]) == 4
    assert "au-delà" in limited["files"]["failures"][0]["error"]
    with pytest.raises(offboarding.OffboardingError, match="Plafond"):
        offboarding.export_organization(cloud_database_url, fake_storage_client, row["slug"], tmp_path / "capped", max_total_bytes=10)


def test_a_file_over_the_limit_is_never_downloaded_and_names_differing_by_case_are_reported(
        cloud_database_url, fake_storage_client, two_organizations, tmp_path, monkeypatch):
    (org_id, *_), _ = two_organizations
    row = _slug(cloud_database_url, org_id)
    downloaded = []
    real = cloud_storage.download
    monkeypatch.setattr(cloud_storage, "download", lambda c, b, p: downloaded.append(p) or real(c, b, p))
    offboarding.export_organization(cloud_database_url, fake_storage_client, row["slug"], tmp_path / "small", max_file_bytes=3)
    assert downloaded == []  # the size comes from Storage's listing, before any download
    with cloud_db.connect(cloud_database_url) as conn:
        conn.execute("UPDATE reports SET storage_path_csv = %s, storage_path_not_found_csv = %s WHERE org_id = %s",
                     (f"{org_id}/r/MATCHES.csv", f"{org_id}/r/matches.csv", org_id))
    fake_storage_client.store[("reports", f"{org_id}/r/MATCHES.csv")] = b"upper"
    manifest = offboarding.export_organization(cloud_database_url, fake_storage_client, row["slug"], tmp_path / "case")
    assert not manifest["complete"] and any("casse" in f["error"] for f in manifest["files"]["failures"])
    assert "logos des marques" in " ".join(manifest["not_exported"])
