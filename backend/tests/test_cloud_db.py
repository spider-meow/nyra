"""backend/nyra/cloud/db.py against a real Postgres.

Skips entirely unless TEST_DATABASE_URL is set (see conftest.py) — the
rest of the suite stays hermetic by default. Point it at a disposable
local Postgres with the `vector` extension available, e.g.:

    createdb nyra_test
    psql nyra_test -c "create extension vector;"
    TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:5432/nyra_test pytest backend/tests/test_cloud_db.py
"""

from __future__ import annotations

import uuid

import numpy as np
import pytest

pytest.importorskip("psycopg")

from nyra.cloud import db as cloud_db


def test_normalize_database_url_encodes_reserved_characters_in_password():
    raw = "postgresql://postgres.abc:p@ss word@aws-0-eu.pooler.supabase.com:5432/postgres"
    assert cloud_db.normalize_database_url(raw) == (
        "postgresql://postgres.abc:p%40ss%20word@aws-0-eu.pooler.supabase.com:5432/postgres"
    )


def test_slugify_folds_accents_and_separators():
    assert cloud_db.slugify("Rémy Martin") == "remy-martin"
    assert cloud_db.slugify("  remy--martin  ") == "remy-martin"
    assert cloud_db.slugify("Client_Name") == "client-name"


def test_slugify_rejects_a_value_with_no_letters_or_digits():
    with pytest.raises(ValueError):
        cloud_db.slugify("---")


def test_normalize_database_url_leaves_a_plain_password():
    raw = "postgresql://postgres.abc:secret@aws-0-eu.pooler.supabase.com:5432/postgres"
    assert cloud_db.normalize_database_url(raw) == raw


def test_reference_image_upsert_and_embedding_roundtrip(cloud_database_url, cloud_org):
    embedding = np.random.rand(512).astype(np.float32)
    with cloud_db.connect(cloud_database_url) as conn:
        cloud_db.upsert_reference_image(
            conn, org_id=cloud_org, filename="a.jpg", storage_path="refs/a.jpg",
            expiry_date="2026-12-01", credit="Credit", notes="Notes",
            phash="ffff0000ffff0000", dhash="ffff0000ffff0000", embedding=embedding,
            width=100, height=100,
        )
        refs = cloud_db.get_reference_images(conn, cloud_org)

    assert len(refs) == 1
    assert refs[0]["filename"] == "a.jpg"
    assert refs[0]["credit"] == "Credit"
    roundtrip = refs[0]["embedding"].to_numpy()
    assert np.allclose(roundtrip, embedding, atol=1e-5)


def test_reference_image_upsert_is_idempotent_on_filename(cloud_database_url, cloud_org):
    with cloud_db.connect(cloud_database_url) as conn:
        id1 = cloud_db.upsert_reference_image(
            conn, org_id=cloud_org, filename="a.jpg", storage_path="refs/a.jpg",
            expiry_date="2026-01-01", credit=None, notes=None,
        )
        id2 = cloud_db.upsert_reference_image(
            conn, org_id=cloud_org, filename="a.jpg", storage_path="refs/a.jpg",
            expiry_date="2027-01-01", credit=None, notes=None,
        )
        refs = cloud_db.get_reference_images(conn, cloud_org)

    assert id1 == id2
    assert len(refs) == 1
    assert refs[0]["expiry_date"].isoformat() == "2027-01-01"


def test_delete_reference_image(cloud_database_url, cloud_org):
    with cloud_db.connect(cloud_database_url) as conn:
        cloud_db.upsert_reference_image(
            conn, org_id=cloud_org, filename="a.jpg", storage_path="refs/a.jpg",
            expiry_date=None, credit=None, notes=None,
        )
        cloud_db.delete_reference_image(conn, org_id=cloud_org, filename="a.jpg")
        refs = cloud_db.get_reference_images(conn, cloud_org)
    assert refs == []


def test_site_image_upsert_reports_is_new_only_once(cloud_database_url, cloud_org):
    with cloud_db.connect(cloud_database_url) as conn:
        site_id = cloud_db.upsert_site(conn, org_id=cloud_org, url="https://example.com")
        id1, is_new1 = cloud_db.upsert_site_image(
            conn, org_id=cloud_org, site_id=site_id, url="https://example.com/a.jpg",
            phash="aaaa", dhash="aaaa",
        )
        id2, is_new2 = cloud_db.upsert_site_image(
            conn, org_id=cloud_org, site_id=site_id, url="https://example.com/a.jpg",
            phash="aaaa", dhash="aaaa",
        )

    assert id1 == id2
    assert is_new1 is True
    assert is_new2 is False


def test_pages_and_image_pages_linkage(cloud_database_url, cloud_org):
    with cloud_db.connect(cloud_database_url) as conn:
        site_id = cloud_db.upsert_site(conn, org_id=cloud_org, url="https://example.com")
        page_id = cloud_db.upsert_page(conn, org_id=cloud_org, site_id=site_id, url="https://example.com/p1")
        cloud_db.mark_page_crawled(conn, page_id, http_status=200)
        assert cloud_db.get_crawled_urls(conn, site_id) == {"https://example.com/p1"}

        image_id, _ = cloud_db.upsert_site_image(
            conn, org_id=cloud_org, site_id=site_id, url="https://example.com/i.jpg"
        )
        cloud_db.link_image_page(conn, image_id, page_id)
        assert cloud_db.get_pages_for_image(conn, image_id) == ["https://example.com/p1"]


def test_matches_unmatched_and_reviews(cloud_database_url, cloud_org):
    with cloud_db.connect(cloud_database_url) as conn:
        site_id = cloud_db.upsert_site(conn, org_id=cloud_org, url="https://example.com")
        matched_ref = cloud_db.upsert_reference_image(
            conn, org_id=cloud_org, filename="matched.jpg", storage_path="x", expiry_date=None, credit=None, notes=None,
        )
        unmatched_ref = cloud_db.upsert_reference_image(
            conn, org_id=cloud_org, filename="unmatched.jpg", storage_path="x", expiry_date=None, credit=None, notes=None,
        )
        site_img_id, _ = cloud_db.upsert_site_image(
            conn, org_id=cloud_org, site_id=site_id, url="https://example.com/i.jpg"
        )
        cloud_db.write_matches(conn, cloud_org, [(matched_ref, site_img_id, "phash", 1.0, "haut")])
        cloud_db.stamp_compared(conn, [matched_ref, unmatched_ref], [site_img_id])

        matches = cloud_db.get_matches(conn, cloud_org)
        unmatched = cloud_db.get_unmatched_references(conn, cloud_org)

    assert len(matches) == 1
    assert matches[0]["filename"] == "matched.jpg"
    assert matches[0]["decision"] is None
    assert len(unmatched) == 1
    assert unmatched[0]["filename"] == "unmatched.jpg"
    assert unmatched[0]["compared_at"] is not None  # stamped, just found nothing

    reviewer = uuid.uuid4()
    with cloud_db.connect(cloud_database_url) as conn:
        conn.execute("INSERT INTO auth.users (id) VALUES (%s)", (reviewer,))
        cloud_db.set_reviews(
            conn, org_id=cloud_org, reference_id=matched_ref, site_image_ids=[site_img_id],
            decision="retenu", reviewed_by=reviewer,
        )
        matches_after = cloud_db.get_matches(conn, cloud_org)
    assert matches_after[0]["decision"] == "retenu"


def test_match_signature_roundtrip(cloud_database_url, cloud_org):
    with cloud_db.connect(cloud_database_url) as conn:
        assert cloud_db.get_match_signature(conn, cloud_org) is None
        cloud_db.set_match_signature(conn, cloud_org, "sig-1")
        assert cloud_db.get_match_signature(conn, cloud_org) == "sig-1"
        cloud_db.set_match_signature(conn, cloud_org, "sig-2")
        assert cloud_db.get_match_signature(conn, cloud_org) == "sig-2"


def test_crawl_run_lifecycle(cloud_database_url, cloud_org):
    with cloud_db.connect(cloud_database_url) as conn:
        site_id = cloud_db.upsert_site(conn, org_id=cloud_org, url="https://example.com")
        run_id = cloud_db.start_crawl_run(conn, org_id=cloud_org, site_id=site_id, triggered_by=None)
        cloud_db.update_crawl_run_progress(
            conn, run_id, pages_visited=3, images_found=5, images_stored=4, images_new=2, blocked_by_robots=1
        )
        cloud_db.finish_crawl_run(conn, run_id, status="done", errors=["https://x: timeout"])
        run = cloud_db.get_crawl_run(conn, cloud_org, run_id)
        latest = cloud_db.get_latest_crawl_run(conn, cloud_org)

    assert run["status"] == "done"
    assert run["pages_visited"] == 3
    assert run["blocked_by_robots"] == 1
    assert run["errors"] == ["https://x: timeout"]
    assert latest["id"] == run_id


def test_reports_lifecycle(cloud_database_url, cloud_org):
    with cloud_db.connect(cloud_database_url) as conn:
        report_id = cloud_db.create_report(
            conn, org_id=cloud_org, within_days=90, storage_path_html="r.html",
            storage_path_csv="m.csv", storage_path_not_found_csv="nf.csv",
            stats={"reference_images": 3}, generated_by=None,
        )
        reports = cloud_db.list_reports(conn, cloud_org)
        report = cloud_db.get_report(conn, cloud_org, report_id)

    assert len(reports) == 1
    assert report["stats"] == {"reference_images": 3}


def test_stats(cloud_database_url, cloud_org):
    with cloud_db.connect(cloud_database_url) as conn:
        site_id = cloud_db.upsert_site(conn, org_id=cloud_org, url="https://example.com")
        cloud_db.upsert_reference_image(
            conn, org_id=cloud_org, filename="a.jpg", storage_path="x", expiry_date=None, credit=None, notes=None,
        )
        page_id = cloud_db.upsert_page(conn, org_id=cloud_org, site_id=site_id, url="https://example.com/p1")
        cloud_db.mark_page_crawled(conn, page_id, http_status=200)
        cloud_db.upsert_site_image(conn, org_id=cloud_org, site_id=site_id, url="https://example.com/i.jpg")

        stats = cloud_db.get_stats(conn, cloud_org)

    assert stats.reference_images == 1
    assert stats.pages_crawled == 1
    assert stats.site_images == 1
    assert stats.matches == 0


def test_memberships(cloud_database_url, cloud_org):
    user_id = uuid.uuid4()
    with cloud_db.connect(cloud_database_url) as conn:
        conn.execute("INSERT INTO auth.users (id, email) VALUES (%s, %s)", (user_id, "a@b.com"))
        cloud_db.add_membership(conn, user_id=user_id, org_id=cloud_org, role="admin")
        membership = cloud_db.get_membership(conn, user_id=user_id, org_id=cloud_org)
        memberships = cloud_db.list_memberships_for_user(conn, user_id)

        # role changes on re-add (upsert), not a duplicate row
        cloud_db.add_membership(conn, user_id=user_id, org_id=cloud_org, role="client")
        updated = cloud_db.get_membership(conn, user_id=user_id, org_id=cloud_org)

    assert membership["role"] == "admin"
    assert len(memberships) == 1
    assert updated["role"] == "client"


def test_ping(cloud_database_url):
    assert cloud_db.ping(cloud_database_url) is True
