"""`nyra serve` (what the web image runs) hands the web process every setting of the environment, e-mail included."""

from __future__ import annotations

from typer.testing import CliRunner

from nyra import cli

ENV = {
    "DATABASE_URL": "postgresql://user:unit-test-value@localhost:5432/nyra",
    "SUPABASE_URL": "https://example.supabase.co",
    "SUPABASE_SERVICE_ROLE_KEY": "unit-test-value",
    "SUPABASE_ANON_KEY": "unit-test-value",
    "SENTRY_DSN": "",  # empty but present: the `.env` file of a developer is never read over it
    "SENTRY_BROWSER_DSN": "",
    "NYRA_PUBLIC_URL": "https://nyra.example.com",
    "RESEND_API_KEY": "unit-test-value",
    "NYRA_MAIL_FROM": "Nyra <nyra@example.com>",
}


def _serve(monkeypatch, **changes: str):
    """Run `nyra serve` without a server and return the settings the web process was built with."""
    built: dict = {}
    for name, value in {**ENV, **changes}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr("nyra.cloud.api.create_app", lambda settings: built.setdefault("settings", settings))
    monkeypatch.setattr("uvicorn.run", lambda *args, **kwargs: None)
    result = CliRunner().invoke(cli.app, ["serve"])
    assert result.exit_code == 0, result.output
    return built["settings"]


def test_the_web_process_gets_the_mail_settings_of_the_environment(monkeypatch):
    settings = _serve(monkeypatch)
    assert settings.resend_api_key == "unit-test-value"
    assert settings.mail_from == "Nyra <nyra@example.com>"
    assert settings.public_url == "https://nyra.example.com"


def test_a_key_without_a_sender_leaves_the_mail_off_without_stopping_the_web_process(monkeypatch):
    settings = _serve(monkeypatch, NYRA_MAIL_FROM="")
    assert settings.resend_api_key == "unit-test-value" and settings.mail_from == ""
