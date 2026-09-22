"""Shared fixtures. Only the cloud tests use this — the rest of the suite
(test_match.py, test_crawl.py, test_integration.py, test_webapp.py) needs
no external services and doesn't touch anything here.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "supabase" / "migrations"

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
        already_migrated = conn.execute(
            "SELECT to_regclass('public.organizations') IS NOT NULL"
        ).fetchone()[0]
        if not already_migrated:
            conn.execute(_STUB_AUTH_AND_STORAGE)
            for migration in sorted(MIGRATIONS_DIR.glob("*.sql")):
                conn.execute(migration.read_text(encoding="utf-8"))

    return url


@pytest.fixture()
def cloud_org(cloud_database_url: str):
    """A fresh organization for one test, so tests don't collide."""
    from nyra.cloud import db as cloud_db

    with cloud_db.connect(cloud_database_url) as conn:
        org_id = cloud_db.create_organization(conn, name="Test Org", slug=f"test-{uuid.uuid4().hex[:12]}")
    return org_id


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

    def create_signed_url(self, path, expires_in, options=None):
        return {"signedUrl": f"https://fake-storage.test/{self.bucket}/{path}?expires_in={expires_in}"}


class FakeSupabaseClient:
    def __init__(self):
        self.store: dict = {}
        self.storage = self

    def from_(self, bucket: str) -> _FakeBucketProxy:
        return _FakeBucketProxy(self.store, bucket)


@pytest.fixture()
def fake_storage_client() -> FakeSupabaseClient:
    return FakeSupabaseClient()
