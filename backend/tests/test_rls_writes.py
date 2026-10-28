"""What an organization admin or client can write straight into Postgres, as the `authenticated` role with a JWT subject (what PostgREST does).

`test_isolation.py` goes through the backend's routes, which use the service role and never meet the RLS policies. This one does:
each attempt runs in a savepoint, as the role, and says whether the write went through. The audit of the write policies (AXE-579) is
this table: `DENIED` rows must stay denied; `ACCEPTED` rows are known and intended; `GAPS` are the holes found, expected to fail
(`xfail`, strict) until the migration of AXE-625 closes them, and then the test says so.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

import pytest

pytest.importorskip("psycopg")
import psycopg  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402

from nyra.cloud import db as cloud_db  # noqa: E402


@dataclass
class World:
    conn: psycopg.Connection
    admin: uuid.UUID
    client: uuid.UUID
    stranger: uuid.UUID
    org_a: uuid.UUID
    org_b: uuid.UUID
    brand_a: uuid.UUID
    brand_b: uuid.UUID
    ref: uuid.UUID
    site: uuid.UUID
    image: uuid.UUID

    def allowed(self, user: uuid.UUID, sql: str, params: Any = ()) -> bool:
        """Whether this write went through for `user` (an error, or no row seen by RLS, is a refusal). Nothing stays."""
        self.conn.execute("SAVEPOINT attempt")
        try:
            self.conn.execute("SET LOCAL ROLE authenticated")
            self.conn.execute("SELECT set_config('request.jwt.claims', %s, true)", (json.dumps({"sub": str(user), "role": "authenticated"}),))
            return self.conn.execute(sql, params).rowcount != 0
        except psycopg.Error:
            return False
        finally:
            self.conn.execute("ROLLBACK TO SAVEPOINT attempt")
            self.conn.execute("RESET ROLE")


@pytest.fixture()
def world(cloud_database_url):
    """Two organizations, an admin and a client of the first, an admin of the second, a stranger; rolled back at the end."""
    conn = psycopg.connect(cloud_database_url, row_factory=dict_row)
    conn.execute("DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'authenticated') THEN CREATE ROLE authenticated NOLOGIN; END IF; END $$")
    admin, client, admin_b, stranger = (uuid.uuid4() for _ in range(4))
    for user in (admin, client, admin_b, stranger):
        conn.execute("INSERT INTO auth.users (id, email) VALUES (%s, %s)", (user, f"{user}@rls.test"))
    org_a = cloud_db.create_organization(conn, name="A", slug=f"a-{uuid.uuid4().hex[:8]}")
    org_b = cloud_db.create_organization(conn, name="B", slug=f"b-{uuid.uuid4().hex[:8]}")
    cloud_db.add_membership(conn, user_id=admin, org_id=org_a, role="admin")
    cloud_db.add_membership(conn, user_id=client, org_id=org_a, role="client")
    cloud_db.add_membership(conn, user_id=admin_b, org_id=org_b, role="admin")
    brand_a = conn.execute("SELECT id FROM brands WHERE org_id = %s", (org_a,)).fetchone()["id"]
    brand_b = conn.execute("SELECT id FROM brands WHERE org_id = %s", (org_b,)).fetchone()["id"]
    ref = conn.execute("INSERT INTO reference_images (org_id, brand_id, filename, storage_path) VALUES (%s, %s, 'a.jpg', %s) RETURNING id",
                       (org_a, brand_a, f"{org_a}/{brand_a}/a.jpg")).fetchone()["id"]
    site = conn.execute("INSERT INTO sites (org_id, brand_id, url) VALUES (%s, %s, 'https://a.test/') RETURNING id", (org_a, brand_a)).fetchone()["id"]
    image = conn.execute("INSERT INTO site_images (org_id, site_id, url) VALUES (%s, %s, 'https://a.test/i.jpg') RETURNING id", (org_a, site)).fetchone()["id"]
    # What Supabase gives `authenticated` by default on every public table: the policies are then all that is left.
    conn.execute("GRANT USAGE ON SCHEMA public TO authenticated")
    conn.execute("GRANT ALL ON ALL TABLES IN SCHEMA public TO authenticated")
    yield World(conn, admin, client, stranger, org_a, org_b, brand_a, brand_b, ref, site, image)
    conn.rollback()
    conn.close()


# (label, who, SQL, parameters from the world). `who` is a World attribute.
DENIED = [
    ("a member of another organization cannot add a member here", "admin", "INSERT INTO memberships (user_id, org_id, role) VALUES (%(stranger)s, %(org_b)s, 'client')"),
    ("an admin cannot move a membership to another organization", "admin", "UPDATE memberships SET org_id = %(org_b)s WHERE user_id = %(client)s"),
    ("an admin cannot set what only the Nyra team sets (monthly e-mail)", "admin", "UPDATE memberships SET monthly_email_allowed = false WHERE user_id = %(client)s"),
    ("a client cannot promote themselves", "client", "UPDATE memberships SET role = 'admin' WHERE user_id = %(client)s"),
    ("an admin cannot rename their organization", "admin", "UPDATE organizations SET name = 'x' WHERE id = %(org_a)s"),
    ("an admin cannot change their quotas", "admin", "UPDATE organizations SET quota_overrides = '{\"brands\": 9999}' WHERE id = %(org_a)s"),
    ("an admin cannot create an organization", "admin", "INSERT INTO organizations (name, slug) VALUES ('z', 'z-rls')"),
    ("an admin cannot move a brand to another organization", "admin", "UPDATE brands SET org_id = %(org_b)s WHERE id = %(brand_a)s"),
    ("an admin cannot give a reference another organization's brand", "admin", "UPDATE reference_images SET brand_id = %(brand_b)s WHERE id = %(ref)s"),
    ("a client cannot add a site", "client", "INSERT INTO sites (org_id, brand_id, url) VALUES (%(org_a)s, %(brand_a)s, 'https://c.test/')"),
    ("a client cannot edit a reference", "client", "UPDATE reference_images SET credit = 'x' WHERE id = %(ref)s"),
    ("a stranger cannot add a brand to the organization", "stranger", "INSERT INTO brands (org_id, name, slug) VALUES (%(org_a)s, 'e', 'e-rls')"),
    ("a client cannot write a review in another organization", "client", "INSERT INTO reviews (reference_id, site_image_id, org_id, decision) VALUES (%(ref)s, %(image)s, %(org_b)s, 'ecarte')"),
    ("nobody writes jobs", "admin", "INSERT INTO jobs (org_id, brand_id, kind) VALUES (%(org_a)s, %(brand_a)s, 'crawl')"),
    ("nobody writes collections", "admin", "INSERT INTO collections (org_id, brand_id, name) VALUES (%(org_a)s, %(brand_a)s, 'c')"),
    ("nobody writes the audit log", "admin", "INSERT INTO audit_log (org_id, action, object_type) VALUES (%(org_a)s, 'x', 'y')"),
    ("nobody makes themselves a platform admin", "admin", "INSERT INTO platform_staff (user_id) VALUES (%(admin)s)"),
]

# Known and intended: the backend lets an admin do the same through its routes.
ACCEPTED = [
    ("an admin promotes a client (as the Members page does)", "admin", "UPDATE memberships SET role = 'admin' WHERE user_id = %(client)s"),
    ("an admin removes a co-member (as the Members page does)", "admin", "DELETE FROM memberships WHERE user_id = %(client)s AND org_id = %(org_a)s"),
    ("an admin writes the settings: read back through `validate_overrides`, which keeps the whitelist and the caps", "admin",
     "INSERT INTO org_settings (org_id, overrides) VALUES (%(org_a)s, '{\"crawl\": {\"max_pages\": 100000000}}')"),
    ("an admin adds an address: the crawler refuses internal ones (`netguard`) when it reads", "admin",
     "INSERT INTO sites (org_id, brand_id, url) VALUES (%(org_a)s, %(brand_a)s, 'http://169.254.169.254/')"),
]

# The holes found by the audit. Each fails the day the migration of AXE-625 closes it (strict xfail: then remove the mark).
GAPS = [
    ("AXE-622", "an admin points a reference at another organization's file", "admin",
     "UPDATE reference_images SET storage_path = %(other_path)s, thumb_path = %(other_path)s WHERE id = %(ref)s"),
    ("AXE-622", "an admin points a site image at another organization's file", "admin",
     "UPDATE site_images SET storage_path = %(other_path)s, thumb_path = %(other_path)s WHERE id = %(image)s"),
    ("AXE-623", "an admin creates a brand beyond the quota", "admin", "INSERT INTO brands (org_id, name, slug) VALUES (%(org_a)s, 'extra', 'extra-rls')"),
    ("AXE-623", "an admin adds a reference beyond the quota", "admin",
     "INSERT INTO reference_images (org_id, brand_id, filename, storage_path) VALUES (%(org_a)s, %(brand_a)s, 'q.jpg', 'x/q.jpg')"),
    ("AXE-623", "an admin adds any existing account as a member, without an invitation", "admin",
     "INSERT INTO memberships (user_id, org_id, role) VALUES (%(stranger)s, %(org_a)s, 'admin')"),
    ("AXE-624", "an admin invents a match", "admin",
     "INSERT INTO matches (org_id, reference_id, site_image_id, level, score, confidence) VALUES (%(org_a)s, %(ref)s, %(image)s, 'phash', 1.0, 'haut')"),
    ("AXE-624", "an admin invents a read of a site", "admin",
     "INSERT INTO crawl_runs (org_id, site_id, status, started_at, finished_at, pages_visited) VALUES (%(org_a)s, %(site)s, 'done', now(), now(), 999999)"),
    ("AXE-624", "a client signs a review with another member's name", "client",
     "INSERT INTO reviews (reference_id, site_image_id, org_id, decision, reviewed_by) VALUES (%(ref)s, %(image)s, %(org_a)s, 'ecarte', %(admin)s)"),
]


def _run(world: World, who: str, sql: str) -> bool:
    values = {"admin": world.admin, "client": world.client, "stranger": world.stranger, "org_a": world.org_a, "org_b": world.org_b,
              "brand_a": world.brand_a, "brand_b": world.brand_b, "ref": world.ref, "site": world.site, "image": world.image,
              "other_path": f"{world.org_b}/{world.brand_b}/secret.jpg"}
    return world.allowed(getattr(world, who), sql, values)


@pytest.mark.parametrize("label,who,sql", DENIED, ids=[row[0] for row in DENIED])
def test_these_writes_stay_denied(world, label, who, sql):
    assert not _run(world, who, sql), label


@pytest.mark.parametrize("label,who,sql", ACCEPTED, ids=[row[0] for row in ACCEPTED])
def test_these_writes_are_known_and_intended(world, label, who, sql):
    assert _run(world, who, sql), label


@pytest.mark.parametrize("issue,label,who,sql", GAPS, ids=[f"{row[0]} {row[1]}" for row in GAPS])
def test_these_writes_must_be_denied_once_the_write_policies_are_gone(world, request, issue, label, who, sql):
    request.applymarker(pytest.mark.xfail(strict=True, reason=f"{issue}: closed by the migration of AXE-625"))
    assert not _run(world, who, sql), label


def test_a_member_reads_the_organization_and_a_stranger_does_not(world):
    """The reads stay: only the writes are in question."""
    assert world.allowed(world.client, "SELECT 1 FROM reference_images WHERE id = %(ref)s", {"ref": world.ref})
    assert not world.allowed(world.stranger, "SELECT 1 FROM reference_images WHERE id = %(ref)s", {"ref": world.ref})
