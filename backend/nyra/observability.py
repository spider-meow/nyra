"""Logs and error reporting for the web and worker processes.

Both are opt-in through the environment, so a local run stays as it was:

- `NYRA_LOG_FORMAT=json` writes one JSON object per line (what Grafana
  Loki, Better Stack & co. parse without configuration), with the
  organization and job of the work in progress when there is one.
- `SENTRY_DSN` sends exceptions (and a sample of request/job timings,
  `SENTRY_TRACES_SAMPLE_RATE`, default 0.1) to Sentry. Needs the
  `sentry-sdk` package (`pip install -e ".[observability]"`).

`job_scope` tags everything logged or reported while a job runs.
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator, Optional

_org_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("nyra_org_id", default=None)
_job_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("nyra_job_id", default=None)
_job_kind: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("nyra_job_kind", default=None)

TEXT_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


class JsonFormatter(logging.Formatter):
    def __init__(self, process: str):
        super().__init__()
        self.process = process

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "process": self.process,
            "message": record.getMessage(),
        }
        for key, var in (("org_id", _org_id), ("job_id", _job_id), ("job_kind", _job_kind)):
            value = var.get()
            if value:
                entry[key] = value
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def configure_logging(process: str) -> None:
    """Root logging for `nyra serve` / `nyra worker`: level and format from the environment."""
    handler = logging.StreamHandler()
    if os.environ.get("NYRA_LOG_FORMAT", "").lower() == "json":
        handler.setFormatter(JsonFormatter(process))
    else:
        handler.setFormatter(logging.Formatter(TEXT_FORMAT))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(os.environ.get("NYRA_LOG_LEVEL", "INFO").upper())
    # One line per image request drowns everything else.
    for noisy in ("httpx", "httpcore", "huggingface_hub"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def init_sentry(process: str) -> bool:
    """Start Sentry if `SENTRY_DSN` is set and the SDK is installed. Returns whether it started."""
    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if not dsn:
        return False
    try:
        import sentry_sdk
    except ImportError:
        logging.getLogger("nyra").warning("SENTRY_DSN is set but sentry-sdk is not installed; errors are not reported.")
        return False
    try:
        rate = float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0.1"))
    except ValueError:
        rate = 0.1
    sentry_sdk.init(
        dsn=dsn,
        environment=os.environ.get("NYRA_ENV", "production"),
        release=os.environ.get("NYRA_RELEASE") or None,
        traces_sample_rate=rate,
        # Reference filenames and site URLs are client data: keep request
        # bodies and personal data out of Sentry.
        send_default_pii=False,
        max_request_body_size="never",
    )
    sentry_sdk.set_tag("process", process)
    return True


@contextmanager
def job_scope(*, org_id: str, job_id: str, kind: str) -> Iterator[None]:
    """Tag logs (and Sentry events, when enabled) with the job being run."""
    tokens = (_org_id.set(org_id), _job_id.set(job_id), _job_kind.set(kind))
    try:
        try:
            import sentry_sdk
        except ImportError:
            sentry_sdk = None
        if sentry_sdk is not None:
            with sentry_sdk.new_scope() as scope:
                scope.set_tag("org_id", org_id)
                scope.set_tag("job_kind", kind)
                scope.set_tag("job_id", job_id)
                yield
        else:
            yield
    finally:
        _job_kind.reset(tokens[2])
        _job_id.reset(tokens[1])
        _org_id.reset(tokens[0])
