"""nyra/cloud/storage.py: what saves egress (worker disk cache, reused signed URLs)."""

from __future__ import annotations

import os

import pytest

pytest.importorskip("supabase")

from nyra.cloud import storage  # noqa: E402


class CountingBucket:
    def __init__(self, owner, bucket):
        self.owner, self.bucket = owner, bucket

    def download(self, path, options=None, query_params=None):
        self.owner.downloads += 1
        return self.owner.objects[(self.bucket, path)]

    def upload(self, path, file, file_options=None):
        self.owner.objects[(self.bucket, path)] = file
        self.owner.options[(self.bucket, path)] = file_options

    def remove(self, paths):
        for path in paths:
            self.owner.objects.pop((self.bucket, path), None)

    def create_signed_urls(self, paths, expires_in, options=None):
        self.owner.signings += 1
        return [{"path": p, "signedURL": f"https://s.test/{self.bucket}/{p}?v={self.owner.signings}", "error": None} for p in paths]


class CountingClient:
    def __init__(self):
        self.objects, self.options, self.downloads, self.signings = {}, {}, 0, 0
        self.storage = self

    def from_(self, bucket):
        return CountingBucket(self, bucket)


def test_the_worker_downloads_an_image_once_until_it_changes(tmp_path, monkeypatch):
    monkeypatch.setenv("NYRA_IMAGE_CACHE", str(tmp_path))
    client = CountingClient()
    client.objects[("refs", "o/b/work/a.jpg")] = b"v1"
    assert storage.cached_download(client, "refs", "o/b/work/a.jpg", version="h1") == b"v1"
    assert storage.cached_download(client, "refs", "o/b/work/a.jpg", version="h1") == b"v1"
    assert client.downloads == 1
    client.objects[("refs", "o/b/work/a.jpg")] = b"v2"  # re-uploaded under the same name: new hash
    assert storage.cached_download(client, "refs", "o/b/work/a.jpg", version="h2") == b"v2"
    assert client.downloads == 2


def test_the_cache_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("NYRA_IMAGE_CACHE", "off")
    client = CountingClient()
    client.objects[("refs", "x")] = b"data"
    storage.cached_download(client, "refs", "x")
    storage.cached_download(client, "refs", "x")
    assert client.downloads == 2


def test_signed_urls_are_reused_so_the_browser_can_cache_images():
    client = CountingClient()
    first = storage.signed_urls(client, "reports", ["p/1.jpg", "p/2.jpg"])
    again = storage.signed_urls(client, "reports", ["p/2.jpg", "p/1.jpg"])
    assert again == first and client.signings == 1
    storage.upload(client, "reports", "p/1.jpg", b"new")  # changed: it gets a new URL
    fresh = storage.signed_urls(client, "reports", ["p/1.jpg", "p/2.jpg"])
    assert fresh["p/1.jpg"] != first["p/1.jpg"] and fresh["p/2.jpg"] == first["p/2.jpg"]
    assert client.signings == 2


def test_signed_urls_cache_is_bounded_per_tenant_and_keeps_urls_while_valid(monkeypatch):
    monkeypatch.setattr(storage, "SIGNED_URL_CACHE_MAX", 3)
    storage._signed.clear()
    try:
        client = CountingClient()
        small = storage.signed_urls(client, "refs", ["o2/x"])
        first = storage.signed_urls(client, "refs", ["o1/a"])
        assert storage.signed_urls(client, "refs", ["o1/a"]) == first and client.signings == 2  # same URL while valid
        storage.signed_urls(client, "refs", [f"o1/{name}" for name in "bcde"])
        scope = storage._signed[("refs", "o1")]
        assert len(scope) == 3 and "o1/a" not in scope and "o1/e" in scope  # oldest of that tenant dropped first
        assert storage.signed_urls(client, "refs", ["o2/x"]) == small  # the other tenant keeps its URL
        # A refreshed entry goes to the back and evicts nobody, even when the scope is full.
        storage._remember_signed("refs", "o1/c", "fresh", 1e12)
        assert len(scope) == 3 and list(scope)[-1] == "o1/c" and "o1/d" in scope and "o1/e" in scope
        storage.forget_signed("refs", ["o2/x", "o1/c", None])
        assert ("refs", "o2") not in storage._signed and "o1/c" not in scope  # an emptied scope is dropped
    finally:
        storage._signed.clear()


def test_a_tenant_over_its_cap_does_not_evict_the_others_and_a_cyclic_walk_resigns_only_itself(monkeypatch):
    monkeypatch.setattr(storage, "SIGNED_URL_CACHE_MAX", 4)
    storage._signed.clear()
    try:
        client = CountingClient()
        urls = {path: storage.signed_urls(client, "refs", [path])[path] for path in ("o2/p", "o3/p")}
        big = [f"o1/{i}" for i in range(5)]  # cap + 1 paths of one tenant, walked in the same order each time
        for _ in range(3):
            for path in big:
                storage.signed_urls(client, "refs", [path])
        assert client.signings == 2 + 15  # known degenerate case: the cycle never finds its URL, only for that tenant
        assert all(storage.signed_urls(client, "refs", [path])[path] == url for path, url in urls.items())
        assert client.signings == 2 + 15  # the others were served from the cache
    finally:
        storage._signed.clear()


def test_signed_url_scopes_are_bounded_too(monkeypatch):
    monkeypatch.setattr(storage, "SIGNED_URL_SCOPES_MAX", 2)
    storage._signed.clear()
    try:
        client = CountingClient()
        for org in ("o1", "o2", "o1", "o3"):  # o1 is used again, so o2 is the least recently used
            storage.signed_urls(client, "refs", [f"{org}/p"])
        assert list(storage._signed) == [("refs", "o1"), ("refs", "o3")]
    finally:
        storage._signed.clear()


def test_a_url_in_use_is_the_last_to_be_evicted(monkeypatch):
    monkeypatch.setattr(storage, "SIGNED_URL_CACHE_MAX", 3)
    storage._signed.clear()
    try:
        client = CountingClient()
        storage.signed_urls(client, "refs", ["org/a", "org/b", "org/c"])
        storage.signed_urls(client, "refs", ["org/a"])  # a hit: "a" is now the most recently used
        storage.signed_urls(client, "refs", ["org/d"])  # full: the least recently used ("b") goes, not "a"
        kept = set(storage._signed[("refs", "org")])
        assert kept == {"org/a", "org/c", "org/d"}
    finally:
        storage._signed.clear()


def test_only_hash_named_site_images_get_a_long_cache_control():
    client = CountingClient()
    storage.upload(client, "site-images", "o/thumbs/h.jpg", b"x", content_type="image/jpeg")
    storage.upload(client, "refs", "o/b/a.jpg", b"x", content_type="image/jpeg")
    assert client.options[("site-images", "o/thumbs/h.jpg")]["cache-control"] == "31536000"
    assert "cache-control" not in client.options[("refs", "o/b/a.jpg")]


def test_trim_cache_brings_the_cache_under_its_size_at_once_oldest_first(tmp_path, monkeypatch):
    monkeypatch.setenv("NYRA_IMAGE_CACHE", str(tmp_path))
    monkeypatch.setenv("NYRA_IMAGE_CACHE_MB", "1")
    for age, name in enumerate(("newest", "middle", "oldest")):
        (tmp_path / name).write_bytes(b"x" * 600_000)
        os.utime(tmp_path / name, (1_000_000 - age, 1_000_000 - age))
    storage.trim_cache()
    # 1.8 MB for a 1 MB limit: the oldest goes, and the trim stops at 80 % of the limit (800 kB), so one file is left
    assert not (tmp_path / "oldest").exists() and not (tmp_path / "middle").exists() and (tmp_path / "newest").exists()
    monkeypatch.setenv("NYRA_IMAGE_CACHE", "off")
    storage.trim_cache()  # no cache: nothing to do
