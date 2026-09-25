"""nyra/cloud/storage.py: what saves egress (worker disk cache, reused signed URLs)."""

from __future__ import annotations

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

    def remove(self, paths):
        for path in paths:
            self.owner.objects.pop((self.bucket, path), None)

    def create_signed_urls(self, paths, expires_in, options=None):
        self.owner.signings += 1
        return [{"path": p, "signedURL": f"https://s.test/{self.bucket}/{p}?v={self.owner.signings}", "error": None} for p in paths]


class CountingClient:
    def __init__(self):
        self.objects, self.downloads, self.signings = {}, 0, 0
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
