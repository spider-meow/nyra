"""Browser Sentry: what the server hands the page (`/api/auth/config`) and the one origin the
Content-Security-Policy opens for it. No database needed."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("psycopg")

from fastapi.testclient import TestClient

from nyra.cloud.api import CloudSettings, create_app
from nyra.observability import sentry_ingest_origin

DSN = "https://abc123@o1.ingest.de.sentry.io/42"


def _client(dsn: str = "") -> TestClient:
    settings = CloudSettings(
        database_url="postgresql://unused", supabase_url="https://x.supabase.co", service_role_key="key",
        jwt_secret=None, anon_key="anon", sentry_browser_dsn=dsn,
    )
    return TestClient(create_app(settings))


def _connect_src(client: TestClient) -> str:
    csp = client.get("/").headers["Content-Security-Policy"]
    return next(part for part in csp.split("; ") if part.startswith("connect-src"))


def test_csp_is_unchanged_and_config_has_no_sentry_without_a_dsn():
    client = _client()
    assert _connect_src(client) == "connect-src 'self' https://x.supabase.co"
    assert client.get("/api/auth/config").json() == {"supabaseUrl": "https://x.supabase.co", "anonKey": "anon", "sentry": None}


def test_csp_opens_only_the_ingest_origin(monkeypatch):
    monkeypatch.setenv("NYRA_ENV", "staging")
    monkeypatch.setenv("NYRA_RELEASE", "abc1234")
    monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", "0.25")
    client = _client(DSN)
    assert _connect_src(client) == "connect-src 'self' https://x.supabase.co https://o1.ingest.de.sentry.io"
    assert "abc123" not in client.get("/").headers["Content-Security-Policy"]  # the key stays out of the header
    assert client.get("/api/auth/config").json()["sentry"] == {
        "dsn": DSN, "environment": "staging", "release": "abc1234", "tracesSampleRate": 0.25,
    }


def test_config_defaults_follow_the_server_defaults(monkeypatch):
    for name in ("NYRA_ENV", "NYRA_RELEASE", "SENTRY_TRACES_SAMPLE_RATE"):
        monkeypatch.delenv(name, raising=False)
    assert _client(DSN).get("/api/auth/config").json()["sentry"] == {
        "dsn": DSN, "environment": "production", "release": "", "tracesSampleRate": 0.1,
    }
    monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", "lots")
    assert _client(DSN).get("/api/auth/config").json()["sentry"]["tracesSampleRate"] == 0.1


@pytest.mark.parametrize("dsn", [
    "not a dsn",
    "http://abc@o1.ingest.sentry.io/42",  # not https
    "https://o1.ingest.sentry.io/42",  # no public key
    "https://abc@o1.ingest.sentry.io/",  # no project
    "https://abc@evil.com; script-src *@o1.ingest.sentry.io/42",  # would inject into the header
    "https://abc@o1.ingest.sentry.io/42\r\nX-Evil: 1",
    "https://abc@*.sentry.io/42",
    "https://k@o1.ingest.\u017fentry.io/42",  # U+017F "long s" matches [a-z] under IGNORECASE: cannot go in a header
    "https://k@o1.ingest.sentry.io:\u0668\u0660/42",  # Arabic-Indic digits
    "https://k@o1.ingest.sentry.io:99999/42",  # not a port
])
def test_malformed_dsn_is_ignored(dsn):
    client = _client(dsn)
    assert _connect_src(client) == "connect-src 'self' https://x.supabase.co"
    assert client.get("/api/auth/config").json()["sentry"] is None


def test_ingest_origin_keeps_host_and_port_only():
    assert sentry_ingest_origin("https://k@Sentry.Example.com:9000/prefix/7") == "https://sentry.example.com:9000"
    assert sentry_ingest_origin("https://k:legacy@o1.ingest.sentry.io/42") == "https://o1.ingest.sentry.io"
    assert sentry_ingest_origin("") == ""


def test_the_legacy_secret_of_a_dsn_is_never_published():
    config = _client("https://k:legacysecret@o1.ingest.sentry.io/42").get("/api/auth/config").json()["sentry"]
    assert config["dsn"] == "https://k@o1.ingest.sentry.io/42"


def test_an_unusable_setting_is_logged_once_at_startup(caplog, monkeypatch):
    monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", "2")
    with caplog.at_level("WARNING", logger="nyra"):
        _client("https://k@o1.ingest.sentry.io/42/")  # a trailing slash is not a DSN
    messages = [record.getMessage() for record in caplog.records]
    assert any("SENTRY_BROWSER_DSN is set but is not a usable DSN" in message for message in messages)
    assert any("SENTRY_TRACES_SAMPLE_RATE='2'" in message for message in messages)
    caplog.clear()
    with caplog.at_level("WARNING", logger="nyra"):
        monkeypatch.delenv("SENTRY_TRACES_SAMPLE_RATE")
        _client(DSN)  # a good DSN and the default rate say nothing
    assert not [record for record in caplog.records if record.name == "nyra"]
