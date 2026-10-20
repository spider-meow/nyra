"""Security headers on the interface and on the API. No database needed."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("psycopg")

from fastapi.testclient import TestClient

from nyra.cloud.api import CloudSettings, create_app


def test_interface_and_api_send_the_security_headers():
    settings = CloudSettings(
        database_url="postgresql://unused", supabase_url="https://x.supabase.co", service_role_key="key", jwt_secret=None,
    )
    client = TestClient(create_app(settings))
    for path in ("/", "/login", "/api/auth/config", "/api/does-not-exist"):
        headers = client.get(path).headers
        assert headers["x-frame-options"] == "DENY", path
        assert headers["x-content-type-options"] == "nosniff", path
        assert headers["referrer-policy"] == "strict-origin-when-cross-origin", path
    # The CSP governs documents: the interface carries it, the JSON API does not need it.
    assert "frame-ancestors 'none'" in client.get("/login").headers["content-security-policy"]
