"""Supabase Storage — replaces local disk (`data/library/images`,
`data/site_images`, `out/` in the local product) for the multi-tenant
product.

Every object path is `{org_id}/{rest}` inside one of three buckets
(`refs`, `site-images`, `reports` — created by
`supabase/migrations/0006_storage_buckets.sql`), matching the prefix the
Storage RLS policies there check. `path_for()` builds that prefix so
callers never hand-assemble it and risk crossing an org boundary by typo.

Thin wrapper around the official `supabase-py` client rather than the
Storage HTTP API directly — the client already handles auth headers,
retries, and response parsing, and it's what a Supabase-familiar
developer would expect to find here.
"""

from __future__ import annotations

import uuid
from typing import Optional

from supabase import Client, create_client

BUCKET_REFS = "refs"
BUCKET_SITE_IMAGES = "site-images"
BUCKET_REPORTS = "reports"


def get_client(supabase_url: str, service_role_key: str) -> Client:
    """A client authenticated as the service role — bypasses Storage RLS,
    same trust model as `cloud.db.connect`'s privileged Postgres role."""
    return create_client(supabase_url, service_role_key)


def path_for(org_id: uuid.UUID, *parts: str) -> str:
    return "/".join([str(org_id), *parts])


def upload(
    client: Client,
    bucket: str,
    path: str,
    data: bytes,
    *,
    content_type: Optional[str] = None,
) -> None:
    options = {"upsert": "true"}
    if content_type:
        options["content-type"] = content_type
    client.storage.from_(bucket).upload(path, data, file_options=options)


def download(client: Client, bucket: str, path: str) -> bytes:
    return client.storage.from_(bucket).download(path)


def exists(client: Client, bucket: str, path: str) -> bool:
    return client.storage.from_(bucket).exists(path)


def delete(client: Client, bucket: str, paths: list[str]) -> None:
    if paths:
        client.storage.from_(bucket).remove(paths)


def signed_url(client: Client, bucket: str, path: str, *, expires_in: int = 3600) -> str:
    """A time-limited URL the frontend can use directly (thumbnails, report
    downloads) without proxying bytes through the backend. Buckets are
    private (see the storage migration), so this is the only way to reach
    an object from outside the backend's own service-role session."""
    result = client.storage.from_(bucket).create_signed_url(path, expires_in)
    url = result.get("signedUrl") or result.get("signedURL")
    if not url:
        raise RuntimeError(f"Supabase Storage did not return a signed URL for {bucket}/{path}")
    return url
