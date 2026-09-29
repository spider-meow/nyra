"""JSON logs tagged with the job in progress, and Sentry staying off without a DSN."""

from __future__ import annotations

import json
import logging

from nyra import observability


def _format(formatter: logging.Formatter, message: str) -> dict:
    record = logging.LogRecord("nyra.worker", logging.INFO, __file__, 1, message, None, None)
    return json.loads(formatter.format(record))


def test_json_lines_carry_the_job_only_while_it_runs():
    formatter = observability.JsonFormatter("worker")
    outside = _format(formatter, "idle")
    assert outside["message"] == "idle" and outside["process"] == "worker" and "job_id" not in outside

    with observability.job_scope(org_id="org-1", job_id="job-1", kind="crawl"):
        inside = _format(formatter, "crawling")
    assert (inside["org_id"], inside["job_id"], inside["job_kind"]) == ("org-1", "job-1", "crawl")
    assert "job_id" not in _format(formatter, "after")


def test_log_format_follows_the_environment(monkeypatch):
    monkeypatch.setenv("NYRA_LOG_FORMAT", "json")
    observability.configure_logging("web")
    assert isinstance(logging.getLogger().handlers[0].formatter, observability.JsonFormatter)
    monkeypatch.setenv("NYRA_LOG_FORMAT", "")
    observability.configure_logging("web")
    assert not isinstance(logging.getLogger().handlers[0].formatter, observability.JsonFormatter)


def test_sentry_is_off_without_a_dsn(monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    assert observability.init_sentry("web") is False
