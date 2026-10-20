"""Supabase Storage for the hosted product.

Every object path is `{org_id}/{rest}` inside one of three private buckets
(`refs`, `site-images`, `reports`), matching the prefix the Storage RLS
policies check. `path_for()` builds that prefix so callers never
hand-assemble it and risk crossing an org boundary by typo. References
live under `{org_id}/{brand_id}/` (a filename is unique per brand, not per
organization), their thumbnails under `{org_id}/{brand_id}/thumbs/`.
Crawled images are shared by the whole organization, keyed by content
hash, thumbnails under `{org_id}/thumbs/`. References uploaded before
brands existed keep their `{org_id}/...` paths; the stored path is what
counts.

Signed URLs are how the browser reaches objects without going through the
backend. `signed_urls` signs a whole list in one request — never sign in a
loop, a list of 500 references would mean 500 round trips to Supabase.
"""

from __future__ import annotations

import hashlib
import os
import re
import threading
import time
import unicodedata
import uuid
from pathlib import Path
from typing import Iterable, Optional

import httpx
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


_KEY_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_key(filename: str) -> str:
    """A Storage object name for a file name.

    Supabase Storage refuses keys with accents and many punctuation marks
    ("Invalid key"). The file name itself is kept as is in the database;
    only the object name is folded to ASCII, with a short hash of the
    original so "é.jpg" and "e.jpg" don't land on the same object.
    """
    folded = unicodedata.normalize("NFKD", filename).encode("ascii", "ignore").decode("ascii")
    key = _KEY_UNSAFE.sub("_", folded).strip("._") or "image"
    if key == filename:
        return key
    stem, dot, suffix = key.rpartition(".")
    digest = hashlib.sha1(filename.encode("utf-8")).hexdigest()[:8]
    return f"{stem}-{digest}.{suffix}" if dot and stem else f"{key}-{digest}"


def ref_path(org_id: uuid.UUID, brand_id: uuid.UUID, filename: str) -> str:
    return path_for(org_id, str(brand_id), safe_key(filename))


def ref_thumb_path(org_id: uuid.UUID, brand_id: uuid.UUID, filename: str) -> str:
    return path_for(org_id, str(brand_id), "thumbs", f"{safe_key(filename)}.jpg")


def ref_work_path(org_id: uuid.UUID, brand_id: uuid.UUID, filename: str) -> str:
    return path_for(org_id, str(brand_id), "work", f"{safe_key(filename)}.jpg")


def site_work_path(org_id: uuid.UUID, content_hash: str) -> str:
    return path_for(org_id, "work", f"{content_hash}.jpg")


def site_thumb_path(org_id: uuid.UUID, content_hash: str) -> str:
    return path_for(org_id, "thumbs", f"{content_hash}.jpg")


UPLOAD_ATTEMPTS = 3

# Site images are named by content hash: the object at a path never changes, so the
# browser may keep it for a year instead of Storage's one-hour default.
IMMUTABLE_CACHE_SECONDS = str(365 * 24 * 3600)


def is_transient(exc: Exception) -> bool:
    """A network hiccup worth another try (reset connection, busy socket, timeout), not a refusal."""
    if isinstance(exc, (httpx.TransportError, ConnectionError, TimeoutError)):
        return True
    return isinstance(exc, OSError) and not isinstance(exc, FileNotFoundError)


def upload(client: Client, bucket: str, path: str, data: bytes, *, content_type: Optional[str] = None) -> None:
    """Upsert, so trying again is harmless: a dropped connection is retried twice before giving up."""
    options = {"upsert": "true"}
    if bucket == BUCKET_SITE_IMAGES:
        options["cache-control"] = IMMUTABLE_CACHE_SECONDS
    if content_type:
        options["content-type"] = content_type
    for attempt in range(1, UPLOAD_ATTEMPTS + 1):
        try:
            client.storage.from_(bucket).upload(path, data, file_options=options)
            forget_signed(bucket, [path])
            return
        except Exception as exc:
            if attempt == UPLOAD_ATTEMPTS or not is_transient(exc):
                raise
            time.sleep(0.5 * attempt)


def download(client: Client, bucket: str, path: str) -> bytes:
    return client.storage.from_(bucket).download(path)


# --- the worker's local copy of what it downloads -------------------------------
#
# The worker reads the same images at every comparison (the keypoint check of
# CLIP candidates) and at every index job. Each download is billed egress, so
# it keeps them on its own disk: NYRA_IMAGE_CACHE (default ~/.cache/nyra/images,
# "off" to disable), at most NYRA_IMAGE_CACHE_MB (default 2048), least recently
# used first out. The web process never uses it.

_cache_lock = threading.Lock()
_cache_writes = 0


def _cache_root() -> Optional[Path]:
    setting = os.environ.get("NYRA_IMAGE_CACHE", "").strip()
    if setting.lower() in {"off", "0", "false", "none"}:
        return None
    return Path(setting) if setting else Path.home() / ".cache" / "nyra" / "images"


def _prune(root: Path) -> None:
    limit = int(os.environ.get("NYRA_IMAGE_CACHE_MB", "2048")) * 1_000_000
    files = [(entry.stat().st_atime, entry.stat().st_size, entry) for entry in root.rglob("*") if entry.is_file()]
    total = sum(size for _, size, _ in files)
    if total <= limit:
        return
    for _, size, entry in sorted(files, key=lambda item: item[0]):
        entry.unlink(missing_ok=True)
        total -= size
        if total <= limit * 0.8:
            break


def cached_download(client: Client, bucket: str, path: str, *, version: str = "") -> bytes:
    """`download`, through the worker's disk cache.

    `version` changes when the object at `path` does (a reference re-uploaded
    under the same name): pass the image's hash, and a stale copy is never served.
    """
    global _cache_writes
    root = _cache_root()
    if root is None:
        return download(client, bucket, path)
    key = hashlib.sha1(f"{bucket}/{path}#{version}".encode("utf-8")).hexdigest()
    target = root / key[:2] / key
    try:
        data = target.read_bytes()
        os.utime(target)  # most recently used
        return data
    except OSError:
        pass
    data = download(client, bucket, path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_suffix(f".{os.getpid()}.{threading.get_ident()}.part")
        partial.write_bytes(data)
        partial.replace(target)
        with _cache_lock:
            _cache_writes += 1
            if _cache_writes % 200 == 0:
                _prune(root)
    except OSError:
        pass  # a full or read-only disk only costs the cache
    return data


def exists(client: Client, bucket: str, path: str) -> bool:
    return client.storage.from_(bucket).exists(path)


def delete(client: Client, bucket: str, paths: Iterable[Optional[str]]) -> None:
    paths = [path for path in paths if path]
    if paths:
        client.storage.from_(bucket).remove(paths)
        forget_signed(bucket, paths)


LIST_BATCH = 100
LIST_MAX_CALLS = 200_000  # per pass: 20 million objects at LIST_BATCH each, far past any organization, but the loop ends
DELETE_MAX_PASSES = 10_000


def _list_pass(api, prefix: str, *, remove: bool) -> tuple[int, int]:
    """Walk the folders under `prefix/` once: (entries seen, files removed or, without `remove`, files counted).

    Each folder is listed from its start and its files are removed, so a folder holding more than `LIST_BATCH`
    entries is finished by the next pass (an emptied folder disappears from the listing). Without `remove`
    nothing disappears, so a folder is listed page after page instead."""
    todo, seen, files_done = [(prefix, 0)], 0, 0
    for _ in range(LIST_MAX_CALLS):
        if not todo:
            break
        folder, offset = todo.pop()
        entries = api.list(folder, {"limit": LIST_BATCH, "offset": offset}) or []
        files = [f"{folder}/{entry['name']}" for entry in entries if entry.get("id")]
        if files and remove:
            api.remove(files)
        seen, files_done = seen + len(entries), files_done + len(files)
        if not remove and len(entries) >= LIST_BATCH:
            todo.append((folder, offset + len(entries)))
        todo.extend((f"{folder}/{entry['name']}", 0) for entry in entries if not entry.get("id"))
    else:
        raise RuntimeError(f"listing {prefix}/ did not end after {LIST_MAX_CALLS} calls")
    return seen, files_done


def delete_prefix(client: Client, bucket: str, prefix: str, *, dry_run: bool = False) -> int:
    """Delete every object under `prefix/` (folders included, any depth) and return how many; with `dry_run`,
    only count them.

    Passes repeat until one finds nothing. A pass that finds objects and removes none (Storage accepts the call
    and keeps them) stops with an error instead of reporting a deletion that did not happen."""
    api = client.storage.from_(bucket)
    if dry_run:
        return _list_pass(api, prefix, remove=False)[1]
    deleted = 0
    for _ in range(DELETE_MAX_PASSES):
        seen, removed = _list_pass(api, prefix, remove=True)
        deleted += removed
        if not seen:
            return deleted
        if not removed:
            break
    raise RuntimeError(f"{bucket}/{prefix}/ is not empty after deleting {deleted} object(s)")


def signed_url(client: Client, bucket: str, path: str, *, expires_in: int = SIGNED_URL_TTL_SECONDS) -> str:
    result = client.storage.from_(bucket).create_signed_url(path, expires_in)
    url = result.get("signedUrl") or result.get("signedURL")
    if not url:
        raise RuntimeError(f"Supabase Storage did not return a signed URL for {bucket}/{path}")
    return url


# Signed URLs are handed out again while they have at least this long to live.
# The same image then keeps the same URL across page loads and list refreshes,
# so the browser serves it from its cache instead of downloading it again
# (every download is billed egress).
SIGNED_URL_REUSE_MARGIN_SECONDS = 2400
# ...but only while it has at least this long to live: the interface keeps a thumbnail URL up to
# ~30 minutes before it asks again, so a URL handed out must outlive that.

# The bound is per scope (bucket, organization: the first path segment), so a large
# tenant only evicts its own entries and never defeats the browser cache of the others.
# An entry is the path, the URL (~480 characters) and a tuple: ~800 bytes measured, so a
# full scope is ~2.4 MB and the 16 scopes together stay under ~40 MB (the web container
# has 256-512 MB). Within a scope the least recently used entry goes first. Known
# degenerate case: one tenant listing more than SIGNED_URL_CACHE_MAX paths again and again
# walks them in a cycle and re-signs them all, as there is no room to keep them.
SIGNED_URL_CACHE_MAX = 3_000
SIGNED_URL_SCOPES_MAX = 16  # dormant tenants are dropped, least recently used first

_signed: dict[tuple[str, str], dict[str, tuple[str, float]]] = {}
_signed_lock = threading.Lock()


def _scope(bucket: str, path: str) -> dict[str, tuple[str, float]]:
    """The entries of a path's scope, now the most recently used one (call under `_signed_lock`)."""
    key = (bucket, path.split("/", 1)[0])
    scope = _signed.pop(key, None) or {}
    _signed[key] = scope
    while len(_signed) > SIGNED_URL_SCOPES_MAX:
        del _signed[next(iter(_signed))]  # dicts iterate in insertion order: least recently used first
    return scope


def _remember_signed(bucket: str, path: str, url: str, expires_at: float) -> None:
    """Keep a URL for reuse; when its scope is full, drop its least recently used one."""
    with _signed_lock:
        scope = _scope(bucket, path)
        scope.pop(path, None)  # a refreshed URL goes to the back of the line and evicts nobody
        while len(scope) >= SIGNED_URL_CACHE_MAX:
            del scope[next(iter(scope))]
        scope[path] = (url, expires_at)


def forget_signed(bucket: str, paths: Iterable[Optional[str]]) -> None:
    """An object changed or went away: its next URL must be a fresh one."""
    with _signed_lock:
        for path in paths:
            key = (bucket, path.split("/", 1)[0]) if path else None
            if key in _signed:
                _signed[key].pop(path, None)
                if not _signed[key]:
                    del _signed[key]


def signed_urls(
    client: Client, bucket: str, paths: Iterable[Optional[str]], *, expires_in: int = SIGNED_URL_TTL_SECONDS
) -> dict[str, str]:
    """path -> signed URL for every distinct non-empty path, in one request for those not signed recently.

    A path Storage doesn't know is simply missing from the result.
    """
    unique = sorted({path for path in paths if path})
    if not unique:
        return {}
    now = time.monotonic()
    out: dict[str, str] = {}
    with _signed_lock:
        for path in unique:
            cached = _scope(bucket, path).get(path)
            if cached and cached[1] - now > SIGNED_URL_REUSE_MARGIN_SECONDS:
                out[path] = cached[0]
                scope = _scope(bucket, path)
                scope[path] = scope.pop(path)  # used: it goes to the back, so a hot thumbnail is the last to be evicted
    missing = [path for path in unique if path not in out]
    for start in range(0, len(missing), 1000):
        chunk = missing[start : start + 1000]
        for item in client.storage.from_(bucket).create_signed_urls(chunk, expires_in) or []:
            url = item.get("signedURL") or item.get("signedUrl")
            if url and item.get("path") and not item.get("error"):
                out[item["path"]] = url
                _remember_signed(bucket, item["path"], url, now + expires_in)
    return out
