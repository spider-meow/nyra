"""Supabase Storage for the hosted product.

Every object path is `{org_id}/{rest}` inside one of three private buckets
(`refs`, `site-images`, `reports`), matching the prefix the Storage RLS
policies check. `path_for()` builds that prefix so callers never
hand-assemble it and risk crossing an org boundary by typo. Thumbnails
live under `{org_id}/thumbs/` in the same bucket as their original.

Signed URLs are how the browser reaches objects without going through the
backend. `signed_urls` signs a whole list in one request — never sign in a
loop, a list of 500 references would mean 500 round trips to Supabase.
"""

from __future__ import annotations

import uuid
from typing import Iterable, Optional

from supabase import Client, create_client

BUCKET_REFS = "refs"
BUCKET_SITE_IMAGES = "site-images"
BUCKET_REPORTS = "reports"

SIGNED_URL_TTL_SECONDS = 3600


def get_client(supabase_url: str, service_role_key: str) -> Client:
    """A client authenticated as the service role — bypasses Storage RLS."""
    return create_client(supabase_url, service_role_key)


def path_for(org_id: uuid.UUID, *parts: str) -> str:
    return "/".join([str(org_id), *parts])


def ref_thumb_path(org_id: uuid.UUID, filename: str) -> str:
    return path_for(org_id, "thumbs", f"{filename}.jpg")


def site_thumb_path(org_id: uuid.UUID, content_hash: str) -> str:
    return path_for(org_id, "thumbs", f"{content_hash}.jpg")


def upload(client: Client, bucket: str, path: str, data: bytes, *, content_type: Optional[str] = None) -> None:
    options = {"upsert": "true"}
    if content_type:
        options["content-type"] = content_type
    client.storage.from_(bucket).upload(path, data, file_options=options)


def download(client: Client, bucket: str, path: str) -> bytes:
    return client.storage.from_(bucket).download(path)


def exists(client: Client, bucket: str, path: str) -> bool:
    return client.storage.from_(bucket).exists(path)


def delete(client: Client, bucket: str, paths: Iterable[Optional[str]]) -> None:
    paths = [path for path in paths if path]
    if paths:
        client.storage.from_(bucket).remove(paths)


def signed_url(client: Client, bucket: str, path: str, *, expires_in: int = SIGNED_URL_TTL_SECONDS) -> str:
    result = client.storage.from_(bucket).create_signed_url(path, expires_in)
    url = result.get("signedUrl") or result.get("signedURL")
    if not url:
        raise RuntimeError(f"Supabase Storage did not return a signed URL for {bucket}/{path}")
    return url


def signed_urls(
    client: Client, bucket: str, paths: Iterable[Optional[str]], *, expires_in: int = SIGNED_URL_TTL_SECONDS
) -> dict[str, str]:
    """path -> signed URL for every distinct non-empty path, in one request.

    A path Storage doesn't know is simply missing from the result.
    """
    unique = sorted({path for path in paths if path})
    if not unique:
        return {}
    out: dict[str, str] = {}
    for start in range(0, len(unique), 1000):
        chunk = unique[start : start + 1000]
        for item in client.storage.from_(bucket).create_signed_urls(chunk, expires_in) or []:
            url = item.get("signedURL") or item.get("signedUrl")
            if url and item.get("path") and not item.get("error"):
                out[item["path"]] = url
    return out
