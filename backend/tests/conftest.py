"""Shared fixtures. Only the cloud tests use this — the rest of the suite
(test_match.py, test_crawl.py, test_integration.py, test_units.py) needs
no external services and doesn't touch anything here.
"""

from __future__ import annotations

import importlib
import os
import uuid
from pathlib import Path

import pytest

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "supabase" / "migrations"

# Tests never read or fill the worker's real image cache (tests of the cache set their own).
os.environ.setdefault("NYRA_IMAGE_CACHE", "off")

# Minimal stand-ins for the parts of Supabase's `auth`/`storage` schemas the
# migrations reference (auth.users, auth.uid(), storage.buckets/objects).
# Every statement is guarded (IF NOT EXISTS / check-before-create) so this
# is a safe no-op against a real Supabase project's auth/storage schemas —
# but TEST_DATABASE_URL should still only ever point at a disposable local
# or CI Postgres, never a real project. This is what let us validate
# supabase/migrations/ against a real Postgres+pgvector instance by hand
# before committing them (see supabase/README.md) — the same setup, just
# automated here.
_STUB_AUTH_AND_STORAGE = """
create extension if not exists pgcrypto;
create schema if not exists auth;
create table if not exists auth.users (
    id uuid primary key default gen_random_uuid(),
    email text
);
do $$
begin
    if not exists (
        select 1 from pg_proc p
        join pg_namespace n on n.oid = p.pronamespace
        where n.nspname = 'auth' and p.proname = 'uid'
    ) then
        create function auth.uid() returns uuid
        language sql stable
        as $f$
            select nullif(current_setting('request.jwt.claims', true)::json->>'sub', '')::uuid
        $f$;
    end if;
end $$;
create schema if not exists storage;
create table if not exists storage.buckets (
    id text primary key,
    name text not null,
    public boolean not null default false
);
create table if not exists storage.objects (
    id uuid primary key default gen_random_uuid(),
    bucket_id text references storage.buckets(id),
    name text,
    owner uuid
);
alter table storage.objects add column if not exists metadata jsonb;
do $$
begin
    if not exists (
        select 1 from pg_proc p
        join pg_namespace n on n.oid = p.pronamespace
        where n.nspname = 'storage' and p.proname = 'foldername'
    ) then
        create function storage.foldername(name text) returns text[]
        language sql immutable
        as $f$ select string_to_array(name, '/') $f$;
    end if;
end $$;
"""


@pytest.fixture(scope="session")
def cloud_database_url() -> str:
    """The connection string for cloud tests, or a skip if not configured.

    Set TEST_DATABASE_URL to a disposable Postgres (with the `vector`
    extension available) to run the cloud test suite, e.g.:
        postgresql://postgres:devpassword@127.0.0.1:5432/nyra_dev
    """
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — skipping cloud tests (see backend/tests/conftest.py)")

    import psycopg

    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(_STUB_AUTH_AND_STORAGE)
        conn.execute("CREATE TABLE IF NOT EXISTS public._test_migrations (name text PRIMARY KEY)")
        applied = {row[0] for row in conn.execute("SELECT name FROM public._test_migrations")}
        for migration in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if migration.name in applied:
                continue
            conn.execute(migration.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO public._test_migrations (name) VALUES (%s)", (migration.name,))

    return url


@pytest.fixture(autouse=True)
def _leave_no_rows_behind(request):
    """The cloud tests share one database: what a test adds to `organizations` (everything of an organization goes with it, by
    cascade) and to `auth.users` is removed after it. Else a second run on the same database finds 70 organizations from the
    first, the back office lists are cut off before the test's own, and an organization made through the API (« Hennessy »)
    already exists (AXE-593)."""
    if "cloud_database_url" not in request.fixturenames:
        yield
        return
    import psycopg

    url = request.getfixturevalue("cloud_database_url")
    with psycopg.connect(url, autocommit=True) as conn:
        orgs = [row[0] for row in conn.execute("SELECT id FROM public.organizations")]
        users = [row[0] for row in conn.execute("SELECT id FROM auth.users")]
    yield
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("DELETE FROM public.organizations WHERE id <> ALL(%s)", (orgs,))
        conn.execute("DELETE FROM auth.users WHERE id <> ALL(%s)", (users,))


@pytest.fixture()
def cloud_org(cloud_database_url: str):
    """A fresh organization for one test, so tests don't collide."""
    from nyra.cloud import db as cloud_db

    with cloud_db.connect(cloud_database_url) as conn:
        org_id = cloud_db.create_organization(conn, name="Test Org", slug=f"test-{uuid.uuid4().hex[:12]}")
    return org_id


@pytest.fixture()
def cloud_brand(cloud_database_url: str, cloud_org):
    """The test organization's brand (every organization is created with one)."""
    from nyra.cloud import db as cloud_db

    with cloud_db.connect(cloud_database_url) as conn:
        return conn.execute("SELECT id FROM brands WHERE org_id = %s", (cloud_org,)).fetchone()["id"]


class _FakeBucketProxy:
    """Duck-types the subset of supabase-py's storage bucket API
    `cloud.storage` calls, backed by an in-memory dict — no network, no
    real Supabase project needed to exercise ingest/crawl/report code
    that uploads, downloads, checks existence, or signs URLs."""

    def __init__(self, store: dict, bucket: str):
        self.store = store
        self.bucket = bucket

    def upload(self, path, file, file_options=None):
        self.store[(self.bucket, path)] = file if isinstance(file, bytes) else file.read()

    def download(self, path, options=None, query_params=None):
        return self.store[(self.bucket, path)]

    def exists(self, path) -> bool:
        return (self.bucket, path) in self.store

    def remove(self, paths: list[str]):
        for p in paths:
            self.store.pop((self.bucket, p), None)
        return []

    def list(self, path=None, options=None):
        """The children of a folder like Storage lists them: files have an id, folders do not."""
        options = options or {}
        prefix, children = f"{path}/", {}
        for (bucket, name), data in self.store.items():
            if bucket == self.bucket and name.startswith(prefix):
                head, _, rest = name[len(prefix):].partition("/")
                folder, size = children.get(head, (False, 0))
                children[head] = (folder or bool(rest), len(data))
        entries = [{"name": name, "id": None if is_folder else "object", "metadata": None if is_folder else {"size": size}}
                   for name, (is_folder, size) in sorted(children.items()) if options.get("search", "") in name]
        start = options.get("offset", 0)
        return entries[start:start + options.get("limit", 100)]

    def create_signed_url(self, path, expires_in, options=None):
        return {"signedUrl": f"https://fake-storage.test/{self.bucket}/{path}?expires_in={expires_in}"}

    def create_signed_urls(self, paths, expires_in, options=None):
        return [
            {"path": p, "signedURL": f"https://fake-storage.test/{self.bucket}/{p}?expires_in={expires_in}", "error": None}
            for p in paths
        ]


class FakeSupabaseClient:
    """Storage in memory, and the Auth admin API reduced to `delete_user` (the ids asked for are kept)."""

    def __init__(self):
        self.store: dict = {}
        self.deleted_users: list[str] = []
        self.storage = self
        self.auth = self
        self.admin = self

    def delete_user(self, user_id: str) -> None:
        self.deleted_users.append(user_id)

    def from_(self, bucket: str) -> _FakeBucketProxy:
        return _FakeBucketProxy(self.store, bucket)


@pytest.fixture()
def fake_storage_client() -> FakeSupabaseClient:
    return FakeSupabaseClient()


SECRET = "test-secret-at-least-32-bytes-long-enough!!"


@pytest.fixture()
def client(cloud_database_url, fake_storage_client):
    """The cloud app on the test database and the fake Storage, called with a TestClient (sign a user with SECRET)."""
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from nyra.cloud.api import CloudSettings, create_app

    settings = CloudSettings(
        database_url=cloud_database_url, supabase_url="https://x.supabase.co",
        service_role_key="key", jwt_secret=SECRET,
    )
    settings.storage_client = lambda: fake_storage_client  # no network
    return TestClient(create_app(settings))


@pytest.fixture()
def labels_on(monkeypatch):
    """Labels are off unless NYRA_LABELS=1; this must come before `client` in a test's arguments (the app reads it when built)."""
    monkeypatch.setenv("NYRA_LABELS", "1")


@pytest.fixture(scope="session")
def require_clip() -> None:
    """torch and open_clip for the CLIP tests: ignored on a machine without them, an error when `CI` is set
    (GitHub Actions does), so that a broken install cannot turn the CLIP tests into a silent skip."""
    for name in ("torch", "open_clip"):
        if os.environ.get("CI"):
            importlib.import_module(name)
        else:
            pytest.importorskip(name)
